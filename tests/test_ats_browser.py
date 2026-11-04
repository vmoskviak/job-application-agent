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
    assert fields[0].ashby_autofill and not resume.ashby_autofill   # the autofill uploader is known by its markup
    assert ".pdf" in resume.accept and not resume.multiple
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


def test_shadow_dom_fields_are_read_and_filled(browser, tmp_path):
    """Web-component forms (SmartRecruiters, SAP UI5): fields inside open shadow roots are read with
    their labels, marked, and filled through the mark."""
    from datetime import date

    from bot import client
    from bot.apply.ats_answers import AnswerContext
    from bot.apply.ats_form import _form_ready, fill_form

    page = browser.new_page(viewport={"width": 1280, "height": 900})
    page.goto((FIXTURES / "shadow_application.html").as_uri())
    fields = fields_from(page.evaluate(FIELD_JS))
    by_label = {f.label: f for f in fields if f.label}
    assert all(f.marked for f in fields)
    assert classify(by_label["First name"]) == "first_name" and by_label["First name"].required
    assert classify(by_label["Last name"]) == "last_name"
    assert classify(by_label["Email"]) == "email" and by_label["Email"].required
    assert classify(by_label["Phone number"]) == "phone"
    assert by_label["Resume"].type == "file" and classify(by_label["Resume"]) == "resume"
    assert _form_ready(page)

    resume = tmp_path / "CV.pdf"
    resume.write_bytes(b"%PDF-1.4\n%%EOF\n")
    profile = dict(client.active().profile)
    ctx = AnswerContext(profile=profile, lead={"company": "Contract Co", "title": "Senior Android Engineer"},
                        note="", resume=resume, today=date(2026, 10, 29), lang="en")
    rep = fill_form(page, ctx, None, "")
    values = page.evaluate("""() => [...document.querySelectorAll('form-field, labelled-input, aria-input')]
        .map(h => h.shadowRoot.querySelector('input').value)""")
    files = page.evaluate("() => document.querySelector('upload-box').shadowRoot.querySelector('input').files.length")
    page.close()
    assert all(values), (values, rep)
    assert files == 1 and rep.uploaded, (files, rep)


def test_avatar_never_gets_the_cv(browser, tmp_path):
    """SmartRecruiters 30.10.2026: the CV went into the avatar input. Synthetic page: an unlabelled
    image-only avatar and a «Resume» dropzone whose input the visibility check cannot see. Neither
    with its accept list nor without one does the avatar get the CV; the form is a hand-off."""
    from datetime import date

    from bot import client
    from bot.apply.ats_answers import AnswerContext
    from bot.apply.ats_form import fill_form

    resume = tmp_path / "CV.pdf"
    resume.write_bytes(b"%PDF-1.4\n%%EOF\n")
    ctx = AnswerContext(profile=dict(client.active().profile), lead={"company": "Contract Co", "title": "Engineer"},
                        note="", resume=resume, today=date(2026, 10, 30), lang="en")
    avatar_files = "() => document.querySelector('avatar-upload').shadowRoot.querySelector('input').files.length"
    for strip_accept in (False, True):
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.goto((FIXTURES / "avatar_application.html").as_uri())
        if strip_accept:
            page.evaluate("() => document.querySelector('avatar-upload').shadowRoot.querySelector('input')"
                          ".removeAttribute('accept')")
        fields = [f for f in fields_from(page.evaluate(FIELD_JS)) if f.type == "file"]
        avatar, dropzone = fields
        assert avatar.visible and not avatar.label and (classify(avatar) == "photo") != strip_accept
        assert dropzone.label == "Resume" and not dropzone.visible
        rep = fill_form(page, ctx, None, "")
        files = page.evaluate(avatar_files)
        page.close()
        assert files == 0 and not rep.cv_uploaded and not rep.uploaded, (strip_accept, rep)


def test_linkedin_apply_controls_both_variants(browser):
    """LinkedIn job page: the classic controls carry data-view-name, the 29.10.2026 variant only an
    aria-label. Both must be found, and the Easy Apply selector must never match the offsite link."""
    from bot.apply.linkedin import EASY_APPLY_SELECTOR, OFFSITE_SELECTOR

    classic = ('<button data-view-name="job-apply-button">Easy Apply</button>'
               '<a data-view-name="job-apply-button" href="https://www.linkedin.com/safety/go/?url=x">Apply</a>')
    variant = ('<button aria-label="Easy Apply to this job" class="x1 x2">Easy Apply</button>'
               '<a aria-label="Apply on company website" target="_blank" '
               'href="https://www.linkedin.com/safety/go/?url=x">Apply</a>'
               '<a href="/jobs/view/1/">Senior Mobile Engineer</a>')
    page = browser.new_page()
    for html in (classic, variant):
        page.set_content(f"<html><body>{html}</body></html>")
        assert page.locator(EASY_APPLY_SELECTOR).count() == 1
        assert page.locator(OFFSITE_SELECTOR).count() == 1
        assert page.locator(EASY_APPLY_SELECTOR).first.evaluate("e => e.tagName") == "BUTTON"
    page.close()


def test_linkedin_modal_variant_upload_and_selected_resume(browser):
    """The 29.10.2026 modal variant: «Upload resume» without data-view-name and the resume list as
    input[type=radio] with the file name in aria-label; STEP_MARKER_JS reads the progress."""
    from bot.apply.linkedin import (
        STEP_MARKER_JS,
        UPLOAD_RESUME_SELECTOR,
        resume_radio_selector,
    )

    html = ('<dialog open><h2>Apply to Contract Co</h2><span>2/4 pages</span>'
            '<div role="button">PDF Old_CV.pdf 10/15/2018</div>'
            '<input type="radio" name="g" aria-label="Old_CV.pdf">'
            '<input type="radio" name="g" id="r2" aria-label="CV_Erika.pdf" checked>'
            '<button>Upload resume</button><button>Upload cover letter</button><button>Next</button></dialog>')
    page = browser.new_page()
    page.set_content(f"<html><body>{html}</body></html>")
    assert page.locator(UPLOAD_RESUME_SELECTOR).count() == 1
    assert page.locator(resume_radio_selector("CV_Erika.pdf")).count() == 1
    assert page.locator(resume_radio_selector("Old_CV.pdf")).count() == 0
    assert page.evaluate(STEP_MARKER_JS).startswith("2/4 pages|")
    page.close()


def test_styled_consent_radio_is_visible_and_gets_checked(browser, tmp_path):
    """A radio hidden by styling with a visible label («◯ Ja») counts as visible and is checked
    through its label; the JOIN consent question is acknowledged."""
    from datetime import date

    from bot import client
    from bot.apply.ats_answers import AnswerContext
    from bot.apply.ats_form import fill_form

    html = ('<dialog open><span id="q">Stimmst du den AGBs von JOIN zu und hast die Datenschutzbestimmungen '
            'zur Kenntnis genommen? *</span><div><input type="radio" id="ja" name="agb" required '
            'aria-labelledby="q" style="opacity:0;position:absolute;width:0;height:0">'
            '<label for="ja">Ja</label></div></dialog>')
    page = browser.new_page()
    page.set_content(f"<html><body>{html}</body></html>")
    fields = fields_from(page.evaluate(FIELD_JS))
    assert fields[0].visible and classify(fields[0]) == "ack"
    ctx = AnswerContext(profile=dict(client.active().profile), lead={"company": "Contract Co", "title": "x"},
                        note="", resume=tmp_path / "CV.pdf", today=date(2026, 10, 29), lang="de")
    fill_form(page, ctx, None, "")
    assert page.evaluate("() => document.getElementById('ja').checked")
    page.close()


def test_consent_radio_with_an_empty_label_takes_the_nearby_option_text(browser):
    """LinkedIn's radio: an empty <label> (the drawn circle) and the option text «Ja» in a sibling."""
    html = ('<dialog open><span id="q">Stimmst du den AGBs von JOIN zu und hast die Datenschutzbestimmungen '
            'zur Kenntnis genommen? *</span><div class="opt"><input type="radio" id="ja" name="agb" required '
            'aria-labelledby="q" style="opacity:0;position:absolute;width:0;height:0">'
            '<label for="ja" style="display:inline-block;width:16px;height:16px"></label><span>Ja</span></div>'
            '</dialog>')
    page = browser.new_page()
    page.set_content(f"<html><body>{html}</body></html>")
    f = fields_from(page.evaluate(FIELD_JS))[0]
    page.close()
    assert f.label.startswith("Ja ") and f.visible and classify(f) == "ack"


def test_label_reaches_an_input_whose_wrapper_reuses_its_id(browser):
    """Abacus job portal (FormKit, 02.11.2026): <input-field> carries the input's id, so label[for]
    binds to the wrapper and the input's own labels list is empty. Without the label the filler saw
    only «LAMAPPL_xxx_FirstName», left name and address open and put the start date into the birth date."""
    def field(name, label, type_="text"):
        return (f'<input-field name="{name}" label="{label}" id="u-{name}"><div class="formkit-outer">'
                f'<label class="formkit-label" for="u-{name}">{label}<span class="formkit-asterisk">*</span></label>'
                f'<input type="{type_}" name="{name}" id="u-{name}" required></div></input-field>')
    html = field("LAMAPPL_xxx_FirstName", "Vorname") + field("LAMAPPL_xxx_DateOfBirth", "Geburtsdatum", "date")
    page = browser.new_page()
    page.set_content(f"<html><body>{html}</body></html>")
    first, birth = fields_from(page.evaluate(FIELD_JS))
    page.close()
    assert first.label.startswith("Vorname") and classify(first) == "first_name"
    assert birth.label.startswith("Geburtsdatum") and classify(birth) == "birth_date"


_FRC = ('<div class="frc-captcha"><button type="button" class="frc-button" onclick="{click}">Hier klicken</button>'
        '<input type="hidden" class="frc-captcha-solution" name="frc-captcha-solution" value=".UNSTARTED"></div>')


def test_friendly_captcha_wait_starts_the_widget_and_waits_for_the_token(browser):
    """softgarden 02.11.2026: the worker clicked «Bewerbung absenden» while FriendlyCaptcha was still
    verifying; softgarden dropped the submit and redrew the form without name, email and cover letter."""
    from bot.apply.forms import wait_friendly_captcha

    solve = ("const s=document.querySelector(\'[name=frc-captcha-solution]\'); s.value=\'.FETCHING\';"
             " setTimeout(() => { s.value=\'AAAA.token\'; }, 400);")
    page = browser.new_page()
    page.set_content(f"<html><body><form>{_FRC.format(click=solve)}</form></body></html>")
    assert wait_friendly_captcha(page, timeout_ms=5000, poll_ms=100) == "solved"
    page.set_content("<html><body><form><input name=x></form></body></html>")
    assert wait_friendly_captcha(page, timeout_ms=500, poll_ms=100) == "none"
    page.set_content(f"<html><body><form>{_FRC.format(click='')}</form></body></html>")
    assert wait_friendly_captcha(page, timeout_ms=500, poll_ms=100) == "pending"
    page.close()


def test_submit_is_the_filled_forms_button_not_the_opener(browser, tmp_path):
    """SUBMIT_JS in Chromium: a sticky «Jetzt bewerben» above, a newsletter form with its own «Senden»
    and the application form whose type=submit button is «Bewerbung absenden»; then the same form with
    its button in a shadow root and a disabled twin."""
    from bot.apply.ats_form import SUBMIT_JS, find_submit, pick_submit

    cv = tmp_path / "CV.pdf"
    cv.write_bytes(b"%PDF-1.4\n%%EOF\n")
    html = ('<header><button type="button">Jetzt bewerben</button></header>'
            '<form id="news"><input type="email" id="nl"><button type="submit">Senden</button></form>'
            '<form id="app"><input id="fn"><input id="em" type="email"><input type="file" id="cv">'
            '<button type="button">Mit XING bewerben</button><button type="submit">Bewerbung absenden</button></form>')
    page = browser.new_page()
    page.set_content(f"<html><body>{html}</body></html>")
    page.fill("#nl", "erika@example.com")
    page.fill("#fn", "Erika")
    page.fill("#em", "erika@example.com")
    page.set_input_files("#cv", str(cv))
    cands = page.evaluate(SUBMIT_JS)
    by_text = {c["text"]: c for c in cands}
    assert by_text["Senden"]["other_form"] and not by_text["Senden"]["in_form"]
    assert by_text["Bewerbung absenden"]["in_form"] and by_text["Bewerbung absenden"]["submit_type"]
    assert not by_text["Jetzt bewerben"]["in_form"] and not by_text["Jetzt bewerben"]["other_form"]
    assert pick_submit(cands)["text"] == "Bewerbung absenden"
    assert find_submit(page).inner_text() == "Bewerbung absenden"

    page.set_content('<html><body><button type="button">Jetzt bewerben</button>'
                     '<button disabled>Absenden</button><send-box></send-box><script>'
                     'customElements.define("send-box", class extends HTMLElement { connectedCallback() {'
                     ' this.attachShadow({mode: "open"}).innerHTML = "<button>Absenden</button>"; } });'
                     '</script></body></html>')
    cands = page.evaluate(SUBMIT_JS)
    assert [(c["text"], c["disabled"]) for c in cands] == [("Jetzt bewerben", False), ("Absenden", True),
                                                          ("Absenden", False)]
    btn = find_submit(page)
    assert btn.inner_text() == "Absenden" and btn.is_enabled()
    page.close()


def test_german_documents_upload_takes_the_whole_package(browser, tmp_path):
    """A form whose only upload is «Bewerbungsunterlagen» (multiple): CV, Anschreiben and the Zeugnisse
    go in together, and the CV counts as uploaded."""
    from datetime import date

    from bot import client
    from bot.apply.ats_answers import AnswerContext
    from bot.apply.ats_form import fill_form

    names = ("Erika_Muster_CV.pdf", "01_Anschreiben.pdf", "Zeugnis_A.pdf")
    for n in names:
        (tmp_path / n).write_bytes(b"%PDF-1.4\n%%EOF\n")
    html = ('<form><label for="vn">Vorname</label><input id="vn" required>'
            '<label for="nn">Nachname</label><input id="nn" required>'
            '<label for="em">E-Mail</label><input id="em" type="email" required>'
            '<label for="docs">Bewerbungsunterlagen</label><input id="docs" type="file" multiple accept=".pdf">'
            '<label for="pic">Bewerbungsfoto</label><input id="pic" type="file" accept="image/*">'
            '<button type="submit">Bewerbung absenden</button></form>')
    page = browser.new_page()
    page.set_content(f"<html><body>{html}</body></html>")
    docs = next(f for f in fields_from(page.evaluate(FIELD_JS)) if f.id == "docs")
    assert docs.multiple and classify(docs) == "documents"
    ctx = AnswerContext(profile=dict(client.active().profile), lead={"company": "Contract Co", "title": "x"},
                        note="", resume=tmp_path / names[0], today=date(2026, 10, 29), lang="de",
                        attachments=[tmp_path / n for n in names], market="de_employment",
                        cover_letter=tmp_path / names[1])
    rep = fill_form(page, ctx, None, "")
    uploaded = page.evaluate("() => Array.from(document.getElementById('docs').files).map(f => f.name)")
    photo = page.evaluate("() => document.getElementById('pic').files.length")
    page.close()
    assert rep.cv_uploaded and uploaded == list(names) and photo == 0


def test_wizard_button_belongs_to_the_visible_step_not_a_hidden_finished_one(browser):
    """Abacus 02.11.2026: the finished step 1 stays as a hidden form with 11 values; its count made the
    visible step 2 «another form», and its «Weiter» was never taken."""
    from bot.apply.ats_form import SUBMIT_JS, pick_next

    step1 = "".join(f'<input name="a{k}" value="x{k}">' for k in range(11))
    html = (f'<form id="s1" style="display:none">{step1}<button type="submit">Weiter</button></form>'
            '<form id="s2"><input name="b1" value="Düsseldorf"><input name="b2" value="46000">'
            '<button type="button">Zurück</button><button type="submit">Weiter</button></form>')
    page = browser.new_page()
    page.set_content(f"<html><body>{html}</body></html>")
    cands = page.evaluate(SUBMIT_JS)
    page.close()
    hit = pick_next(cands)
    assert hit is not None and hit["text"] == "Weiter" and hit["in_form"] and not hit["other_form"]
