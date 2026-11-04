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
    resp = {"from": "bewerbung@spasswerk.beispiel",
            "subject": "Ihre Bewerbung, Kennung 5671",
            "body": "Sehr geehrte Frau Mustermann, ..."}
    companies = ["Spaßwerk.Beispiel", "Andere Firma GmbH"]
    assert match_response_to_company(resp, companies) == "Spaßwerk.Beispiel"


def test_match_company_transliterated_body():
    from bot.generators.responses import match_response_to_company
    resp = {"from": "noreply@ats.example",
            "subject": "Ihre Bewerbung bei Spasswerk.Beispiel",
            "body": "vielen Dank fuer Ihre Bewerbung bei Spasswerk.Beispiel"}
    companies = ["Spaßwerk.Beispiel"]
    assert match_response_to_company(resp, companies) == "Spaßwerk.Beispiel"


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


def test_classify_english_ats_replies():
    """Контрактний ринок: Greenhouse-лист «Thank you for applying» ішов як unknown."""
    from bot.generators.responses import classify_response

    assert classify_response("Thank you for applying to Beispiel Labs! Our team will review it.") == "confirmed"
    assert classify_response("Unfortunately, we have decided not to move forward with your application.") == "rejected"
    assert classify_response("We decided to move forward with other candidates at this time.") == "rejected"


def test_classify_reads_only_the_new_part_of_a_reply():
    # 28.10.2026: запрошення на дзвінок стало «rejected» через «leider» у процитованому
    # нижче листі кандидатки
    from bot.generators.responses import classify_response, strip_quoted
    body = ("Sehr geehrte Frau Beispiel,\n\ndas ist kein Problem.\nMorgen ist ein Termin frei geworden.\n"
            "Ich kann Sie gerne um 17:30 Uhr anrufen.\n\nMit freundlichen Grüßen\nA. Muster\n\n"
            "-----Ursprüngliche Nachricht-----\nVon: Erika Beispiel <erika@example.com>\n"
            "Gesendet: Donnerstag, 22. Oktober 2026 14:34\nAn: Posteingang\nBetreff: Re: Tätigkeit\n\n"
            "leider kann ich heute nicht.")
    assert "leider" not in strip_quoted(body)
    assert classify_response(body) == "interview"
    gmail = "Danke, Gespräch am Montag passt.\n\nAm Mo., 27. Okt. 2026 schrieb Erika <e@example.com>:\n> leider nein"
    assert classify_response(gmail) == "interview"
    outlook_en = "We'd like to interview you.\nFrom: Erika\nSent: Monday\nTo: HR\nSubject: Re\n\nunfortunately"
    assert classify_response(outlook_en) == "interview"


def test_soft_regret_yields_to_interview_but_hard_rejection_wins():
    from bot.generators.responses import classify_response
    assert classify_response("Leider nur telefonisch: wir möchten Sie zu einem Gespräch einladen") == "interview"
    assert classify_response("Nach unserem Gespräch müssen wir Ihnen leider absagen") == "rejected"
    assert classify_response("Sie sind leider nicht mit in die engere Wahl gekommen") == "rejected"
    assert classify_response("Leider können wir Ihnen nichts anbieten") == "rejected"
    assert classify_response("> leider absage") == "rejected"  # лист лише з цитати читається як є


def test_auto_acknowledgement_that_mentions_future_interviews_is_confirmed():
    from bot.generators.responses import classify_response
    """29.10.2026: an applicant-platform auto-mail («Thank you for applying… under review… we will select
    candidates for preliminary screening interviews») was read as an interview and got a reply card."""
    auto_en = ("Hello there, Thank you for applying to Senior Android Engineer. Your profile is currently under "
               "review. We're reviewing applications and will select the top matching candidates for preliminary "
               "screening interviews. If you're among them, we'll contact you to arrange a convenient time.")
    assert classify_response(auto_en) == "confirmed"
    auto_de = ("Vielen Dank für Ihre Bewerbung. Wir haben Ihre Bewerbung erhalten und melden uns, sobald wir "
               "sie gesichtet haben. Kommen Sie in die engere Auswahl, folgt ein persönliches Gespräch.")
    assert classify_response(auto_de) == "confirmed"


def test_invitation_after_a_thank_you_is_still_an_interview():
    from bot.generators.responses import classify_response
    assert classify_response("Thank you for applying! We would like to invite you to a first call. "
                             "Please book a slot: https://calendly.com/contractco/30min") == "interview"
    assert classify_response("Vielen Dank für Ihre Bewerbung. Gerne möchten wir Sie zu einem Gespräch "
                             "einladen: Donnerstag 10 Uhr per Teams.") == "interview"
    assert classify_response("Thanks for applying. Are you available for an interview on Monday?") == "interview"


def test_rejection_with_phone_hours_in_the_signature_is_rejected():
    """02.11.2026: a plain Absage («für eine andere Bewerbung entschieden») became an interview card
    because the signature said «telefonische Sprechzeiten»."""
    from bot.generators.responses import classify_response
    body = ("Sehr geehrte Frau Muster,\nvielen Dank für Ihre Bewerbung. Nach sorgfältiger Prüfung müssen wir "
            "Ihnen leider mitteilen, dass wir uns in diesem Auswahlprozess für eine andere Bewerbung entschieden "
            "haben.\nMit freundlichen Grüßen\nBeispiel GmbH\ntelefonische Sprechzeiten Mo-Do 9-12")
    assert classify_response(body) == "rejected"
    assert classify_response("Wir haben uns für einen anderen Bewerber entschieden. Tel. Rückruf unter 0221") == "rejected"
    assert classify_response("dass ich Sie in der engeren Auswahl nicht weiter berücksichtigen kann. "
                             "Telefonisch erreichen Sie uns Mo-Fr") == "rejected"
    assert classify_response("Dennoch haben wir uns nach reiflicher Überlegung entschieden, den Auswahlprozess "
                             "für diese Position mit anderen Bewerber:innen fortzusetzen.") == "rejected"


def test_rescheduled_interview_with_leider_mitteilen_stays_interview():
    from bot.generators.responses import classify_response
    body = ("Leider müssen wir Ihnen mitteilen, dass wir den Termin verschieben müssen. "
            "Wir möchten Sie stattdessen am Donnerstag um 10 Uhr zu einem Gespräch einladen.")
    assert classify_response(body) == "interview"
