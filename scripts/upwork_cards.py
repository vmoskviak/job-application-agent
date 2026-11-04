#!/usr/bin/env python3
"""Upwork cards for a Claude session that submits from the owner's own Chrome (02.11.2026).

Auto-submit from the server is blocked by Cloudflare, so the owner says "submit Upwork" in a
Claude session on his Mac; Claude reads the open cards here, submits in his Chrome and closes
each card here, so the owner never taps anything.

    run_service.sh scripts/upwork_cards.py <slug> list
    run_service.sh scripts/upwork_cards.py <slug> done <card> --proposal-id N --bid 75 --connects 13 --left 78
    run_service.sh scripts/upwork_cards.py <slug> skip <card> --reason "needs iOS"

list: one JSON object per open Upwork card (the draft, the alert's job link and the job id behind it,
resolved through the tracking redirect without requesting upwork.com). done: the card is closed as
submitted, the tracker gets its row (pushed at once), the Telegram card loses its buttons and a short
note follows. skip: the card is closed without a tracker row, the buttons go.
"""
from __future__ import annotations

import argparse
import html
import json
import sys
from datetime import datetime
from pathlib import Path

ENGINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE))
sys.path.insert(0, str(ENGINE / "scripts"))

from bot import client as _client
from bot.generators.upwork_alert import money
from bot.state import JsonlStore, state_dir
from bot.telegram.dispatch import upwork_row

BY = "submitted by Claude from the owner's Chrome"


def _transport(c):
    import os

    from bot.telegram.api import HttpTransport

    tg = c.telegram
    token = os.environ.get(tg["token_env"])
    return (HttpTransport(token), tg["chat_id"]) if token and tg["chat_id"] else (None, None)


def _clear(c, card: dict, note: str) -> None:
    transport, chat_id = _transport(c)
    if transport is None:
        print("telegram: no token or chat id, buttons left as they are", file=sys.stderr)
        return
    if card.get("tg_message_id"):
        try:
            transport.clear_buttons(chat_id, int(card["tg_message_id"]))
        except Exception as e:  # noqa: BLE001 — an old message: the note still goes
            print(f"telegram: buttons not cleared: {e}", file=sys.stderr)
    transport.send(chat_id, note, None)


def cmd_list(c, cards: JsonlStore) -> int:
    from bot.upwork_submit import http_head, resolve_job_id

    for card in cards.read():
        if card.get("kind") != "upwork" or card.get("status") not in ("open", "queued"):
            continue
        link = card.get("job_link") or (card.get("job") or {}).get("job_link") or ""
        job_id = card.get("job_id") or ""
        if not job_id and link:
            try:
                job_id = resolve_job_id(link, http_head)
                cards.update(card["id"], job_id=job_id)
            except Exception as e:  # noqa: BLE001 — shown, the session opens job_link in the owner's Chrome
                job_id = f"unresolved: {e}"
        print(json.dumps({k: card.get(k) for k in ("id", "title", "budget", "country", "fit", "reason",
                                                      "saved_search", "created", "message_id", "draft")}
                         | {"job_id": job_id, "job_link": link}, ensure_ascii=False))
    return 0


def cmd_done(c, cards: JsonlStore, args) -> int:
    card = cards.get(args.card)
    if card is None or card.get("kind") != "upwork":
        print(f"ERR no Upwork card {args.card}", file=sys.stderr)
        return 1
    if card.get("status") == "done":
        print(f"OK already done {args.card}")
        return 0
    today = datetime.now().astimezone().date().isoformat()
    fields = {"status": "done", "submitted_by": "claude-mac", "proposal_id": args.proposal_id, "bid": args.bid,
              "connects": args.connects, "connects_left": args.left, "closed_at": today}
    cards.update(args.card, **fields)
    followup = int(c.targeting.get("followup_days") or 7)
    upwork_row(c.tracker, {**card, **fields}, today, followup, "contract", BY)
    from inbox_watch import push_tracker  # the watcher's commit-and-push of tracker.md

    push_tracker(c.root, f"{c.slug}: Upwork proposal {args.proposal_id} submitted",
                 lambda msg, tail="": print(f"ALERT {msg} {tail}", file=sys.stderr))
    bid = f"{money(args.bid)}/h" if args.kind == "hourly" else money(args.bid)
    left = f", {args.left} left" if args.left is not None else ""
    note = (f"<b>Upwork · submitted</b>\n{html.escape(card.get('title') or '')}\n"
            f"{bid} · {args.connects} Connects{left}\n"
            f'<a href="https://www.upwork.com/nx/proposals/{html.escape(args.proposal_id)}">Proposal</a>')
    _clear(c, card, note)
    print(f"OK done {args.card} proposal {args.proposal_id}")
    return 0


def cmd_skip(c, cards: JsonlStore, args) -> int:
    card = cards.get(args.card)
    if card is None or card.get("kind") != "upwork":
        print(f"ERR no Upwork card {args.card}", file=sys.stderr)
        return 1
    cards.update(args.card, status="skipped", skipped_by="claude-mac", skip_reason=args.reason,
                 closed_at=datetime.now().astimezone().date().isoformat())
    _clear(c, card, f"<b>Upwork · skipped</b>\n{html.escape(card.get('title') or '')}\n{html.escape(args.reason)}")
    print(f"OK skipped {args.card}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    d = sub.add_parser("done")
    d.add_argument("card")
    d.add_argument("--proposal-id", required=True)
    d.add_argument("--bid", type=float, required=True)
    d.add_argument("--kind", choices=("hourly", "fixed"), default="hourly")
    d.add_argument("--connects", type=int, required=True)
    d.add_argument("--left", type=int)
    s = sub.add_parser("skip")
    s.add_argument("card")
    s.add_argument("--reason", required=True)
    args = ap.parse_args()
    c = _client.active()
    cards = JsonlStore(state_dir(c.var) / "cards.jsonl")
    if args.cmd == "list":
        return cmd_list(c, cards)
    return cmd_done(c, cards, args) if args.cmd == "done" else cmd_skip(c, cards, args)


if __name__ == "__main__":
    sys.exit(main())
