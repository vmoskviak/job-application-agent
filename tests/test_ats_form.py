"""Драйвер ATS-форми на фейковій сторінці.

FakePage тримає список полів у форматі FIELD_JS і змінює їх `value` на
fill/check/click, тож повторне читання після проходу бачить результат.
Пошуки кнопок, опцій комбобокса й посилання Apply змодельовано мінімально.
"""
import re
from dataclasses import replace
from datetime import date

import pytest

from bot import client
from bot.apply import ats_form
from bot.apply.ats_answers import AnswerContext
from bot.apply.ats_detect import PROBE_JS
from bot.apply.ats_fields import FIELD_JS, FIELD_SELECTOR
from bot.apply.ats_form import (
    POLL_MS,
    SUBMIT_WAIT_MS,
    FillReport,
    apply,
    field_js,
    field_selector,
    fill_form,
    form_url,
    pick_apply,
    probe,
    submit,
)

GH_APPLY = 'a:has-text("Apply"), button:has-text("Apply")'
ANY_APPLY = ats_form.APPLY_SELECTOR
FORM_READY = "input[type=file], form, input, textarea, select"
PRE_SUBMIT = {"submit_visible": True, "challenge_visible": False, "invalid_count": 0, "error_texts": [], "text": "Apply for this job"}


def _f(i, label, type="text", tag="input", required=False, visible=True, value="", options=None,
       group="", combobox=False, id="", name="", **extra):
    return {"i": i, "tag": tag, "type": type, "name": name, "id": id, "required": required, "label": label,
            "placeholder": "", "visible": visible, "value": value, "options": options, "group": group,
            "combobox": combobox, **extra}


def _key(d):
    return d["label"] or d["group"]


class FakeEl:
    def __init__(self, page, i):
        self.page, self.i = page, i
        self.d = page.fields[i]

    def get_attribute(self, attr):
        return self.page.attr_override.get((self.d["id"], attr), self.d.get(attr))

    def fill(self, value, timeout=None):
        self.d["value"] = value
        self.page.filled[_key(self.d)] = value
        self.page.after("fill", self.d)

    def type(self, value, delay=None, timeout=None):
        self.d["value"] = value
        self.page.typed[_key(self.d)] = value

    def press(self, key, timeout=None):
        # Enter у <form> — неявна відправка: драйвер не має тиснути його ніде.
        assert key != "Enter", "Enter pressed during fill"
        self.page.pressed.append((_key(self.d), key))

    def set_input_files(self, files, timeout=None):
        files = files if isinstance(files, list) else [files]
        self.d["value"] = str(files[0])
        self.page.files[_key(self.d)] = [str(x) for x in files]

    def check(self, timeout=None, force=False):
        self.d["value"] = "on"
        self.page.checked.append(_key(self.d))

    def click(self, timeout=None, force=False):
        if self.d["type"] not in ("radio", "checkbox"):
            self.page.el_clicks.append(_key(self.d))   # комбобокс відкривається кліком
            return
        for other in self.page.fields:
            if other["type"] == "radio" and other["group"] == self.d["group"]:
                other["value"] = ""
        self.d["value"] = "on"
        self.page.clicked.append(f'{self.d["group"]}={self.d["label"]}')
        self.page.after("click", self.d)

    def select_option(self, label=None, timeout=None):
        self.d["value"] = label
        self.page.selected[_key(self.d)] = label


class FieldList:
    def __init__(self, page):
        self.page = page

    def nth(self, i):
        return FakeEl(self.page, i)


class Clickable:
    """Кнопка/опція/посилання: список текстів, фільтр, nth, first, click."""

    def __init__(self, page, items, on_click):
        self.page, self.items, self.on_click = page, items, on_click

    @property
    def first(self):
        return Clickable(self.page, self.items[:1], self.on_click)

    def nth(self, k):
        return Clickable(self.page, self.items[k:k + 1], self.on_click)

    def filter(self, has_text=None):
        rx = has_text if isinstance(has_text, re.Pattern) else re.compile(re.escape(has_text), re.IGNORECASE)
        return Clickable(self.page, [t for t in self.items if rx.search(t)], self.on_click)

    def count(self):
        return len(self.items)

    def is_visible(self, timeout=None):
        return bool(self.items)

    def click(self, timeout=None, force=False):
        if not self.items:
            raise TimeoutError("element not found")
        self.on_click(self.items[0])


class YesNoBox:
    """Контейнер Ashby [data-field-path]: label[for=uuid] з питанням, кнопки Yes/No
    (aria-pressed, data-option) і прихований checkbox name=uuid."""

    def __init__(self, page, uuid):
        self.page, self.uuid = page, uuid
        self.q = page.yesno[uuid]

    @property
    def first(self):
        return self

    def count(self):
        return 1

    def locator(self, sel, has_text=None):
        m = re.fullmatch(r"button\[data-option=(yes|no)\]", sel)
        if m:
            label = m.group(1).capitalize()
            return Clickable(self.page, [label] if label in self.q["buttons"] else [], self._click)
        assert sel == "button"
        rx = has_text if isinstance(has_text, re.Pattern) else re.compile(re.escape(has_text), re.IGNORECASE)
        return Clickable(self.page, [x for x in self.q["buttons"] if rx.search(x)], self._click)

    def _click(self, label):
        self.q["pressed"] = label
        self.page.group_clicks.append((self.q["question"], label))
        for d in self.page.fields:   # FIELD_JS: кнопка aria-pressed=true у контейнері → value "on"
            if d["type"] == "checkbox" and d["name"] == self.uuid:
                d["value"] = "on"


class EmptyBox:
    """get_by_text(...).first.locator("xpath=..") без кнопок: клік падає, як у Playwright."""

    first = property(lambda self: self)

    def count(self):
        return 0

    def locator(self, sel, has_text=None):
        return Clickable(None, [], lambda x: None) if sel != "xpath=.." else self


class FakePage:
    def __init__(self, fields, body="Apply for this job", buttons=("Submit Application",)):
        self.fields = fields
        self.body = body
        self.buttons = list(buttons)
        self.url = ""
        self.visited: list[str] = []
        self.filled, self.typed, self.files, self.selected = {}, {}, {}, {}
        self.checked, self.clicked, self.pressed, self.group_clicks, self.el_clicks = [], [], [], [], []
        self.combo_options: list[str] = []
        self.option_clicks: list[str] = []
        self.yesno: dict[str, dict] = {}   # uuid → {"question", "buttons", "pressed"}
        self.fields_after_apply = None
        self.apply_clicks = 0
        self.apply_selector = GH_APPLY
        self.apply_buttons = ["Apply"]   # visible buttons the opener sees while fields_after_apply is pending
        self.apply_clicked: list[str] = []
        self.clicked_submit = False
        self.submit_clicks: list[int] = []   # indexes into buttons, in click order
        self.probe_after_submit = dict(PRE_SUBMIT)
        self.shots: list[str] = []
        self.hooks: dict = {}            # (дія, id поля) → функція(page)
        self.attr_override: dict = {}    # (id поля, атрибут) → що віддає get_attribute
        self.probe_errors = 0            # скільки перших знімків PROBE_JS кидають виняток
        self.waits: list[str] = []       # селектори wait_for_selector у порядку викликів
        self.reads = 0                   # скільки разів читали FIELD_JS
        self.reads_before_wait = 0
        self.form_never_appears = False  # wait_for_selector завжди TimeoutError
        self.dead = False                # inner_text кидає, як закрита сторінка
        self.waited_ms = 0

    def add_yesno(self, i, question, uuid, required=True):
        """Питання Yes/No Ashby: прихований checkbox name=uuid, label[for=uuid], дві кнопки."""
        self.yesno[uuid] = {"question": question, "buttons": ["Yes", "No"], "pressed": None}
        self.fields.append(_f(i, question, type="checkbox", required=required, group=question, name=uuid))

    def after(self, action, d):
        hook = self.hooks.pop((action, d["id"]), None)
        if hook:
            hook(self)

    def insert(self, pos, d):
        """Нове поле в DOM: індекси решти зсуваються, як у FIELD_JS."""
        self.fields.insert(pos, d)
        for n, x in enumerate(self.fields):
            x["i"] = n

    def goto(self, url, wait_until=None, timeout=None):
        self.url = url
        self.visited.append(url)
        self.clicked_submit = False

    def wait_for_selector(self, sel, timeout=None):
        self.waits.append(sel)
        if self.form_never_appears:
            raise TimeoutError(f"Timeout {timeout}ms exceeded waiting for {sel}")

    def evaluate(self, js):
        if js == FIELD_JS:
            self.reads += 1
            if self.form_never_appears:
                return []
            if not self.waits:
                self.reads_before_wait += 1
            return [dict(d) for d in self.fields]
        if js == ats_form.CANDIDATES_JS:
            if self.fields_after_apply is None or self.apply_clicks:
                return []
            return [{"i": k, "text": t, "href": "", "target": "", "tag": "button"}
                    for k, t in enumerate(self.apply_buttons)]
        if js == ats_form.SUBMIT_JS:
            return [self._submit_cand(k, b) for k, b in enumerate(self.buttons)]
        if js == PROBE_JS:
            if self.probe_errors:
                self.probe_errors -= 1
                raise RuntimeError("Execution context was destroyed, most likely because of a navigation")
            p = self.probe_after_submit if self.clicked_submit else PRE_SUBMIT
            return {"html": "<html></html>", **{k: v for k, v in p.items() if k != "text"}}
        raise AssertionError("unexpected evaluate")

    def inner_text(self, sel):
        if self.dead:
            raise RuntimeError("Target page, context or browser has been closed")
        return self.probe_after_submit.get("text", "") if self.clicked_submit else self.body

    def locator(self, sel, has=None, has_text=None):
        if sel == FIELD_SELECTOR:
            return FieldList(self)
        if sel == "[role=option]":
            return Clickable(self, self.combo_options, self.option_clicks.append)
        if sel == "label":
            rx = has_text if isinstance(has_text, re.Pattern) else re.compile(re.escape(has_text), re.IGNORECASE)
            return Clickable(self, [q["question"] for q in self.yesno.values() if rx.search(q["question"])], lambda x: None)
        if sel == "[data-field-path]":
            wanted = set(has.items) if has is not None else set()
            hits = [u for u, q in self.yesno.items() if q["question"] in wanted]
            return YesNoBox(self, hits[0]) if hits else Clickable(self, [], lambda x: None)
        m = re.fullmatch(r'\[data-js-submit="(\d+)"\]', sel)
        if m:
            k = int(m.group(1))
            return Clickable(self, [self._submit_cand(k, self.buttons[k])["text"]],
                             lambda text, k=k: (self.submit_clicks.append(k), self._button_click(text)))
        m = re.fullmatch(r'\[data-js-cand="(\d+)"\]', sel)
        if m and self.fields_after_apply is not None:
            return Clickable(self, [self.apply_buttons[int(m.group(1))]], self._apply_click)
        return Clickable(self, [], lambda x: None)   # cookie-банерів немає

    def get_by_role(self, role, name=None):
        assert role == "button"
        return Clickable(self, [b for b in self.buttons if name.search(b)], self._button_click)

    def get_by_text(self, text, exact=False):
        return EmptyBox()

    @staticmethod
    def _submit_cand(k, b):
        """A button as SUBMIT_JS returns it; a plain string is a button outside any form."""
        d = {"text": b} if isinstance(b, str) else dict(b)
        return {"i": k, "text": d["text"], "submit_type": d.get("submit_type", False),
                "in_form": d.get("in_form", False), "other_form": d.get("other_form", False),
                "disabled": d.get("disabled", False)}

    def _button_click(self, text):
        self.clicked_submit = True

    def _apply_click(self, text):
        self.apply_clicks += 1
        self.apply_clicked.append(text)
        self.fields = self.fields_after_apply

    def wait_for_timeout(self, ms):
        self.waited_ms += ms

    def screenshot(self, path=None, full_page=False):
        self.shots.append(path)

    def content(self):
        return "<html></html>"


def base_fields():
    return [
        _f(0, "", type="file"),                         # безіменний автозаповнювач Ashby
        _f(1, "Legal Name", required=True),
        _f(2, "Email", type="email", required=True),
        _f(3, "Resume", type="file", required=True),
        _f(4, "Additional Information", type="textarea", tag="textarea"),
        _f(5, "I confirm I have read the above.", type="checkbox", required=True),
        _f(6, "", type="hidden", visible=False),
    ]


@pytest.fixture
def ctx(tmp_path):
    profile = dict(client.active().profile)
    profile["email"] = "erika@example.com"
    return AnswerContext(
        profile=profile, lead={"company": "Contract Co", "title": "Senior Android Engineer"},
        note="Short cover note.", resume=tmp_path / "CV.pdf", today=date(2026, 10, 27), lang="en",
    )


@pytest.fixture
def fake_page():
    return FakePage(base_fields())


@pytest.fixture
def fake_page_prefilled():
    fs = base_fields()
    fs[1]["value"] = "Erika M."
    fs[2]["value"] = "old@example.com"
    return FakePage(fs)


@pytest.fixture
def fake_page_with_choice():
    fs = base_fields() + [
        _f(7, "Yes", type="radio", required=True, group="Have you used our product?"),
        _f(8, "No", type="radio", required=True, group="Have you used our product?"),
    ]
    return FakePage(fs)


@pytest.fixture
def fake_page_with_why():
    fs = base_fields() + [
        _f(7, "Why do you want to join Contract Co? Tell us in a few sentences.", type="textarea", tag="textarea",
           required=True),
    ]
    return FakePage(fs)


class FakeLLM:
    def __init__(self, answer):
        self.answer, self.prompts = answer, []

    def complete(self, prompt):
        self.prompts.append(prompt)
        return self.answer


def test_fill_uses_profile_and_note(fake_page, ctx):
    rep = fill_form(fake_page, ctx, llm=None, brief="")
    assert isinstance(rep, FillReport)
    assert fake_page.filled["Legal Name"] == "Erika Mustermann" and fake_page.files["Resume"] == [str(ctx.resume)]
    assert fake_page.filled["Additional Information"] == ctx.note and rep.open_required == []
    assert fake_page.checked == ["I confirm I have read the above."]
    assert "" not in fake_page.files   # автозаповнювач не чіпаємо
    assert rep.uploaded == ["Resume"] and "Legal Name" in rep.filled


def test_prefilled_email_mismatch_is_refilled(fake_page_prefilled, ctx):   # email field value "old@example.com"
    fill_form(fake_page_prefilled, ctx, llm=None, brief="")
    assert fake_page_prefilled.filled["Email"] == "erika@example.com"
    assert "Legal Name" not in fake_page_prefilled.filled   # prefilled name left alone


def test_required_choice_without_answer_is_handoff(fake_page_with_choice, ctx, tmp_path):
    res = apply(fake_page_with_choice, "https://jobs.ashbyhq.com/contractco/1/application", ctx, None, "", tmp_path, dry_run=False)
    assert res.status == "handoff" and "Have you used our product?" in res.reason
    assert res.portal == "ashby" and res.reason.count("Have you used our product?") == 1
    assert not fake_page_with_choice.clicked_submit


def test_required_choice_with_answer_clicks_radio(fake_page_with_choice, ctx):
    ctx.profile["form_answers"] = [{"pattern": "used our product", "choice": "no"}]
    rep = fill_form(fake_page_with_choice, ctx, llm=None, brief="")
    assert fake_page_with_choice.clicked == ["Have you used our product?=No"] and rep.open_required == []


def test_ashby_yes_no_buttons(ctx):
    """Фінальне рев'ю, I5: питання Yes/No Ashby (checkbox name=uuid, label[for=uuid], кнопки
    data-option у тому ж [data-field-path]): клік по потрібній кнопці, checkbox читається як відповідь."""
    page = FakePage(base_fields())
    q1, q2 = "Are you based in Germany?", "Will you now or in the future require sponsorship for a visa?"
    page.add_yesno(7, q1, "1d2a9c36-0000-4000-8000-000000000001")
    page.add_yesno(8, q2, "1d2a9c36-0000-4000-8000-000000000002")
    ctx.profile["form_answers"] = [{"pattern": "based in germany", "choice": "yes"},
                                   {"pattern": "sponsorship", "choice": "no"}]
    rep = fill_form(page, ctx, llm=None, brief="")
    assert page.group_clicks == [(q1, "Yes"), (q2, "No")] and rep.open_required == []
    assert page.fields[7]["value"] == "on" and page.fields[8]["value"] == "on"


def test_ashby_yes_no_without_answer_is_open_required(ctx):
    """C2 (c): обов'язкове Yes/No про візу без form_answers не йде до моделі й блокує Submit."""
    page = FakePage(base_fields())
    q = "Will you now or in the future require sponsorship for employment visa status?"
    page.add_yesno(7, q, "1d2a9c36-0000-4000-8000-000000000003")
    ctx.profile["form_answers"] = []
    llm = FakeLLM("Yes")
    rep = fill_form(page, ctx, llm=llm, brief="")
    assert page.group_clicks == [] and rep.open_required == [q] and llm.prompts == []


def test_llm_only_when_allowed(fake_page_with_why, ctx):
    llm = FakeLLM("Because I like Android.")
    rep = fill_form(fake_page_with_why, ctx, llm=llm, brief="brief")
    assert rep.llm_calls == 1 and fake_page_with_why.filled["Why do you want to join Contract Co? Tell us in a few sentences."] == "Because I like Android."
    assert fake_page_with_why.filled["Additional Information"] == ctx.note   # нотатка йде лише в одне поле


def test_generic_message_box_gets_the_note_without_a_model_call(ctx):
    """30.10.2026, a German landing-page form: Name, E-Mail, Telefon, «Nachricht», a file upload. The
    message box takes the cover note; the model is not asked."""
    page = FakePage([
        _f(0, "Name", required=True, id="name"),
        _f(1, "E-Mail", type="email", required=True, id="mail"),
        _f(2, "Telefon", type="tel", id="tel"),
        _f(3, "Nachricht", type="textarea", tag="textarea", id="msg"),
        _f(4, "Datei", type="file", id="file"),
    ])
    llm = FakeLLM("Ich habe Erfahrung.")
    rep = fill_form(page, ctx, llm=llm, brief="brief")
    assert page.filled["Nachricht"] == ctx.note and llm.prompts == [] and rep.llm_calls == 0


def test_meta_reply_from_the_model_is_not_typed(ctx):
    """A model reply that asks back or echoes the rules leaves the field open: a hand-off, not a typed meta text."""
    q = "Tell us about a project you are proud of."
    page = FakePage(base_fields() + [_f(7, q, type="textarea", tag="textarea", required=True)])
    reply = ("Поле не містить конкретного питання, уточніть, що саме написати. Формат відповіді: "
             "німецькою, до 600 знаків, plain text.")
    llm = FakeLLM(reply)
    rep = fill_form(page, ctx, llm=llm, brief="brief")
    assert rep.llm_calls == 1 and q not in page.filled and rep.open_required == [q]


def test_llm_budget_and_no_llm_for_identity(ctx):
    fs = base_fields() + [
        _f(7 + k, f"Question number {k} about your experience with large teams?", required=True) for k in range(4)
    ]
    page = FakePage(fs)
    llm = FakeLLM("An answer.")
    rep = fill_form(page, ctx, llm=llm, brief="", max_llm=3)
    assert rep.llm_calls == 3 and len(llm.prompts) == 3
    assert rep.open_required == ["Question number 3 about your experience with large teams?"]


def test_combobox_types_and_picks_option(ctx):
    page = FakePage(base_fields() + [_f(7, "Location", required=True, combobox=True)])
    page.combo_options = ["Düsseldorf, North Rhine-Westphalia, Germany"]
    fill_form(page, ctx, llm=None, brief="")
    assert page.typed["Location"].startswith("Düsseldorf") and page.option_clicks == page.combo_options


def test_combobox_prefers_exact_option(ctx):
    """Відповідь «No» у react-select: точна опція, не перша з підрядком («Not sure»)."""
    page = FakePage(base_fields() + [_f(7, "Will you now or in the future require visa sponsorship?", required=True, combobox=True)])
    page.combo_options = ["Not sure", "No", "Yes"]
    ctx.profile["form_answers"] = [{"pattern": "sponsorship", "answer": "No"}]
    rep = fill_form(page, ctx, llm=FakeLLM("prose"), brief="")
    assert page.option_clicks == ["No"] and rep.llm_calls == 0 and rep.open_required == []


def test_country_combobox_option_answer(ctx):
    """C2: «Which country do you intend to work from?» друкує Germany й клікає опцію."""
    page = FakePage(base_fields() + [_f(7, "Which country do you intend to work from?", required=True, combobox=True)])
    page.combo_options = ["Germany", "Ghana"]
    rep = fill_form(page, ctx, llm=FakeLLM("prose"), brief="")
    assert page.typed["Which country do you intend to work from?"] == "Germany"
    assert page.option_clicks == ["Germany"] and rep.llm_calls == 0 and rep.open_required == []


def test_eeo_combobox_declines(ctx):
    page = FakePage(base_fields() + [_f(7, "I identify my gender as:", required=True, combobox=True, id="130")])
    page.combo_options = ["Man", "Woman", "I don't wish to answer"]
    rep = fill_form(page, ctx, llm=None, brief="")
    assert page.el_clicks == ["I identify my gender as:"] and page.option_clicks == ["I don't wish to answer"]
    assert rep.open_required == [] and "I identify my gender as:" in rep.filled


def test_combobox_without_option_left_open(ctx):
    page = FakePage(base_fields() + [_f(7, "Location", required=True, combobox=True)])
    rep = fill_form(page, ctx, llm=None, brief="")
    assert page.option_clicks == [] and rep.open_required == ["Location"]


def test_select_and_date(ctx):
    page = FakePage(base_fields() + [
        _f(7, "Country", tag="select", type="select", required=True, options=["France", "Germany"]),
        _f(8, "Earliest start date", type="date", required=True),
    ])
    rep = fill_form(page, ctx, llm=None, brief="")
    assert page.selected["Country"] == "Germany"
    assert page.filled["Earliest start date"] == "2026-10-27" and page.pressed == [("Earliest start date", "Tab")]
    assert rep.open_required == []


def test_dry_run_never_submits(fake_page, ctx, tmp_path):
    res = apply(fake_page, "https://job-boards.greenhouse.io/contractco/jobs/1", ctx, None, "", tmp_path, dry_run=True)
    assert res.status == "dry_run" and not fake_page.clicked_submit and res.screenshots
    assert res.portal == "greenhouse" and fake_page.apply_clicks == 0


def test_greenhouse_apply_button_reveals_form(ctx, tmp_path):
    page = FakePage([_f(0, "Search jobs")])
    page.fields_after_apply = base_fields()
    res = apply(page, "https://job-boards.greenhouse.io/contractco/jobs/1", ctx, None, "", tmp_path, dry_run=True)
    assert page.apply_clicks == 1 and res.status == "dry_run" and page.files["Resume"] == [str(ctx.resume)]


def test_submit_outcomes(fake_page, ctx, tmp_path):
    fake_page.probe_after_submit = {"submit_visible": False, "challenge_visible": False, "invalid_count": 0, "error_texts": [], "text": "Thank you for applying!"}
    assert apply(fake_page, "https://jobs.ashbyhq.com/c/1/application", ctx, None, "", tmp_path, dry_run=False, wait_ms=0).status == "submitted"
    fake_page.probe_after_submit = {"submit_visible": True, "challenge_visible": True, "invalid_count": 0, "error_texts": [], "text": ""}
    assert apply(fake_page, "https://jobs.ashbyhq.com/c/1/application", ctx, None, "", tmp_path, dry_run=False, wait_ms=0).reason == "captcha"


def test_submitted_has_confirmation_and_screenshots(fake_page, ctx, tmp_path):
    fake_page.probe_after_submit = {"submit_visible": False, "challenge_visible": False, "invalid_count": 0, "error_texts": [], "text": "Thank you for applying!"}
    res = apply(fake_page, "https://jobs.lever.co/c/1/apply", ctx, None, "", tmp_path, dry_run=False, wait_ms=0)
    assert res.confirmation_text == "thank you for applying" and res.portal == "lever"
    assert [p.rsplit("/", 1)[-1] for p in res.screenshots] == ["apply_form_filled.png", "apply_confirmation.png"]


def test_validation_and_no_confirmation(fake_page, ctx, tmp_path):
    fake_page.probe_after_submit = {"submit_visible": True, "challenge_visible": False, "invalid_count": 1, "error_texts": ["Phone is required"], "text": ""}
    res = apply(fake_page, "https://apply.workable.com/c/j/1/apply/", ctx, None, "", tmp_path, dry_run=False, wait_ms=0)
    assert res.status == "handoff" and res.reason == "validation: Phone is required"
    fake_page.probe_after_submit = dict(PRE_SUBMIT)
    res = apply(fake_page, "https://apply.workable.com/c/j/1/apply/", ctx, None, "", tmp_path, dry_run=False, wait_ms=2000)
    assert res.status == "handoff" and res.reason == "no_confirmation"


def test_unknown_host_portal_is_ats(fake_page, ctx, tmp_path):
    assert apply(fake_page, "https://careers.example.com/job/1", ctx, None, "", tmp_path, dry_run=True).portal == "ats"


def test_submit_prefers_submit_application(ctx):
    page = FakePage(base_fields(), buttons=("Apply", "Submit Application"))
    clicked = []
    page._button_click = clicked.append
    submit(page)
    assert clicked == ["Submit Application"]


def test_submit_short_word_is_exact():
    page = FakePage([], buttons=("Apply for another role", "Apply"))
    clicked = []
    page._button_click = clicked.append
    submit(page)
    assert clicked == ["Apply"]


def _clicks(page):
    clicked = []
    page._button_click = clicked.append
    submit(page)
    return clicked


def test_final_submit_wins_over_the_opener_above_the_form():
    """«Jetzt bewerben» is also the opener above a form or a sticky header button: the final text wins."""
    assert _clicks(FakePage([], buttons=("Jetzt bewerben", "Absenden"))) == ["Absenden"]
    assert _clicks(FakePage([], buttons=("Jetzt bewerben", "Senden"))) == ["Senden"]
    assert _clicks(FakePage([], buttons=("Jetzt bewerben", "Bewerbung jetzt absenden"))) == ["Bewerbung jetzt absenden"]
    assert _clicks(FakePage([], buttons=("Apply now", "Submit your application"))) == ["Submit your application"]


def test_submit_button_of_the_filled_form_wins():
    """A type=submit button inside the form that holds the filled fields beats any text elsewhere."""
    page = FakePage([], buttons=("Absenden", {"text": "Jetzt bewerben", "submit_type": True, "in_form": True}))
    assert _clicks(page) == ["Jetzt bewerben"]
    page = FakePage([], buttons=("Jetzt bewerben", {"text": "Los geht's", "submit_type": True, "in_form": True}))
    assert _clicks(page) == ["Los geht's"]


def test_apply_text_inside_the_form_wins_over_the_same_text_outside():
    page = FakePage([], buttons=("Jetzt bewerben", {"text": "Jetzt bewerben", "in_form": True}))
    _clicks(page)
    assert page.submit_clicks == [1]


def test_buttons_of_another_form_are_never_the_submit():
    """A newsletter or search form next to the application: its «Senden» would send that form."""
    page = FakePage([], buttons=({"text": "Senden", "other_form": True}, {"text": "Bewerben", "in_form": True}))
    assert _clicks(page) == ["Bewerben"]


def test_social_apply_buttons_are_never_the_submit():
    page = FakePage([], buttons=("Mit XING jetzt bewerben", "Mit LinkedIn bewerben", "Apply with Indeed",
                                 "Sign in with Google", "Continue with Apple",
                                 {"text": "Mit XING bewerben", "submit_type": True, "in_form": True}))
    with pytest.raises(LookupError):
        ats_form.find_submit(page)
    page.buttons.append("Bewerben")
    assert _clicks(page) == ["Bewerben"]


def test_next_step_upload_and_share_buttons_are_not_a_submit():
    page = FakePage([], buttons=({"text": "Weiter", "submit_type": True, "in_form": True},
                                 {"text": "Lebenslauf hochladen", "submit_type": True, "in_form": True},
                                 "Code senden", "Job per E-Mail senden", "Bewerbung starten"))
    with pytest.raises(LookupError):
        ats_form.find_submit(page)


def test_enabled_submit_wins_over_a_disabled_twin():
    page = FakePage([], buttons=({"text": "Absenden", "disabled": True}, "Absenden"))
    _clicks(page)
    assert page.submit_clicks == [1]


def test_disabled_submit_is_handoff_without_a_click(fake_page, ctx, tmp_path):
    """A disabled button means the page still misses something; a click would only time out after
    the attempt counted as a submission."""
    fake_page.buttons = [{"text": "Bewerbung absenden", "disabled": True}]
    res = apply(fake_page, "https://careers.example.com/job/1", ctx, None, "", tmp_path, dry_run=False, wait_ms=0)
    assert res.status == "handoff" and res.reason == "submit_disabled" and not fake_page.clicked_submit


def test_opener_above_the_form_is_not_pressed_live(fake_page, ctx, tmp_path):
    fake_page.buttons = ["Jetzt bewerben", {"text": "Absenden", "submit_type": True, "in_form": True}]
    fake_page.probe_after_submit = {"submit_visible": False, "challenge_visible": False, "invalid_count": 0,
                                    "error_texts": [], "text": "Vielen Dank für Ihre Bewerbung"}
    apply(fake_page, "https://careers.example.com/job/1", ctx, None, "", tmp_path, dry_run=False, wait_ms=0)
    assert fake_page.submit_clicks == [1]


def test_probe_reads_text(fake_page):
    pr = probe(fake_page)
    assert pr.text == "Apply for this job" and pr.submit_visible and pr.html


def test_closed_posting(fake_page_closed, ctx, tmp_path):
    assert apply(fake_page_closed, "https://jobs.lever.co/c/1/apply", ctx, None, "", tmp_path, dry_run=False).status == "closed"


@pytest.fixture
def fake_page_closed():
    return FakePage(base_fields(), body="Sorry, this job is no longer available.")


def _id_fields():
    return [
        _f(0, "Yes", type="radio", required=True, group="Have you used our product?", id="q1-yes"),
        _f(1, "No", type="radio", required=True, group="Have you used our product?", id="q1-no"),
        _f(2, "Legal Name", required=True, id="name"),
        _f(3, "Email", type="email", required=True, id="email"),
        _f(4, "Resume", type="file", required=True, id="resume"),
        _f(5, "I confirm I have read the above.", type="checkbox", required=True, id="ack"),
    ]


def test_revealed_field_does_not_shift_later_answers(ctx):
    """Відповідь «No» відкриває нове питання перед рештою полів: індекси зсуваються."""
    page = FakePage(_id_fields())
    page.hooks[("click", "q1-no")] = lambda pg: pg.insert(2, _f(0, "Which version did you use?", required=True, id="q-version"))
    ctx.profile["form_answers"] = [{"pattern": "used our product", "choice": "no"}]
    rep = fill_form(page, ctx, llm=None, brief="")
    assert page.clicked == ["Have you used our product?=No"]
    assert page.filled == {"Legal Name": "Erika Mustermann", "Email": "erika@example.com"}
    assert page.files == {"Resume": [str(ctx.resume)]} and page.checked == ["I confirm I have read the above."]
    assert rep.open_required == ["Which version did you use?"]


def test_live_element_mismatch_rereads_and_retries(ctx):
    """DOM змінився без нашої дії-тригера: _live бачить чужий id, форма перечитується."""
    page = FakePage(_id_fields())
    page.hooks[("fill", "name")] = lambda pg: pg.insert(3, _f(0, "Company", id="company"))
    fill_form(page, ctx, llm=None, brief="")
    assert page.filled["Email"] == "erika@example.com" and "Company" not in page.filled


def test_persistent_mismatch_skips_field(ctx, caplog):
    page = FakePage(_id_fields())
    page.attr_override[("email", "id")] = "something-else"
    rep = fill_form(page, ctx, llm=None, brief="")
    assert "Email" not in page.filled and "Email" in rep.open_required
    assert page.filled["Legal Name"] == "Erika Mustermann"
    assert "зсунулося" in caplog.text


def test_poll_survives_navigation(fake_page, ctx, tmp_path):
    fake_page.probe_errors = 2
    fake_page.probe_after_submit = {"submit_visible": False, "challenge_visible": False, "invalid_count": 0, "error_texts": [], "text": "Thank you for applying!"}
    res = apply(fake_page, "https://jobs.ashbyhq.com/c/1/application", ctx, None, "", tmp_path, dry_run=False, wait_ms=4000)
    assert res.status == "submitted" and fake_page.probe_errors == 0


def test_poll_failing_until_deadline_is_no_confirmation(fake_page, ctx, tmp_path):
    fake_page.probe_errors = 99
    res = apply(fake_page, "https://jobs.ashbyhq.com/c/1/application", ctx, None, "", tmp_path, dry_run=False, wait_ms=3000)
    assert res.status == "handoff" and res.reason == "no_confirmation"


def test_confirmation_phrase_already_on_page_is_not_submitted(ctx, tmp_path):
    """Опис вакансії Greenhouse на тій самій сторінці вже містить фразу підтвердження."""
    body = "About the role ... thank you for your application, we read every one."
    after = {"submit_visible": False, "challenge_visible": False, "invalid_count": 0, "error_texts": [], "text": body}
    page = FakePage(base_fields(), body=body)
    page.probe_after_submit = after
    res = apply(page, "https://job-boards.greenhouse.io/contractco/jobs/1", ctx, None, "", tmp_path, dry_run=False, wait_ms=0)
    assert res.status == "handoff" and res.reason == "no_confirmation"
    # кнопка Submit зникла на два знімки поспіль: тоді вже подано
    res = apply(page, "https://job-boards.greenhouse.io/contractco/jobs/1", ctx, None, "", tmp_path, dry_run=False, wait_ms=3000)
    assert res.status == "submitted"


def test_new_phrase_after_submit_is_submitted_at_once(ctx, tmp_path):
    body = "About the role ... thank you for your application, we read every one."
    page = FakePage(base_fields(), body=body)
    page.probe_after_submit = {"submit_visible": False, "challenge_visible": False, "invalid_count": 0,
                               "error_texts": [], "text": "Thank you for applying!"}
    assert apply(page, "https://job-boards.greenhouse.io/contractco/jobs/1", ctx, None, "", tmp_path,
                 dry_run=False, wait_ms=0).status == "submitted"


def test_error_reasons_before_and_after_submit(fake_page, ctx, tmp_path, monkeypatch, caplog):
    from bot.apply import ats_form

    def boom(*a, **kw):
        raise RuntimeError("boom")

    monkeypatch.setattr(ats_form, "_poll_after_submit", boom)
    res = apply(fake_page, "https://jobs.ashbyhq.com/c/1/application", ctx, None, "", tmp_path, dry_run=False)
    assert res.status == "handoff" and res.reason.startswith("error_after_submit: RuntimeError")
    monkeypatch.setattr(ats_form, "fill_form", boom)
    res = apply(fake_page, "https://jobs.ashbyhq.com/c/1/application", ctx, None, "", tmp_path, dry_run=False)
    assert res.reason.startswith("error: RuntimeError")
    assert caplog.text.count("Traceback") == 2


# ---- Фінальне рев'ю: відкриття форми за ATS (C1)

def test_form_url_per_ats():
    assert form_url("https://jobs.ashbyhq.com/contractco/1d2a9c36-0000-4000-8000-000000000009", "ashby") == \
        "https://jobs.ashbyhq.com/contractco/1d2a9c36-0000-4000-8000-000000000009/application"
    assert form_url("https://jobs.ashbyhq.com/contractco/1/application", "ashby") == "https://jobs.ashbyhq.com/contractco/1/application"
    assert form_url("https://jobs.lever.co/contractco/1/", "lever") == "https://jobs.lever.co/contractco/1/apply"
    assert form_url("https://jobs.lever.co/contractco/1/apply", "lever") == "https://jobs.lever.co/contractco/1/apply"
    assert form_url("https://apply.workable.com/contractco/j/ABC123/", "workable") == "https://apply.workable.com/contractco/j/ABC123/apply/"
    assert form_url("https://apply.workable.com/contractco/j/ABC123/apply", "workable") == "https://apply.workable.com/contractco/j/ABC123/apply/"
    assert form_url("https://job-boards.greenhouse.io/contractco/jobs/1?gh_jid=1", "greenhouse") == "https://job-boards.greenhouse.io/contractco/jobs/1?gh_jid=1"
    assert form_url("https://careers.example.com/job/1", None) == "https://careers.example.com/job/1"


def test_ashby_overview_url_opens_application_page(fake_page, ctx, tmp_path):
    res = apply(fake_page, "https://jobs.ashbyhq.com/contractco/1d2a9c36-0000-4000-8000-000000000009", ctx, None, "", tmp_path, dry_run=True)
    assert fake_page.visited == ["https://jobs.ashbyhq.com/contractco/1d2a9c36-0000-4000-8000-000000000009/application"]
    assert res.status == "dry_run" and res.url.endswith("000000000009")   # у результаті адреса ліда


def test_lever_and_workable_urls_open_apply_page(fake_page, ctx, tmp_path):
    apply(fake_page, "https://jobs.lever.co/contractco/1", ctx, None, "", tmp_path, dry_run=True)
    assert fake_page.visited[-1] == "https://jobs.lever.co/contractco/1/apply"
    apply(fake_page, "https://apply.workable.com/contractco/j/ABC123/", ctx, None, "", tmp_path, dry_run=True)
    assert fake_page.visited[-1] == "https://apply.workable.com/contractco/j/ABC123/apply/"


def test_ready_form_is_read_without_clicks(fake_page, ctx, tmp_path):
    res = apply(fake_page, "https://jobs.ashbyhq.com/contractco/1/application", ctx, None, "", tmp_path, dry_run=True)
    assert res.status == "dry_run" and fake_page.apply_clicks == 0


def test_greenhouse_clicks_apply_then_reads_form(ctx, tmp_path):
    page = FakePage([_f(0, "Search jobs")])
    page.fields_after_apply = base_fields()
    res = apply(page, "https://job-boards.greenhouse.io/contractco/jobs/1", ctx, None, "", tmp_path, dry_run=True)
    assert page.apply_clicks == 1 and res.status == "dry_run"


def test_unknown_ats_clicks_generic_apply_link(ctx, tmp_path):
    page = FakePage([_f(0, "Search jobs")])
    page.fields_after_apply = base_fields()
    res = apply(page, "https://careers.example.com/job/1", ctx, None, "", tmp_path, dry_run=True)
    assert page.apply_clicks == 1 and res.status == "dry_run" and page.visited == ["https://careers.example.com/job/1"]


def test_im_interested_button_opens_form(ctx, tmp_path):
    """SmartRecruiters: the form sits behind "I'm interested", next to "Refer a friend"."""
    page = FakePage([])
    page.apply_buttons = ["Refer a friend", "I\u2019m interested", "Share"]
    page.fields_after_apply = base_fields()
    res = apply(page, "https://jobs.example-ats.com/contractco/1", ctx, None, "", tmp_path, dry_run=True)
    assert page.apply_clicked == ["I\u2019m interested"] and res.status == "dry_run"


def test_zero_size_form_and_search_box_are_not_a_form(ctx, tmp_path):
    """Recruitee: a zero-size <form> and a search box sit on the job page; the opener must still click Bewerben."""
    page = FakePage([_f(0, "", type="hidden", visible=False), _f(1, "Search")])
    page.apply_buttons = ["Jobdetails", "Bewerben", "Job teilen"]
    page.fields_after_apply = base_fields()
    res = apply(page, "https://contractco.example-ats.com/o/android", ctx, None, "", tmp_path, dry_run=True)
    assert page.apply_clicked == ["Bewerben"] and res.status == "dry_run"


def test_form_in_hidden_tab_is_not_ready(ctx, tmp_path):
    """Recruitee renders the whole form inside a display:none tab on the job page."""
    hidden = [{**d, "visible": False} for d in base_fields()]
    page = FakePage(hidden)
    page.apply_buttons = ["Bewerben"]
    page.fields_after_apply = base_fields()
    res = apply(page, "https://contractco.example-ats.com/o/android", ctx, None, "", tmp_path, dry_run=True)
    assert page.apply_clicked == ["Bewerben"] and res.status == "dry_run"


def test_login_wall_is_not_clicked(ctx, tmp_path):
    page = FakePage([_f(0, "Email address", type="email", required=True)])
    page.apply_buttons = ["Continue as Erika", "Continue with Apple", "Continue"]
    page.fields_after_apply = base_fields()
    res = apply(page, "https://jobs.example-board.com/viewjob?jk=1", ctx, None, "", tmp_path, dry_run=False)
    assert res.status == "handoff" and res.reason == "form_not_found" and page.apply_clicks == 0


def test_llm_picks_the_button_when_no_known_word(ctx, tmp_path):
    page = FakePage([])
    page.apply_buttons = ["Details", "Los geht's", "Kontakt"]
    page.fields_after_apply = base_fields()
    llm = FakeLLM("1")
    res = apply(page, "https://careers.example.com/job/1", ctx, llm, "", tmp_path, dry_run=True)
    assert page.apply_clicked == ["Los geht's"] and res.status == "dry_run"
    assert "application form" in llm.prompts[0]


def test_llm_choice_of_a_login_button_is_ignored(ctx, tmp_path):
    page = FakePage([])
    page.apply_buttons = ["Details", "Sign in"]
    page.fields_after_apply = base_fields()
    res = apply(page, "https://careers.example.com/job/1", ctx, FakeLLM("1"), "", tmp_path, dry_run=False)
    assert res.status == "handoff" and res.reason == "form_not_found" and page.apply_clicks == 0


def test_direct_form_ats_never_clicks(ctx, tmp_path):
    page = FakePage([_f(0, "Search jobs")])
    page.fields_after_apply = base_fields()
    res = apply(page, "https://jobs.ashbyhq.com/contractco/1", ctx, None, "", tmp_path, dry_run=False)
    assert res.status == "handoff" and res.reason == "form_not_found" and page.apply_clicks == 0


def test_pick_apply_ranking():
    def cands(*texts):
        return [{"i": k, "text": t, "href": h, "tag": "a", "target": ""} for k, (t, h) in enumerate(texts)]
    assert pick_apply(cands(("Apply with LinkedIn", ""), ("Apply now", "")))["text"] == "Apply now"
    assert pick_apply(cands(("Submit application", ""),)) is None
    assert pick_apply(cands(("apply@contractco.example", "mailto:apply@contractco.example"),)) is None
    assert pick_apply(cands(("Mehr", "https://contractco.example/jobs/1/bewerbung"),))["text"] == "Mehr"
    assert pick_apply(cands(("Jetzt bewerben", ""), ("Bewerben", "")))["text"] == "Jetzt bewerben"


def test_form_never_appears_is_handoff(fake_page, ctx, tmp_path):
    fake_page.form_never_appears = True
    res = apply(fake_page, "https://jobs.ashbyhq.com/contractco/1", ctx, None, "", tmp_path, dry_run=False)
    assert res.status == "handoff" and res.reason == "form_not_found" and not fake_page.filled
    assert [p.rsplit("/", 1)[-1] for p in res.screenshots] == ["apply_form_not_found.png"]


def test_form_never_appears_on_dead_page_is_still_handoff(fake_page, ctx, tmp_path):
    fake_page.form_never_appears = True
    fake_page.dead = True
    res = apply(fake_page, "https://jobs.ashbyhq.com/contractco/1", ctx, None, "", tmp_path, dry_run=False)
    assert res.status == "handoff" and res.reason == "form_not_found"


def test_native_select_with_combobox_role_uses_select_option(ctx):
    page = FakePage(base_fields() + [
        _f(7, "Country", tag="select", type="select", required=True, options=["France", "Germany"], combobox=True),
    ])
    page.combo_options = ["Germany"]
    rep = fill_form(page, ctx, llm=None, brief="")
    assert page.selected["Country"] == "Germany" and page.option_clicks == [] and page.typed == {}
    assert rep.open_required == []


def test_form_never_appears_on_closed_posting_is_closed(fake_page_closed, ctx, tmp_path):
    fake_page_closed.form_never_appears = True
    assert apply(fake_page_closed, "https://jobs.lever.co/contractco/1", ctx, None, "", tmp_path, dry_run=False).status == "closed"


# ---- C3: необов'язкове резюме Greenhouse усе одно вантажиться

def test_optional_greenhouse_resume_is_uploaded(ctx, tmp_path):
    fs = [
        _f(0, "First Name", required=True, id="first_name"),
        _f(1, "Last Name", required=True, id="last_name"),
        _f(2, "Email", type="email", required=True, id="email"),
        _f(3, "Attach", type="file", required=False, id="resume", group="Resume/CV"),
        _f(4, "Attach", type="file", required=False, id="cover_letter", group="Cover Letter"),
    ]
    page = FakePage([dict(d) for d in fs])
    rep = fill_form(page, ctx, llm=None, brief="")
    assert page.files == {"Attach": [str(ctx.resume)]} and rep.uploaded == ["Attach"] and rep.open_required == []
    page = FakePage([dict(d) for d in fs])
    res = apply(page, "https://job-boards.greenhouse.io/contractco/jobs/1", ctx, None, "", tmp_path, dry_run=True)
    assert res.status == "dry_run" and "uploaded 1" in res.reason and page.files == {"Attach": [str(ctx.resume)]}


# ---- I6: сусідні radio з одним name

def test_radio_shift_before_click_still_hits_matching_label(ctx):
    """Два radio Yes/No з одним name без id; DOM зсунувся між читанням і кліком: клік по «No»."""
    q = "Do you need visa sponsorship?"
    fs = [
        _f(0, "Legal Name", required=True, id="name"),
        _f(1, "Yes", type="radio", required=True, group=q, name="sponsor"),
        _f(2, "No", type="radio", required=True, group=q, name="sponsor"),
    ]
    fs[1]["attr_value"], fs[2]["attr_value"] = "yes", "no"
    page = FakePage(fs)
    page.hooks[("fill", "name")] = lambda pg: pg.insert(1, _f(0, "Company", id="company"))
    ctx.profile["form_answers"] = [{"pattern": "sponsorship", "choice": "no"}]
    rep = fill_form(page, ctx, llm=None, brief="")
    assert page.clicked == [f"{q}=No"] and rep.open_required == []
    assert page.fields[3]["value"] == "on" and page.fields[2]["value"] == ""


# ---- I7: вікно після Submit 30 с

def test_default_submit_window_is_30s(fake_page, ctx, tmp_path):
    assert SUBMIT_WAIT_MS == 30000
    res = apply(fake_page, "https://jobs.ashbyhq.com/contractco/1/application", ctx, None, "", tmp_path, dry_run=False)
    assert res.reason == "no_confirmation" and fake_page.waited_ms >= SUBMIT_WAIT_MS
    assert fake_page.waited_ms // POLL_MS >= SUBMIT_WAIT_MS // POLL_MS


# ---- I10: клік Submit, що кинув виняток

def test_click_that_throws_is_error_after_submit(fake_page, ctx, tmp_path):
    def boom(text):
        fake_page.clicked_submit = True
        raise RuntimeError("Target closed")
    fake_page._button_click = boom
    res = apply(fake_page, "https://jobs.ashbyhq.com/contractco/1/application", ctx, None, "", tmp_path, dry_run=False)
    assert res.status == "handoff" and res.reason.startswith("error_after_submit: RuntimeError: Target closed")


def test_combobox_yes_no_never_picks_option_by_substring(ctx):
    """Рев'ю live-fixes, I3: «No» підрядком сидить у «…sponsorship now». Для Yes/No лише точна опція
    чи та, що з цього слова починається; інакше поле очищується й іде в hand-off."""
    q = "Will you now or in the future require visa sponsorship?"
    ctx.profile["form_answers"] = [{"pattern": "sponsorship", "choice": "no"}]
    page = FakePage(base_fields() + [_f(7, q, required=True, combobox=True)])
    page.combo_options = ["I will require sponsorship now", "I will require sponsorship in the future",
                          "I do not require sponsorship"]
    rep = fill_form(page, ctx, llm=None, brief="")
    assert page.typed[q] == "No" and page.option_clicks == [] and rep.open_required == [q]
    page = FakePage(base_fields() + [_f(7, q, required=True, combobox=True)])
    page.combo_options = ["Yes, I will need sponsorship", "No, I will not need sponsorship"]
    rep = fill_form(page, ctx, llm=None, brief="")
    assert page.option_clicks == ["No, I will not need sponsorship"] and rep.open_required == []


def test_combobox_yes_needs_a_single_leading_option(ctx):
    """Повторне рев'ю: «Yes» на початку двох опцій («Yes, I am an EU citizen», «Yes, I have a work
    permit») не дає права взяти першу: поле очищується й іде в hand-off."""
    q = "Are you legally authorized to work in Germany?"
    ctx.profile["form_answers"] = [{"pattern": "authori[sz]ed", "choice": "yes"}]
    page = FakePage(base_fields() + [_f(7, q, required=True, combobox=True)])
    page.combo_options = ["Yes, I am an EU citizen", "Yes, I have a work permit", "No"]
    rep = fill_form(page, ctx, llm=None, brief="")
    assert page.typed[q] == "Yes" and page.option_clicks == [] and rep.open_required == [q]


def test_field_js_and_selector_scoped_to_root():
    """Корінь (модалка LinkedIn) звужує і FIELD_JS, і локатор полів: індекси збігаються."""
    assert FIELD_JS.count('document.querySelectorAll("input,select,textarea")') == 1
    assert field_js("") == FIELD_JS and field_selector("") == FIELD_SELECTOR
    assert field_selector("dialog[open]") == "dialog[open] input,dialog[open] select,dialog[open] textarea"
    assert ('document.querySelectorAll("dialog[open] input,dialog[open] select,dialog[open] textarea")'
            in field_js("dialog[open]"))


class ScopedPage(FakePage):
    """Сторінка, де поля читаються й знаходяться лише в межах dialog[open]."""

    def evaluate(self, js):
        if js == FIELD_JS:
            raise AssertionError("unscoped FIELD_JS")
        return super().evaluate(FIELD_JS if js == field_js("dialog[open]") else js)

    def locator(self, sel, has=None, has_text=None):
        assert sel != FIELD_SELECTOR, "unscoped field locator"
        if sel == field_selector("dialog[open]"):
            return FieldList(self)
        return super().locator(sel, has, has_text)


def test_fill_form_with_root_and_text_answer_into_select(ctx):
    """Email у <select> (LinkedIn): текстова відповідь стає вибором опції; немає опції, поле відкрите."""
    page = ScopedPage([
        _f(0, "Email address*", type="select", tag="select", required=True, value="other@example.com",
           options=["", "erika@example.com"], id="em"),
        _f(1, "Phone country code", type="select", tag="select", required=True,
           options=["", "Germany (+49)"], id="cc"),
    ])
    rep = fill_form(page, ctx, llm=None, brief="", root="dialog[open]")
    assert page.selected == {"Email address*": "erika@example.com"}
    assert rep.filled == ["Email address*"] and rep.open_required == ["Phone country code"]


def test_option_on_combobox_in_scoped_page_uses_scoped_locator(ctx):
    """Опція в комбобоксі всередині кореня: локатор звужений так само, як читання."""
    q = "Country"
    page = ScopedPage([_f(0, q, required=True, combobox=True, options=["Germany", "Ghana"], id="c")])
    page.combo_options = ["Germany", "Ghana"]
    rep = fill_form(page, ctx, llm=None, brief="", root="dialog[open]")
    assert page.typed[q] == "Germany" and page.option_clicks == ["Germany"]
    assert rep.filled == [q] and rep.open_required == []


def test_text_answer_into_select_rereads_fields(ctx):
    """Вибір опції в select може відкрити залежні поля: після нього FIELD_JS перечитується."""
    page = ScopedPage([_f(0, "Email address*", type="select", tag="select", required=True, value="other@example.com",
                          options=["", "erika@example.com"], id="em")])
    fill_form(page, ctx, llm=None, brief="", root="dialog[open]")
    assert page.reads == 3   # перше читання, після вибору, open_required


def test_email_select_without_profile_address_is_open_required(ctx):
    """Адреси профілю нема серед опцій: чуже передзаповнене значення не пропускається мовчки."""
    page = ScopedPage([
        _f(0, "Email address*", type="select", tag="select", required=True, value="other@example.com",
           options=["", "other@example.com"], id="em"),
        _f(1, "Email (optional)", type="select", tag="select", options=["", "x@example.com"], id="em2"),
    ])
    rep = fill_form(page, ctx, llm=None, brief="", root="dialog[open]")
    assert page.selected == {} and rep.filled == []
    assert rep.open_required == ["Email address*", "Email (optional)"]


def test_email_combobox_without_option_is_open_required(ctx):
    page = FakePage([_f(0, "Email", type="email", combobox=True, value="old@example.com", id="em")])
    page.combo_options = ["old@example.com"]
    rep = fill_form(page, ctx, llm=None, brief="")
    assert rep.open_required == ["Email"]


def test_source_radio_group_picks_linkedin(ctx):
    """Recruitee asks "Wie bist du auf uns aufmerksam geworden?" as radios."""
    q = "Wie bist du auf uns aufmerksam geworden?"
    page = FakePage(base_fields() + [
        _f(7, "Karriere-Webseite", type="radio", required=True, group=q),
        _f(8, "LinkedIn", type="radio", required=True, group=q),
        _f(9, "Sonstiges", type="radio", required=True, group=q),
    ])
    rep = fill_form(page, ctx, llm=None, brief="")
    assert q in rep.filled and q not in rep.open_required and page.clicked == [f"{q}=LinkedIn"]


def test_date_format_follows_the_page_locale(ctx):
    """LinkedIn with an English UI rejects 2026-11-12 in its date field and takes 11/12/2026."""
    page = FakePage([_f(0, "Earliest start date", required=True)] + base_fields()[1:])
    fill_form(page, ctx, llm=None, brief="", date_format="%m/%d/%Y")
    assert re.fullmatch(r"\d{2}/\d{2}/\d{4}", page.filled["Earliest start date"])
    page = FakePage([_f(0, "Earliest start date", required=True)] + base_fields()[1:])
    fill_form(page, ctx, llm=None, brief="")
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", page.filled["Earliest start date"])


# ---- Guard: a form is never submitted without the CV

def _no_file_fields():
    return [
        _f(0, "First name", required=True, id="fn"),
        _f(1, "Last name", required=True, id="ln"),
        _f(2, "Email", type="email", required=True, id="em"),
    ]


def test_form_without_cv_upload_is_handoff_live(ctx, tmp_path):
    page = FakePage(_no_file_fields())
    res = apply(page, "https://careers.example.com/job/1", ctx, None, "", tmp_path, dry_run=False, wait_ms=0)
    assert res.status == "handoff" and res.reason == "no_cv_uploaded" and not page.clicked_submit


def test_form_without_cv_upload_is_handoff_in_dry_run(ctx, tmp_path):
    """The shadow pass reports what the live pass would do: a hand-off, not a ready form."""
    page = FakePage(_no_file_fields())
    res = apply(page, "https://careers.example.com/job/1", ctx, None, "", tmp_path, dry_run=True)
    assert res.status == "handoff" and res.reason == "no_cv_uploaded" and not page.clicked_submit


def test_cover_letter_alone_is_not_a_cv(ctx, tmp_path):
    ctx.cover_letter = tmp_path / "Anschreiben.pdf"
    page = FakePage(_no_file_fields() + [_f(3, "Cover Letter", type="file", id="cl")])
    res = apply(page, "https://careers.example.com/job/1", ctx, None, "", tmp_path, dry_run=False, wait_ms=0)
    assert page.files == {"Cover Letter": [str(ctx.cover_letter)]}
    assert res.status == "handoff" and res.reason == "no_cv_uploaded" and not page.clicked_submit


def test_fill_report_marks_the_cv_upload(fake_page, ctx):
    assert fill_form(fake_page, ctx, llm=None, brief="").cv_uploaded is True
    assert fill_form(FakePage(_no_file_fields()), ctx, llm=None, brief="").cv_uploaded is False


def test_cv_already_in_the_file_field_counts(ctx):
    """A second pass over the same tab: the browser shows the chosen file as C:\\fakepath\\<name>."""
    fs = _no_file_fields() + [_f(3, "Resume", type="file", required=True, id="cv",
                                 value="C:\\fakepath\\" + ctx.resume.name)]
    rep = fill_form(FakePage(fs), ctx, llm=None, brief="")
    assert rep.cv_uploaded is True and rep.uploaded == []
    fs[3]["value"] = "C:\\fakepath\\Other.pdf"
    assert fill_form(FakePage(fs), ctx, llm=None, brief="").cv_uploaded is False


# ---- Job boards: an Indeed job page is never filled, the worker goes to the employer by URL

JK = "0123456789abcdef"
INDEED = f"https://de.indeed.com/viewjob?jk={JK}"
APPLYSTART = f"https://de.indeed.com/applystart?jk={JK}&from=vj"
EMBEDDED = f'<script>{{"viewJobButtonLinkModel":{{"href":"{APPLYSTART.replace("/", chr(92) + "u002F")}"}}}}</script>'


class BoardPage(FakePage):
    """An Indeed job page: a location search box and a job-alert email field, the employer redirect
    in the page data or on a link. goto(applystart) is the server redirect to `employer`."""

    def __init__(self, html="", cands=(), employer="https://careers.example.com/job/1", land="",
                 body="Softwareentwickler (m/w/d) - Berlin", fail_goto=False):
        super().__init__([_f(0, "Ort, PLZ oder Stadt", id="where"), _f(1, "E-Mail-Adresse", type="email", id="alert")],
                         body=body)
        self.html, self.board_cands = html, [dict(c) for c in cands]
        self.employer, self.land, self.fail_goto = employer, land, fail_goto

    def goto(self, url, wait_until=None, timeout=None):
        if self.fail_goto:
            raise TimeoutError("Timeout 30000ms exceeded")
        super().goto(url, wait_until, timeout)
        if "applystart" in url:
            self.url = self.employer
            self.fields = base_fields()
        elif self.land and url == INDEED:
            self.url = self.land

    def content(self):
        return self.html if "indeed.com" in self.url else "<html></html>"

    def evaluate(self, js):
        from bot.apply import job_boards
        if js == job_boards.BOARD_JS:
            return [dict(c) for c in self.board_cands]
        return super().evaluate(js)


def _board_cand(text, href="", **kw):
    return {"text": text, "href": href, "id": "", "testid": "", "in_apply_box": False, "own_apply_box": False, **kw}


def test_indeed_page_goes_to_the_employer_form_by_url(ctx, tmp_path):
    page = BoardPage(html=EMBEDDED)
    res = apply(page, INDEED, ctx, None, "", tmp_path, dry_run=True)
    assert page.visited == [INDEED, APPLYSTART, "https://careers.example.com/job/1"]
    assert res.status == "dry_run" and page.files == {"Resume": [str(ctx.resume)]}
    assert "Ort, PLZ oder Stadt" not in page.filled and page.apply_clicks == 0
    assert res.url == INDEED


def test_indeed_link_to_a_known_ats_is_submitted_live(ctx, tmp_path):
    page = BoardPage(cands=[_board_cand("Auf Unternehmenswebsite bewerben", APPLYSTART)],
                     employer="https://job-boards.greenhouse.io/contractco/jobs/1")
    page.probe_after_submit = {"submit_visible": False, "challenge_visible": False, "invalid_count": 0,
                               "error_texts": [], "text": "Thank you for applying!"}
    res = apply(page, INDEED, ctx, None, "", tmp_path, dry_run=False, wait_ms=0)
    assert res.status == "submitted" and res.portal == "greenhouse"


def test_indeed_apply_only_is_handoff(ctx, tmp_path):
    page = BoardPage(html='{"indeedApplyButtonContainer":{"x":1}}',
                     cands=[_board_cand("Jetzt bewerben", id="indeedApplyButton", own_apply_box=True)])
    res = apply(page, INDEED, ctx, None, "", tmp_path, dry_run=False)
    assert res.status == "handoff" and res.reason == "indeed_apply"
    assert not page.filled and not page.clicked_submit and page.visited == [INDEED]
    assert [p.rsplit("/", 1)[-1] for p in res.screenshots] == ["apply_indeed.png"]


def test_indeed_page_without_any_apply_link_is_handoff(ctx, tmp_path):
    res = apply(BoardPage(), INDEED, ctx, None, "", tmp_path, dry_run=True)
    assert res.status == "handoff" and res.reason == "indeed_no_apply_link"


def test_indeed_login_wall_is_handoff(ctx, tmp_path):
    page = BoardPage(html=EMBEDDED, land="https://secure.indeed.com/auth?hl=de_DE&continue=x")
    res = apply(page, INDEED, ctx, None, "", tmp_path, dry_run=False)
    assert res.status == "handoff" and res.reason == "indeed_login" and not page.filled


def test_indeed_cloudflare_check_is_captcha(ctx, tmp_path):
    page = BoardPage(html=EMBEDDED, body="Additional Verification Required")
    res = apply(page, INDEED, ctx, None, "", tmp_path, dry_run=False)
    assert res.status == "handoff" and res.reason == "captcha" and page.visited == [INDEED]


def test_indeed_block_page_is_handoff(ctx, tmp_path):
    page = BoardPage(body="Anfrage blockiert\nSie wurden gesperrt.")
    res = apply(page, INDEED, ctx, None, "", tmp_path, dry_run=False)
    assert res.status == "handoff" and res.reason == "indeed_blocked" and page.visited == [INDEED]


def test_redirect_that_stays_on_indeed_is_handoff(ctx, tmp_path):
    page = BoardPage(html=EMBEDDED, employer="https://secure.indeed.com/auth?continue=x")
    res = apply(page, INDEED, ctx, None, "", tmp_path, dry_run=False)
    assert res.status == "handoff" and res.reason == "indeed_login" and not page.filled
    page = BoardPage(html=EMBEDDED, employer=INDEED)
    res = apply(page, INDEED, ctx, None, "", tmp_path, dry_run=False)
    assert res.status == "handoff" and res.reason == "indeed_no_employer_page" and not page.filled


def test_indeed_page_that_does_not_load_is_failed(ctx, tmp_path):
    res = apply(BoardPage(fail_goto=True), INDEED, ctx, None, "", tmp_path, dry_run=False)
    assert res.status == "failed" and res.reason.startswith("goto: TimeoutError")


def test_board_page_is_never_a_ready_form():
    page = FakePage(base_fields())
    assert ats_form._form_ready(page)
    page.url = INDEED
    assert not ats_form._form_ready(page)


# ---- More boards: StepStone, Arbeitsagentur and the others are never filled or clicked

BA = "https://www.arbeitsagentur.de/jobsuche/jobdetail/13635-0a1b2c3d_JB1234567-S"
PARTNER = "https://www.partner-board.example/job/1234567.html"
STEPSTONE = "https://www.stepstone.de/stellenangebote--Android-Entwickler-Contract-Co--14675901-inline.html"


class OtherBoardPage(FakePage):
    """A board job page with a search box; BOARD_JS sees `cands` and content() is `html` while the
    page is on the board. goto(employer) loads the application form there."""

    def __init__(self, cands=(), html="", employer=None, body="Softwareentwickler (m/w/d) - Berlin"):
        super().__init__([_f(0, "Was", id="was"), _f(1, "Wo", id="wo")], body=body)
        self.board_cands, self.html, self.employer = [dict(c) for c in cands], html, employer
        self.apply_buttons = [c["text"] for c in cands]   # what the opener would see on this page
        self.fields_after_apply = base_fields()

    def goto(self, url, wait_until=None, timeout=None):
        super().goto(url, wait_until, timeout)
        if url == self.employer:
            self.fields, self.fields_after_apply = base_fields(), None

    def content(self):
        from bot.apply.job_boards import board_of
        return self.html if board_of(self.url) else "<html></html>"

    def evaluate(self, js):
        from bot.apply import job_boards
        if js == job_boards.BOARD_JS:
            return [dict(c) for c in self.board_cands] if job_boards.board_of(self.url) else []
        return super().evaluate(js)


def test_arbeitsagentur_partner_offer_goes_to_the_external_page(ctx, tmp_path):
    page = OtherBoardPage([_board_cand("Externe Seite öffnen", PARTNER, id="detail-beschreibung-externe-url-btn")],
                          employer=PARTNER)
    res = apply(page, BA, ctx, None, "", tmp_path, dry_run=True)
    assert page.visited[:2] == [BA, PARTNER] and res.status == "dry_run"
    assert "Was" not in page.filled and page.files == {"Resume": [str(ctx.resume)]} and res.url == BA


def test_arbeitsagentur_contacts_behind_captcha_is_handoff(ctx, tmp_path):
    """A BA offer without a partner link: the employer's contacts sit behind the BA's own captcha,
    whose «Absenden» is a submit button. Only a human goes on from there."""
    page = OtherBoardPage([_board_cand("Info zur Bewerbung"), _board_cand("Absenden")],
                          html='<input id="kontaktdaten-captcha-input"><button type="submit">Absenden</button>')
    res = apply(page, BA, ctx, None, "", tmp_path, dry_run=False)
    assert res.status == "handoff" and res.reason == "arbeitsagentur_captcha"
    assert not page.filled and not page.clicked_submit and page.apply_clicks == 0 and page.visited == [BA]


def test_stepstone_form_lead_is_never_clicked(ctx, tmp_path):
    """StepStone's «Ich bin interessiert» may send a profile application by itself: never clicked here."""
    page = OtherBoardPage([_board_cand("Ich bin interessiert", testid="harmonised-apply-button")])
    res = apply(page, STEPSTONE, ctx, None, "", tmp_path, dry_run=False)
    assert res.status == "handoff" and res.reason == "stepstone_no_apply_link"
    assert page.apply_clicks == 0 and not page.filled and not page.clicked_submit
    assert [p.rsplit("/", 1)[-1] for p in res.screenshots] == ["apply_stepstone.png"]


def test_employer_page_that_redirects_to_a_board_is_handoff(ctx, tmp_path):
    """A lead URL that lands on a board (a tracking link to StepStone): the opener clicks nothing there."""

    class Redirect(FakePage):
        def goto(self, url, wait_until=None, timeout=None):
            super().goto(url, wait_until, timeout)
            self.url = STEPSTONE

    page = Redirect([_f(0, "Was", id="was")])
    page.apply_buttons = ["Ich bin interessiert"]
    page.fields_after_apply = base_fields()
    res = apply(page, "https://careers.example.com/job/1", ctx, None, "", tmp_path, dry_run=False)
    assert res.status == "handoff" and res.reason == "stepstone_redirect"
    assert page.apply_clicks == 0 and not page.filled and not page.clicked_submit


def test_apply_link_on_the_employer_page_that_opens_a_board_is_handoff(ctx, tmp_path):
    """The employer's «Jetzt bewerben» leads to its StepStone ad: one click on the employer page, none on StepStone."""

    class ToBoard(FakePage):
        def _apply_click(self, text):
            super()._apply_click(text)
            self.url = STEPSTONE
            self.fields_after_apply = None

    page = ToBoard([])
    page.apply_buttons = ["Jetzt bewerben"]
    page.fields_after_apply = base_fields()
    res = apply(page, "https://careers.example.com/job/1", ctx, None, "", tmp_path, dry_run=False)
    assert res.status == "handoff" and res.reason == "stepstone_redirect"
    assert page.apply_clicked == ["Jetzt bewerben"] and not page.filled and not page.files


# ---- German file fields: the CV goes into a generic documents upload when there is no CV field

@pytest.fixture
def package(ctx, tmp_path):
    """The de_employment package: CV, Anschreiben and two Zeugnisse marked portal: true."""
    files = {n: tmp_path / n for n in ("Erika_Muster_CV.pdf", "01_Anschreiben.pdf", "Zeugnis_A.pdf", "Zeugnis_B.pdf")}
    for p in files.values():
        p.write_bytes(b"%PDF-1.4\n%%EOF\n")
    ctx.resume, ctx.cover_letter = files["Erika_Muster_CV.pdf"], files["01_Anschreiben.pdf"]
    ctx.attachments = list(files.values())
    return [str(p) for p in files.values()]


def test_documents_field_takes_the_cv_when_the_form_has_no_cv_field(ctx, tmp_path):
    for label in ("Anlagen", "Unterlagen", "Bewerbungsunterlagen", "Dokumente", "Dateien", "Anhänge"):
        page = FakePage(_no_file_fields() + [_f(3, label, type="file", id="docs")])
        rep = fill_form(page, ctx, llm=None, brief="")
        assert page.files == {label: [str(ctx.resume)]} and rep.cv_uploaded, label


def test_documents_field_that_takes_several_files_gets_the_whole_package(ctx, package):
    page = FakePage(_no_file_fields() + [_f(3, "Bewerbungsunterlagen", type="file", id="docs", multiple=True)])
    assert fill_form(page, ctx, llm=None, brief="").cv_uploaded
    assert page.files == {"Bewerbungsunterlagen": package}


def test_package_skips_the_cover_letter_when_the_form_has_its_own_field(ctx, package):
    page = FakePage(_no_file_fields() + [_f(3, "Anschreiben", type="file", id="cl"),
                                         _f(4, "Weitere Unterlagen", type="file", id="docs", multiple=True)])
    assert fill_form(page, ctx, llm=None, brief="").cv_uploaded
    assert page.files == {"Anschreiben": [package[1]], "Weitere Unterlagen": [package[0], package[2], package[3]]}


def test_combined_cv_and_documents_field_gets_the_whole_package(ctx, package):
    label = "Bewerbungsunterlagen (Lebenslauf, Anschreiben, Zeugnisse)"
    page = FakePage(_no_file_fields() + [_f(3, label, type="file", id="docs", required=True, multiple=True)])
    fill_form(page, ctx, llm=None, brief="")
    assert page.files == {label: package}


def test_single_cv_field_that_takes_several_files_gets_the_cv_only(ctx, package):
    page = FakePage(_no_file_fields() + [_f(3, "Lebenslauf", type="file", id="cv", multiple=True)])
    fill_form(page, ctx, llm=None, brief="")
    assert page.files == {"Lebenslauf": [package[0]]}
    # The words that count are the ones a person reads, not an id like cv_file.
    page = FakePage(_no_file_fields() + [_f(3, "Lebenslauf", type="file", id="cv_file", multiple=True)])
    fill_form(page, ctx, llm=None, brief="")
    assert page.files == {"Lebenslauf": [package[0]]}


def test_form_with_a_cv_field_keeps_the_old_rules(ctx, package):
    """«Lebenslauf» takes the CV; an optional «Weitere Unterlagen» the cover letter, a required one the CV."""
    page = FakePage(_no_file_fields() + [_f(3, "Lebenslauf", type="file", id="cv"),
                                         _f(4, "Weitere Unterlagen", type="file", id="docs", multiple=True)])
    fill_form(page, ctx, llm=None, brief="")
    assert page.files == {"Lebenslauf": [package[0]], "Weitere Unterlagen": [package[1]]}
    page = FakePage(_no_file_fields() + [_f(3, "Lebenslauf", type="file", id="cv"),
                                         _f(4, "Unterlagen", type="file", id="docs", required=True)])
    fill_form(page, ctx, llm=None, brief="")
    assert page.files == {"Lebenslauf": [package[0]], "Unterlagen": [package[0]]}


def test_second_documents_field_keeps_the_old_rule(ctx, package):
    page = FakePage(_no_file_fields() + [_f(3, "Unterlagen", type="file", id="d1"),
                                         _f(4, "Sonstige Dokumente", type="file", id="d2")])
    assert fill_form(page, ctx, llm=None, brief="").cv_uploaded
    assert page.files == {"Unterlagen": [package[0]], "Sonstige Dokumente": [package[1]]}


def test_lone_unlabelled_file_input_takes_the_cv_outside_ashby(ctx, tmp_path):
    page = FakePage(_no_file_fields() + [_f(3, "", type="file")])
    res = apply(page, "https://careers.example.com/job/1", ctx, None, "", tmp_path, dry_run=True)
    assert res.status == "dry_run" and page.files == {"": [str(ctx.resume)]}


def test_unlabelled_file_input_on_ashby_is_the_autofill(ctx, tmp_path):
    page = FakePage(_no_file_fields() + [_f(3, "", type="file")])
    res = apply(page, "https://jobs.ashbyhq.com/contractco/1", ctx, None, "", tmp_path, dry_run=True)
    assert res.status == "handoff" and res.reason == "no_cv_uploaded" and not page.files
    page = FakePage(_no_file_fields() + [_f(3, "", type="file", ashby_autofill=True)])
    res = apply(page, "https://careers.example.com/job/1", ctx, None, "", tmp_path, dry_run=True)
    assert res.status == "handoff" and res.reason == "no_cv_uploaded" and not page.files


def test_two_unlabelled_file_inputs_take_nothing(ctx, tmp_path):
    """Which of two unlabelled inputs is the photo is a guess: the operator decides."""
    page = FakePage(_no_file_fields() + [_f(3, "", type="file"), _f(4, "", type="file")])
    res = apply(page, "https://careers.example.com/job/1", ctx, None, "", tmp_path, dry_run=True)
    assert res.status == "handoff" and res.reason == "no_cv_uploaded" and not page.files


def test_photo_field_never_gets_a_document(ctx, package, tmp_path):
    page = FakePage(_no_file_fields() + [_f(3, "Bewerbungsfoto", type="file", id="photo"),
                                         _f(4, "Anlagen", type="file", id="docs")])
    res = apply(page, "https://careers.example.com/job/1", ctx, None, "", tmp_path, dry_run=True)
    assert res.status == "dry_run" and page.files == {"Anlagen": [package[0]]}


# ---- The avatar is never the CV field (SmartRecruiters, 30.10.2026)

SR_IMAGES = ".BMP,.GIF,.JFIF,.JPEG,.JPG,.PNG,.TIF,.TIFF,.WEBP"


def test_avatar_next_to_the_resume_dropzone_gets_nothing(ctx, tmp_path):
    """Both inputs unlabelled, as in a web-component form: the image-only avatar is a photo, the required
    dropzone the CV field. A required photo stays open for the operator."""
    page = FakePage(_no_file_fields() + [_f(3, "", type="file", required=True, accept=SR_IMAGES),
                                         _f(4, "", type="file", required=True, id="dropzone")])
    res = apply(page, "https://careers.example.com/job/1", ctx, None, "", tmp_path, dry_run=True)
    assert page.fields[3]["value"] == "" and page.fields[4]["value"] == str(ctx.resume)
    assert res.status == "handoff" and res.reason.startswith("open_required")
    # An optional avatar: the form is ready.
    page = FakePage(_no_file_fields() + [_f(3, "", type="file", accept=SR_IMAGES),
                                         _f(4, "", type="file", required=True, id="dropzone")])
    res = apply(page, "https://careers.example.com/job/1", ctx, None, "", tmp_path, dry_run=True)
    assert res.status == "dry_run" and page.fields[3]["value"] == "" and page.fields[4]["value"] == str(ctx.resume)


def test_avatar_is_not_a_lone_upload_when_another_file_input_exists(ctx, tmp_path):
    """The resume dropzone's input was not seen as visible: the unlabelled avatar is not «the only upload»."""
    page = FakePage(_no_file_fields() + [_f(3, "", type="file"),
                                         _f(4, "Resume", type="file", required=True, visible=False, id="cv")])
    res = apply(page, "https://careers.example.com/job/1", ctx, None, "", tmp_path, dry_run=True)
    assert res.status == "handoff" and res.reason == "no_cv_uploaded" and not page.files


def test_lone_image_only_upload_gets_nothing(ctx, tmp_path):
    page = FakePage(_no_file_fields() + [_f(3, "", type="file", accept=SR_IMAGES)])
    res = apply(page, "https://careers.example.com/job/1", ctx, None, "", tmp_path, dry_run=True)
    assert res.status == "handoff" and res.reason == "no_cv_uploaded" and not page.files


def test_cv_never_goes_into_an_input_that_does_not_accept_it(ctx, tmp_path):
    page = FakePage(_no_file_fields() + [_f(3, "Resume", type="file", required=True, accept=".doc,.docx", id="cv")])
    res = apply(page, "https://careers.example.com/job/1", ctx, None, "", tmp_path, dry_run=True)
    assert res.status == "handoff" and not page.files


def test_file_input_that_changed_under_the_index_gets_nothing(ctx, tmp_path):
    """The read saw the dropzone at this index, the live element is now an image-only avatar: no upload."""
    page = FakePage(_no_file_fields() + [_f(3, "", type="file", required=True)])
    page.attr_override[("", "accept")] = SR_IMAGES
    res = apply(page, "https://careers.example.com/job/1", ctx, None, "", tmp_path, dry_run=True)
    assert res.status == "handoff" and not page.files


def test_documents_only_form_is_submitted_live(ctx, tmp_path):
    page = FakePage(_no_file_fields() + [_f(3, "Anlagen", type="file", id="docs")], buttons=("Absenden",))
    page.probe_after_submit = {"submit_visible": False, "challenge_visible": False, "invalid_count": 0,
                               "error_texts": [], "text": "Thank you for your application"}
    res = apply(page, "https://careers.example.com/job/1", ctx, None, "", tmp_path, dry_run=False, wait_ms=0)
    assert res.status == "submitted" and page.files == {"Anlagen": [str(ctx.resume)]}


def test_email_select_with_another_own_address_is_kept(ctx):
    """02.11.2026: LinkedIn offered only the client's job-search address, the profile has the main one;
    every Easy Apply went to hand-off. Any of the client's own addresses is a valid answer."""
    ctx = replace(ctx, own_emails=("erika.jobs@example.com",))
    page = ScopedPage([
        _f(0, "E-Mail-Adresse*", type="select", tag="select", required=True, value="erika.jobs@example.com",
           options=["erika.jobs@example.com"], id="em"),
    ])
    rep = fill_form(page, ctx, llm=None, brief="", root="dialog[open]")
    assert page.selected == {} and rep.open_required == []


def test_separate_house_number_field_splits_the_street(ctx):
    """Abacus 02.11.2026: «Strasse*» and «Hausnummer*» are two fields; the filler put the whole
    «Musterstraße 1» into both. With a house-number field the street gets only its name."""
    page = FakePage([_f(0, "Strasse*", required=True, id="st"), _f(1, "Hausnummer*", required=True, id="hn")])
    rep = fill_form(page, ctx, llm=None, brief="")
    assert page.filled == {"Strasse*": "Musterstraße", "Hausnummer*": "1"} and rep.open_required == []


def test_combined_street_field_keeps_the_house_number(ctx):
    page = FakePage([_f(0, "Straße & Hausnr.*", required=True, id="st")])
    fill_form(page, ctx, llm=None, brief="")
    assert page.filled == {"Straße & Hausnr.*": "Musterstraße 1"}


class WizardPage(FakePage):
    """A multi-step form: each step has its fields and buttons; the step's next button shows the next
    one, a final button (or a next button on the last step, like Abacus) shows `done_text`."""

    def __init__(self, steps, done_text="Vielen Dank für deine Bewerbung", stuck=False):
        super().__init__(steps[0][0], body="Bewerbung", buttons=steps[0][1])
        self.steps = steps
        self.k = 0
        self.done_text = done_text
        self.stuck = stuck
        self.step_clicks: list[str] = []

    def _button_click(self, text):
        self.step_clicks.append(text)
        if self.stuck:
            self.probe_after_submit = {"submit_visible": True, "challenge_visible": False, "invalid_count": 1,
                                       "error_texts": ["Bitte füllen Sie alle Pflichtfelder aus"], "text": "Bewerbung"}
            self.clicked_submit = True
            return
        if self.k + 1 < len(self.steps):
            self.k += 1
            self.fields, self.buttons = self.steps[self.k]
            return
        self.clicked_submit = True
        self.probe_after_submit = {"submit_visible": False, "challenge_visible": False, "invalid_count": 0,
                                   "error_texts": [], "text": self.done_text}


def _wizard(last_button="Bewerbung absenden"):
    return [
        ([_f(0, "Vorname", required=True), _f(1, "E-Mail", type="email", required=True)], ["Zurück", "Weiter"]),
        ([_f(0, "Telefon", required=True)], ["Zurück", "zum letzten Schritt"]),
        ([_f(0, "Lebenslauf", type="file", required=True)], ["Zurück", last_button]),
    ]


def test_wizard_steps_through_to_the_submit(ctx, tmp_path):
    """softgarden/Abacus 02.11.2026: step 1 had no submit button, the worker stopped with
    no_submit_button; the CV sat on the last step (Abacus), so no_cv_uploaded came first."""
    page = WizardPage(_wizard())
    res = apply(page, "https://careers.example.com/job/1", ctx, None, "", tmp_path, dry_run=False, wait_ms=0)
    assert res.status == "submitted" and page.step_clicks == ["Weiter", "zum letzten Schritt", "Bewerbung absenden"]
    names = [p.rsplit("/", 1)[-1] for p in res.screenshots]
    assert names[:3] == ["apply_form_filled.png", "apply_form_step2.png", "apply_form_step3.png"]


def test_wizard_whose_last_next_button_sends(ctx, tmp_path):
    """Abacus: the third step's «Weiter» is the send button; the confirmation after it is the result."""
    page = WizardPage(_wizard(last_button="Weiter"), done_text="Bewerbung erfolgreich abgeschickt. Vielen Dank für deine Bewerbung")
    res = apply(page, "https://careers.example.com/job/1", ctx, None, "", tmp_path, dry_run=False, wait_ms=0)
    assert res.status == "submitted" and page.step_clicks == ["Weiter", "zum letzten Schritt", "Weiter"]


def test_wizard_that_does_not_advance_is_a_validation_handoff(ctx, tmp_path):
    page = WizardPage(_wizard(), stuck=True)
    res = apply(page, "https://careers.example.com/job/1", ctx, None, "", tmp_path, dry_run=False, wait_ms=0)
    assert res.status == "handoff" and res.reason.startswith("validation: Bitte füllen")
    assert page.step_clicks == ["Weiter"]


def test_wizard_in_shadow_stops_at_the_first_next_button(ctx, tmp_path):
    page = WizardPage(_wizard())
    res = apply(page, "https://careers.example.com/job/1", ctx, None, "", tmp_path, dry_run=True)
    assert res.status == "dry_run" and "step 1" in res.reason and page.step_clicks == []


def test_next_step_texts():
    from bot.apply.ats_form import pick_next

    def cand(text, **kw):
        return {"i": 0, "text": text, "submit_type": False, "in_form": True, "other_form": False, "disabled": False, **kw}
    for t in ("Weiter", "zum nächsten Schritt", "Zum letzten Schritt", "Next", "Continue", "Hochladen und weiter",
              "Weiter zur Bewerbungsvorschau"):
        assert pick_next([cand(t)]) is not None, t
    for t in ("Weiter mit XING", "Zurück", "Weiter einkaufen ohne Anmeldung bei Google", "Weiterempfehlen", "Bewerben"):
        assert pick_next([cand(t)]) is None, t
    assert pick_next([cand("Weiter", other_form=True)]) is None
