"""Адаптер GULP на фейковій сторінці: проєкт і форма CXS (Salesforce).

FakeGulpPage тримає HTML сторінки проєкту і HTML форми. Перехід на
`<проєкт>/bewerbung-mit-profil` (прямий goto чи клік по `applyButton`) відкриває
форму; без форми (`form_html=None`) сторінка лишається на проєкті. Поля форми
шукаються за маркерами в розмітці; поле CV живе в iframe і доступне лише через
`frame_locator(...).locator(...)`; після `set_input_files` ім'я файлу з'являється
в body iframe (`cv_shows="frame"`), на сторінці (`"page"`) чи ніде (`None`).
Клік по `#cxsApplyButton` позначає `submitted` і міняє body: фраза подяки
(`confirm=True`), довільний `body_after_submit` чи текст блоку applications-info
(`info_after_submit`).
"""
import html as _html
import re
from pathlib import Path

import pytest

from bot.apply import gulp
from bot.apply.gulp import (
    APPLICATIONS_INFO_SELECTOR,
    APPLY_BUTTON_SELECTOR,
    APPLY_PATH,
    CURRENCY_SELECTOR,
    CV_BODY_SELECTOR,
    CV_FRAME_SELECTOR,
    CV_INPUT_SELECTOR,
    MOTIVATION_MAX,
    MOTIVATION_SELECTOR,
    RATE_SELECTOR,
    SUBMIT_SELECTOR,
    apply,
    page_state,
)

FIX = Path(__file__).parent / "fixtures" / "platforms"
URL = "https://www.gulp.de/gulp2/g/projekte/agentur/C01234567"
CV = Path("/tmp/cv/Erika_Mustermann_CV.pdf")
THANKS = "Thank you for your application! We will get back to you."
LOGGED_IN = "Dashboard Erika Mustermann "


def fixture_html(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


def _text_of(html: str) -> str:
    html = re.sub(r"<(script|style)\b.*?</\1>", " ", html, flags=re.DOTALL)
    return " ".join(_html.unescape(re.sub(r"<[^>]+>", " ", html)).split())


class Loc:
    def __init__(self, page, key, n):
        self.page, self.key, self.n = page, key, n

    @property
    def first(self):
        return self

    def count(self):
        return self.n

    def _need(self):
        if not self.n:
            raise TimeoutError(f"element {self.key} not found")

    def fill(self, value, timeout=None):
        self._need()
        if self.page.fill_raises:
            raise RuntimeError("fill exploded")
        self.page.filled[self.key] = value

    def inner_text(self, timeout=None):
        self._need()
        if self.key == "cv_body":
            return self.page.frame_text
        return self.page.info_text()

    def wait_for(self, state=None, timeout=None):
        self._need()

    def select_option(self, value=None, label=None, timeout=None):
        self._need()
        self.page.selected[self.key] = value if value is not None else label

    def set_input_files(self, files, timeout=None):
        self._need()
        self.page.uploaded = list(files) if isinstance(files, list) else [files]
        name = Path(self.page.uploaded[0]).name
        if self.page.cv_shows == "frame":
            self.page.frame_text = f"CV {name} X"
        elif self.page.cv_shows == "page":
            self.page.body += f" CV {name} X"

    def click(self, timeout=None, force=False):
        self._need()
        self.page.clicks.append(self.key)
        if self.key == "apply":
            self.page.goto(self.page.url.rstrip("/") + APPLY_PATH)
        elif self.key == "submit":
            self.page.on_submit()


class FrameLoc:
    def __init__(self, page):
        self.page = page

    def locator(self, sel):
        if sel == CV_BODY_SELECTOR:
            return Loc(self.page, "cv_body", int(self.page._present("cv")))
        assert sel == CV_INPUT_SELECTOR, sel
        return Loc(self.page, "cv", int(self.page._present("cv")))


class FakeGulpPage:
    def __init__(self, project_html, form_html, confirm=False, body_after_submit=None,
                 logged_in=True, click_raises=False, goto_raises=False, screenshot_raises=False,
                 cv_input=True, cv_shows="frame", info_after_submit=None, fill_raises=False,
                 wait_raises=False):
        self.project_html = project_html
        self.form_html = form_html
        self.html = ""
        self.url = ""
        self.body = ""
        self.confirm = confirm
        self.body_after_submit = body_after_submit
        self.logged_in = logged_in
        self.click_raises = click_raises
        self.goto_raises = goto_raises
        self.screenshot_raises = screenshot_raises
        self.cv_input = cv_input
        self.cv_shows = cv_shows
        self.frame_text = "Documents CV Upload"
        self.info_after_submit = info_after_submit
        self.info_override = None
        self.fill_raises = fill_raises
        self.wait_raises = wait_raises
        self.submitted = False
        self.filled: dict[str, str] = {}
        self.selected: dict[str, str] = {}
        self.uploaded: list[str] = []
        self.clicks: list[str] = []
        self.gotos: list[str] = []
        self.shots: list[str] = []

    def _show(self, html):
        self.html = html
        self.body = (LOGGED_IN if self.logged_in else "") + _text_of(html)

    def _present(self, key):
        marker = {
            "apply": 'data-testid="applyButton"',
            "motivation": 'name="cxsrec__motivation__c"',
            "rate": 'name="rsd_purchaseprice__c"',
            "currency": "cxsFieldName_rsd_currency__c",
            "submit": 'id="cxsApplyButton"',
            "cv": "cxsFileUploadFrame",
            "info": 'data-testid="applications-info"',
        }[key]
        if key == "cv" and not self.cv_input:
            return False
        if key == "info":
            return self.info_override is not None or marker in self.html
        return marker in self.html and not self.submitted

    # Playwright API
    def goto(self, url, wait_until=None, timeout=None):
        if self.goto_raises:
            raise RuntimeError("net::ERR_NAME_NOT_RESOLVED")
        self.gotos.append(url)
        if url.endswith(APPLY_PATH) and self.form_html is not None:
            self.url = url
            self._show(self.form_html)
        else:
            # Без форми GULP лишає сторінку проєкту.
            self.url = url.removesuffix(APPLY_PATH)
            self._show(self.project_html)

    def wait_for_timeout(self, ms):
        pass

    def wait_for_selector(self, sel, timeout=None, state=None):
        if self.wait_raises:
            raise TimeoutError(f"waiting for {sel} timed out")

    def info_text(self):
        if self.info_override is not None:
            return self.info_override
        m = re.search(r'<p[^>]*data-testid="applications-info"[^>]*>(.*?)</p>', self.html, re.DOTALL)
        return _text_of(m.group(1)) if m else ""

    def inner_text(self, sel):
        assert sel == "body"
        return self.body

    def screenshot(self, path=None, full_page=False):
        if self.screenshot_raises:
            raise RuntimeError("screenshot exploded")
        self.shots.append(Path(path).name)

    def locator(self, sel):
        keys = {
            APPLY_BUTTON_SELECTOR: "apply",
            MOTIVATION_SELECTOR: "motivation",
            RATE_SELECTOR: "rate",
            CURRENCY_SELECTOR: "currency",
            SUBMIT_SELECTOR: "submit",
            APPLICATIONS_INFO_SELECTOR: "info",
        }
        if sel not in keys:
            # Ім'я, прізвище, email: адаптер їх не чіпає взагалі.
            raise AssertionError(f"unexpected selector: {sel}")
        return Loc(self, keys[sel], int(self._present(keys[sel])))

    def frame_locator(self, sel):
        assert sel == CV_FRAME_SELECTOR, sel
        return FrameLoc(self)

    def on_submit(self):
        self.submitted = True
        if self.click_raises:
            raise RuntimeError("click detached")
        if self.info_after_submit is not None:
            self.info_override = self.info_after_submit
        if self.body_after_submit is not None:
            self.body = self.body_after_submit
        elif self.confirm:
            self.body = LOGGED_IN + THANKS


@pytest.fixture(autouse=True)
def _no_settle(monkeypatch):
    # settle_body міряє реальний час; у фейку чекати нема чого.
    monkeypatch.setattr(gulp, "SETTLE_MS", 0)


def _page(form="gulp_apply_form.html", **kw):
    return FakeGulpPage(fixture_html("gulp_project.html"), form and fixture_html(form), **kw)


def test_page_state():
    assert page_state(URL, "powered by … Registrieren Anmelden") == "login_required"
    assert page_state(URL, "GULP Sign in Login") == "login_required"
    assert page_state(URL, "Dashboard Erika Mustermann Dieses Projekt ist nicht mehr verfügbar") == "closed"
    assert page_state(URL, "Dashboard Erika Mustermann This project is no longer available") == "closed"
    assert page_state(URL, "Dashboard Erika Mustermann Das Projekt ist bereits vergeben") == "closed"
    assert page_state(URL, "Dashboard Erika Mustermann Apply now Job ID C1") == "open"
    # Залогінений хедер має «Dashboard»: слово Login деінде в тексті не стіна.
    assert page_state(URL, "Dashboard Erika Mustermann Login history Job ID C1") == "open"


def test_page_state_on_the_fixture_is_open():
    text = LOGGED_IN + _text_of(fixture_html("gulp_project.html"))
    assert page_state(URL, text) == "open"


@pytest.mark.parametrize("form", ["gulp_apply_form.html", "gulp_apply_form_de.html"])
def test_happy_path(form, tmp_path):
    page = _page(form, confirm=True)
    res = apply(page, URL, "Motivation text", "105", "EUR", CV, tmp_path, dry_run=False)
    assert res.status == "submitted" and res.portal == "gulp" and res.url == URL
    assert page.url.endswith("/bewerbung-mit-profil")
    assert page.filled["motivation"] == "Motivation text" and page.filled["rate"] == "105"
    assert page.selected["currency"] == "EUR" and page.uploaded == [str(CV)]
    assert page.submitted and "thank you for your application" in res.confirmation_text.lower()
    assert "apply_form_filled.png" in page.shots and "apply_confirmation.png" in page.shots


def test_german_confirmation_phrase(tmp_path):
    page = _page("gulp_apply_form_de.html", body_after_submit="Vielen Dank für Ihre Bewerbung!")
    res = apply(page, URL, "m", "105", "EUR", CV, tmp_path, False, wait_ms=3000)
    assert res.status == "submitted"


def test_applied_marker_on_project_page_confirms(tmp_path):
    page = _page(body_after_submit=LOGGED_IN + "Senior Android Engineer Job ID C01234567",
                 info_after_submit="You have already applied for this project.")
    res = apply(page, URL, "m", "105", "EUR", CV, tmp_path, False, wait_ms=3000)
    assert res.status == "submitted"


def test_applicant_counter_is_not_a_confirmation(tmp_path):
    # «17 freelancers have applied» стоїть на кожному проєкті: це не підтвердження.
    page = _page(body_after_submit=LOGGED_IN + _text_of(fixture_html("gulp_project.html")))
    res = apply(page, URL, "m", "105", "EUR", CV, tmp_path, False, wait_ms=3000)
    assert res.status == "handoff" and res.reason == "no_confirmation"


def test_dry_run_and_form_not_found(tmp_path):
    page = _page()
    res = apply(page, URL, "m", "105", "EUR", CV, tmp_path, dry_run=True)
    assert res.status == "dry_run" and not page.submitted
    page = _page(None)   # форма не відкрилась
    res = apply(page, URL, "m", "105", "EUR", CV, tmp_path, False)
    assert res.status == "handoff" and res.reason == "form_not_found" and not page.submitted


def test_dry_run_fills_but_never_uploads_the_cv(tmp_path):
    # onchange поля CV одразу вантажить файл у GULP: dry run не передає нічого.
    page = _page()
    res = apply(page, URL, "Motivation text", "105", "EUR", CV, tmp_path, dry_run=True)
    assert res.status == "dry_run"
    assert page.uploaded == [] and "submit" not in page.clicks
    assert page.filled == {"motivation": "Motivation text", "rate": "105"}
    assert page.selected == {"currency": "EUR"}
    assert "uploaded 0 (dry run skips the CV upload)" in res.reason
    assert "apply_form_filled.png" in page.shots


def test_goes_to_the_form_url_and_does_not_double_the_path(tmp_path):
    page = _page(confirm=True)
    apply(page, URL + "/", "m", "105", "EUR", CV, tmp_path, False)
    assert page.gotos[0] == URL + "/" and page.gotos[-1] == URL + APPLY_PATH
    page = _page(confirm=True)
    res = apply(page, URL + APPLY_PATH, "m", "105", "EUR", CV, tmp_path, False)
    assert res.status == "submitted" and all(g.count(APPLY_PATH) <= 1 for g in page.gotos)


def test_motivation_is_cut_at_a_word_boundary(tmp_path):
    note = ("Kotlin Android " * 400).strip()        # ~6000 символів
    page = _page(confirm=True)
    apply(page, URL, note, "105", "EUR", CV, tmp_path, False)
    sent = page.filled["motivation"]
    assert len(sent) <= MOTIVATION_MAX and note.startswith(sent)
    assert sent.split()[-1] in ("Kotlin", "Android") and not sent.endswith(" ")


def test_login_required(tmp_path):
    page = FakeGulpPage("<html><body>powered by GULP Registrieren Anmelden</body></html>",
                        fixture_html("gulp_apply_form.html"), logged_in=False)
    res = apply(page, URL, "m", "105", "EUR", CV, tmp_path, False)
    assert res.status == "login_required" and not page.submitted and page.filled == {}


def test_closed_project(tmp_path):
    page = FakeGulpPage("<html><body>Senior Android Engineer Dieses Projekt ist nicht mehr verfügbar</body></html>",
                        fixture_html("gulp_apply_form.html"))
    res = apply(page, URL, "m", "105", "EUR", CV, tmp_path, False)
    assert res.status == "closed" and not page.submitted and page.filled == {}


def test_already_applied_project_is_closed(tmp_path):
    project = fixture_html("gulp_project.html").replace(
        "freelancers have applied for this project!",
        "freelancers have applied for this project! You have already applied for this project.")
    page = FakeGulpPage(project, fixture_html("gulp_apply_form.html"))
    res = apply(page, URL, "m", "105", "EUR", CV, tmp_path, False)
    assert res.status == "closed" and res.reason == "already applied"
    assert not page.submitted and page.filled == {} and len(page.gotos) == 1


def test_no_confirmation_is_handoff(tmp_path):
    page = _page()
    res = apply(page, URL, "m", "105", "EUR", CV, tmp_path, False, wait_ms=3000)
    assert page.submitted and res.status == "handoff" and res.reason == "no_confirmation"


def test_click_that_raises_is_error_after_submit(tmp_path):
    page = _page(click_raises=True)
    res = apply(page, URL, "m", "105", "EUR", CV, tmp_path, False)
    assert res.status == "handoff" and res.reason.startswith("error_after_submit: RuntimeError")


def test_missing_cv_input_is_form_not_found(tmp_path):
    page = _page(cv_input=False)
    res = apply(page, URL, "m", "105", "EUR", CV, tmp_path, False)
    assert res.status == "handoff" and res.reason == "form_not_found" and not page.submitted


def test_goto_failure_is_failed(tmp_path):
    page = _page(goto_raises=True)
    res = apply(page, URL, "m", "105", "EUR", CV, tmp_path, False)
    assert res.status == "failed" and res.reason.startswith("goto: RuntimeError")


def test_screenshot_failure_does_not_change_the_result(tmp_path):
    page = _page(confirm=True, screenshot_raises=True)
    res = apply(page, URL, "m", "105", "EUR", CV, tmp_path, False)
    assert res.status == "submitted" and res.screenshots == []


PROSE = ("Ideally you have applied Kotlin Multiplatform in production. "
         "Wer sich bereits beworben haben, bitte nicht erneut.")


def test_prose_in_the_description_is_not_already_applied(tmp_path):
    # Фрази в описі проєкту не є маркером «ви вже подались»: guard дивиться лише applications-info.
    project = fixture_html("gulp_project.html").replace("Project Description", "Project Description " + PROSE)
    assert page_state(URL, LOGGED_IN + _text_of(project)) == "open"
    page = FakeGulpPage(project, fixture_html("gulp_apply_form.html"), confirm=True)
    res = apply(page, URL, "m", "105", "EUR", CV, tmp_path, False)
    assert res.status == "submitted" and page.submitted


def test_prose_after_submit_is_not_a_confirmation(tmp_path):
    page = _page(body_after_submit=LOGGED_IN + _text_of(fixture_html("gulp_project.html")) + " " + PROSE)
    res = apply(page, URL, "m", "105", "EUR", CV, tmp_path, False, wait_ms=3000)
    assert res.status == "handoff" and res.reason == "no_confirmation"


def test_applied_re_is_anchored():
    assert gulp.APPLIED_RE.search("You have already applied for this project.")
    assert gulp.APPLIED_RE.search("You've applied for this job")
    assert gulp.APPLIED_RE.search("Sie haben sich bereits auf dieses Projekt beworben")
    assert not gulp.APPLIED_RE.search(PROSE)
    assert not gulp.APPLIED_RE.search("17 freelancers have applied for this project!")


def test_cv_upload_never_confirmed_is_handoff_without_submit(tmp_path):
    page = _page(confirm=True, cv_shows=None)
    res = apply(page, URL, "m", "105", "EUR", CV, tmp_path, False)
    assert res.status == "handoff" and res.reason == "cv_upload_unconfirmed"
    assert page.uploaded == [str(CV)] and "submit" not in page.clicks and not page.submitted
    assert "apply_cv_unconfirmed.png" in page.shots


def test_cv_name_on_the_main_page_confirms_the_upload(tmp_path):
    page = _page(confirm=True, cv_shows="page")
    res = apply(page, URL, "m", "105", "EUR", CV, tmp_path, False)
    assert res.status == "submitted"


def test_rendered_project_without_apply_button_is_form_not_found(tmp_path):
    project = fixture_html("gulp_project.html").replace('data-testid="applyButton"', 'data-testid="other"')
    page = FakeGulpPage(project, fixture_html("gulp_apply_form.html"))
    res = apply(page, URL, "m", "105", "EUR", CV, tmp_path, False)
    assert res.status == "handoff" and res.reason == "form_not_found: no apply button"
    assert len(page.gotos) == 1 and not page.submitted


def test_already_applied_marker_on_the_form_page_is_closed(tmp_path):
    form = fixture_html("gulp_apply_form.html").replace(
        "Personal information", "Personal information You have already applied for this project.")
    page = FakeGulpPage(fixture_html("gulp_project.html"), form)
    res = apply(page, URL, "m", "105", "EUR", CV, tmp_path, False)
    assert res.status == "closed" and res.reason == "already applied" and page.filled == {}


def test_wait_for_selector_timeout_is_not_an_error(tmp_path):
    page = _page(confirm=True, wait_raises=True)
    assert apply(page, URL, "m", "105", "EUR", CV, tmp_path, False).status == "submitted"
    page = _page(None, wait_raises=True)
    res = apply(page, URL, "m", "105", "EUR", CV, tmp_path, False)
    assert res.status == "handoff" and res.reason == "form_not_found"


def test_fill_exception_before_click_is_error(tmp_path):
    page = _page(fill_raises=True)
    res = apply(page, URL, "m", "105", "EUR", CV, tmp_path, False)
    assert res.status == "handoff" and res.reason.startswith("error: RuntimeError")
    assert not page.submitted
