"""Спільні вигадані дані тестів етапу 2a: листи, агентури, трекер контрактного клієнта."""
import json
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent / "fixtures"
MAILS: dict[str, dict] = json.loads((FIXTURES / "inbox_mails.json").read_text(encoding="utf-8"))
AGENCIES = ["example-agentur.de", "computerfutures.com"]

# Головна таблиця трекера: три рядки sent, два з email-тредом, один через портал.
CONTRACT_TRACKER = """# Application Tracker — Jordan Example

Статуси: `ready` · `sent` · `portal_submitted (портал)` · `confirmed` · `interview` · `offer` · `rejected` · `ghosted` · `withdrew` · `skipped`

| # | Date | Company | Position | Source | Salary range | Status | Last update | Next action | Notes |
|---|---|---|---|---|---|---|---|---|---|
| 1 | 2026-10-22 | Example Co | Android Engineer | HN Who is hiring (email) | — | sent | 2026-10-22 | чекати відповідь | E-Mail на jobs@example-co.com, Msg-ID 1a2b3c4d5e6f7a8b, CV_Android.pdf |
| 2 | 2026-10-21 | Portal Co | Senior Android Developer | LinkedIn Easy Apply | — | sent | 2026-10-21 | чекати відповідь | Msg-ID 1b2b3c4d5e6f7a8c (confirmation платформи) |
| 3 | 2026-10-25 | Fresh Co | Kotlin Multiplatform Engineer | HN Who is hiring (email) | — | sent | 2026-10-25 | чекати відповідь | E-Mail на hiring@fresh-co.example, Msg-ID 1c2b3c4d5e6f7a8d |
"""
JORDAN_TRACKER_WITH_TWO_ROWS = CONTRACT_TRACKER


def jordan_profile() -> dict:
    """profile.yaml вигаданого контрактного клієнта jordan."""
    from bot import client as _client
    return dict(_client.load("jordan", base=FIXTURES.parent.parent / "examples" / "clients").profile)
