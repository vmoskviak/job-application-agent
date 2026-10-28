"""Класи вхідних листів EN/DE, відправники платформ, мова чернетки (bot.generators.inbox)."""
import pytest

from bot.generators.inbox import (
    append_note,
    classify_mail,
    currency_for_mail,
    draft_language,
    is_agency,
    platform_queries,
    sender_domain,
    status_for_class,
)
from bot.generators.responses import classify_response
from tests.inbox_fixtures import AGENCIES, JORDAN_TRACKER_WITH_TWO_ROWS, MAILS


@pytest.mark.parametrize("name,expected", [
    ("greenhouse_confirmed_en", "confirmed"), ("ats_rejected_en", "rejected"),
    ("company_interview_de", "interview"), ("company_info_request_de", "info_request"),
    ("agency_outreach_de", "outreach"), ("linkedin_inmail_de", "outreach"),
    ("malt_invitation_de", "invitation"), ("upwork_message_en", "invitation"),
    ("freelance_newsletter_de", "system"), ("djinni_weekly_uk", "system"),
    ("stranger_en", "unknown"),
    ("linkedin_profile_views_en", "system"), ("xing_profile_views_de", "system"),
    ("gulp_alert_en", "system"), ("freelancermap_alert_de", "system"),
])
def test_classify_mail(name, expected):
    assert classify_mail(MAILS[name], AGENCIES) == expected


def test_fixture_classification_matches_classify_response():
    for name, item in MAILS.items():
        assert classify_response(item["body"] + " " + item["subject"]) == item["classification"], name


def test_draft_language_agency_beats_english_text():
    item = {**MAILS["agency_outreach_de"], "subject": "Opportunity", "body": "We have a contract role for you."}
    assert draft_language(item, AGENCIES) == "de"
    assert draft_language(MAILS["stranger_en"], AGENCIES) == "en"
    assert draft_language(MAILS["company_interview_de"], AGENCIES) == "de"


def test_sender_domain_and_agency():
    assert sender_domain("Simon <s.r@mail.computerfutures.com>") == "computerfutures.com"
    assert sender_domain("no-reply@eu.greenhouse-mail.io") == "greenhouse-mail.io"
    assert is_agency("x@mail.computerfutures.com", AGENCIES) is True
    assert is_agency("x@gmail.example", AGENCIES) is False


def test_platform_queries_exclude_alerts_and_chunk():
    qs = platform_queries(2, ["jobalerts-noreply@linkedin.com"])
    assert qs
    assert all("newer_than:2d in:inbox" in q and "-from:jobalerts-noreply@linkedin.com" in q for q in qs)
    assert all(q.count("from:") - q.count("-from:") <= 20 for q in qs)
    assert any("from:linkedin.com" in q for q in qs)


def test_status_for_class():
    assert status_for_class("confirmed") == "confirmed"
    assert status_for_class("rejected") == "rejected"
    assert status_for_class("interview") == "interview"
    assert status_for_class("outreach") is None


def test_currency_for_mail():
    assert currency_for_mail({"subject": "Rate?", "body": "Budget is $90/hour."}, "en") == "USD"
    assert currency_for_mail({"subject": "Rate?", "body": "Budget is $90/hour."}, "de") == "EUR"
    assert currency_for_mail({"subject": "Projekt", "body": "Stundensatz?"}, "en") == "EUR"


def test_append_note(tmp_path):
    t = tmp_path / "tracker.md"
    t.write_text(JORDAN_TRACKER_WITH_TWO_ROWS, encoding="utf-8")
    assert append_note(t, "Example Co", "2026-10-26 reply sent Msg 1a2b3c4d5e6f7a8b") is True
    assert "· 2026-10-26 reply sent Msg 1a2b3c4d5e6f7a8b |" in t.read_text(encoding="utf-8")
    assert append_note(t, "Nobody Co", "x") is False


def test_append_note_by_row(tmp_path):
    from bot.generators.inbox import append_note_by_row
    t = tmp_path / "tracker.md"
    t.write_text(JORDAN_TRACKER_WITH_TWO_ROWS, encoding="utf-8")
    assert append_note_by_row(t, "2", "2026-10-29 follow-up Msg x1") is True
    lines = [ln for ln in t.read_text(encoding="utf-8").splitlines() if ln.startswith("| 2 |")]
    assert lines and lines[0].rstrip().endswith("· 2026-10-29 follow-up Msg x1 |")
    assert append_note_by_row(t, "99", "x") is False
