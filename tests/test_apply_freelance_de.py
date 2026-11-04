"""freelance.de adapter on a fake project page shaped like the live form of 29.10.2026."""
from pathlib import Path

import pytest

from bot.apply import freelance_de
from bot.apply.freelance_de import (
    DATA_POLICY_SELECTOR,
    DOC_ATTACH_SELECTOR,
    PROFILE_ATTACH_SELECTOR,
    SUBJECT_SELECTOR,
    SUBMIT_SELECTOR,
    TEXT_SELECTOR,
    UP_TO_DATE_SELECTOR,
    apply,
    page_state,
)

URL = "https://www.freelance.de/projekte/projekt-1-Android-Entwickler"


@pytest.fixture(autouse=True)
def _no_settle(monkeypatch):
    monkeypatch.setattr(freelance_de, "SETTLE_MS", 0)
FORM_TEXT = ("Ihre Bewerbung Betreff * Bewerbungstext * Anhang Mein freelance.de-Profil Projektliste (DE) "
             "Aktualität * Ich bestätige, dass mein Profil sowie meine Dokumente aktuell und korrekt sind. "
             "Einwilligungserklärung Datenschutz * Ja, ich stimme den Datenschutzbestimmungen von Contract Co zu. "
             "* Pflichtfeld Bewerbung senden")


class Box:
    def __init__(self, page, key, k=0):
        self.page, self.key, self.k = page, key, k

    @property
    def first(self):
        return self

    def nth(self, k):
        return Box(self.page, self.key, k)

    def count(self):
        return self.page.counts.get(self.key, 0)

    def is_enabled(self):
        return self.page.enabled

    def is_checked(self):
        return self.page.checked.get((self.key, self.k), False)

    def check(self, timeout=None):
        if self.key in self.page.refuse_check:
            raise RuntimeError("element is not visible")
        self.page.checked[(self.key, self.k)] = True

    def evaluate(self, js):
        if "labels" in js:   # the label click fallback
            self.page.checked[(self.key, self.k)] = True

    def fill(self, value, timeout=None):
        self.page.filled[self.key] = value

    def input_value(self):
        return self.page.subject

    def click(self, timeout=None):
        assert self.key == SUBMIT_SELECTOR
        self.page.clicks += 1
        self.page.after_click()


class Page:
    def __init__(self, text=FORM_TEXT, form=True, enabled=True, confirm="Ihre Bewerbung wurde erfolgreich versendet",
                 after_text=None, subject="Bewerbung auf Android Entwickler"):
        self.text, self.enabled, self.subject = text, enabled, subject
        self.confirm, self.after_text = confirm, after_text
        self.counts = {TEXT_SELECTOR: int(form), SUBMIT_SELECTOR: int(form), SUBJECT_SELECTOR: int(form),
                       PROFILE_ATTACH_SELECTOR: int(form), DOC_ATTACH_SELECTOR: int(form),
                       UP_TO_DATE_SELECTOR: int(form), DATA_POLICY_SELECTOR: int(form)}
        # attachments are checked by default on the live page, the consents are not
        self.checked = {(PROFILE_ATTACH_SELECTOR, 0): True, (DOC_ATTACH_SELECTOR, 0): True}
        self.refuse_check: set[str] = set()
        self.filled: dict[str, str] = {}
        self.clicks = 0
        self.url = ""
        self.shots: list[str] = []

    def goto(self, url, wait_until=None, timeout=None):
        self.url = url

    def wait_for_selector(self, sel, timeout=None):
        if not self.counts.get(TEXT_SELECTOR):
            raise TimeoutError("no form")

    def wait_for_timeout(self, ms):
        pass

    def inner_text(self, sel):
        return self.text

    def locator(self, sel):
        return Box(self, sel)

    def screenshot(self, path=None, full_page=False):
        self.shots.append(Path(path).name)

    def after_click(self):
        if self.after_text is not None:
            self.text = self.after_text
        elif self.confirm:
            self.text = f"{self.confirm}. Vielen Dank."
            self.counts[TEXT_SELECTOR] = 0


def test_page_state():
    assert page_state("Bitte loggen Sie sich ein, um sich zu bewerben", False, False) == "login_required"
    assert page_state("Dieses Projekt ist nicht mehr online", False, False) == "closed"
    assert page_state(FORM_TEXT + " Ab EXPERT-Mitgliedschaft möglich", True, False) == "membership"
    assert page_state(FORM_TEXT, True, False) == "membership"
    assert page_state("Sie haben sich am 28.10.2026 auf dieses Projekt beworben", False, False) == "applied"
    assert page_state(FORM_TEXT, True, True) == "open"
    assert page_state("Projektbeschreibung ohne Formular", False, False) == "closed"


def test_dry_run_fills_text_and_consents_without_submit(tmp_path):
    page = Page()
    res = apply(page, URL, "Sehr geehrte Damen und Herren, …", tmp_path, dry_run=True)
    assert res.status == "dry_run" and page.clicks == 0
    assert page.filled[TEXT_SELECTOR].startswith("Sehr geehrte")
    assert page.checked[(UP_TO_DATE_SELECTOR, 0)] and page.checked[(DATA_POLICY_SELECTOR, 0)]
    assert page.shots[-1] == "apply_form_filled.png"


def test_submit_with_confirmation(tmp_path):
    page = Page()
    res = apply(page, URL, "Text", tmp_path, dry_run=False, wait_ms=3000)
    assert res.status == "submitted" and page.clicks == 1 and res.portal == "freelance_de"
    assert "erfolgreich versendet" in res.confirmation_text


def test_styled_consent_is_checked_through_its_label(tmp_path):
    page = Page()
    page.refuse_check = {DATA_POLICY_SELECTOR}
    res = apply(page, URL, "Text", tmp_path, dry_run=True)
    assert res.status == "dry_run" and page.checked[(DATA_POLICY_SELECTOR, 0)]


def test_basic_membership_is_a_handoff(tmp_path):
    page = Page(enabled=False, text=FORM_TEXT + " Ab EXPERT-Mitgliedschaft möglich")
    res = apply(page, URL, "Text", tmp_path, dry_run=False)
    assert res.status == "handoff" and res.reason.startswith("platform_membership") and page.clicks == 0


def test_no_confirmation_is_a_handoff_not_a_resubmit(tmp_path):
    page = Page(confirm="", after_text=FORM_TEXT)
    res = apply(page, URL, "Text", tmp_path, dry_run=False, wait_ms=3000)
    assert res.status == "handoff" and res.reason == "no_confirmation" and page.clicks == 1


def test_legend_before_the_click_is_not_a_validation_error_but_a_new_message_is(tmp_path):
    page = Page(confirm="", after_text=FORM_TEXT + " Der Bewerbungstext ist ein Pflichtfeld")
    res = apply(page, URL, "Text", tmp_path, dry_run=False, wait_ms=3000)
    assert res.status == "handoff" and res.reason == "validation: ist ein Pflichtfeld"


def test_logged_out_and_closed_pages(tmp_path):
    out = Page(form=False, text="Bitte loggen Sie sich ein")
    assert apply(out, URL, "Text", tmp_path, dry_run=False).status == "login_required"
    gone = Page(form=False, text="Dieses Projekt ist nicht mehr online")
    res = apply(gone, URL, "Text", tmp_path, dry_run=False)
    assert res.status == "closed" and res.reason == "posting closed"


def test_module_names_its_portal():
    assert freelance_de.PORTAL == "freelance_de"
