#!/usr/bin/env python3
"""Telegram bot service for one client (systemd: jobsearch-telegram@<slug>).

    JOBSEARCH_CLIENT=<slug> .venv/bin/python scripts/server/telegram_bot.py

The bot token comes only from the environment (accounts.yaml -> telegram.token_env names
the variable), the operator's chat_id from accounts.yaml. Long polling, buttons go to the
dispatcher, the file var/state/stop ends the loop. The dispatcher sees the lead queue
(var/state/queue.jsonl): the buttons of a browser worker's hand-off card close the lead
too, not only the card.
/status and /platform show the application platforms (targeting.yaml plus var/state/platforms.json).
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ENGINE = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ENGINE))

from bot import client as _client
from bot.inbox_watch import watch_summary
from bot.sendqueue import QUEUE_FILE
from bot.sendqueue import summary as queue_summary
from bot.state import JsonlStore, state_dir
from bot.telegram.api import HttpTransport
from bot.telegram.dispatch import Dispatcher, GmailRestSender
from bot.telegram.service import BotService


def _today():
    return datetime.now(tz=timezone.utc).astimezone().date()


def main() -> int:
    c = _client.active()
    tg = c.telegram
    token = os.environ.get(tg["token_env"])
    if not token:
        print(f"[telegram] {tg['token_env']} not set: the bot token must live in the server environment",
              file=sys.stderr)
        return 1
    state = state_dir(c.var)
    sender = GmailRestSender(ENGINE, Path(sys.executable), c.slug)
    followup_days = int(c.targeting.get("followup_days") or 7)
    # queue: "Submitted" / "Skip" on the form worker's hand-off card also close the lead's
    # entry in the queue (sent with a tracker row, or held), even if the worker no longer waits (stage 2c)
    dispatcher = Dispatcher(sender, JsonlStore(state / "cards.jsonl"), c.tracker, state,
                            lambda: _today().isoformat(), queue=JsonlStore(state / QUEUE_FILE),
                            source_label="contract", followup_days=followup_days)
    def status() -> dict:   # /status: mail (2a), application queue (2b), platforms (2d)
        return {**watch_summary(state, _today()),
                "queue": queue_summary(JsonlStore(state / QUEUE_FILE), _today().isoformat()),
                # the same line as /platform: bot.platforms.status_lines with queue counters
                "platforms": service.platform_lines()}

    service = BotService(HttpTransport(token), state, tg["chat_id"], dispatcher,
                         status, lang=c.report_lang,
                         followup_days=followup_days, targeting=c.targeting)
    print(f"[telegram] {c.slug}: polling, chat_id={tg['chat_id']}", file=sys.stderr)
    service.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
