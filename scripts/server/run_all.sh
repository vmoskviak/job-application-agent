#!/bin/bash
# Запуск evening|morning для всіх клієнтів зі status: active, по черзі.
# Використання: run_all.sh evening|morning
# Клієнт з paused/placed пропускається: вимкнути пошук = змінити status
# у profile.yaml і запушити, cron чіпати не треба.
set -uo pipefail

MODE="${1:?usage: run_all.sh evening|morning}"
ENGINE="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
[ -f "${JOBSEARCH_ENV_FILE:-/root/.claude-env}" ] && set -a && source "${JOBSEARCH_ENV_FILE:-/root/.claude-env}" && set +a
export JOBSEARCH_CLIENTS_DIR="${JOBSEARCH_CLIENTS_DIR:-$ENGINE/../jobsearch-clients/clients}"

# свіжий список клієнтів і статусів
git -C "$JOBSEARCH_CLIENTS_DIR" pull --rebase >/dev/null 2>&1 || true

RC=0
for SLUG in $(PYTHONPATH="$ENGINE" "$ENGINE/.venv/bin/python" -m bot.clients list --status active --slugs); do
  "$ENGINE/scripts/server/run_agent.sh" "$SLUG" "$MODE" || RC=$?
done
exit $RC
