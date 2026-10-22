#!/usr/bin/env python3
"""Знайти відповіді роботодавців у обох скриньках і назвати те, що чекає людину.

  .venv/bin/python scripts/employer_scan.py            # 7 днів, обидві скриньки
  .venv/bin/python scripts/employer_scan.py --days 2 --json
  .venv/bin/python scripts/employer_scan.py --account jobs

Exit: 0 — нічого не висить · 2 — є незакрита співбесіда/запит документів.

Навіщо (інцидент 25.07.2026): запрошення від Falk & Falk пролежало 29 годин у
jobs, бо ранковий ран шукав відповіді лише за доменами з трекера + `label:Employer`,
і лише в main. Деталі в bot/generators/employer_scan.py.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import httpx

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

import gmail_rest as g  # noqa: E402
from alert_volume import ALL_SENDERS  # noqa: E402
from bot.generators.employer_scan import (  # noqa: E402
    build_employer_queries,
    exit_code,
    is_closed,
    tracker_known_msgids,
    summarize,
    tracker_domains,
    tracker_status_by_company,
    scan_accounts,
)
from bot.generators.responses import (  # noqa: E402
    classify_response,
    match_response_to_company,
)

API = "https://gmail.googleapis.com/gmail/v1/users/me/"
from bot import client as _client  # noqa: E402

TRACKER = _client.active().tracker
OUR_ADDRESSES = tuple(
    acc["address"] for acc in _client.active().accounts.get("gmail", {}).values()
)


def _get(account: str, path: str, **params):
    tok = g.access_token(account)
    r = httpx.request("GET", API + path, headers={"Authorization": f"Bearer {tok}"},
                      timeout=60, params=params or None)
    if r.status_code >= 400:
        raise RuntimeError(f"GET {path} -> {r.status_code}: {r.text[:200]}")
    return r.json() if r.content.strip() else {}


def _ids(account: str, query: str) -> list[str]:
    out, token = [], None
    while True:
        params = {"q": query, "maxResults": 500}
        if token:
            params["pageToken"] = token
        res = _get(account, "messages", **params)
        out += [m["id"] for m in (res.get("messages") or [])]
        token = res.get("nextPageToken")
        if not token:
            return out


def _plain_body(payload: dict) -> str:
    """Текст листа: перший text/plain, інакше зачищений text/html."""
    import base64
    import re

    chunks: list[str] = []

    def walk(part: dict) -> None:
        data = (part.get("body") or {}).get("data")
        mime = part.get("mimeType", "")
        if data and mime in ("text/plain", "text/html"):
            raw = base64.urlsafe_b64decode(data + "==").decode("utf-8", "replace")
            if mime == "text/html":
                raw = re.sub(r"(?is)<(style|script|head).*?</\1>", " ", raw)
                raw = re.sub(r"<[^>]+>", " ", raw)
            chunks.append(raw)
        for sub in part.get("parts") or []:
            walk(sub)

    walk(payload)
    import html as html_mod
    import re as re2

    text = html_mod.unescape(" ".join(chunks))
    return re2.sub(r"\s+", " ", text).strip()


def _thread_answered(account: str, thread_id: str) -> bool:
    """Чи є в треді наш власний лист (тобто відповідь уже пішла)."""
    th = _get(account, f"threads/{thread_id}", format="metadata", metadataHeaders="From")
    for m in th.get("messages", []):
        hdr = {h["name"]: h["value"] for h in m["payload"]["headers"]}
        frm = hdr.get("From", "").lower()
        if any(addr in frm for addr in OUR_ADDRESSES):
            return True
    return False


def scan(account: str, days: int, companies: list[str], tracker: Path = TRACKER) -> list[dict]:
    domains = tracker_domains(tracker)
    queries = build_employer_queries(account, domains, exclude_senders=ALL_SENDERS, days=days)
    seen_ids: set[str] = set()
    items: list[dict] = []
    for q in queries:
        for mid in _ids(account, q):
            if mid in seen_ids:
                continue
            seen_ids.add(mid)
            msg = _get(account, f"messages/{mid}", format="full")
            hdr = {h["name"]: h["value"] for h in msg["payload"]["headers"]}
            frm = hdr.get("From", "")
            if any(a in frm.lower() for a in OUR_ADDRESSES):
                continue  # наші ж листи
            subject = hdr.get("Subject", "")
            body = _plain_body(msg["payload"])
            classification = classify_response(f"{body} {subject}")
            resp = {"from": frm, "subject": subject, "body": body}
            items.append({
                "message_id": mid,
                "thread_id": msg.get("threadId"),
                "account": account,
                "date": hdr.get("Date", "")[:31],
                "from": frm,
                "subject": subject,
                "classification": classification,
                "company": match_response_to_company(resp, companies),
                "thread_answered": (
                    _thread_answered(account, msg["threadId"])
                    if classification in ("interview", "info_request") else None
                ),
            })
    return items


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--account", choices=["jobs", "main", "both", "all"], default="all",
                    help="all = усі скриньки з accounts.yaml (main у клієнта може не бути)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--tracker", type=Path, default=TRACKER,
                    help="інший трекер (для перевірки вартового на історичному стані)")
    args = ap.parse_args()
    tracker = args.tracker

    from bot.filters.rules import parse_tracker_applications

    companies = list({a["company"] for a in parse_tracker_applications(tracker)})
    try:
        accounts = scan_accounts(_client.active().gmail_aliases, args.account)
    except ValueError as e:
        print(f"ПОМИЛКА: {e}", file=sys.stderr)
        return 1

    items: list[dict] = []
    for acc in accounts:
        items += scan(acc, args.days, companies, tracker)

    # Eingangsbestätigung від того самого роботодавця теж ловиться як interview
    # (у тексті є «Vorstellungsgespräch»). Якщо трекер уже знає про співбесіду —
    # дія закрита, інакше вартовий висів би у exit=2 щодня.
    statuses = tracker_status_by_company(tracker)
    known = tracker_known_msgids(tracker)
    for i in items:
        i["answered"] = is_closed(i, statuses, known)

    summary = summarize(items)
    summary["days"] = args.days
    summary["accounts"] = accounts

    if args.json:
        print(json.dumps({"summary": summary, "items": items}, ensure_ascii=False, indent=1))
    else:
        print(f"Antworten von Arbeitgebern, letzte {args.days} Tage ({', '.join(accounts)}): {len(items)}")
        for cls, n in sorted(summary["counts"].items(), key=lambda kv: -kv[1]):
            print(f"  {n:3}  {cls}")
        if summary["action_needed"]:
            print("\n🔴 WARTET AUF ANTWORT:")
            for i in summary["action_needed"]:
                print(f"  [{i['classification']}] {i['date']}  {i['account']}  "
                      f"{i['company'] or '?'}\n      {i['subject'][:80]}\n      Msg {i['message_id']}")
        else:
            print("\nKeine offenen Einladungen oder Rückfragen.")
    return exit_code(summary)


if __name__ == "__main__":
    sys.exit(main())
