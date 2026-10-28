#!/usr/bin/env python3
"""Прохід диспетчера подач контрактного клієнта (таймер jobsearch-dispatch@<slug>).

    js scripts/dispatch_leads.py [--dry-run]

Шле queued email-ліди з var/state/queue.jsonl у межах денного ліміту, пише
рядок у трекер, квитанцію в Telegram (без токена чи chat_id лише в лог). Без
LLM і без дисплея, тому без лока машини. Тіньовий тиждень: --dry-run або
JOBSEARCH_DISPATCH_DRY_RUN=1 в оточенні машини, тоді все, що пішло б, лягає
у var/state/queue_dry.jsonl, а пошта, трекер і Telegram не чіпаються. Перед
проходом дані підтягуються (ff-only), після живої подачі трекер комітиться й
пушиться (помічники scripts/inbox_watch.py).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

ENGINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE))
sys.path.insert(0, str(ENGINE / "scripts"))

from bot import client as _client
from bot.dispatch_leads import DispatchConfig, run_dispatch
from bot.sendqueue import QUEUE_FILE, limits_from
from bot.state import JsonlStore, state_dir
from bot.telegram.api import HttpTransport, Transport
from bot.telegram.dispatch import GmailRestSender
from inbox_watch import pull_data, push_tracker, queue_alert

DRY_RUN_ENV = "JOBSEARCH_DISPATCH_DRY_RUN"


def _now():
    return datetime.now(tz=timezone.utc).astimezone()


def build_config(c: _client.Client, args: argparse.Namespace) -> DispatchConfig:
    dry = bool(args.dry_run) or str(os.environ.get(DRY_RUN_ENV, "")).strip().lower() in ("1", "true", "yes")
    policy = str(c.targeting.get("send_policy") or "").strip().lower()
    if policy != "auto":   # усе, крім явного auto, означає тіньовий режим (спека §6: send_policy)
        print(f"[dispatch] targeting.send_policy={policy or 'відсутній'}: лише тіньовий режим, нічого не шлю",
              file=sys.stderr)
        dry = True
    return DispatchConfig(account=c.send_from, limits=limits_from(c.targeting), lang=c.report_lang,
                          chat_id=c.telegram["chat_id"], dry_run=dry,
                          followup_days=int(c.targeting.get("followup_days") or 7))


def run_pass(c: _client.Client, cfg: DispatchConfig, transport: Transport | None) -> int:
    pull_data(c.root, queue_alert)
    now = _now()
    res = run_dispatch(cfg, JsonlStore(state_dir(c.var) / QUEUE_FILE), c.tracker, state_dir(c.var),
                       GmailRestSender(ENGINE, Path(sys.executable), c.slug), transport,
                       now.date().isoformat(), now.strftime("%Y-%m-%d %H:%M"))
    if res.sent and not cfg.dry_run:
        push_tracker(c.root, f"{c.slug}: dispatch {now.date().isoformat()}: {len(res.sent)} sent", queue_alert)
    print("DISPATCH OK: " + json.dumps(asdict(res), ensure_ascii=False))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="тіньовий режим: нічого не слати, записати у queue_dry.jsonl")
    args = ap.parse_args()
    c = _client.active()
    cfg = build_config(c, args)
    tg = c.telegram
    token = os.environ.get(tg["token_env"])
    transport = HttpTransport(token) if token and tg["chat_id"] else None
    if transport is None:
        print(f"[dispatch] без {tg['token_env']} або telegram.chat_id квитанції лише в лог", file=sys.stderr)
    return run_pass(c, cfg, transport)


if __name__ == "__main__":
    sys.exit(main())
