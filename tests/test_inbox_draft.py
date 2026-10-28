"""Промпт чернетки відповіді, розбір відповіді моделі, збірка листа (bot.generators.inbox)."""
import pytest

from bot.generators.inbox import assemble_draft, draft_prompt, parse_draft
from tests.inbox_fixtures import MAILS, jordan_profile

RATE_DE = "Mein Stundensatz liegt bei 95 € netto zzgl. USt."


def test_draft_prompt_contains_mail_brief_and_rate():
    p = draft_prompt(MAILS["agency_outreach_de"], "outreach", "de", "BRIEF TEXT", jordan_profile(), RATE_DE)
    assert "BRIEF TEXT" in p and "Mein Stundensatz" in p and '"class"' in p and "80" in p
    assert MAILS["agency_outreach_de"]["body"][:60] in p
    assert "hourly_min" not in p and "80 €" not in p   # walk-away never leaves the profile


def test_draft_prompt_unknown_asks_for_class_first():
    p = draft_prompt(MAILS["stranger_en"], "unknown", "en", "brief", jordan_profile(), "")
    assert "decide the class" in p.lower()


def test_parse_draft_accepts_fenced_json_and_rejects_prose():
    assert parse_draft('Here:\n```json\n{"class": "outreach", "draft": "Danke."}\n```')["draft"] == "Danke."
    with pytest.raises(ValueError):
        parse_draft("I cannot help with that")
    with pytest.raises(ValueError):
        parse_draft('{"class": "spam", "draft": "x"}')
    with pytest.raises(ValueError):
        parse_draft('{"class": "outreach"}')


def test_assemble_draft_de_has_rate_and_no_dashes():
    profile = jordan_profile()
    text = assemble_draft("Gern – melde ich mich.", "de", profile, RATE_DE, "Agentur AG")
    assert text.startswith("Guten Tag,") and "Mein Stundensatz" in text and "–" not in text
    assert text.rstrip().endswith("Jordan Example\nLisbon\n" + profile["cv_contact_extra"][0])


def test_assemble_draft_en_greets_company():
    text = assemble_draft("Thanks for reaching out.", "en", jordan_profile(), "", "Example Co")
    assert text.startswith("Hello Example Co team,") and "Best regards" in text


def test_assemble_draft_does_not_repeat_rate_line():
    """Модель повторює речення про ставку в тілі (так просить промпт); збірка не додає його вдруге."""
    text = assemble_draft("Gern. " + RATE_DE + " Melden Sie sich.", "de", jordan_profile(), RATE_DE, None)
    assert text.count("Mein Stundensatz") == 1


def test_draft_prompt_names_forbidden_words():
    p = draft_prompt(MAILS["agency_outreach_de"], "outreach", "de", "brief", jordan_profile(), "",
                     forbidden=["Example Corp", "SuperApp"])
    assert "Example Corp" in p and "SuperApp" in p and "never name" in p.lower()


def test_draft_prompt_forbids_claiming_actions_taken():
    # Модель у тіньовому тижні «забронювала» слот у Calendly, якого ніхто не бронював
    p = draft_prompt(MAILS["agency_outreach_de"], "interview", "de", "BRIEF", jordan_profile(), "")
    assert "never claim that an action has already been taken" in p
    assert "propose times" in p
