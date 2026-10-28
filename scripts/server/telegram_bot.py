#!/usr/bin/env python3
"""Сервіс Telegram-бота одного клієнта (systemd: jobsearch-telegram@<slug>).

    JOBSEARCH_CLIENT=<slug> .venv/bin/python scripts/server/telegram_bot.py

Токен бота лише з оточення (accounts.yaml → telegram.token_env називає змінну),
chat_id оператора з accounts.yaml. Довгий polling, кнопки йдуть у диспетчер,
файл var/state/stop зупиняє цикл. Диспетчер бачить чергу лідів (var/state/queue.jsonl):
кнопки hand-off картки браузерного воркера закривають і лід, не лише картку.
/status і /platform показують платформи подачі (targeting.yaml плюс var/state/platforms.json).
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
        print(f"[telegram] {tg['token_env']} не задано: токен бота має лежати в оточенні сервера",
              file=sys.stderr)
        return 1
    state = state_dir(c.var)
    sender = GmailRestSender(ENGINE, Path(sys.executable), c.slug)
    followup_days = int(c.targeting.get("followup_days") or 7)
    # queue: «Подано» / «Пропустити» на hand-off картці воркера форм закривають і запис ліда
    # в черзі (sent з рядком у трекері чи held), навіть якщо воркер уже не чекає (етап 2c)
    dispatcher = Dispatcher(sender, JsonlStore(state / "cards.jsonl"), c.tracker, state,
                            lambda: _today().isoformat(), queue=JsonlStore(state / QUEUE_FILE),
                            source_label="contract", followup_days=followup_days)
    def status() -> dict:   # /status: пошта (2a), черга подач (2b), платформи (2d)
        return {**watch_summary(state, _today()),
                "queue": queue_summary(JsonlStore(state / QUEUE_FILE), _today().isoformat()),
                # той самий рядок, що /platform: bot.platforms.status_lines з лічильниками черги
                "platforms": service.platform_lines()}

    service = BotService(HttpTransport(token), state, tg["chat_id"], dispatcher,
                         status, lang=c.report_lang,
                         followup_days=followup_days, targeting=c.targeting)
    print(f"[telegram] {c.slug}: polling, chat_id={tg['chat_id']}", file=sys.stderr)
    service.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
