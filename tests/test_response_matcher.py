"""Регресії match_response_to_company.

25.07.2026: лист-підтвердження Falk & Falk приліпився до «Bildungswerk Düsseldorf», бо в
підписі стоїть місто Düsseldorf, а перемагав найдовший токен (`düsseldorf` 10
літер проти `falk` 4). Такий матч пише статус у чужий рядок трекера.
"""
from bot.generators.responses import match_response_to_company

COMPANIES = ["Falk & Falk LLP", "Bildungswerk Düsseldorf", "Weststrom GmbH", "Duftwerk"]


class TestCityDoesNotHijack:
    def test_city_in_signature_does_not_win_over_real_name(self):
        resp = {
            "from": "notifications@app.softgarden.io",
            "subject": "Ihre Bewerbung als Assistenz (m/w/d) Arbeitsrecht I Falk & Falk LLP",
            "body": "Falk & Falk LLP Düsseldorf | Frankfurt | Hamburg | München Germany",
        }
        assert match_response_to_company(resp, COMPANIES) == "Falk & Falk LLP"

    def test_city_only_match_is_no_match(self):
        resp = {
            "from": "hr@fremdefirma.de",
            "subject": "Ihre Bewerbung",
            "body": "Wir sitzen in Düsseldorf und melden uns später.",
        }
        assert match_response_to_company(resp, COMPANIES) is None

    def test_generic_legal_form_is_no_match(self):
        resp = {"from": "hr@fremdefirma.de", "subject": "Bewerbung",
                "body": "Die GmbH hat entschieden."}
        assert match_response_to_company(resp, COMPANIES) is None


class TestStillMatches:
    def test_domain_match(self):
        resp = {"from": "karriere@weststrom.net", "subject": "Ihre Bewerbung", "body": "Danke."}
        assert match_response_to_company(resp, COMPANIES) == "Weststrom GmbH"

    def test_single_word_company_in_subject(self):
        resp = {"from": "no-reply@ats.example", "subject": "Duftwerk: Ihre Bewerbung",
                "body": "Vielen Dank für Ihre Bewerbung bei Duftwerk."}
        assert match_response_to_company(resp, COMPANIES) == "Duftwerk"

    def test_city_company_matches_when_own_name_present(self):
        resp = {"from": "info@bildungswerk-duesseldorf.de", "subject": "Bildungswerk Düsseldorf – Ihre Bewerbung",
                "body": "Die Bildungswerk Düsseldorf bestätigt den Eingang."}
        assert match_response_to_company(resp, COMPANIES) == "Bildungswerk Düsseldorf"

    def test_no_companies_gives_none(self):
        assert match_response_to_company({"from": "a@b.de", "subject": "x", "body": "y"}, []) is None
