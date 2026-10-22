#!/bin/bash
# Headless-запуск агента для одного клієнта.
# Використання: run_agent.sh <slug> evening|morning
# Env: APPLY_MODE=dry-run — trial-режим для morning (читає агент-промпт).
#      JOBSEARCH_RUN_NOTE="..." — разова вказівка оператора агенту.
#
# Конфіг машини — /root/.claude-env (поза git):
#   CLAUDE_CODE_OAUTH_TOKEN     токен з `claude setup-token`
#   JOBSEARCH_CLIENTS_DIR       каталог clients/ у клоні репозиторію даних
#   JOBSEARCH_OPERATOR_EMAIL    кому йдуть звіти й запрошення на співбесіди
#   JOBSEARCH_ALERT_TO          кому йдуть алерти про збої
#   JOBSEARCH_OPERATOR_MAIL     evening (дефолт): оператору один зведений лист після
#                               вечірнього рану, вночі нічого; always: лист після кожного рану
#   PERPLEXITY_API_KEY, FIRECRAWL_API_KEY, CLAUDE_MODEL (опційно)
set -uo pipefail

SLUG="${1:?usage: run_agent.sh <slug> evening|morning}"
MODE="${2:?usage: run_agent.sh <slug> evening|morning}"
ENGINE="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

# Headless auth і конфіг машини
[ -f "${JOBSEARCH_ENV_FILE:-/root/.claude-env}" ] && set -a && source "${JOBSEARCH_ENV_FILE:-/root/.claude-env}" && set +a

# LXC працює під root; без цього claude відмовляє у --dangerously-skip-permissions
export IS_SANDBOX=1
export JOBSEARCH_CLIENT="$SLUG"
export JOBSEARCH_ENGINE="$ENGINE"
export JOBSEARCH_CLIENTS_DIR="${JOBSEARCH_CLIENTS_DIR:-$ENGINE/../jobsearch-clients/clients}"
export PATH="$ENGINE/bin:$PATH"
# discover і портальні подачі тривають довше за стандартні 10 хв Bash-інструмента.
# Без цього агент відправляє їх у фон і завершує хід, а headless-процес на
# цьому виходить: команда гине, ран лишається без результату.
export BASH_DEFAULT_TIMEOUT_MS="${BASH_DEFAULT_TIMEOUT_MS:-1800000}"
export BASH_MAX_TIMEOUT_MS="${BASH_MAX_TIMEOUT_MS:-3600000}"

ROOT="$JOBSEARCH_CLIENTS_DIR/$SLUG"
PY="$ENGINE/.venv/bin/python"
DATE="$(date +%Y-%m-%d)"
LOG_DIR="$ROOT/var/logs"
LOCK_DIR="${JOBSEARCH_LOCK_DIR:-/var/lock}"
LOG="$LOG_DIR/${MODE}_${DATE}.log"

if [ ! -d "$ROOT" ]; then
  echo "[$(date)] клієнта '$SLUG' не знайдено: $ROOT" >&2
  exit 1
fi
mkdir -p "$LOG_DIR" "$ROOT/var/run"

export JOBSEARCH_OPERATOR_MAIL="${JOBSEARCH_OPERATOR_MAIL:-evening}"

alert() {  # alert <message> [log-tail]
  # evening: алерт стає в чергу й іде у вечірньому зведеному листі, вночі пошта мовчить
  local queue=""
  [ "$JOBSEARCH_OPERATOR_MAIL" = "evening" ] && queue="--queue"
  "$PY" "$ENGINE/scripts/server/send_alert.py" --run "$MODE" --message "$1" \
    ${2:+--log-tail "$2"} $queue || true
}

operator_summary() {  # один лист оператору на добу, після вечірнього рану
  [ "$JOBSEARCH_OPERATOR_MAIL" = "evening" ] && [ "$MODE" = "evening" ] || return 0
  "$PY" "$ENGINE/scripts/server/operator_summary.py" --send >> "$LOG" 2>&1 \
    || echo "[$(date)] operator_summary не надіслано" >> "$LOG"
}

# Один спільний лок на всю машину: запуски різних клієнтів і ручні сесії
# ніколи не перетинаються (спільний ліміт підписки, один дисплей).
if [ -z "${JOBSEARCH_LOCK_HELD:-}" ]; then
  exec 9>"$LOCK_DIR/jobsearch-agent.lock"
  if ! flock -n 9; then
    echo "[$(date)] $SLUG $MODE: інший запуск активний — виходжу" >> "$LOG"
    exit 0
  fi
fi

# Фікси деплояться самі: лок уже наш. --ff-only: локальні незакомічені
# зміни/дивергенція = не тягнемо, ран іде на тому, що є, і це видно в лозі.
if [ -z "${JOBSEARCH_LOCK_HELD:-}" ]; then
  ENGINE_BEFORE="$(git -C "$ENGINE" rev-parse HEAD 2>/dev/null || true)"
  git -C "$ENGINE" pull --ff-only >> "$LOG" 2>&1 \
    || echo "[$(date)] $MODE: git pull рушія failed — ран на локальному стані" >> "$LOG"
  if [ "$ENGINE_BEFORE" != "$(git -C "$ENGINE" rev-parse HEAD 2>/dev/null || true)" ]; then
    # Цей скрипт щойно змінився на диску, а bash читає його по ходу виконання.
    # Стартуємо заново з нової версії; лок (fd 9) успадковується.
    echo "[$(date)] $MODE: рушій оновлено — перезапуск обгортки" >> "$LOG"
    JOBSEARCH_LOCK_HELD=1 exec "$0" "$@"
  fi
fi
git -C "$ROOT" pull --rebase >> "$LOG" 2>&1 \
  || echo "[$(date)] $MODE: git pull даних failed — ран на локальному стані" >> "$LOG"

PROMPT="$("$PY" "$ENGINE/scripts/render_run.py" "$MODE" --headless 2>> "$LOG")"
if [ -z "$PROMPT" ] || [ ! -f "$PROMPT" ]; then
  echo "[$(date)] $MODE: не вдалося зрендерити промпт" >> "$LOG"
  alert "Prompt konnte nicht gerendert werden" "$(tail -c 2000 "$LOG")"
  operator_summary
  exit 1
fi

cd "$ROOT"
# HEAD до рану — щоб потім перевірити лише Msg-ID, які агент додав у трекер
BEFORE_SHA="$(git rev-parse HEAD 2>/dev/null || true)"
echo "==== [$(date)] $SLUG $MODE run start (APPLY_MODE=${APPLY_MODE:-live}, model=${CLAUDE_MODEL:-opus}) ====" >> "$LOG"

set +e
# Модель: Opus за замовчуванням (менше галюцинує статуси/Msg-ID).
# Override: CLAUDE_MODEL=... у файлі оточення.
# JOBSEARCH_RUN_NOTE: разова вказівка оператора до цього запуску (репетиція,
# звужений обсяг). Дописується в кінець промпта.
PROMPT_TEXT="$(cat "$PROMPT")"
if [ -n "${JOBSEARCH_RUN_NOTE:-}" ]; then
  PROMPT_TEXT="$PROMPT_TEXT

## Примітка оператора до цього запуску (прямий запит, має пріоритет)

$JOBSEARCH_RUN_NOTE"
  echo "[$(date)] run note: $JOBSEARCH_RUN_NOTE" >> "$LOG"
fi
timeout 3h claude -p "$PROMPT_TEXT" \
  --model "${CLAUDE_MODEL:-opus}" \
  --mcp-config "$ROOT/var/run/mcp.json" --strict-mcp-config \
  --settings "$ROOT/var/run/settings.json" \
  --dangerously-skip-permissions \
  >> "$LOG" 2>&1
EXIT_CODE=$?
# set -e тут НЕ вмикати: перевірки нижче мають право впасти, а їхній код
# читається явно. З -e перший же збій мовчки обривав скрипт до алертів.

echo "==== [$(date)] $SLUG $MODE run end (exit $EXIT_CODE) ====" >> "$LOG"

# Msg-ID валідатор: ловить сфабриковані Msg-ID, які агент міг вписати у трекер.
if [ -n "${BEFORE_SHA:-}" ]; then
  MSGID_OUT="$("$PY" "$ENGINE/scripts/validate_run_msgids.py" --base "$BEFORE_SHA" 2>&1)"
  MSGID_RC=$?
  echo "$MSGID_OUT" >> "$LOG"
  [ $MSGID_RC -ne 0 ] && alert "Erfundene oder fehlende Msg-IDs im Tracker, bitte prüfen und korrigieren" "$MSGID_OUT"
fi

# Вартовий відповідей роботодавців: чи не лежить незакрита співбесіда/запит
# документів, яку ран пропустив.
EMP_OUT="$("$PY" "$ENGINE/scripts/employer_scan.py" --days 3 2>&1)"
EMP_RC=$?
echo "$EMP_OUT" >> "$LOG"
[ $EMP_RC -eq 2 ] && alert "Offene Antwort eines Arbeitgebers (Einladung oder Rückfrage), heute beantworten" "$EMP_OUT"
# Сам скан упав (типово: протух OAuth-токен скриньки) — пошту ніхто не
# вартує, запрошення може пролежати непоміченим.
[ $EMP_RC -ne 0 ] && [ $EMP_RC -ne 2 ] && alert "employer_scan fehlgeschlagen (exit $EMP_RC), OAuth der Postfächer prüfen" "$(echo "$EMP_OUT" | tail -c 1500)"

# Успіх = exit 0 І маркер завершення в лозі
MARKER="$(echo "$MODE" | tr '[:lower:]' '[:upper:]') OK"
if [ $EXIT_CODE -ne 0 ] || ! grep -q "$MARKER" "$LOG"; then
  TAIL="$(tail -c 3000 "$LOG")"
  if echo "$TAIL" | grep -qiE "limit reached|rate.?limit|usage limit"; then
    REASON="Claude-Limit erreicht"
  elif [ $EXIT_CODE -eq 124 ]; then
    REASON="Timeout nach 3 Stunden"
  else
    REASON="Lauf fehlgeschlagen: exit=$EXIT_CODE, Marker '$MARKER' fehlt"
  fi
  alert "$REASON" "$TAIL"
fi

operator_summary
exit $EXIT_CODE
