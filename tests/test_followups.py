import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from bot.followups import parse_tracker_rows, find_due_followups

# Мінімальна фікстура-трекер: заголовок + рядки різних кейсів.
FIXTURE = """\
| # | Дата | Компанія | Позиція | Канал | Зарплата | Статус | Оновлено | Next action | Notes |
|---|------|----------|---------|-------|----------|--------|----------|-------------|-------|
| 10 | 2026-06-01 | Alpha GmbH | Teamassistenz (m/w/d) | Email | 42K | sent | 2026-06-01 | Follow-up якщо тиша до 2026-06-15 | ✅ Email jobs `19eef7a62563eac1` · Düsseldorf |
| 11 | 2026-06-02 | Beta AG | Junior BA (m/w/d) | Email | 48K | sent | 2026-06-02 | follow-up якщо тиша до 2026-07-20 | ✅ 2026-07-10 follow-up (Msg `19f0000000000001`) · тред jobs |
| 12 | 2026-06-03 | Gamma KG | Office Manager | Portal | 40K | sent | 2026-06-03 | Follow-up якщо тиша до 2026-06-10 | Портал concludis, email-каналу нема |
| 13 | 2026-06-04 | Delta SE | Sachbearbeitung | Email | 38K | sent | 2026-06-04 | Follow-up якщо тиша до 2026-12-31 | ✅ Email jobs `19f1111111111111` |
| 14 | 2026-06-05 | Epsilon | Empfang | Email main | 35K | sent | 2026-06-05 | Follow-up якщо тиша до 2026-06-20 | ✅ reply роботодавцю у thread `19f2222222222222` |
| 15 | 2026-06-06 | Zeta GmbH | Verwaltung | Email | 36K | rejected | 2026-07-01 | — | ❌ Absage `19f3333333333333` |
| 16 | 2026-06-07 | Eta Health | Executive Assistant | LinkedIn Easy Apply | 42K | sent | 2026-06-07 | Follow-up якщо тиша до 2026-06-21 | ✅ LinkedIn confirmation (jobs · Msg `19f4444444444444`) |
"""


def _rows(today):
    rows = parse_tracker_rows(FIXTURE, default_year=today.year)
    return find_due_followups(rows, today)


def test_due_basic_case():
    due, _ = _rows(date(2026, 7, 1))
    ids = {d.row_id for d in due}
    # 10 (дедлайн 15.06 минув, Msg-ID є, не followed up) → DUE
    assert "10" in ids
    d10 = next(d for d in due if d.row_id == "10")
    assert d10.msg_id == "19eef7a62563eac1"
    assert d10.account == "jobs"
    assert "Teamassistenz" in d10.body


def test_skip_already_followed_up():
    due, skipped = _rows(date(2026, 7, 25))
    ids = {d.row_id for d in due}
    assert "11" not in ids  # already has follow-up marker
    assert any(s["row_id"] == "11" and "already" in s["reason"] for s in skipped)


def test_skip_portal_without_msgid():
    _, skipped = _rows(date(2026, 7, 1))
    assert any(s["row_id"] == "12" and "no Msg-ID" in s["reason"] for s in skipped)


def test_future_deadline_not_due():
    due, _ = _rows(date(2026, 7, 1))
    assert "13" not in {d.row_id for d in due}  # deadline 31.12 у майбутньому


def test_main_account_classification():
    due, _ = _rows(date(2026, 7, 1))
    d14 = next((d for d in due if d.row_id == "14"), None)
    assert d14 is not None and d14.account == "main"


def test_rejected_status_ignored():
    due, skipped = _rows(date(2026, 7, 1))
    all_ids = {d.row_id for d in due} | {s["row_id"] for s in skipped}
    assert "15" not in all_ids  # rejected — не активний, взагалі поза розглядом


def test_linkedin_confirmation_not_repliable():
    due, skipped = _rows(date(2026, 7, 1))
    assert "16" not in {d.row_id for d in due}  # LinkedIn confirmation ≠ тред
    assert any(s["row_id"] == "16" and "non-email channel" in s["reason"]
               for s in skipped)


def test_main_falls_back_to_jobs_when_client_has_no_main():
    """Клієнт без main: нотатка «reply роботодавцю у thread» не має вести в
    неіснуючу скриньку, інакше gmail_rest упаде на KeyError."""
    today = date(2026, 7, 1)
    rows = parse_tracker_rows(FIXTURE, default_year=today.year, aliases=["jobs"])
    due, _ = find_due_followups(rows, today)
    d14 = next(d for d in due if d.row_id == "14")
    assert d14.account == "jobs"


# --- контрактний ринок: follow-up за віком заявки (етап 2a) ---

def test_find_due_by_age_contract_rows():
    from datetime import date as _date

    from bot.followups import find_due_by_age, parse_tracker_rows
    from tests.inbox_fixtures import CONTRACT_TRACKER
    rows = parse_tracker_rows(CONTRACT_TRACKER, 2026, aliases=["jobs", "main"])
    assert rows[0].sent_on == "2026-10-22"
    due, skipped = find_due_by_age(rows, _date(2026, 10, 29), days=7)
    assert [d.company for d in due] == ["Example Co"]        # sent 2026-10-22 by email, Msg-ID in notes
    assert due[0].msg_id == "1a2b3c4d5e6f7a8b" and due[0].deadline == "2026-10-29"
    assert {s["company"]: s["reason"] for s in skipped}["Portal Co"].startswith("non-email channel")
    assert all(d.company != "Fresh Co" for d in due)         # sent 2026-10-25: not due yet


def test_followup_body_en_has_no_dashes_and_names_role():
    from bot.followups import followup_body
    body = followup_body("Android Engineer", "en", "Jordan Example", "+351 000")
    assert "Android Engineer" in body and "–" not in body and "—" not in body
    assert body.rstrip().endswith("Jordan Example\n+351 000")
    assert 60 <= len(body.split()) <= 110
    de = followup_body("Android Engineer", "de", "Jordan Example", "+351 000")
    assert de.startswith("Sehr geehrte Damen und Herren") and "Android Engineer" in de
