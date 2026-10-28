#!/bin/bash
# Run an engine service or timer job for one client (systemd -> this script).
# Usage: run_service.sh <script-relative-to-engine> <slug> [args...]
#   run_service.sh scripts/server/telegram_bot.py <slug>
#   run_service.sh scripts/inbox_watch.py <slug> --days 2 --dry-run
# The machine environment comes from /root/.claude-env (lines of export KEY=VALUE):
# systemd's EnvironmentFile does not read such lines, hence the source here.
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
