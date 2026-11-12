"""Регресії match_response_to_company.

Лист-підтвердження від «Falk & Falk» не має липнути до «Kulturbüro Düsseldorf»
лише тому, що в підписі стоїть місто Düsseldorf і цей токен довший за `falk`.
Такий матч пише статус у чужий рядок трекера.
"""
from bot.generators.responses import match_response_to_company

COMPANIES = ["Falk & Falk LLP", "Kulturbüro Düsseldorf", "Nordnetz GmbH", "Parfumia"]


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
        resp = {"from": "karriere@nordnetz.example", "subject": "Ihre Bewerbung", "body": "Danke."}
        assert match_response_to_company(resp, COMPANIES) == "Nordnetz GmbH"

    def test_single_word_company_in_subject(self):
        resp = {"from": "no-reply@ats.example", "subject": "Parfumia: Ihre Bewerbung",
                "body": "Vielen Dank für Ihre Bewerbung bei Parfumia."}
        assert match_response_to_company(resp, COMPANIES) == "Parfumia"

    def test_city_company_matches_when_own_name_present(self):
        resp = {"from": "info@kulturbuero-duesseldorf.example", "subject": "Kulturbüro Düsseldorf – Ihre Bewerbung",
                "body": "Das Kulturbüro Düsseldorf bestätigt den Eingang."}
        assert match_response_to_company(resp, COMPANIES) == "Kulturbüro Düsseldorf"

    def test_no_companies_gives_none(self):
        assert match_response_to_company({"from": "a@b.de", "subject": "x", "body": "y"}, []) is None


class TestDomainNeedsAWholeLabel:
    """12.11.2026: a private mail from a metering company was matched to an agency row because
    the short token «tech» of the agency name is a substring of the sender's domain. The row
    went to rejected."""

    COMPANIES = ["Norwind Tech Solutions GmbH (agency; end client: digital agency in Spain)",
                 "Delivery Falcon SE", "Nordnetz GmbH"]

    def test_short_token_inside_a_longer_domain_label_is_no_match(self):
        resp = {"from": "service@techhaus.example", "subject": "AW: Ihre Anfrage",
                "body": "leider duerfen wir Ihnen keine Auskuenfte erteilen."}
        assert match_response_to_company(resp, self.COMPANIES) is None

    def test_note_in_parentheses_is_not_part_of_the_name(self):
        resp = {"from": "hello@digital-agency.example", "subject": "Newsletter",
                "body": "Our digital agency in Spain has news."}
        assert match_response_to_company(resp, self.COMPANIES) is None

    def test_exact_label_still_matches(self):
        resp = {"from": "jobs@norwind.example", "subject": "Your application", "body": "Thanks."}
        assert match_response_to_company(resp, self.COMPANIES) == self.COMPANIES[0]

    def test_joined_name_as_domain_still_matches(self):
        resp = {"from": "careers@deliveryfalcon.example", "subject": "Your application", "body": "Thanks."}
        assert match_response_to_company(resp, self.COMPANIES) == "Delivery Falcon SE"

    def test_hyphenated_domain_still_matches(self):
        resp = {"from": "careers@nordnetz-karriere.example", "subject": "Bewerbung", "body": "Danke."}
        assert match_response_to_company(resp, self.COMPANIES) == "Nordnetz GmbH"
