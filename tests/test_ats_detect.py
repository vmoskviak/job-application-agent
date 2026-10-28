from dataclasses import replace

from bot.apply.ats_detect import (
    PROBE_JS,
    PageProbe,
    closed_in,
    confirmation_in,
    outcome_after_submit,
)


def test_confirmation_phrases():
    assert confirmation_in("Thank you for applying to Contract Co!") == "thank you for applying"
    assert confirmation_in("Vielen Dank für Ihre Bewerbung") == "vielen dank für ihre bewerbung"
    assert confirmation_in("Apply now") == ""


def test_confirmation_requires_form_gone():
    p = PageProbe(text="How to apply: your application submitted via this form…", html="", submit_visible=True, challenge_visible=False, invalid_count=0, error_texts=[])
    assert outcome_after_submit(p) == "pending"
    assert outcome_after_submit(replace(p, submit_visible=False)) == "submitted"


def test_challenge_visible_after_submit():
    p = PageProbe(text="", html="", submit_visible=True, challenge_visible=True, invalid_count=0, error_texts=[])
    assert outcome_after_submit(p) == "challenge"


def test_validation_errors():
    p = PageProbe(text="", html="", submit_visible=True, challenge_visible=False, invalid_count=2, error_texts=["Required"])
    assert outcome_after_submit(p) == "validation"


def test_closed():
    assert closed_in("This job is no longer accepting applications.")
    assert not closed_in("Apply for this job")


def test_probe_js_mentions_bframe():
    assert "bframe" in PROBE_JS and "aria-invalid" in PROBE_JS


def test_import_does_not_load_candidate():
    import importlib
    import sys

    for m in ("bot.apply.ats_detect", "bot.apply.candidate", "bot.apply.forms"):
        sys.modules.pop(m, None)
    importlib.import_module("bot.apply.ats_detect")
    assert "bot.apply.candidate" not in sys.modules
