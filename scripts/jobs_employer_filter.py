#!/usr/bin/env python3
"""Лейбл+фільтр Employer у скриньці jobs (у main він є, у jobs його не було).

  python3 jobs_employer_filter.py            # показати стан
  python3 jobs_employer_filter.py --create   # створити лейбл і фільтр
  python3 jobs_employer_filter.py --backfill # довішати лейбл на старі листи (30 дн.)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import httpx

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))
import gmail_rest as g  # noqa: E402

API = "https://gmail.googleapis.com/gmail/v1/users/me/"
ACCOUNT = "jobs"
LABEL = "Employer"

# Той самий набір, що Filter 2 у main (docs/10_automation_setup.md), плюс слова з
# реальних листів ATS: Einladung/Vorstellungsgespräch/Zusage/Absage/Datenschutz.
SUBJECT_TERMS = [
    "Bewerbung", "application", "Vorstellungsgespräch", "Einladung", "Interview",
    "Telefoninterview", "Screening", "Eingangsbestätigung", "Zusage", "Absage",
    "Datenschutzerklärung", "Bewerbungsunterlagen",
]


def _raw(method: str, path: str, **kw):
    tok = g.access_token(ACCOUNT)
    r = httpx.request(method, API + path,
                      headers={"Authorization": f"Bearer {tok}"}, timeout=60, **kw)
    if r.status_code >= 400:
        raise RuntimeError(f"{method} {path} -> {r.status_code}: {r.text[:300]}")
    return r.json() if r.content.strip() else {}


def label_id(name: str) -> str | None:
    for lb in _raw("GET", "labels").get("labels", []):
        if lb["name"].lower() == name.lower():
            return lb["id"]
    return None


def ensure_label() -> str:
    lid = label_id(LABEL)
    if lid:
        print(f"лейбл «{LABEL}» вже є: {lid}")
        return lid
    created = _raw("POST", "labels", json={
        "name": LABEL,
        "labelListVisibility": "labelShow",
        "messageListVisibility": "show",
    })
    print(f"лейбл «{LABEL}» створено: {created['id']}")
    return created["id"]


def subject_query() -> str:
    return "subject:(" + " OR ".join(f'"{t}"' for t in SUBJECT_TERMS) + ")"


def show() -> int:
    lid = label_id(LABEL)
    print(f"акаунт: {ACCOUNT}\nлейбл «{LABEL}»: {lid or 'НЕМА'}")
    fs = _raw("GET", "settings/filters").get("filter", [])
    print(f"фільтрів усього: {len(fs)}")
    for f in fs:
        crit = f.get("criteria", {})
        act = f.get("action", {})
        desc = crit.get("query") or crit.get("from") or crit.get("subject") or "?"
        print(f"  {f['id']}: {str(desc)[:90]}  -> add={act.get('addLabelIds')} "
              f"remove={act.get('removeLabelIds')}")
    if lid:
        n = len(_raw("GET", "messages", params={"q": f'label:"{LABEL}"', "maxResults": 500})
                .get("messages") or [])
        print(f"листів під лейблом: {n}")
    return 0


def create() -> int:
    lid = ensure_label()
    q = subject_query()
    existing = _raw("GET", "settings/filters").get("filter", [])
    for f in existing:
        if f.get("criteria", {}).get("query") == q:
            print(f"фільтр із таким запитом уже є: {f['id']}")
            return 0
    made = _raw("POST", "settings/filters", json={
        "criteria": {"query": q},
        # лейбл + IMPORTANT; INBOX НЕ знімаємо — відповідь роботодавця мусить
        # лишатись на очах, на відміну від алертів
        "action": {"addLabelIds": [lid, "IMPORTANT"]},
    })
    print(f"фільтр створено: {made['id']}\n  query: {q}")
    return 0


def backfill(days: int = 30) -> int:
    lid = ensure_label()
    q = f'{subject_query()} newer_than:{days}d -label:"{LABEL}"'
    ids, token = [], None
    while True:
        params = {"q": q, "maxResults": 500}
        if token:
            params["pageToken"] = token
        res = _raw("GET", "messages", params=params)
        ids += [m["id"] for m in (res.get("messages") or [])]
        token = res.get("nextPageToken")
        if not token:
            break
    print(f"знайдено без лейбла: {len(ids)}")
    for i in range(0, len(ids), 800):
        _raw("POST", "messages/batchModify",
             json={"ids": ids[i:i + 800], "addLabelIds": [lid]})
    print(f"лейбл довішано на {len(ids)} листів")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--create", action="store_true")
    ap.add_argument("--backfill", action="store_true")
    ap.add_argument("--days", type=int, default=30)
    a = ap.parse_args()
    if a.create:
        sys.exit(create())
    if a.backfill:
        sys.exit(backfill(a.days))
    sys.exit(show())
