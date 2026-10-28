"""Сповіщення оператору в Telegram поза картками (bot.notify): спільний бот на кількох
клієнтів, алерти, нові співбесіди після рану, картка режиму notify."""
from __future__ import annotations

from pathlib import Path

from bot import client as _client
from bot import notify
from bot import operator_report as rep
from bot.telegram.cards import notify_card
from tests.test_telegram_api import FakeTransport

TRACKER_BEFORE = """| # | Date | Company | Position | Source | Salary range | Status | Last update | Next action | Notes |
|---|---|---|---|---|---|---|---|---|---|
| 1 | 2026-10-20 | Alpha GmbH | Assistenz | StepStone | — | sent | 2026-10-20 | — | Msg 1a11ae48cb6c8d84 |
| 2 | 2026-10-21 | Beta AG | Sachbearbeitung | Email | — | interview_1 | 2026-10-25 | — | Teams 27.10 |
| 3 | 2026-10-22 | Gamma SE | Empfang | Email | — | sent | 2026-10-22 | — | — |
"""
TRACKER_AFTER = """| # | Date | Company | Position | Source | Salary range | Status | Last update | Next action | Notes |
|---|---|---|---|---|---|---|---|---|---|
| 1 | 2026-10-20 | Alpha GmbH | Assistenz | StepStone | — | 🟢 **interview_1** | 2026-10-28 | Anruf 28.10 17:30 | 🎯 **28.10 Telefoninterview 17:30** (Frau Beispiel, Msg `1a18`) |
| 2 | 2026-10-21 | Beta AG | Sachbearbeitung | Email | — | interview_1 | 2026-10-25 | — | Teams 27.10 |
| 3 | 2026-10-22 | Gamma SE | Empfang | Email | — | action_needed | 2026-10-28 | Zeugnisse senden | Bitte senden Sie Ihre Zeugnisse |
| 4 | 2026-10-28 | Delta KG | Rezeption | Email | — | rejected | 2026-10-28 | — | Absage |
"""


def _rows(tmp_path: Path, name: str, text: str):
    f = tmp_path / name
    f.write_text(text, encoding="utf-8")
    return rep.parse_rows(f)


def test_run_changes_lists_only_rows_that_became_actionable(tmp_path):
    before = _rows(tmp_path, "a.md", TRACKER_BEFORE)
    after = _rows(tmp_path, "b.md", TRACKER_AFTER)
    text = notify.run_changes_text(before, after, "morning", "uk")
    assert text.startswith("🎯 ранковий ран: нові співбесіди й запити")
    assert "<b>Alpha GmbH</b> · Assistenz" in text and "17:30" in text
    assert "<b>Gamma SE</b>" in text and "Zeugnisse" in text
    assert "Beta AG" not in text      # уже була interview_1 до рану
    assert "Delta KG" not in text     # відмова не є подією для оператора


def test_run_changes_none_without_new_actionable(tmp_path):
    rows = _rows(tmp_path, "a.md", TRACKER_BEFORE)
    assert notify.run_changes_text(rows, rows, "evening", "en") is None
    assert notify.run_changes_text([], rows, "evening", "en") is not None  # перший ран бачить усе


def test_status_word_ignores_emoji_and_bold():
    assert notify._status_word("🟢 **interview_1 28.10**") == "interview_1"
    assert notify._status_word("") == ""


def test_notify_prefixes_client_name_and_skips_without_transport():
    c = _client.active()
    t = FakeTransport()
    assert notify.notify(c, "hello", transport=t) is True
    _, text, _ = t.sent[0]
    assert text.startswith("<b>Erika</b> · hello")
    assert notify.notify(c, "hello", transport=None) is False  # erika без токена в оточенні


def test_configured_needs_token_and_chat_id(monkeypatch):
    c = _client.active()
    assert notify.configured(c) is False
    monkeypatch.setenv(c.telegram["token_env"], "t")
    assert notify.configured(c) is False  # chat_id в erika порожній


def test_alert_text_keeps_log_tail_in_pre():
    text = notify.alert_text("morning", "Offene Antwort <eines> Arbeitgebers", "Firma X\nMsg 1a18", "uk")
    assert text.startswith("⚠️ ранковий ран: Offene Antwort &lt;eines&gt; Arbeitgebers")
    assert "<pre>Firma X\nMsg 1a18</pre>" in text
    assert "<pre>" not in notify.alert_text("inbox", "x", "", "en")


def test_notify_card_has_excerpt_and_gmail_button():
    item = {"from": "HR <hr@example-co.de>", "subject": "Einladung", "company": "Example Co",
            "date": "Wed, 28 Oct 2026 09:00:00 +0100", "body": "Wir   laden Sie ein. <b>Termin</b> 17:30."}
    card = notify_card(item, "interview", "https://mail.google.com/x", "uk")
    assert card.kind == "notify"
    assert card.text.startswith("<b>Example Co</b> · Співбесіда")
    assert "Wir laden Sie ein. &lt;b&gt;Termin&lt;/b&gt; 17:30." in card.text
    assert card.buttons == [[card.buttons[0][0]]] and card.buttons[0][0].url == "https://mail.google.com/x"
