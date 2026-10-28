"""Збережені сторінки Ashby і Greenhouse у Chromium: FIELD_JS і PROBE_JS на справжній розмітці.

Запуск лише з JOBSEARCH_BROWSER_TESTS=1 (потрібен Playwright з Chromium), інакше skip.
HTML-фікстури зняті з живих публічних вакансій і вичищені (роботодавець «Contract Co»,
без скриптів, стилів, картинок і опису вакансії; правила приховування скопійовані в
<style>). JSON-фікстури ashby_form_fields.json і greenhouse_form_fields.json, які
використовують тести без браузера, є результатом саме цього прогону: із
JOBSEARCH_REGEN_FIXTURES=1 вони перезаписуються, без нього перевіряється, що
комітнутий JSON не відстав від HTML.
"""
from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path

import pytest

from bot.apply.ats_answers import llm_allowed
from bot.apply.ats_detect import PROBE_JS
from bot.apply.ats_fields import FIELD_JS, classify, fields_from

FIXTURES = Path(__file__).parent / "fixtures" / "ats"
pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(not os.environ.get("JOBSEARCH_BROWSER_TESTS"), reason="JOBSEARCH_BROWSER_TESTS=1 не задано"),
]


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        yield b
        b.close()


def _run(browser, html_name: str, json_name: str):
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    page.goto((FIXTURES / html_name).as_uri())
    raw = page.evaluate(FIELD_JS)
    probe = page.evaluate(PROBE_JS)
    page.close()
    path = FIXTURES / json_name
    if os.environ.get("JOBSEARCH_REGEN_FIXTURES"):
        path.write_text(json.dumps(raw, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    assert json.loads(path.read_text(encoding="utf-8")) == raw, f"{json_name} відстав від {html_name}: JOBSEARCH_REGEN_FIXTURES=1"
    fields = fields_from(raw)
    return fields, probe


def _by_id(fields, id_):
    return next(f for f in fields if f.id == id_)


def _by_label(fields, text):
    return next(f for f in fields if text in f.label)


def test_ashby_application(browser):
    fields, probe = _run(browser, "ashby_application.html", "ashby_form_fields.json")
    name = _by_id(fields, "_systemfield_name")
    assert name.label == "Name" and classify(name) == "full_name" and name.required
    assert classify(_by_id(fields, "_systemfield_email")) == "email"
    resume = _by_id(fields, "_systemfield_resume")
    assert resume.type == "file" and classify(resume) == "resume" and not resume.required and resume.visible
    assert classify(fields[0]) == "unknown" and fields[0].type == "file"   # автозаповнювач без підпису
    country = _by_label(fields, "Which country do you intend to work from?")
    assert country.combobox and classify(country) == "country" and country.required and not llm_allowed(country)
    yesno = _by_label(fields, "require sponsorship for employment visa")
    assert yesno.type == "checkbox" and yesno.name and yesno.id == "" and yesno.group == yesno.label
    assert classify(yesno) == "choice" and yesno.visible and yesno.required and yesno.value == ""
    assert not llm_allowed(yesno)
    gender = _by_label(fields, "Woman")
    assert gender.type == "radio" and classify(gender) == "eeo" and "gender" in gender.group.lower()
    assert classify(_by_label(fields, "Describe a project you are proud of")) == "free_text"
    assert all(not f.visible for f in fields if f.name == "g-recaptcha-response")
    assert probe["submit_visible"] and not probe["challenge_visible"] and probe["invalid_count"] == 0


def test_greenhouse_application(browser):
    fields, probe = _run(browser, "greenhouse_application.html", "greenhouse_form_fields.json")
    assert classify(_by_id(fields, "first_name")) == "first_name"
    assert classify(_by_id(fields, "last_name")) == "last_name"
    assert classify(_by_id(fields, "preferred_name")) == "preferred_name"
    assert _by_id(fields, "first_name").label == "First Name"   # без дубля з aria-labelledby
    assert classify(_by_id(fields, "email")) == "email"
    resume = _by_id(fields, "resume")
    assert resume.type == "file" and resume.label == "Attach" and resume.group.rstrip("*") == "Resume/CV"
    assert classify(resume) == "resume" and resume.visible
    assert resume.required   # aria-required=true на role=group, не на input; у цієї вакансії резюме обов'язкове
    assert classify(replace(resume, required=False)) == "resume"   # і необов'язкове теж вантажиться
    assert classify(_by_id(fields, "cover_letter")) == "cover_letter_file"
    country = _by_id(fields, "country")   # код країни телефону в групі «Phone»: усе одно country
    assert country.combobox and country.group == "Phone" and classify(country) == "country" and country.required
    sponsorship = _by_label(fields, "require visa sponsorship")
    assert sponsorship.combobox and sponsorship.required and not llm_allowed(sponsorship)
    auth = _by_label(fields, "authorized to work")
    assert auth.combobox and not llm_allowed(auth)
    gender = _by_label(fields, "I identify my gender as")
    assert gender.combobox and classify(gender) == "eeo"
    assert classify(_by_label(fields, "I identify as transgender")) == "eeo"
    assert classify(_by_label(fields, "sexual orientation")) == "eeo"
    mirrors = [f for f in fields if f.id == "" and f.name == "" and f.type == "text" and f.required]
    assert mirrors and all(not f.visible for f in mirrors)   # aria-hidden дзеркала react-select
    assert classify(_by_id(fields, "gdpr_demographic_data_consent_given_1")) == "ack"
    assert probe["submit_visible"] and not probe["challenge_visible"]
