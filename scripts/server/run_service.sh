#!/bin/bash
# Запуск сервісу чи таймера рушія для одного клієнта (systemd → цей скрипт).
# Використання: run_service.sh <script-relative-to-engine> <slug> [args...]
#   run_service.sh scripts/server/telegram_bot.py <slug>
#   run_service.sh scripts/inbox_watch.py <slug> --days 2 --dry-run
# Оточення машини береться з /root/.claude-env (рядки export KEY=VALUE):
# EnvironmentFile у systemd таких рядків не читає, тому source тут.
set -uo pipefail

SCRIPT="${1:?usage: run_service.sh <script> <slug> [args...]}"
SLUG="${2:?usage: run_service.sh <script> <slug> [args...]}"
shift 2
ENGINE="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

[ -f "${JOBSEARCH_ENV_FILE:-/root/.claude-env}" ] && set -a && source "${JOBSEARCH_ENV_FILE:-/root/.claude-env}" && set +a

export IS_SANDBOX=1
export JOBSEARCH_CLIENT="$SLUG"
export JOBSEARCH_ENGINE="$ENGINE"
export JOBSEARCH_CLIENTS_DIR="${JOBSEARCH_CLIENTS_DIR:-$ENGINE/../jobsearch-clients/clients}"
export PATH="$ENGINE/bin:$PATH"

exec "$ENGINE/.venv/bin/python" "$ENGINE/$SCRIPT" "$@"
