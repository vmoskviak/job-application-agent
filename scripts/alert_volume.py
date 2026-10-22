#!/usr/bin/env python3
"""Обсяг job-алертів по джерелах — і сторож самого лейбла `Job Alerts`.

    .venv/bin/python scripts/alert_volume.py                 # звіт за 7 днів
    .venv/bin/python scripts/alert_volume.py --days 1        # за добу
    .venv/bin/python scripts/alert_volume.py --json
    .venv/bin/python scripts/alert_volume.py --check-filter   # exit 2, якщо фільтр битий
    .venv/bin/python scripts/alert_volume.py --backfill       # довішати лейбл на старе

Чому це окремий скрипт, а не запит `label:job-alerts` в агентських промптах:
25.07 виявилось, що Gmail-фільтр навішував лейбл лише на `jobs-noreply@linkedin.com`,
бо в criteria стояли адреси, яких платформи не використовують (`newsletter@stepstone.de`
замість реального `info@jobagent.stepstone.de` і так далі). Метрика показувала
2 листи за тиждень замість 112, тобто рефокус алертів було нічим міряти.
Тепер список відправників живе в коді (ALERT_SENDERS), а --check-filter звіряє з ним
живий Gmail-фільтр і падає, якщо хтось випав. Обсяг рахується ПО ВІДПРАВНИКАХ,
тому працює навіть коли лейбл знову зламається.
"""

from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).parent))
import gmail_rest as g  # noqa: E402

ACCOUNT = "jobs"
LABEL_NAME = "Job Alerts"
API = "https://gmail.googleapis.com/gmail/v1/users/me/"

# Реальні відправники, перевірені по 14 днях пошти (25.07). Ключ — джерело у звіті.
# messages-noreply@linkedin.com сюда НЕ входить: це InMail рекрутерів, він має
# лишатись в інбоксі, а не їхати під лейбл із removeLabelIds:[INBOX].
ALERT_SENDERS: dict[str, list[str]] = {
    "stepstone": [
        "info@jobagent.stepstone.de",
        "info@email.stepstone.de",
        "newsletter@stepstone.de",
    ],
    "linkedin": [
        "jobalerts-noreply@linkedin.com",   # канал А: saved searches
        "jobs-noreply@linkedin.com",        # канал Б: рекомендації по Job-Präferenzen
        "jobs-listings@linkedin.com",
    ],
    "xing": [
        "jobs@mail.xing.com",
        "mailrobot@mail.xing.com",
        "no-reply@xing.com",
        "notification@xing.com",
    ],
    "indeed": [
        "donotreply@match.indeed.com",      # рекомендації
        "donotreply@jobalert.indeed.com",   # налаштовані алерти
        "alert@indeed.com",
        "donotreply@indeed.com",
    ],
    "interamt": ["suchprofil@interamt.de"],
    "arbeitsagentur": ["jobboerse@arbeitsagentur.de"],
    # Не наші алерти — агрегатори, які підписались самі. Тримаємо під лейблом,
    # щоб не сміттили в інбоксі, але у звіті вони окремим рядком.
    "aggregators": [
        "no-reply@alerts.talent.com",
        "contact@jobijoba.com",
        "job-roboter@get-in.de",
        "info@glassdoor.com",
        "noreply@glassdoor.com",
    ],
}

ALL_SENDERS = [addr for addrs in ALERT_SENDERS.values() for addr in addrs]


def _raw(method: str, path: str, **kw):
    """як gmail_rest.api, але терпить порожнє тіло (DELETE, batchModify)."""
    tok = g.access_token(ACCOUNT)
    r = httpx.request(method, API + path,
                      headers={"Authorization": f"Bearer {tok}"}, timeout=60, **kw)
    if r.status_code >= 400:
        raise RuntimeError(f"{method} {path} -> {r.status_code}: {r.text[:300]}")
    return r.json() if r.content.strip() else {}


def msg_ids(query: str) -> list[str]:
    """Усі id під запит. maxResults без пагінації ріже на 500 — а StepStone сам
    дає ~90 листів за два тижні, тож без циклу цифри тихо занижуються."""
    out: list[str] = []
    token = None
    while True:
        params: dict[str, object] = {"q": query, "maxResults": 500}
        if token:
            params["pageToken"] = token
        res = _raw("GET", "messages", params=params)
        out += [m["id"] for m in (res.get("messages") or [])]
        token = res.get("nextPageToken")
        if not token:
            return out


def volume(days: int) -> dict:
    """{'stepstone': 42, ...} + службові поля про покриття лейблом."""
    per_source: dict[str, int] = {}
    for source, addrs in ALERT_SENDERS.items():
        q = f'newer_than:{days}d from:({" OR ".join(addrs)})'
        per_source[source] = len(msg_ids(q))

    alerts_total = sum(per_source.values())
    labelled = len(msg_ids(f'newer_than:{days}d label:"{LABEL_NAME}"'))
    unlabelled = len(msg_ids(
        f'newer_than:{days}d -label:"{LABEL_NAME}" from:({" OR ".join(ALL_SENDERS)})'))
    return {
        "days": days,
        "per_source": per_source,
        "alerts_total": alerts_total,
        "labelled": labelled,
        "unlabelled_alerts": unlabelled,
        "per_day": round(alerts_total / days, 1) if days else 0.0,
    }


def check_filter() -> tuple[bool, list[str]]:
    """Чи всі ALERT_SENDERS реально стоять у живому Gmail-фільтрі."""
    filters = _raw("GET", "settings/filters").get("filter") or []
    label_id = label_id_for(LABEL_NAME)
    criteria = " ".join(
        (f.get("criteria") or {}).get("from") or ""
        for f in filters
        if label_id in ((f.get("action") or {}).get("addLabelIds") or [])
    ).lower()
    missing = [a for a in ALL_SENDERS if a.lower() not in criteria]
    return (not missing and bool(criteria)), missing


def label_id_for(name: str) -> str:
    for lbl in _raw("GET", "labels").get("labels") or []:
        if lbl["name"].lower() == name.lower():
            return lbl["id"]
    raise RuntimeError(f"лейбла «{name}» нема в акаунті {ACCOUNT}")


def backfill(days: int = 30) -> int:
    """Довішати лейбл на алерти, які прийшли до фікса фільтра. INBOX не знімає:
    ретроспективна архівація сховала б з інбоксу листи, які власниця могла читати."""
    ids = msg_ids(
        f'newer_than:{days}d -label:"{LABEL_NAME}" from:({" OR ".join(ALL_SENDERS)})')
    if not ids:
        return 0
    lid = label_id_for(LABEL_NAME)
    for i in range(0, len(ids), 200):
        _raw("POST", "messages/batchModify",
             json={"ids": ids[i:i + 200], "addLabelIds": [lid]})
    return len(ids)


def senders_seen(days: int) -> collections.Counter:
    """Фактичні адреси відправників за період — щоб ловити нові канали
    (наприклад match.indeed.com, який не з Job-Präferenzen)."""
    cnt: collections.Counter = collections.Counter()
    for mid in msg_ids(f"newer_than:{days}d -in:sent"):
        d = _raw("GET", f"messages/{mid}",
                 params={"format": "metadata", "metadataHeaders": "From"})
        headers = (d.get("payload") or {}).get("headers") or []
        frm = next((h["value"] for h in headers if h["name"].lower() == "from"), "?")
        cnt[frm.split("<")[-1].strip(">").strip().lower()] += 1
    return cnt


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--check-filter", action="store_true")
    ap.add_argument("--backfill", action="store_true")
    ap.add_argument("--senders", action="store_true",
                    help="усі відправники за період, не лише відомі")
    args = ap.parse_args()

    if args.backfill:
        n = backfill()
        print(f"довішано лейбл на {n} листів (INBOX не знімався)")
        return 0

    ok, missing = check_filter()
    if args.check_filter:
        if ok:
            print(f'✅ Gmail-фільтр містить усі {len(ALL_SENDERS)} відправників')
            return 0
        print(f"❌ у фільтрі нема {len(missing)}: {', '.join(missing)}")
        print("   → лейбл на ці листи не навішується, метрика обсягу занижена")
        return 2

    v = volume(args.days)
    if args.json:
        print(json.dumps({**v, "filter_ok": ok, "filter_missing": missing},
                         ensure_ascii=False))
    else:
        print(f"Обсяг job-алертів за {v['days']} дн. "
              f"({v['per_day']}/день, усього {v['alerts_total']}):")
        for source, n in sorted(v["per_source"].items(), key=lambda x: -x[1]):
            print(f"  {n:>4}  {source}")
        print(f"  лейбл «{LABEL_NAME}» стоїть на {v['labelled']} листах, "
              f"без лейбла алертів: {v['unlabelled_alerts']}")
        print(f'  фільтр: {"✅ ок" if ok else "❌ нема " + ", ".join(missing)}')

    if args.senders:
        print("\nусі відправники за період:")
        for addr, n in senders_seen(args.days).most_common():
            known = "" if addr in ALL_SENDERS else "  ← новий"
            print(f"  {n:>4}  {addr}{known}")

    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
