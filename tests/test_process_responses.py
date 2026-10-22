import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from bot.generators.responses import (
    classify_response, process_responses, update_tracker_status,
)

TRACKER = """\
| # | Date | Company | Position | Source | Salary range | Status | Last update | Next action | Notes |
|---|---|---|---|---|---|---|---|---|---|
| 1 | 2026-06-20 | Musterfirma GmbH | Teamassistenz (m/w/d) | StepStone | ~42K | sent | 2026-06-20 | Follow-up | Düsseldorf |
| 2 | 2026-06-21 | Beispiel AG | Junior Business Analyst (m/w/d) | LinkedIn | ~48K | confirmed | 2026-06-22 | Follow-up | Köln |
| 3 | 2026-06-22 | Alte Firma GmbH | Sachbearbeiter (m/w/d) | Email | — | rejected | 2026-06-30 | — | закрито |

| 2026-06-23 | Office Manager (m/w/d) | Kleinbetrieb UG, Neuss | ✅ Email sent |
"""


def _tracker(tmp_path) -> Path:
    p = tmp_path / "tracker.md"
    p.write_text(TRACKER, encoding="utf-8")
    return p


def test_classify_confirmed():
    assert classify_response("Eingangsbestätigung: Wir haben Ihre Bewerbung erhalten") == "confirmed"
    assert classify_response("Thank you for your application!") == "confirmed"


def test_update_tracker_status_main_table(tmp_path):
    tracker = _tracker(tmp_path)
    assert update_tracker_status(tracker, "Musterfirma GmbH", "interview_1", "2026-07-08")
    text = tracker.read_text(encoding="utf-8")
    row = next(l for l in text.splitlines() if "Musterfirma" in l)
    assert "| interview_1 |" in row
    assert "| 2026-07-08 |" in row


def test_update_tracker_status_never_downgrades_terminal(tmp_path):
    tracker = _tracker(tmp_path)
    assert not update_tracker_status(tracker, "Alte Firma GmbH", "confirmed")
    assert "rejected" in tracker.read_text(encoding="utf-8")


def test_update_tracker_status_legacy_row(tmp_path):
    tracker = _tracker(tmp_path)
    assert update_tracker_status(tracker, "Kleinbetrieb UG", "rejected", "2026-07-08")
    row = next(l for l in tracker.read_text(encoding="utf-8").splitlines()
               if "Kleinbetrieb" in l)
    assert "rejected (2026-07-08)" in row


def test_update_tracker_status_legacy_row_keeps_url(tmp_path):
    # Статус перезаписує клітинку цілком; URL вакансії має лишитись для dedup.
    tracker = tmp_path / "tracker.md"
    tracker.write_text(
        "| 2026-10-16 | Teamassistenz (m/w/d) | Kleinbetrieb UG, Düsseldorf "
        "| 🔄 Файли готові — подати вручну · https://www.kleinbetrieb.de/jobs/42 |\n",
        encoding="utf-8",
    )
    assert update_tracker_status(tracker, "Kleinbetrieb UG", "rejected", "2026-10-20")
    row = tracker.read_text(encoding="utf-8").splitlines()[0]
    assert "rejected (2026-10-20) · https://www.kleinbetrieb.de/jobs/42 |" in row


def test_process_responses_end_to_end(tmp_path):
    tracker = _tracker(tmp_path)
    responses = [
        {
            "account": "jobs", "message_id": "m1",
            "from": "recruiting@musterfirma.de",
            "subject": "Einladung zum Vorstellungsgespräch",
            "body": "Wir möchten Sie gerne zu einem Gespräch einladen am 15.07. um 10:00.",
        },
        {
            "account": "main", "message_id": "m2",
            "from": "noreply@beispiel.de",
            "subject": "Ihre Bewerbung",
            "body": "Leider können wir Sie nicht in die engere Auswahl nehmen.",
        },
        {
            "account": "jobs", "message_id": "m3",
            "from": "hr@kleinbetrieb.de",
            "subject": "Eingangsbestätigung",
            "body": "Wir haben Ihre Bewerbung erhalten und melden uns.",
        },
        {
            "account": "jobs", "message_id": "m4",
            "from": "info@fremde-firma.de",
            "subject": "Newsletter",
            "body": "Ganz anderer Inhalt ohne Schlüsselwörter.",
        },
    ]

    report = process_responses(responses, tracker)

    assert len(report["interviews"]) == 1
    assert report["interviews"][0]["company"] == "Musterfirma GmbH"

    by_id = {u["message_id"]: u for u in report["updated"]}
    assert by_id["m1"]["classification"] == "interview"
    assert by_id["m2"]["classification"] == "rejected"
    assert by_id["m2"]["safe_to_trash"] is False          # main — ніколи
    assert by_id["m3"]["classification"] == "confirmed"
    assert by_id["m3"]["safe_to_trash"] is True           # jobs + confirmed

    assert len(report["unknown"]) == 1
    assert report["unknown"][0]["message_id"] == "m4"

    text = tracker.read_text(encoding="utf-8")
    assert "| interview_1 |" in text            # Musterfirma
    row = next(l for l in text.splitlines() if "Beispiel AG" in l)
    assert "| rejected |" in row
