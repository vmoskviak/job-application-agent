"""Upwork alert parser (bot.generators.upwork_alert): the plain-text mail becomes one job record."""
from __future__ import annotations

from dataclasses import replace

import pytest

from bot.generators.upwork_alert import (
    UnknownFormat,
    budget_text,
    fingerprint,
    parse_alert,
)
from tests.upwork_fixtures import (
    META,
    PLUS_JOB_ID,
    PLUS_SUBJECT,
    PLUS_TITLE,
    SUBJECT,
    alert_body,
    job_block,
    plus_alert_body,
    track,
)

DATE = "Thu, 29 Oct 2026 09:12:00 +0000"


def _hourly_meta(*extra: str) -> tuple[str, ...]:
    return (" • More than 6 months", " • Less than 30 hrs/week", "    Payment verified", " • 4.9",
            " • $10K+\nspent", " • USA", *extra)


def test_fixed_price_alert_parses_every_field():
    job = parse_alert(SUBJECT, alert_body(), message_id="19a0000000000001", date=DATE)
    assert job.title == "Example Fitness Companion App"
    assert job.snippet == ("We are looking for an experienced mobile engineer for a focused enhancement phase on an "
                           "existing product. The scope will include improving personalization and daily-routine ...")
    assert (job.budget_type, job.budget_min, job.budget_max) == ("fixed", 2000.0, 2000.0)
    assert job.duration == "3 to 6 months"
    assert job.payment_verified is True and job.rating == 5.0 and job.spent == "$2k" and job.country == "PRT"
    assert job.category == "Full-Stack Development" and job.skills == ["iOS", "Android", "Health & Wellness"]
    assert job.saved_search == "android kotlin"
    assert job.job_link == track(13) and len(job.job_link) >= 900
    assert job.message_id == "19a0000000000001" and job.date == DATE


def test_filler_and_broken_lines_do_not_leak_into_fields():
    job = parse_alert(SUBJECT, alert_body())
    for value in (job.title, job.snippet, job.duration, job.spent, job.country, job.category,
                  job.saved_search, *job.skills):
        assert "\u034f" not in value and "\u00ad" not in value and "\n" not in value
        assert "http" not in value and "  " not in value


def test_hourly_range_with_workload():
    body = alert_body(job_block(budget="Hourly:\n$40.00-\n$80.00", meta=_hourly_meta()))
    job = parse_alert(SUBJECT, body)
    assert (job.budget_type, job.budget_min, job.budget_max) == ("hourly", 40.0, 80.0)
    assert job.duration == "More than 6 months, Less than 30 hrs/week"
    assert job.rating == 4.9 and job.spent == "$10K+" and job.country == "USA"
    assert budget_text(job) == "hourly $40-$80"


def test_hourly_without_amounts_keeps_amounts_unknown():
    job = parse_alert(SUBJECT, alert_body(job_block(budget="Hourly", meta=_hourly_meta())))
    assert (job.budget_type, job.budget_min, job.budget_max) == ("hourly", None, None)
    assert budget_text(job) == "hourly"


@pytest.mark.parametrize("budget, kind", [("Hourly:\n$0-\n$0", "hourly"), ("Hourly:\n$0.00-\n$0.00", "hourly"),
                                          ("Fixed-price:\n$0", "fixed")])
def test_zero_amounts_mean_the_client_gave_no_budget(budget, kind):
    """Upwork writes "Hourly: $0-$0" when the client left the budget empty; that is unknown, not $0."""
    job = parse_alert(SUBJECT, alert_body(job_block(budget=budget, meta=_hourly_meta())))
    assert (job.budget_type, job.budget_min, job.budget_max) == (kind, None, None)
    assert budget_text(job) == kind


def test_payment_line_absent_or_unverified():
    no_line = tuple(m for m in META if "Payment" not in m)
    assert parse_alert(SUBJECT, alert_body(job_block(meta=no_line))).payment_verified is None
    unverified = tuple(m.replace("Payment verified", "Payment unverified") for m in META)
    assert parse_alert(SUBJECT, alert_body(job_block(meta=unverified))).payment_verified is False


def test_missing_budget_and_signals_stay_empty():
    job = parse_alert(SUBJECT, alert_body(job_block(budget="", meta=(), tags=())))
    assert (job.budget_type, job.budget_min, job.budget_max) == ("unknown", None, None)
    assert job.duration == "" and job.rating is None and job.spent == "" and job.country == ""
    assert job.category == "" and job.skills == [] and budget_text(job) == "unknown"
    assert job.snippet.endswith("daily-routine ...")


def test_short_description_without_more_link_ends_at_the_budget():
    body = alert_body(job_block(snippet="")).replace(
        "\n\nFixed\nprice:", "\n\nNeed a Kotlin Multiplatform module ported to Compose.\n\nFixed\nprice:", 1)
    job = parse_alert(SUBJECT, body)
    assert job.snippet == "Need a Kotlin Multiplatform module ported to Compose."
    assert job.budget_type == "fixed" and job.budget_max == 2000.0


def test_title_with_parentheses_and_a_straight_apostrophe():
    title = "Android developer (Kotlin, Compose) for a booking app"
    body = alert_body(job_block(title=title, ready_line=" Take a look and submit\na proposal when you're ready."))
    assert parse_alert(f"New job alert: {title}", body).title == title


def test_title_from_subject_when_the_anchor_phrase_changed():
    body = alert_body(job_block(ready_line=" Have a look at this job."))
    job = parse_alert(SUBJECT, body)
    assert job.title == "Example Fitness Companion App" and job.budget_max == 2000.0


def test_wrong_subject_is_unknown_format():
    with pytest.raises(UnknownFormat, match="subject"):
        parse_alert("Jobs picked for you", alert_body())


def test_two_jobs_in_one_mail_is_unknown_format():
    body = alert_body(job_block(), job_block(title="Second Example Job", first_link=20))
    with pytest.raises(UnknownFormat, match="2 'View job details'"):
        parse_alert(SUBJECT, body)


def test_no_view_job_details_is_unknown_format():
    body = alert_body().replace("View job details", "See the job")
    with pytest.raises(UnknownFormat, match="0 'View job details'"):
        parse_alert(SUBJECT, body)


def test_title_not_found_is_unknown_format():
    body = alert_body(job_block(ready_line=" Have a look at this job."))
    with pytest.raises(UnknownFormat, match="title"):
        parse_alert("New job alert: Some Other Title", body)


def test_fingerprint_ignores_case_and_punctuation_but_not_budget_or_country():
    job = parse_alert(SUBJECT, alert_body())
    same = replace(job, title="example fitness companion app!", message_id="other", saved_search="kmp")
    assert fingerprint(job) == fingerprint(same)
    assert fingerprint(job) != fingerprint(replace(job, budget_min=3000.0, budget_max=3000.0))
    assert fingerprint(job) != fingerprint(replace(job, country="DEU"))
    assert budget_text(job) == "fixed $2,000"


def test_fingerprint_tells_apart_two_jobs_with_a_generic_title():
    """Two "Android Developer" jobs, same country and budget: only the description excerpt differs."""
    job = parse_alert(SUBJECT, alert_body())
    other = replace(job, snippet="Build a parking app with offline maps ...")
    assert fingerprint(job) != fingerprint(other)
    assert fingerprint(job) == fingerprint(replace(job, snippet=job.snippet.upper() + "!"))


# ── Freelancer Plus layout ───────────────────────────────────────────────────

def test_plus_alert_parses_every_field():
    job = parse_alert(PLUS_SUBJECT, plus_alert_body(), message_id="19a0000000000009", date=DATE)
    assert job.title == PLUS_TITLE
    assert job.snippet == ("We need an engineer to package our product features as an SDK that partner apps can "
                           "embed. Mobile experie...")
    assert (job.budget_type, job.budget_min, job.budget_max) == ("fixed", 3500.0, 3500.0)
    assert job.duration == "More than 6 months"
    assert job.payment_verified is True and job.rating == 4.8 and job.spent == "$9K" and job.country == "Portugal"
    assert job.skills == ["SDK", "Android", "iOS"] and job.category == "" and job.saved_search == ""
    assert job.job_link == f"https://www.upwork.com/jobs/{PLUS_JOB_ID}?frkscc=VIEW0"
    assert job.message_id == "19a0000000000009" and job.date == DATE


def test_plus_job_link_names_the_job_without_a_redirect():
    """The direct link holds the job id: no click tracker to resolve, no request at all."""
    from bot.upwork_submit import resolve_job_id

    def no_request(url):
        raise AssertionError(f"requested {url}")

    assert resolve_job_id(parse_alert(PLUS_SUBJECT, plus_alert_body()).job_link, no_request) == PLUS_JOB_ID


def test_plus_hourly_without_budget_stays_unknown():
    job = parse_alert(PLUS_SUBJECT, plus_alert_body(budget="Hourly: $0.00 - $0.00",
                                                    client="Payment verified · 4.99 stars · $47K spent · Germany"))
    assert (job.budget_type, job.budget_min, job.budget_max) == ("hourly", None, None)
    assert budget_text(job) == "hourly" and job.country == "Germany" and job.rating == 4.99


def test_plus_hourly_range_and_unverified_client():
    job = parse_alert(PLUS_SUBJECT, plus_alert_body(budget="Hourly: $40.00 - $80.00",
                                                    est="Est. time: 1 to 3 months, Less than 30 hrs/week",
                                                    client="Payment unverified · $0 spent · United Kingdom"))
    assert (job.budget_min, job.budget_max) == (40.0, 80.0)
    assert job.duration == "1 to 3 months, Less than 30 hrs/week"
    assert job.payment_verified is False and job.rating is None and job.country == "United Kingdom"


def test_plus_short_description_without_more_link():
    body = plus_alert_body(snippet="").replace("Skills:", "Short description of the job.\n\nSkills:")
    assert parse_alert(PLUS_SUBJECT, body).snippet == "Short description of the job."


def test_plus_two_jobs_or_no_title_is_unknown_format():
    with pytest.raises(UnknownFormat, match="2 'View job details'"):
        parse_alert(PLUS_SUBJECT, plus_alert_body(views=2))
    with pytest.raises(UnknownFormat, match="title"):
        parse_alert(PLUS_SUBJECT, plus_alert_body(title=""))


def test_plus_title_without_its_link_mark_needs_the_subject():
    """Upwork may drop "link=title": the title is then the link text that starts like the subject."""
    body = plus_alert_body().replace("?link=title&", "?")
    assert parse_alert(PLUS_SUBJECT, body).title == PLUS_TITLE
    with pytest.raises(UnknownFormat, match="title"):
        parse_alert("New job alert: Something Else Entirely", body)


def test_plus_and_classic_mail_of_one_job_share_no_fields_by_accident():
    """The layouts differ (country name against code, no saved search), so their fingerprints
    differ too: one job in both mails costs at most a second record, never a merged wrong one."""
    plus = parse_alert(PLUS_SUBJECT, plus_alert_body())
    classic = parse_alert(SUBJECT, alert_body())
    assert fingerprint(plus) != fingerprint(classic)
