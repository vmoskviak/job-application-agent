#!/bin/bash
# Headless agent run for one client.
# Usage: run_agent.sh <slug> evening|morning
# Env: APPLY_MODE=dry-run: trial mode for morning (the agent prompt reads it).
#      JOBSEARCH_RUN_NOTE="...": a one-off operator instruction to the agent.
#
# Machine config is /root/.claude-env (outside git):
#   CLAUDE_CODE_OAUTH_TOKEN     token from `claude setup-token`
#   JOBSEARCH_CLIENTS_DIR       the clients/ directory in the data repo clone
#   JOBSEARCH_OPERATOR_EMAIL    who gets reports and interview invitations
#   JOBSEARCH_ALERT_TO          who gets failure alerts
#   JOBSEARCH_OPERATOR_MAIL     evening (default): one summary mail to the operator after
#                               the evening run, nothing at night; always: mail after every run
#   PERPLEXITY_API_KEY, FIRECRAWL_API_KEY, CLAUDE_MODEL (optional)
set -uo pipefail

SLUG="${1:?usage: run_agent.sh <slug> evening|morning}"
MODE="${2:?usage: run_agent.sh <slug> evening|morning}"
ENGINE="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

# Headless auth and machine config
[ -f "${JOBSEARCH_ENV_FILE:-/root/.claude-env}" ] && set -a && source "${JOBSEARCH_ENV_FILE:-/root/.claude-env}" && set +a

# LXC runs as root; without this claude refuses --dangerously-skip-permissions
export IS_SANDBOX=1
export JOBSEARCH_CLIENT="$SLUG"
export JOBSEARCH_ENGINE="$ENGINE"
export JOBSEARCH_CLIENTS_DIR="${JOBSEARCH_CLIENTS_DIR:-$ENGINE/../jobsearch-clients/clients}"
export PATH="$ENGINE/bin:$PATH"
# discover and portal applications take longer than the Bash tool's default 10 min.
# Without this the agent sends them to the background and ends its turn, and the
# headless process exits at that point: the command dies, the run has no result.
export BASH_DEFAULT_TIMEOUT_MS="${BASH_DEFAULT_TIMEOUT_MS:-1800000}"
export BASH_MAX_TIMEOUT_MS="${BASH_MAX_TIMEOUT_MS:-3600000}"

ROOT="$JOBSEARCH_CLIENTS_DIR/$SLUG"
PY="$ENGINE/.venv/bin/python"
DATE="$(date +%Y-%m-%d)"
LOG_DIR="$ROOT/var/logs"
LOCK_DIR="${JOBSEARCH_LOCK_DIR:-/var/lock}"
LOG="$LOG_DIR/${MODE}_${DATE}.log"

if [ ! -d "$ROOT" ]; then
  echo "[$(date)] client '$SLUG' not found: $ROOT" >&2
  exit 1
fi
mkdir -p "$LOG_DIR" "$ROOT/var/run"

export JOBSEARCH_OPERATOR_MAIL="${JOBSEARCH_OPERATOR_MAIL:-evening}"

alert() {  # alert <message> [log-tail]
  # evening: the alert is queued and goes out in the evening summary mail, mail stays silent at night
  local queue=""
  [ "$JOBSEARCH_OPERATOR_MAIL" = "evening" ] && queue="--queue"
  "$PY" "$ENGINE/scripts/server/send_alert.py" --run "$MODE" --message "$1" \
    ${2:+--log-tail "$2"} $queue || true
}

operator_summary() {  # one mail to the operator per day; the client's report_mode decides after which run
  [ "$JOBSEARCH_OPERATOR_MAIL" = "evening" ] || return 0
  "$PY" "$ENGINE/scripts/server/operator_summary.py" --send --run "$MODE" >> "$LOG" 2>&1 \
    || echo "[$(date)] operator_summary not sent" >> "$LOG"
}

# One shared lock for the whole machine: runs of different clients and manual
# sessions never overlap (shared subscription limit, one display). We wait for the
# lock up to JOBSEARCH_LOCK_WAIT s (15 min): the inbox watcher's short LLM job holds
# the same lock, and with -n the night run would be skipped silently.
if [ -z "${JOBSEARCH_LOCK_HELD:-}" ]; then
  exec 9>"$LOCK_DIR/jobsearch-agent.lock"
  if ! flock -w "${JOBSEARCH_LOCK_WAIT:-900}" 9; then
    echo "[$(date)] $SLUG $MODE: another run has held the lock longer than ${JOBSEARCH_LOCK_WAIT:-900} s, exiting" >> "$LOG"
    exit 0
  fi
fi

# Fixes deploy themselves: the lock is already ours. --ff-only: local uncommitted
# changes/divergence = no pull, the run goes on what is there, and the log shows it.
if [ -z "${JOBSEARCH_LOCK_HELD:-}" ]; then
  ENGINE_BEFORE="$(git -C "$ENGINE" rev-parse HEAD 2>/dev/null || true)"
  git -C "$ENGINE" pull --ff-only >> "$LOG" 2>&1 || {
    echo "[$(date)] $MODE: engine git pull failed, run on local state" >> "$LOG"
    alert "Engine-Pull fehlgeschlagen (git pull --ff-only), Lauf auf lokalem Stand" \
      "$(git -C "$ENGINE" status --short 2>&1 | head -20; tail -c 1500 "$LOG")"
  }
  if [ "$ENGINE_BEFORE" != "$(git -C "$ENGINE" rev-parse HEAD 2>/dev/null || true)" ]; then
    # This script just changed on disk, and bash reads it as it runs.
    # Restart from the new version; the lock (fd 9) is inherited.
    echo "[$(date)] $MODE: engine updated, restarting the wrapper" >> "$LOG"
    JOBSEARCH_LOCK_HELD=1 exec "$0" "$@"
  fi
fi
# A failed data pull (22.10.2026: the same file created on the server and on the Mac)
# used to leave only a log line, and all runs of the day went on stale trackers. Now:
# the half-done rebase is aborted, the run goes on local state, the operator gets an
# alert with the list of files in the way.
git -C "$ROOT" pull --rebase >> "$LOG" 2>&1 || {
  git -C "$ROOT" rebase --abort >/dev/null 2>&1
  echo "[$(date)] $MODE: data git pull failed, run on local state" >> "$LOG"
  alert "Datenpull fehlgeschlagen (git pull --rebase), Lauf auf lokalem Stand" \
    "$(git -C "$ROOT" status --short 2>&1 | head -20; tail -c 1500 "$LOG")"
}

# Market without this mode (contract is evening-only for now): one log line, no
# alert and no summary mail. Otherwise every night cron would report a false failure.
"$PY" "$ENGINE/scripts/render_run.py" "$MODE" --check 2>> "$LOG"
if [ $? -eq 3 ]; then
  echo "[$(date)] $SLUG $MODE: the client's market has no such mode, skipping" >> "$LOG"
  exit 0
fi

PROMPT="$("$PY" "$ENGINE/scripts/render_run.py" "$MODE" --headless 2>> "$LOG")"
if [ -z "$PROMPT" ] || [ ! -f "$PROMPT" ]; then
  echo "[$(date)] $MODE: failed to render the prompt" >> "$LOG"
  alert "Prompt konnte nicht gerendert werden" "$(tail -c 2000 "$LOG")"
  operator_summary
  exit 1
fi

cd "$ROOT"
# HEAD before the run, so that later only the Msg-IDs the agent added to the tracker are checked
BEFORE_SHA="$(git rev-parse HEAD 2>/dev/null || true)"
echo "==== [$(date)] $SLUG $MODE run start (APPLY_MODE=${APPLY_MODE:-live}, model=${CLAUDE_MODEL:-opus}) ====" >> "$LOG"

set +e
# Model: Opus by default (hallucinates statuses/Msg-IDs less).
# Override: CLAUDE_MODEL=... in the environment file.
# JOBSEARCH_RUN_NOTE: a one-off operator instruction for this run (rehearsal,
# narrowed scope). Appended to the end of the prompt.
PROMPT_TEXT="$(cat "$PROMPT")"
if [ -n "${JOBSEARCH_RUN_NOTE:-}" ]; then
  PROMPT_TEXT="$PROMPT_TEXT

## Operator note for this run (direct request, takes priority)

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
# Do NOT enable set -e here: the checks below are allowed to fail, and their exit
# code is read explicitly. With -e the first failure silently cut the script off before the alerts.

echo "==== [$(date)] $SLUG $MODE run end (exit $EXIT_CODE) ====" >> "$LOG"

# Msg-ID validator: catches fabricated Msg-IDs the agent may have written into the tracker.
if [ -n "${BEFORE_SHA:-}" ]; then
  MSGID_OUT="$("$PY" "$ENGINE/scripts/validate_run_msgids.py" --base "$BEFORE_SHA" 2>&1)"
  MSGID_RC=$?
  echo "$MSGID_OUT" >> "$LOG"
  [ $MSGID_RC -ne 0 ] && alert "Erfundene oder fehlende Msg-IDs im Tracker, bitte prüfen und korrigieren" "$MSGID_OUT"
fi

# New interviews, offers and requests from this run go to the operator in Telegram right away
# (when the client has Telegram configured; otherwise the script is silent, the summary remains).
if [ -n "${BEFORE_SHA:-}" ]; then
  "$PY" "$ENGINE/scripts/server/notify_run.py" --base "$BEFORE_SHA" --run "$MODE" >> "$LOG" 2>&1 \
    || echo "[$(date)] notify_run failed" >> "$LOG"
fi

# Employer reply watchdog: is there an unhandled interview / document request
# that the run missed.
EMP_OUT="$("$PY" "$ENGINE/scripts/employer_scan.py" --days 3 2>&1)"
EMP_RC=$?
echo "$EMP_OUT" >> "$LOG"
[ $EMP_RC -eq 2 ] && alert "Offene Antwort eines Arbeitgebers (Einladung oder Rückfrage), heute beantworten" "$EMP_OUT"
# The scan itself failed (typically an expired mailbox OAuth token): nobody is
# watching the mail, an invitation may sit unnoticed.
[ $EMP_RC -ne 0 ] && [ $EMP_RC -ne 2 ] && alert "employer_scan fehlgeschlagen (exit $EMP_RC), OAuth der Postfächer prüfen" "$(echo "$EMP_OUT" | tail -c 1500)"

# Success = exit 0 AND the completion marker in the log
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
