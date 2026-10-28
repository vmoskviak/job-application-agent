#!/bin/bash
# Server install for the engine: Debian 12/13, run as root after cloning the
# engine to /opt/jobsearch-autopilot (or any path: the script uses its own location).
#   scripts/server/install.sh            install everything
#   scripts/server/install.sh --dry-run  print the commands only
# Idempotent: re-running updates packages, the venv and the unit files.
# It does NOT enable cron or timers: rehearse a client first (docs/quickstart.md §4).
set -euo pipefail

ENGINE="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DRY=0; [ "${1:-}" = "--dry-run" ] && DRY=1
run() { if [ "$DRY" = 1 ]; then echo "+ $*"; else echo "+ $*"; "$@"; fi; }

if [ "$(id -u)" -ne 0 ] && [ "$DRY" = 0 ]; then
  echo "run as root (the engine runs from cron as root)" >&2; exit 1
fi

run apt-get install -y git curl gnupg jq locales cron rsync \
  fonts-dejavu fonts-liberation2 libpango-1.0-0 libpangocairo-1.0-0 libcairo2 \
  libgdk-pixbuf-2.0-0 libffi-dev python3 python3-venv python3-pip xvfb x11vnc xdotool

if ! command -v node >/dev/null 2>&1; then
  echo "Node.js 22 is needed for Claude Code and the Gmail MCP server: https://github.com/nodesource/distributions" >&2
  [ "$DRY" = 1 ] || exit 1
fi
command -v claude >/dev/null 2>&1 || run npm install -g @anthropic-ai/claude-code

[ -d "$ENGINE/.venv" ] || run python3 -m venv "$ENGINE/.venv"
run "$ENGINE/.venv/bin/pip" install -q -r "$ENGINE/requirements.txt"
run "$ENGINE/.venv/bin/playwright" install --with-deps chromium

# systemd units for the hourly services (inbox watcher, dispatcher, browser worker, Telegram bot)
run cp "$ENGINE"/scripts/server/systemd/*.service "$ENGINE"/scripts/server/systemd/*.timer /etc/systemd/system/
run systemctl daemon-reload

if [ ! -f /root/.claude-env ]; then
  run cp "$ENGINE/scripts/server/claude-env.example" /root/.claude-env
  run chmod 600 /root/.claude-env
fi

cat <<MSG

Installed. Next:
  1. edit /root/.claude-env (Claude token, operator e-mail, API keys)
  2. clone your private clients repo next to the engine and set JOBSEARCH_CLIENTS_DIR
  3. Gmail tokens per mailbox: docs/quickstart.md §3
  4. rehearse: scripts/server/run_agent.sh <slug> evening
  5. then: crontab $ENGINE/scripts/server/crontab.txt
MSG
