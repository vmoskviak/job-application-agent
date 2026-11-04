"""freelance.de adapter (logged in, EXPERT membership): cover text, attachments, consents, confirmation.

Captured live on 29.10.2026 after the EXPERT upgrade. The form sits on the project page under
«Ihre Bewerbung»: subject `input#subject` (prefilled «Bewerbung auf <title>»), text
`textarea#application_text` (prefilled with a platform template), attachments as checkboxes
(`#my_attached_profile` for the freelance.de profile, `input[id^=doc_]` for uploaded documents,
both checked by default), two required consents (`#profile_up_to_date`, `#data_policy_accepted`:
the client's privacy terms) and the submit button «Bewerbung senden». On the Basic membership the
subject and text are disabled («Ab EXPERT-Mitgliedschaft möglich»): that is a membership stop.

The subject stays as the platform fills it; the text becomes the package note. The KI tab and the
«save as template» field are never touched. The success text after submit was not seen live yet:
anything but a known phrase (or the form gone together with an «applied» marker) is a hand-off,
never a second submission. `apply` does not close the page.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Literal

from bot.apply.models import ApplyResult
from bot.scrapers.browser import settle_body

log = logging.getLogger(__name__)

PORTAL = "freelance_de"

SUBJECT_SELECTOR = "input#subject"
TEXT_SELECTOR = "textarea#application_text"
PROFILE_ATTACH_SELECTOR = "input#my_attached_profile"
DOC_ATTACH_SELECTOR = 'input[id^="doc_"]'
UP_TO_DATE_SELECTOR = "input#profile_up_to_date"
DATA_POLICY_SELECTOR = "input#data_policy_accepted"
# The live button has no type attribute (submit by default), so it is found by its text in the form.
SUBMIT_SELECTOR = ('form[id^="application_form"] button:has-text("Bewerbung senden"), '
                   'form:has(textarea#application_text) button[type=submit]')

MEMBERSHIP_RE = re.compile(r"Ab EXPERT-Mitgliedschaft möglich", re.IGNORECASE)
LOGIN_RE = re.compile(r"Bitte (?:loggen Sie sich ein|melden Sie sich an)|Jetzt einloggen|zum Login", re.IGNORECASE)
CLOSED_RE = re.compile(r"Dieses Projekt ist nicht mehr online|Projekt ist (?:bereits )?beendet|nicht mehr verfügbar",
                       re.IGNORECASE)
ALREADY_APPLIED_RE = re.compile(r"(?:Sie haben sich|Ihre Bewerbung wurde)[^.\n]{0,60}(?:bereits|am \d)",
                                re.IGNORECASE)
CONFIRMATION_RE = re.compile(
    r"Ihre Bewerbung wurde (?:erfolgreich )?(?:versendet|verschickt|gesendet|übermittelt)"
    r"|Bewerbung (?:erfolgreich )?(?:versendet|verschickt|gesendet|übermittelt)"
    r"|Vielen Dank für Ihre Bewerbung",
    re.IGNORECASE)
# The form always shows the legend «* Pflichtfeld»: only messages that were not on the page before
# the click count (see _await_confirmation).
VALIDATION_RE = re.compile(r"ist ein Pflichtfeld|muss ausgefüllt werden|darf nicht leer sein"
                           r"|bitte (?:füllen|wählen|bestätigen)\w*[^.\n]{0,60}|ist erforderlich", re.IGNORECASE)
PLATFORM_ERROR_RE = re.compile(r"Es ist ein Fehler aufgetreten", re.IGNORECASE)

GOTO_TIMEOUT_MS = 30000
SETTLE_MS = 4000
FORM_WAIT_MS = 10000
SUBMIT_WAIT_MS = 30000
POLL_MS = 1000


def page_state(text: str, has_form: bool, form_enabled: bool) -> Literal[
        "login_required", "closed", "applied", "membership", "open"]:
    """State of the project page. Login first (a logged-out page has no form either); a closed or
    already applied project before the membership check; no form at all counts as closed."""
    text = text or ""
    if not has_form and LOGIN_RE.search(text):
        return "login_required"
    if CLOSED_RE.search(text):
        return "closed"
    if ALREADY_APPLIED_RE.search(text):
        return "applied"
    if has_form and (not form_enabled or MEMBERSHIP_RE.search(text)):
        return "membership"
    if not has_form:
        return "closed"
    return "open"


def _open(page, url: str) -> str:
    page.goto(url, wait_until="domcontentloaded", timeout=GOTO_TIMEOUT_MS)
    try:
        page.wait_for_selector(TEXT_SELECTOR, timeout=FORM_WAIT_MS)
    except Exception:  # noqa: BLE001 - no form: login, closed or applied, page_state decides
        log.info("no application form on %s within %d ms", url, FORM_WAIT_MS)
    return settle_body(page, SETTLE_MS, page.inner_text("body"))


def _tick(page, selector: str) -> bool:
    """Checks every matching checkbox that is not checked yet; False when one stays unchecked.
    A styled checkbox refuses check(): its label takes the click."""
    boxes = page.locator(selector)
    for k in range(boxes.count()):
        box = boxes.nth(k)
        if box.is_checked():
            continue
        try:
            box.check(timeout=3000)
        except Exception:  # noqa: BLE001 - styled input
            box.evaluate("e => { const l = e.labels && e.labels[0]; if (l) l.click(); }")
        if not box.is_checked():
            return False
    return True


def apply(page, url: str, note: str, shots_dir: Path, dry_run: bool,
          wait_ms: int = SUBMIT_WAIT_MS) -> ApplyResult:
    """Fills and submits the project's application form; anything unfinished is a hand-off."""
    from bot.apply.forms import screenshot

    shots: list[str] = []
    clicked = False

    def result(status: str, **kw) -> ApplyResult:
        return ApplyResult(status, PORTAL, url, list(shots), **kw)

    def shot(name: str) -> None:
        try:
            shots.append(screenshot(page, shots_dir, name))
        except Exception:
            log.warning("screenshot %s failed", name, exc_info=True)

    try:
        text = _open(page, url)
    except Exception as e:  # noqa: BLE001 - the page did not open: failed, not a crash
        return result("failed", reason=f"goto: {type(e).__name__}: {e}"[:300])

    try:
        area = page.locator(TEXT_SELECTOR)
        has_form = area.count() > 0
        enabled = has_form and area.first.is_enabled()
        state = page_state(text, has_form, enabled)
        if state == "login_required":
            shot("apply_login_required.png")
            return result("login_required", reason="freelance.de login required")
        if state == "closed":
            shot("apply_closed.png")
            return result("closed", reason="posting closed" if CLOSED_RE.search(text) else "closed: no application form")
        if state == "applied":
            return result("closed", reason="already applied")
        if state == "membership":
            shot("apply_membership.png")
            return result("handoff", reason="platform_membership: EXPERT needed for the application text")
        if not (note or "").strip():
            return result("handoff", reason="no_note")
        submit_btn = page.locator(SUBMIT_SELECTOR)
        if not submit_btn.count():
            shot("apply_no_submit.png")
            return result("handoff", reason="no_submit_button")

        area.first.fill(note, timeout=5000)
        subject = page.locator(SUBJECT_SELECTOR)
        if subject.count() and not (subject.first.input_value() or "").strip():
            return result("handoff", reason="open_required: Betreff")
        # Attachments: the profile and every uploaded document; without one the application is weak.
        if page.locator(PROFILE_ATTACH_SELECTOR).count() + page.locator(DOC_ATTACH_SELECTOR).count() == 0:
            shot("apply_no_attachments.png")
            return result("handoff", reason="no_attachments")
        if not (_tick(page, PROFILE_ATTACH_SELECTOR) and _tick(page, DOC_ATTACH_SELECTOR)):
            shot("apply_attachment_unchecked.png")
            return result("handoff", reason="attachment_not_checked")
        if not (_tick(page, UP_TO_DATE_SELECTOR) and _tick(page, DATA_POLICY_SELECTOR)):
            shot("apply_consent_unchecked.png")
            return result("handoff", reason="open_required: consent")
        shot("apply_form_filled.png")
        if dry_run:
            return result("dry_run", reason="filled text, attachments and consents")

        before = page.inner_text("body")
        clicked = True   # set before the click: a click that throws may still have submitted
        submit_btn.first.click(timeout=5000)
        return _await_confirmation(page, before, wait_ms, shot, result)
    except Exception as e:
        log.exception("freelance.de %s", url)
        prefix = "error_after_submit" if clicked else "error"
        return result("handoff", reason=f"{prefix}: {type(e).__name__}: {e}"[:300])


def _await_confirmation(page, before: str, wait_ms: int, shot, result) -> ApplyResult:
    """Polls the page after the click: a success phrase, an «already applied» marker with the form
    gone, a platform error or a validation message; nothing within wait_ms is a hand-off. Texts
    already on the page before the click (`before`) do not count."""
    old_ok = {m.group(0) for m in CONFIRMATION_RE.finditer(before or "")}
    old_bad = {m.group(0) for m in VALIDATION_RE.finditer(before or "")}
    elapsed = 0
    while elapsed < wait_ms:
        page.wait_for_timeout(POLL_MS)
        elapsed += POLL_MS
        try:
            text = page.inner_text("body")
            form_gone = page.locator(TEXT_SELECTOR).count() == 0
        except Exception:  # re-render or navigation: keep waiting
            log.warning("page not readable after submit, waiting", exc_info=True)
            continue
        m = next((x for x in CONFIRMATION_RE.finditer(text) if x.group(0) not in old_ok), None)
        if m:
            shot("apply_confirmation.png")
            return result("submitted", confirmation_text=m.group(0))
        if form_gone and ALREADY_APPLIED_RE.search(text):
            shot("apply_confirmation.png")
            return result("submitted", confirmation_text=ALREADY_APPLIED_RE.search(text).group(0))
        if PLATFORM_ERROR_RE.search(text):
            shot("apply_after_submit.png")
            return result("handoff", reason="error_after_submit: platform error")
        bad = [x.group(0) for x in VALIDATION_RE.finditer(text) if x.group(0) not in old_bad]
        if not form_gone and bad:
            shot("apply_after_submit.png")
            return result("handoff", reason="validation: " + "; ".join(bad)[:200])
    shot("apply_after_submit.png")
    return result("handoff", reason="no_confirmation")
