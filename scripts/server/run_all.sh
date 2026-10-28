#!/bin/bash
# Run evening|morning for every client with status: active, one after another.
# Usage: run_all.sh evening|morning
# A client with paused/placed is skipped: to switch the search off, change status
# in profile.yaml and push; cron needs no change.
set -uo pipefail

MODE="${1:?usage: run_all.sh evening|morning}"
ENGINE="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
[ -f "${JOBSEARCH_ENV_FILE:-/root/.claude-env}" ] && set -a && source "${JOBSEARCH_ENV_FILE:-/root/.claude-env}" && set +a
export JOBSEARCH_CLIENTS_DIR="${JOBSEARCH_CLIENTS_DIR:-$ENGINE/../jobsearch-clients/clients}"

# fresh list of clients and statuses
git -C "$JOBSEARCH_CLIENTS_DIR" pull --rebase >/dev/null 2>&1 || true

RC=0
for SLUG in $(PYTHONPATH="$ENGINE" "$ENGINE/.venv/bin/python" -m bot.clients list --status active --slugs); do
  "$ENGINE/scripts/server/run_agent.sh" "$SLUG" "$MODE" || RC=$?
done
exit $RC
