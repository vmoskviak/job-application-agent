"""Адаптер LinkedIn на фейкових сторінках.

FakeModalPage веде покрокову модалку Easy Apply за `linkedin_easy_steps.json`:
кнопки `continue-unify`/`review-unify` переводять крок, `submit-unify` подає,
поля кроку живуть у форматі FIELD_JS і змінюються на fill/select_option.
FakeJobPage віддає сторінку вакансії з HTML-фікстури.
"""
import copy
import html as _html
import json
import re
from contextlib import contextmanager
from datetime import date
from pathlib import Path

import pytest

from bot import client
from bot.apply.ats_answers import AnswerContext
from bot.apply.ats_detect import PROBE_JS
from bot.apply.ats_form import field_js, field_selector
from bot.apply.linkedin import (
    APPLIED_SLOT_SELECTOR,
    EASY_APPLY_SELECTOR,
    FOLLOW_CHECKBOX_SELECTOR,
    HELPER_ERRORS_JS,
    MODAL_SELECTOR,
    OFFSITE_SELECTOR,
    UPLOAD_RESUME_SELECTOR,
    easy_apply,
    page_state,
    probe_submitted,
    resolve_offsite,
    resume_radio_selector,
)

FIX = Path(__file__).parent / "fixtures" / "platforms"
JOB_URL = "https://www.linkedin.com/jobs/view/1234567/"
JOB_BODY = "Me\nFor Business\nSenior Android Engineer\nContract Co\nEasy Apply\nSave"
CONFIRMATION = "Application sent\nYour application was sent to Contract Co\nDone"


def _button(view):
    return f'{MODAL_SELECTOR} button[data-view-name="{view}"]'


def _text_of(html: str) -> str:
    return " ".join(_html.unescape(re.sub(r"<[^>]+>", " ", html)).split())


class Loc:
    """Локатор: count, first, nth, click, inner_text, get_attribute."""

    def __init__(self, n=0, on_click=None, text="", attrs=None):
        self.n, self.on_click, self.text, self.attrs = n, on_click, text, attrs or {}

    @property
    def first(self):
        return self

    def nth(self, k):
        return self

    def count(self):
        return self.n

    def is_visible(self, timeout=None):
        return self.n > 0

    def click(self, timeout=None, force=False):
        if not self.n:
            raise TimeoutError("element not found")
        self.on_click()

    def inner_text(self, timeout=None):
        if not self.n:
            raise TimeoutError("element not found")
        return self.text

    def get_attribute(self, name, timeout=None):
        if not self.n:
            raise TimeoutError("element not found")
        return self.attrs.get(name)


class FieldEl:
    def __init__(self, page, i):
        self.page, self.d = page, page.fields()[i]

    def get_attribute(self, attr):
        return self.d.get(attr)

    def fill(self, value, timeout=None):
        self.d["value"] = value
        self.page.filled[self.d["label"]] = value

    def select_option(self, label=None, timeout=None):
        self.d["value"] = label
        if "@" in (label or ""):
            self.page.selected_email = label

    def press(self, key, timeout=None):
        assert key != "Enter", "Enter pressed during fill"

    def check(self, timeout=None, force=False):
        self.d["value"] = "on"

    def click(self, timeout=None, force=False):
        self.d["value"] = "on"


class FieldList:
    def __init__(self, page):
        self.page = page

    def nth(self, i):
        return FieldEl(self.page, i)


class Chooser:
    def __init__(self, page):
        self.page = page

    def set_files(self, files, timeout=None):
        self.page.uploaded.extend(files if isinstance(files, list) else [files])


class ChooserInfo:
    value = None


class FakeModalPage:
    def __init__(self, steps, body=JOB_BODY, url=None, invalid_after_next=None, helper_after_next=None,
                 confirm=True, easy_apply_button=True, follow_toggles=True, select_upload=True, hints=None,
                 invalid_count_after_next=None):
        self.steps = copy.deepcopy(steps)
        self.step = 0
        self.body = body
        self.redirect = url
        self.url = ""
        self.easy_apply_button = easy_apply_button
        self.modal_open = False
        self.easy_apply_clicks = 0
        self.uploaded: list[str] = []
        self.follow_checked = True
        self.submitted = False
        self.selected_email = None
        self.filled: dict[str, str] = {}
        self.invalid_after_next = invalid_after_next or {}
        self.helper_after_next = helper_after_next or {}
        self.errors: list[str] = []
        self.helpers: list[dict] = []
        self.confirm = confirm
        self.shots: list[str] = []
        self.pending_chooser = False
        self.follow_toggles = follow_toggles     # False: клік по Follow не перемикає прапорець
        self.select_upload = select_upload       # False: завантажений файл не стає вибраним radio
        self.hints = hints or []                 # тексти під полями, що є на кожному кроці
        self.invalid_count_after_next = invalid_count_after_next or {}
        self.invalid = 0

    # Стан модалки
    def fields(self):
        return self.steps[self.step]["fields"]

    def views(self):
        if not self.modal_open or (self.submitted and self.confirm):
            return []
        return self.steps[self.step]["button_views"]

    def modal_text(self):
        if self.submitted and self.confirm:
            return CONFIRMATION
        return f'Apply to Contract Co\n{self.steps[self.step]["progress"]}\n{self.steps[self.step]["title"]}'

    # Playwright API
    def goto(self, url, wait_until=None, timeout=None):
        self.url = self.redirect or url

    def wait_for_timeout(self, ms):
        pass

    def wait_for_selector(self, sel, timeout=None):
        if sel == MODAL_SELECTOR and not self.modal_open:
            raise TimeoutError(f"Timeout {timeout}ms exceeded waiting for {sel}")

    def inner_text(self, sel):
        assert sel == "body"
        return self.body + ("\n" + self.modal_text() if self.modal_open else "")

    def screenshot(self, path=None, full_page=False):
        self.shots.append(Path(path).name)

    def content(self):
        return "<html></html>"

    def evaluate(self, js):
        if js == field_js(MODAL_SELECTOR):
            return [dict(d) for d in self.fields()] if self.modal_open else []
        if js == PROBE_JS:
            return {"html": "", "submit_visible": True, "challenge_visible": False,
                    "invalid_count": self.invalid, "error_texts": list(self.errors)}
        if js == HELPER_ERRORS_JS:
            return list(self.hints) + list(self.helpers)
        raise AssertionError(f"unexpected evaluate: {js[:60]}")

    @contextmanager
    def expect_file_chooser(self, timeout=None):
        info = ChooserInfo()
        self.pending_chooser = True
        yield info
        assert not self.pending_chooser, "file chooser never opened"
        info.value = Chooser(self)

    def locator(self, sel, has=None, has_text=None):
        if sel == field_selector(MODAL_SELECTOR):
            return FieldList(self)
        if has_text is not None:
            return Loc(0)   # кнопки знаходяться за data-view-name
        if sel == EASY_APPLY_SELECTOR:
            return Loc(int(self.easy_apply_button and not self.modal_open), self._easy_apply)
        if sel == MODAL_SELECTOR:
            return Loc(int(self.modal_open), text=self.modal_text() if self.modal_open else "")
        if sel == APPLIED_SLOT_SELECTOR:
            return Loc(0)
        if sel == FOLLOW_CHECKBOX_SELECTOR:
            return Loc(int("submit-unify" in self.views() and self.follow_checked), self._unfollow)
        if sel.startswith(f"{MODAL_SELECTOR} div[role=radio]"):
            hit = any(sel == resume_radio_selector(Path(u).name) for u in self.uploaded)
            return Loc(int(hit and self.select_upload))
        if sel == UPLOAD_RESUME_SELECTOR:
            return Loc(int("upload-document-unify" in self.views()), self._upload_click)
        for view, action in (("continue-unify", self._next), ("review-unify", self._next),
                             ("submit-unify", self._submit)):
            if sel == _button(view):
                return Loc(int(view in self.views()), action)
        return Loc(0)

    def _easy_apply(self):
        self.easy_apply_clicks += 1
        self.modal_open = True

    def _unfollow(self):
        if self.follow_toggles:
            self.follow_checked = False

    def _upload_click(self):
        assert self.pending_chooser, "upload clicked outside expect_file_chooser"
        self.pending_chooser = False

    def _next(self):
        if self.step in self.invalid_after_next:
            self.errors = self.invalid_after_next[self.step]
        elif self.step in self.helper_after_next:
            self.helpers = self.helper_after_next[self.step]
        elif self.step in self.invalid_count_after_next:
            self.invalid = self.invalid_count_after_next[self.step]
        else:
            self.step += 1

    def _submit(self):
        self.submitted = True


class FakeJobPage:
    """Сторінка вакансії з HTML-фікстури: текст, якір Apply, модалка й маркери за регулярками."""

    def __init__(self, html, url=JOB_URL):
        self.html, self.url, self.shots = html, url, []

    def goto(self, url, wait_until=None, timeout=None):
        self.url = url

    def wait_for_selector(self, sel, timeout=None):
        pass   # статична фікстура: усе вже відрендерено

    def wait_for_timeout(self, ms):
        pass

    def inner_text(self, sel):
        assert sel == "body"
        return _text_of(self.html)

    def content(self):
        return self.html

    def screenshot(self, path=None, full_page=False):
        self.shots.append(Path(path).name)

    def locator(self, sel, has=None, has_text=None):
        if sel == OFFSITE_SELECTOR:
            tags = re.findall(r'<a\b[^>]*data-view-name="job-apply-button"[^>]*>', self.html)
            hrefs = [re.search(r'href="([^"]*)"', t) for t in tags]
            return Loc(len(tags), attrs={"href": _html.unescape(hrefs[0].group(1)) if tags and hrefs[0] else None})
        if sel == EASY_APPLY_SELECTOR:
            return Loc(len(re.findall(r'<button\b[^>]*data-view-name="job-apply-button"', self.html)))
        if sel == APPLIED_SLOT_SELECTOR:
            return Loc(int('id="AppliedHowYouFitSlot"' in self.html))
        if sel == MODAL_SELECTOR:
            m = re.search(r"<dialog\b[^>]*\bopen\b.*?</dialog>", self.html, re.DOTALL)
            return Loc(int(bool(m)), text=_text_of(m.group(0)) if m else "")
        if sel == _button("submit-unify"):
            return Loc(int('data-view-name="submit-unify"' in self.html))
        return Loc(0)


def fixture_html(name):
    return (FIX / name).read_text(encoding="utf-8")


@pytest.fixture
def steps():
    """4 кроки живої модалки; у крок 3 додано два обов'язкові питання про роки з questions.json."""
    data = json.loads((FIX / "linkedin_easy_steps.json").read_text(encoding="utf-8"))
    questions = json.loads((FIX / "linkedin_easy_questions.json").read_text(encoding="utf-8"))[0]["fields"]
    extra = copy.deepcopy(questions)
    for n, d in enumerate(data[2]["fields"] + extra):
        d["i"] = n
    data[2]["fields"] += extra
    return data


def _ctx(tmp_path, years):
    profile = dict(client.active().profile)
    profile["email"] = "erika@example.com"
    profile["experience_years"] = years
    return AnswerContext(
        profile=profile, lead={"company": "Contract Co", "title": "Senior Android Engineer"},
        note="Short cover note.", resume=tmp_path / "CV.pdf", today=date(2026, 10, 28), lang="en",
    )


@pytest.fixture
def ctx(tmp_path):
    return _ctx(tmp_path, [{"pattern": "kvm", "years": 6}, {"pattern": "qemu", "years": 5}])


@pytest.fixture
def ctx_without_years(tmp_path):
    return _ctx(tmp_path, [{"pattern": "android", "years": 8}])


def test_page_state_markers():
    assert page_state("https://www.linkedin.com/checkpoint/challenge/x", "") == "restricted"
    assert page_state("https://www.linkedin.com/jobs/view/1/", "We've restricted your account") == "restricted"
    assert page_state("https://www.linkedin.com/jobs/view/1/", "We’ve detected unusual activity on your account") \
        == "restricted"
    assert page_state("https://www.linkedin.com/jobs/view/1/", "Your account has been restricted") == "restricted"
    assert page_state("https://www.linkedin.com/jobs/view/1/", "Ungewöhnliche Aktivität festgestellt") == "restricted"
    # Проза опису вакансії: моніторинг «unusual activity», доступ «restricted» не рестрикція акаунта.
    prose = "Me You will detect unusual activity in logs and keep restricted your account data safe"
    assert page_state("https://www.linkedin.com/jobs/view/1/", prose) == "open"
    assert page_state("https://www.linkedin.com/login?x", "Sign in") == "login_required"
    assert page_state("https://www.linkedin.com/authwall?trk=x", "") == "login_required"
    assert page_state("https://www.linkedin.com/uas/login?x", "") == "login_required"
    assert page_state("https://www.linkedin.com/jobs/view/1/", "Join now Sign in Senior Android") == "login_required"
    assert page_state("https://www.linkedin.com/jobs/view/1/", "Me For Business Sign in to see more") == "open"
    assert page_state("https://www.linkedin.com/jobs/view/1/", "No longer accepting applications") == "closed"
    assert page_state("https://www.linkedin.com/jobs/view/1/", "Nimmt keine Bewerbungen mehr an") == "closed"
    assert page_state("https://www.linkedin.com/jobs/view/1/", "Easy Apply Senior Android") == "open"


def test_page_state_on_fixtures():
    assert page_state(JOB_URL, _text_of(fixture_html("linkedin_job_easy.html"))) == "open"
    assert page_state(JOB_URL, _text_of(fixture_html("linkedin_job_offsite.html"))) == "open"
    assert page_state(JOB_URL, _text_of(fixture_html("linkedin_applied.html"))) == "closed"


def test_resolve_offsite_reads_apply_anchor_only():
    html = fixture_html("linkedin_job_offsite.html")
    # Посилання safety/go з опису стоїть раніше за якір Apply: брати не його.
    decoy = '<a href="https://www.linkedin.com/safety/go/?url=https%3A%2F%2Fdecoy.example.com%2F&amp;urlhash=x">x</a>'
    page = FakeJobPage(html.replace("<body", decoy + "<body", 1))
    assert resolve_offsite(page, JOB_URL) == ("open", "https://jobs.example.com/contract-co/1234567")
    assert page.url == JOB_URL


def test_resolve_offsite_without_anchor_is_none():
    assert resolve_offsite(FakeJobPage(fixture_html("linkedin_job_easy.html")), JOB_URL) == ("open", None)
    assert resolve_offsite(FakeJobPage(fixture_html("linkedin_applied.html")), JOB_URL) == ("closed", None)


def test_easy_apply_happy_path_submits_and_unfollows(steps, ctx, tmp_path):
    page = FakeModalPage(steps)
    res = easy_apply(page, JOB_URL, ctx, llm=None, brief="", shots_dir=tmp_path, dry_run=False)
    assert res.status == "submitted" and res.portal == "linkedin" and res.url == JOB_URL
    assert page.uploaded == [str(ctx.resume)] and page.follow_checked is False
    assert page.submitted and "sent" in res.confirmation_text
    assert page.filled["How many years of work experience do you have with Linux KVM?"] == "6"
    assert page.filled["How many years of work experience do you have with QEMU?"] == "5"
    assert page.filled["What is your current location?"]
    assert page.easy_apply_clicks == 1
    assert page.shots == ["apply_modal_step1.png", "apply_modal_step2.png", "apply_modal_step3.png",
                          "apply_form_filled.png", "apply_confirmation.png"]


def test_prefilled_contact_fields_are_not_touched(steps, ctx, tmp_path):
    page = FakeModalPage(steps)
    easy_apply(page, JOB_URL, ctx, None, "", tmp_path, dry_run=True)
    assert "First name*" not in page.filled and "Mobile phone number" not in page.filled
    assert page.selected_email is None   # збігається з профілем: не чіпається


def test_easy_apply_dry_run_leaves_modal_open_without_submit(steps, ctx, tmp_path):
    page = FakeModalPage(steps)
    res = easy_apply(page, JOB_URL, ctx, None, "", tmp_path, dry_run=True)
    assert res.status == "dry_run" and not page.submitted and page.modal_open
    assert res.reason == "filled 3, uploaded 1, llm 0, steps 4"
    assert page.follow_checked is False
    assert page.shots[-1] == "apply_form_filled.png"


def test_required_years_without_profile_match_is_handoff(steps, ctx_without_years, tmp_path):
    page = FakeModalPage(steps)   # крок 3 має обов'язкові питання про роки з KVM і QEMU
    res = easy_apply(page, JOB_URL, ctx_without_years, None, "", tmp_path, dry_run=False)
    assert res.status == "handoff" and res.reason.startswith("open_required: How many years")
    assert "QEMU" in res.reason
    assert not page.submitted and page.modal_open and page.step == 2
    assert page.shots[-1] == "apply_modal_step3.png"


def test_email_select_mismatch_is_corrected(steps, ctx, tmp_path):
    steps[0]["fields"][4]["value"] = "other@example.com"   # select email з чужою адресою
    page = FakeModalPage(steps)
    easy_apply(page, JOB_URL, ctx, None, "", tmp_path, dry_run=True)
    assert page.selected_email == ctx.profile["email"]


def test_restricted_and_login_and_closed_short_circuit(steps, ctx, tmp_path):
    for body, url, status in (("We've restricted your account", JOB_URL, "restricted"),
                              ("Sign in", "https://www.linkedin.com/login", "login_required"),
                              ("No longer accepting applications", JOB_URL, "closed")):
        page = FakeModalPage(steps, body=body, url=url)
        res = easy_apply(page, JOB_URL, ctx, None, "", tmp_path, dry_run=False)
        assert res.status == status and res.portal == "linkedin"
        assert page.easy_apply_clicks == 0


def test_already_applied_job_is_closed(ctx, tmp_path):
    page = FakeJobPage(fixture_html("linkedin_applied.html"))
    res = easy_apply(page, JOB_URL, ctx, None, "", tmp_path, dry_run=False)
    assert res.status == "closed" and res.reason == "already applied"


def test_no_easy_apply_button_is_handoff(steps, ctx, tmp_path):
    page = FakeModalPage(steps, easy_apply_button=False)
    res = easy_apply(page, JOB_URL, ctx, None, "", tmp_path, dry_run=False)
    assert res.status == "handoff" and res.reason == "no_easy_apply"


def test_validation_after_next_is_handoff(steps, ctx, tmp_path):
    page = FakeModalPage(steps, invalid_after_next={1: ["Please enter a valid phone number"]})
    res = easy_apply(page, JOB_URL, ctx, None, "", tmp_path, dry_run=False)
    assert res.status == "handoff" and res.reason == "validation: Please enter a valid phone number"
    assert not page.submitted and page.modal_open and page.step == 1


def test_helper_text_error_names_the_field(steps, ctx, tmp_path):
    label = "How many years of work experience do you have with Linux KVM?"
    page = FakeModalPage(steps, helper_after_next={2: [{"label": label, "text": "Invalid input"}]})
    res = easy_apply(page, JOB_URL, ctx, None, "", tmp_path, dry_run=False)
    assert res.status == "handoff" and res.reason == f"validation: {label}: Invalid input"
    assert not page.submitted


def test_no_confirmation_after_submit_is_handoff(steps, ctx, tmp_path):
    page = FakeModalPage(steps, confirm=False)
    res = easy_apply(page, JOB_URL, ctx, None, "", tmp_path, dry_run=False)
    assert res.status == "handoff" and res.reason == "no_confirmation"
    assert page.submitted and page.shots[-1] == "apply_after_submit.png"


def test_too_many_steps_is_handoff(steps, ctx, tmp_path):
    page = FakeModalPage(steps)
    res = easy_apply(page, JOB_URL, ctx, None, "", tmp_path, dry_run=False, max_steps=2)
    assert res.status == "handoff" and res.reason == "too_many_steps"
    assert not page.submitted


def test_exception_before_and_after_submit(steps, ctx, tmp_path, monkeypatch):
    page = FakeModalPage(steps)
    monkeypatch.setattr(page, "expect_file_chooser", lambda **kw: (_ for _ in ()).throw(RuntimeError("boom")))
    res = easy_apply(page, JOB_URL, ctx, None, "", tmp_path, dry_run=False)
    assert res.status == "handoff" and res.reason == "error: RuntimeError: boom"

    page = FakeModalPage(steps)

    def broken_submit():
        page.submitted = True
        raise RuntimeError("detached")

    monkeypatch.setattr(page, "_submit", broken_submit)
    res = easy_apply(page, JOB_URL, ctx, None, "", tmp_path, dry_run=False)
    assert res.status == "handoff" and res.reason == "error_after_submit: RuntimeError: detached"


def test_probe_submitted():
    review = FakeJobPage(fixture_html("linkedin_easy_review.html"))
    assert probe_submitted(review) == ""        # «Your answers and resume will be saved…» не підтвердження
    sent = FakeJobPage("<html><body><dialog open><h2>Application sent</h2>"
                       "<p>Your application was sent to Contract Co</p></dialog></body></html>")
    assert "sent" in probe_submitted(sent).lower()
    # Фраза в модалці, де кнопка Submit ще є, не доказ подачі.
    pending = FakeJobPage('<html><body><dialog open><p>Application sent before?</p>'
                          '<button data-view-name="submit-unify">Submit application</button></dialog></body></html>')
    assert probe_submitted(pending) == ""
    applied = FakeJobPage(fixture_html("linkedin_applied.html"))
    assert probe_submitted(applied) == "Application submitted"
    assert probe_submitted(FakeJobPage(fixture_html("linkedin_job_easy.html"))) == ""


def test_follow_still_checked_after_click_is_handoff(steps, ctx, tmp_path):
    page = FakeModalPage(steps, follow_toggles=False)
    res = easy_apply(page, JOB_URL, ctx, None, "", tmp_path, dry_run=False)
    assert res.status == "handoff" and res.reason == "follow_still_checked"
    assert not page.submitted and page.modal_open and page.follow_checked


def test_uploaded_resume_is_the_selected_one(steps, ctx, tmp_path):
    page = FakeModalPage(steps)
    res = easy_apply(page, JOB_URL, ctx, None, "", tmp_path, dry_run=True)
    assert res.status == "dry_run" and "uploaded 1" in res.reason
    assert page.uploaded == [str(ctx.resume)]


def test_uploaded_resume_not_selected_is_handoff(steps, ctx, tmp_path):
    page = FakeModalPage(steps, select_upload=False)   # лишається вибраним збережене резюме
    res = easy_apply(page, JOB_URL, ctx, None, "", tmp_path, dry_run=False)
    assert res.status == "handoff" and res.reason == "resume_not_selected"
    assert not page.submitted and page.modal_open and page.step == 1
    assert page.shots[-1] == "apply_modal_step2.png"


def test_helper_hint_present_before_next_is_not_an_error(steps, ctx, tmp_path):
    label = "How many years of work experience do you have with Linux KVM?"
    page = FakeModalPage(steps, hints=[{"label": label, "text": "0/20"}])
    res = easy_apply(page, JOB_URL, ctx, None, "", tmp_path, dry_run=False)
    assert res.status == "submitted"


def test_invalid_count_rise_after_next_is_handoff(steps, ctx, tmp_path):
    page = FakeModalPage(steps, invalid_count_after_next={0: 2})
    res = easy_apply(page, JOB_URL, ctx, None, "", tmp_path, dry_run=False)
    assert res.status == "handoff" and res.reason == "validation: 2 invalid"
    assert page.step == 0 and not page.submitted


def test_one_step_modal_fills_fields_then_hands_off_without_resume(steps, ctx, tmp_path):
    """Перший крок має і поля, і Submit: поля заповнено, але без завантаженого резюме Submit не тиснеться."""
    one = [{"title": "Apply", "progress": "1/1", "button_views": ["submit-unify"],
            "fields": copy.deepcopy(steps[2]["fields"])}]
    page = FakeModalPage(one)
    res = easy_apply(page, JOB_URL, ctx, None, "", tmp_path, dry_run=False)
    assert res.status == "handoff" and res.reason == "resume_not_uploaded"
    assert page.filled["What is your current location?"]
    assert page.filled["How many years of work experience do you have with Linux KVM?"] == "6"
    assert not page.submitted and page.modal_open and page.uploaded == []
    assert page.shots[-1] == "apply_form_filled.png"


def test_resume_step_without_upload_button_is_handoff_before_submit(steps, ctx, tmp_path):
    steps[1]["button_views"] = ["back-unify", "continue-unify"]   # інша кнопка резюме: збережене лишається
    page = FakeModalPage(steps)
    res = easy_apply(page, JOB_URL, ctx, None, "", tmp_path, dry_run=False)
    assert res.status == "handoff" and res.reason == "resume_not_uploaded"
    assert page.step == 3 and not page.submitted and page.modal_open
    # Тіньовий прохід показує той самий hand-off, а не dry_run.
    res = easy_apply(FakeModalPage(steps), JOB_URL, ctx, None, "", tmp_path, dry_run=True)
    assert res.status == "handoff" and res.reason == "resume_not_uploaded"


class LateJobPage(FakeJobPage):
    """SPA під логіном: контрол Apply з'являється лише після wait_for_selector (або ніколи)."""

    def __init__(self, html, appears=True):
        super().__init__(re.sub(r'<a\b[^>]*data-view-name="job-apply-button".*?</a>', "", html, flags=re.DOTALL))
        self.full, self.appears, self.waits = html, appears, []

    def wait_for_selector(self, sel, timeout=None):
        self.waits.append((sel, timeout))
        if not self.appears:
            raise TimeoutError(f"Timeout {timeout}ms exceeded waiting for {sel}")
        self.html = self.full


APPLY_WAIT = ('[data-view-name="job-apply-button"], #AppliedHowYouFitSlot', 15000)


def test_resolve_offsite_waits_for_apply_control():
    page = LateJobPage(fixture_html("linkedin_job_offsite.html"))
    assert resolve_offsite(page, JOB_URL) == ("open", "https://jobs.example.com/contract-co/1234567")
    assert page.waits == [APPLY_WAIT]


def test_resolve_offsite_control_never_appears_is_none_after_bounded_wait():
    page = LateJobPage(fixture_html("linkedin_job_offsite.html"), appears=False)
    assert resolve_offsite(page, JOB_URL) == ("open", None)
    assert page.waits == [APPLY_WAIT]


def test_easy_apply_waits_for_apply_control(steps, ctx, tmp_path):
    page = FakeModalPage(steps)
    seen = []
    orig = page.wait_for_selector
    page.wait_for_selector = lambda sel, timeout=None: (seen.append((sel, timeout)), orig(sel, timeout))[1]
    res = easy_apply(page, JOB_URL, ctx, None, "", tmp_path, dry_run=False)
    assert res.status == "submitted" and seen[0] == APPLY_WAIT


def test_email_select_without_profile_address_is_handoff(steps, ctx, tmp_path):
    steps[0]["fields"][4]["value"] = "other@example.com"
    steps[0]["fields"][4]["options"] = ["", "other@example.com"]   # адреси профілю серед опцій нема
    page = FakeModalPage(steps)
    res = easy_apply(page, JOB_URL, ctx, None, "", tmp_path, dry_run=False)
    assert res.status == "handoff" and res.reason == "open_required: Email address*"
    assert not page.submitted and page.step == 0 and page.selected_email is None
