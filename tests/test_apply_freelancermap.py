"""Адаптер freelancermap на фейковій сторінці проєкту.

FakeProjectPage бере HTML-фікстуру і за регулярками знає, чи є форма
(`textarea#cover-letter`), поле файлу, чекбокси й кнопка відправки. Клік по
`contact-send-application` позначає `submitted` і міняє текст body: фраза
підтвердження (`confirm=True`), новий лічильник (`counter_after_submit`),
текст помилки (`error_after_submit=True`) або довільний `body_after_submit`.
Після кліку кнопка зникає, якщо не `button_after_submit=True`. `evaluate` на
полі файлу віддає кількість прикріплених файлів (0 при `attach_fails=True`).
"""
import html as _html
import re
from pathlib import Path

import pytest

from bot.apply import freelancermap
from bot.apply.freelancermap import (
    AI_BUTTON_SELECTOR,
    COVER_SELECTOR,
    EMAIL_CHECKBOX_SELECTOR,
    FILE_INPUT_SELECTOR,
    PHONE_CHECKBOX_SELECTOR,
    SUBMIT_SELECTOR,
    VCARD_SELECTOR,
    apply,
    page_state,
    remaining_quota,
)

FIX = Path(__file__).parent / "fixtures" / "platforms"
URL = "https://www.freelancermap.de/nproj/1234567.html"
SUCCESS = "Ihre Bewerbung wurde erfolgreich versendet."
PLATFORM_ERROR = "Es ist ein Fehler aufgetreten. Bitte versuchen Sie es später erneut."


def fixture_html(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


def _text_of(html: str) -> str:
    html = re.sub(r"<(script|style)\b.*?</\1>", " ", html, flags=re.DOTALL)
    return " ".join(_html.unescape(re.sub(r"<[^>]+>", " ", html)).split())


def _unchecked(html: str) -> str:
    """Та сама форма, але E-Mail і телефон не відмічені."""
    return html.replace('checked="" name="sendEmail"', 'name="sendEmail"').replace(
        'checked="" name="sendPhone"', 'name="sendPhone"')


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
        if self.page.fail_on_fill:
            raise RuntimeError("fill exploded")
        self.page.filled[self.key] = value

    def set_input_files(self, files, timeout=None):
        self._need()
        self.page.uploaded = list(files) if isinstance(files, list) else [files]

    def evaluate(self, js):
        self._need()
        assert self.key == "file" and "files" in js, f"unexpected evaluate on {self.key}: {js}"
        return 0 if self.page.attach_fails else len(self.page.uploaded)

    def is_checked(self, timeout=None):
        self._need()
        return self.page.checked[self.key]

    def check(self, timeout=None, force=False):
        self._need()
        self.page.check_calls.append(self.key)
        if self.key == "vcard":
            self.page.vcard_checked = True
        else:
            self.page.checked[self.key] = True

    def click(self, timeout=None, force=False):
        self._need()
        self.page.clicks.append(self.key)
        if self.key == "submit":
            self.page.on_submit()
        elif self.key == "vcard":
            self.page.vcard_checked = not self.page.vcard_checked


class FakeProjectPage:
    def __init__(self, html, url=None, confirm=False, counter_after_submit=None,
                 error_after_submit=False, body_after_submit=None, click_raises=False,
                 goto_raises=False, screenshot_raises=False, button_after_submit=False,
                 attach_fails=False):
        self.html = html
        self.redirect = url
        self.url = ""
        self.body = _text_of(html)
        self.confirm = confirm
        self.counter_after_submit = counter_after_submit
        self.error_after_submit = error_after_submit
        self.body_after_submit = body_after_submit
        self.click_raises = click_raises
        self.goto_raises = goto_raises
        self.screenshot_raises = screenshot_raises
        self.button_after_submit = button_after_submit
        self.attach_fails = attach_fails
        self.fail_on_fill = False
        self.submitted = False
        self.filled: dict[str, str] = {}
        self.uploaded: list[str] = []
        self.checked = {
            "email": bool(re.search(r'checked="" name="sendEmail"', html)),
            "phone": bool(re.search(r'checked="" name="sendPhone"', html)),
        }
        self.check_calls: list[str] = []
        self.vcard_checked = False
        self.clicks: list[str] = []
        self.shots: list[str] = []
        self.waited = 0

    # Що є в розмітці
    def _present(self, key):
        marker = {
            "cover-letter": 'id="cover-letter"',
            "file": 'class="inputfile',
            "email": 'name="sendEmail"',
            "phone": 'name="sendPhone"',
            "vcard": 'name="vcard[]"',
            "submit": 'data-id="contact-send-application"',
            "ai": 'data-id="ai-application-button"',
        }[key]
        if self.submitted and not (key == "submit" and self.button_after_submit):
            return False
        return marker in self.html

    # Playwright API
    def goto(self, url, wait_until=None, timeout=None):
        if self.goto_raises:
            raise RuntimeError("net::ERR_NAME_NOT_RESOLVED")
        self.url = self.redirect or url.replace("/nproj/1234567.html", "/projekt/senior-android-engineer")

    def wait_for_timeout(self, ms):
        self.waited += ms

    def wait_for_selector(self, sel, timeout=None, state=None):
        pass

    def inner_text(self, sel):
        assert sel == "body"
        return self.body

    def content(self):
        return self.html

    def screenshot(self, path=None, full_page=False):
        if self.screenshot_raises:
            raise RuntimeError("screenshot exploded")
        self.shots.append(Path(path).name)

    def locator(self, sel):
        keys = {
            COVER_SELECTOR: "cover-letter",
            FILE_INPUT_SELECTOR: "file",
            EMAIL_CHECKBOX_SELECTOR: "email",
            PHONE_CHECKBOX_SELECTOR: "phone",
            VCARD_SELECTOR: "vcard",
            SUBMIT_SELECTOR: "submit",
            AI_BUTTON_SELECTOR: "ai",
        }
        if sel not in keys:
            raise AssertionError(f"unexpected selector: {sel}")
        key = keys[sel]
        if key in ("ai", "vcard"):
            # Адаптер не має навіть шукати ці елементи.
            raise AssertionError(f"adapter touched {key}")
        return Loc(self, key, int(self._present(key)))

    def on_submit(self):
        if self.click_raises:
            self.submitted = True
            raise RuntimeError("click detached")
        self.submitted = True
        if self.body_after_submit is not None:
            self.body = self.body_after_submit
        elif self.confirm:
            self.body = "Bewerben " + SUCCESS
        elif self.counter_after_submit:
            self.body = re.sub(r"Monat:\s*\d+/\d+", f"Monat: {self.counter_after_submit}", self.body)
        elif self.error_after_submit:
            self.body = self.body + " " + PLATFORM_ERROR


@pytest.fixture(autouse=True)
def _no_settle(monkeypatch):
    # settle_body міряє реальний час; у фейку чекати нема чого.
    monkeypatch.setattr(freelancermap, "SETTLE_MS", 0)


@pytest.fixture
def files(tmp_path):
    return tmp_path / "CV_Contract.pdf", tmp_path / "Projektliste.pdf"


# --- remaining_quota / page_state ---

def test_remaining_quota():
    assert remaining_quota("… Restliche Bewerbungen für diesen Monat: 7/10 …") == 7
    assert remaining_quota("Restliche Bewerbungen für diesen Monat:  10/10") == 10
    assert remaining_quota("Noch 3/3 kostenlose KI-Bewerbungstexte verfügbar.") is None
    assert remaining_quota("kein Zähler") is None


@pytest.mark.parametrize("url, text, has_form, expected", [
    ("https://www.freelancermap.de/login?redirect=x", "", False, "login_required"),
    (URL, "Das Bewerbungsformular ist nur für eingeloggte Nutzer verfügbar. Einloggen & bewerben.",
     False, "login_required"),
    (URL, ("Keine Kontaktanfrage möglich Sie können sich auf das Projekt nicht bewerben, "
           "da es bereits geschlossen wurde."), False, "closed"),
    (URL, "Bewerbungsphase wurde beendet", True, "closed"),
    (URL, "Bewerbung erfolgreich abgeschickt am 27.10.2026 Zum Postfach", False, "closed"),
    (URL, "Bewerbung erfolgreich abgeschickt am 27.10.2026", True, "closed"),
    (URL, "Senior Android Engineer Beschreibung", False, "closed"),
    (URL, "Bewerben Bewerbungslimit erreicht", True, "quota"),
    (URL, "Bewerben Bewerbungslimit erreicht", False, "quota"),
    (URL, "Restliche Bewerbungen für diesen Monat: 0/10", True, "quota"),
    (URL, "Restliche Bewerbungen für diesen Monat: 3/10", True, "open"),
    (URL, "Bewerben Anschreiben *", True, "open"),
])
def test_page_state(url, text, has_form, expected):
    assert page_state(url, text, has_form) == expected


def test_page_state_on_fixtures():
    project = fixture_html("freelancermap_project.html")
    closed = fixture_html("freelancermap_closed.html")
    assert page_state(URL, _text_of(project), True) == "open"
    assert page_state(URL, _text_of(closed), False) == "closed"


# --- apply ---

def test_quota_zero_holds_without_submit(tmp_path, files):
    page = FakeProjectPage(fixture_html("freelancermap_project.html").replace("10/10", "0/10"))
    res = apply(page, URL, "Anschreiben", [files[0]], tmp_path, dry_run=False)
    assert (res.status, res.reason, res.portal) == ("quota", "platform_quota", "freelancermap")
    assert not page.submitted and page.filled == {} and page.uploaded == []


def test_limit_text_is_quota(tmp_path, files):
    html = fixture_html("freelancermap_project.html").replace(
        "Bewerbung verschicken</button>", "Bewerbung verschicken</button><p>Bewerbungslimit erreicht</p>")
    page = FakeProjectPage(html)
    res = apply(page, URL, "x", [files[0]], tmp_path, dry_run=False)
    assert res.status == "quota" and not page.submitted


def test_happy_path_fills_uploads_and_submits(tmp_path, files):
    cv, projektliste = files
    page = FakeProjectPage(fixture_html("freelancermap_project.html"), confirm=True)
    res = apply(page, URL, "Sehr geehrte Damen und Herren …", [cv, projektliste], tmp_path, dry_run=False)
    assert res.status == "submitted" and res.portal == "freelancermap"
    assert res.confirmation_text == "Ihre Bewerbung wurde erfolgreich versendet"
    assert res.url == URL
    assert page.filled["cover-letter"].startswith("Sehr geehrte")
    assert page.uploaded == [str(cv), str(projektliste)]
    # За замовчуванням обидва чекбокси вже відмічені: не клацаємо, щоб не зняти.
    assert page.checked == {"email": True, "phone": True} and page.check_calls == []
    assert page.vcard_checked is False
    assert page.clicks == ["submit"]
    assert page.shots == ["apply_form_filled.png", "apply_confirmation.png"]
    assert len(res.screenshots) == 2


def test_unchecked_contact_boxes_get_ticked(tmp_path, files):
    page = FakeProjectPage(_unchecked(fixture_html("freelancermap_project.html")), confirm=True)
    res = apply(page, URL, "x", [files[0]], tmp_path, dry_run=False)
    assert res.status == "submitted"
    assert page.checked == {"email": True, "phone": True}
    assert sorted(page.check_calls) == ["email", "phone"]


def test_dry_run_does_not_click_submit(tmp_path, files):
    page = FakeProjectPage(fixture_html("freelancermap_project.html"), confirm=True)
    res = apply(page, URL, "x", list(files), tmp_path, dry_run=True)
    assert (res.status, res.reason) == ("dry_run", "filled 1, uploaded 2")
    assert not page.submitted and page.clicks == []
    assert page.filled == {"cover-letter": "x"} and len(page.uploaded) == 2
    assert page.shots == ["apply_form_filled.png"]


def test_closed_project(tmp_path, files):
    page = FakeProjectPage(fixture_html("freelancermap_closed.html"))
    res = apply(page, URL, "x", [files[0]], tmp_path, False)
    assert (res.status, res.reason) == ("closed", "posting closed")
    assert page.filled == {} and not page.submitted


def test_login_by_url_and_by_text(tmp_path, files):
    res = apply(FakeProjectPage("", url="https://www.freelancermap.de/login"), URL, "x", [files[0]], tmp_path, False)
    assert res.status == "login_required"
    logged_out = "<html><body><p>Das Bewerbungsformular ist nur für eingeloggte Nutzer verfügbar.</p></body></html>"
    res = apply(FakeProjectPage(logged_out), URL, "x", [files[0]], tmp_path, False)
    assert res.status == "login_required"


def test_no_form_without_marker_is_closed(tmp_path, files):
    page = FakeProjectPage("<html><body><h1>Senior Android Engineer</h1></body></html>")
    res = apply(page, URL, "x", [files[0]], tmp_path, False)
    assert (res.status, res.reason) == ("closed", "closed: no application form")


def test_already_applied_never_sends_twice(tmp_path, files):
    html = fixture_html("freelancermap_project.html").replace(
        '<h2 class="mg-b-display-l" id="project-contact-form">Bewerben</h2>',
        '<h2 class="mg-b-display-l" id="project-contact-form">Bewerben</h2>'
        "<p>Bewerbung erfolgreich abgeschickt am 27.10.2026</p>")
    page = FakeProjectPage(html, confirm=True)
    res = apply(page, URL, "x", [files[0]], tmp_path, False)
    assert (res.status, res.reason) == ("closed", "already applied")
    assert not page.submitted and page.filled == {}


def test_counter_drop_counts_as_confirmation(tmp_path, files):
    page = FakeProjectPage(fixture_html("freelancermap_project.html"), counter_after_submit="9/10")
    res = apply(page, URL, "x", [files[0]], tmp_path, False)
    assert (res.status, res.confirmation_text) == ("submitted", "quota 9/10")


def test_counter_drop_with_button_still_there_is_not_confirmation(tmp_path, files):
    page = FakeProjectPage(fixture_html("freelancermap_project.html"), counter_after_submit="9/10",
                           button_after_submit=True)
    res = apply(page, URL, "x", [files[0]], tmp_path, False, wait_ms=3000)
    assert (res.status, res.reason) == ("handoff", "no_confirmation")


def test_counter_drop_by_two_is_not_confirmation(tmp_path, files):
    page = FakeProjectPage(fixture_html("freelancermap_project.html"), counter_after_submit="8/10")
    res = apply(page, URL, "x", [files[0]], tmp_path, False, wait_ms=3000)
    assert (res.status, res.reason) == ("handoff", "no_confirmation")


def test_already_sent_text_after_click_is_confirmation(tmp_path, files):
    page = FakeProjectPage(fixture_html("freelancermap_project.html"),
                           body_after_submit="Bewerbung erfolgreich abgeschickt am 28.10.2026 Zum Postfach")
    res = apply(page, URL, "x", [files[0]], tmp_path, False)
    assert (res.status, res.confirmation_text) == ("submitted", "Bewerbung erfolgreich abgeschickt am")


def test_silence_after_click_is_handoff(tmp_path, files):
    page = FakeProjectPage(fixture_html("freelancermap_project.html"))
    res = apply(page, URL, "x", [files[0]], tmp_path, False, wait_ms=3000)
    assert (res.status, res.reason) == ("handoff", "no_confirmation")
    assert page.submitted and page.shots[-1] == "apply_after_submit.png"
    assert page.waited >= 3000


def test_platform_error_after_click(tmp_path, files):
    page = FakeProjectPage(fixture_html("freelancermap_project.html"), error_after_submit=True)
    res = apply(page, URL, "x", [files[0]], tmp_path, False)
    assert (res.status, res.reason) == ("handoff", "error_after_submit: platform error")


def test_exception_on_click_is_error_after_submit(tmp_path, files):
    page = FakeProjectPage(fixture_html("freelancermap_project.html"), click_raises=True)
    res = apply(page, URL, "x", [files[0]], tmp_path, False)
    assert res.status == "handoff" and res.reason.startswith("error_after_submit: RuntimeError")


def test_exception_before_click_is_error(tmp_path, files):
    page = FakeProjectPage(fixture_html("freelancermap_project.html"))
    page.fail_on_fill = True
    res = apply(page, URL, "x", [files[0]], tmp_path, False)
    assert res.status == "handoff" and res.reason.startswith("error: RuntimeError")
    assert not page.submitted


def test_goto_failure_is_failed(tmp_path, files):
    res = apply(FakeProjectPage("", goto_raises=True), URL, "x", [files[0]], tmp_path, False)
    assert res.status == "failed" and res.reason.startswith("goto: RuntimeError")


def test_screenshot_failure_does_not_change_result(tmp_path, files):
    page = FakeProjectPage(fixture_html("freelancermap_project.html"), confirm=True, screenshot_raises=True)
    res = apply(page, URL, "x", [files[0]], tmp_path, False)
    assert res.status == "submitted" and res.screenshots == []


def test_missing_submit_button_is_handoff(tmp_path, files):
    html = fixture_html("freelancermap_project.html").replace('data-id="contact-send-application"', "")
    page = FakeProjectPage(html)
    res = apply(page, URL, "x", [files[0]], tmp_path, False)
    assert (res.status, res.reason) == ("handoff", "no_submit_button")
    assert page.filled == {}


def test_missing_file_input_with_attachments_is_handoff(tmp_path, files):
    html = fixture_html("freelancermap_project.html").replace('class="inputfile d-none"', 'class="d-none"')
    page = FakeProjectPage(html)
    res = apply(page, URL, "x", [files[0]], tmp_path, False)
    assert (res.status, res.reason) == ("handoff", "no_file_input")
    assert not page.submitted


def test_no_attachments_is_handoff_before_any_fill(tmp_path):
    for dry in (False, True):
        page = FakeProjectPage(fixture_html("freelancermap_project.html"), confirm=True)
        res = apply(page, URL, "x", [], tmp_path, dry)
        assert (res.status, res.reason) == ("handoff", "no_attachments")
        assert page.filled == {} and page.uploaded == [] and not page.submitted


def test_upload_not_attached_is_handoff(tmp_path, files):
    for dry in (False, True):
        page = FakeProjectPage(fixture_html("freelancermap_project.html"), confirm=True, attach_fails=True)
        res = apply(page, URL, "x", list(files), tmp_path, dry)
        assert (res.status, res.reason) == ("handoff", "upload_not_attached")
        assert not page.submitted and page.clicks == []
