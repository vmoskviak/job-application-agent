import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))


def test_classify_response_rejected():
    from bot.generators.responses import classify_response
    assert classify_response("Leider können wir Sie nicht berücksichtigen") == "rejected"
    assert classify_response("Die Stelle wurde anderweitig besetzt") == "rejected"
    assert classify_response("Wir müssen Ihnen eine Absage erteilen") == "rejected"


def test_classify_response_interview():
    from bot.generators.responses import classify_response
    assert classify_response("Wir möchten Sie zu einem Gespräch einladen") == "interview"
    assert classify_response("Können wir ein Interview vereinbaren?") == "interview"
    assert classify_response("Wir würden Sie gerne kennenlernen") == "interview"


def test_classify_response_info_request():
    from bot.generators.responses import classify_response
    assert classify_response("Bitte senden Sie uns noch Ihre Gehaltsvorstellung") == "info_request"
    assert classify_response("Könnten Sie uns weitere Unterlagen zusenden?") == "info_request"


def test_classify_response_unknown():
    from bot.generators.responses import classify_response
    assert classify_response("Vielen Dank für Ihre Bewerbung") == "unknown"


def test_classify_response_transliterated_german():
    """Регресія 17.07: Absage Schmidt & Bachmeier без ü/ä оминула keyword-list."""
    from bot.generators.responses import classify_response
    assert classify_response(
        "Ihre Bewerbung koennen wir nicht beruecksichtigen") == "rejected"
    assert classify_response(
        "Wir freuen uns auf ein persoenliches Gespraech mit Ihnen") == "interview"
    assert classify_response(
        "Eingangsbestaetigung: Ihre Unterlagen sind angekommen") == "confirmed"


def test_match_company_eszett_vs_ss():
    """Регресія: назва з ß не матчилась із доменом через ss."""
    from bot.generators.responses import match_response_to_company
    resp = {"from": "bewerbung@grosswerk.nrw",
            "subject": "Ihre Bewerbung, Kennung 5671",
            "body": "Sehr geehrte Frau Mustermann, ..."}
    companies = ["Großwerk.NRW", "Andere Firma GmbH"]
    assert match_response_to_company(resp, companies) == "Großwerk.NRW"


def test_match_company_transliterated_body():
    from bot.generators.responses import match_response_to_company
    resp = {"from": "noreply@bewerbermanagement.net",
            "subject": "Ihre Bewerbung bei Grosswerk.NRW",
            "body": "vielen Dank fuer Ihre Bewerbung bei Grosswerk.NRW"}
    companies = ["Großwerk.NRW"]
    assert match_response_to_company(resp, companies) == "Großwerk.NRW"


def test_parse_tracker_extracts_sent_entries(tmp_path):
    from bot.generators.responses import parse_tracker_sent
    tracker = tmp_path / "tracker.md"
    tracker.write_text(
        "| Date | Position | Employer | Status |\n"
        "|------|----------|----------|--------|\n"
        "| 2026-05-16 | Junior BA | Kontora, Düsseldorf | ✅ Email sent |\n"
        "| 2026-05-15 | Trainee | GOB, Krefeld | ✅ Email sent |\n"
        "| 2026-05-14 | Consultant | Beispielwerk | ❌ Відмова |\n",
        encoding="utf-8",
    )
    entries = parse_tracker_sent(tracker)
    assert len(entries) == 2
    assert any(e["company"] == "Kontora" for e in entries)
    assert not any(e["company"] == "Beispielwerk" for e in entries)


def test_generate_report_contains_stats():
    from bot.generators.responses import generate_report
    entries = [
        {"company": "A", "status": "interview"},
        {"company": "B", "status": "rejected"},
        {"company": "C", "status": "awaiting"},
        {"company": "D", "status": "awaiting"},
    ]
    report = generate_report(entries, week_number=21)
    assert "KW 21" in report
    assert "interview" in report.lower() or "Interview" in report


def test_extract_domain_from_email():
    from bot.generators.responses import extract_domain
    assert extract_domain("ts-bewerben.de@kontora.example") == "kontora.example"
    assert extract_domain("karriere@firma.de") == "firma.de"
    assert extract_domain(None) is None
