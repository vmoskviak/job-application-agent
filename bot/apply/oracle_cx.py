"""Oracle CX Recruiting (Candidate Experience) auto-apply flow.

URL виду <host>/<lang>/sites/<SITE>/job/<id>: кожен роботодавець має свій хост.
Флоу без акаунта: Bewerben → email → Oracle шле 6-значний Verifizierungscode
на пошту кандидата → верифікація → анкета (CV upload + поля) → submit.

Код читаємо з main-інбоксу через scripts/gmail_rest.py (той самий helper, що
юзають Mac-сесії); беремо ЛИШЕ листи, що прийшли ПІСЛЯ старту флоу — в інбоксі
може лежати старий невикористаний код (інцидент #239, 17.07).

Fail-safe: незнайомий стан → form_unsupported зі скріншотом, наосліп не
сабмітить. dry-run зупиняється на кроці з submit-кнопкою (форма заповнена,
скріншот зроблено, НЕ подано).
"""

from __future__ import annotations

import importlib.util
import re
import time
from pathlib import Path
from urllib.parse import urlparse

from bot.apply import candidate, forms
from bot.apply.models import ApplyResult
from bot.generators.cv_engine import CV_FILE_STEM

PROJECT_ROOT = Path(__file__).parent.parent.parent

_APPLY_CTA = [
    "button:has-text('Ich möchte mich bewerben')",
    "a:has-text('Ich möchte mich bewerben')",
    "button:has-text('Jetzt bewerben')",
    "a:has-text('Jetzt bewerben')",
    "button:has-text('Bewerben')",
    "a:has-text('Bewerben')",
    "[data-qa='applyNowButton']",
]

_NEXT_BUTTONS = [
    "button:has-text('Weiter')",
    "button:has-text('Fortfahren')",
    "button:has-text('Continue')",
]

_SUBMIT_BUTTONS = [
    "button:has-text('Bewerbung absenden')",
    "button:has-text('Bewerbung einreichen')",
    "button:has-text('Bewerbung abschicken')",
    "button:has-text('Absenden')",
    "button:has-text('Einreichen')",
    "button:has-text('Senden')",
    "button:has-text('Submit')",
]

_CONFIRMATION_HINTS = (
    "vielen dank",
    "bewerbung wurde",
    "erfolgreich",
    "eingegangen",
    "erhalten",
    "abgeschlossen",
    "danke für deine bewerbung",
)

_CODE_SUBJECTS = "subject:verifizierungscode OR subject:verification"


def _code_query(url: str) -> str:
    """Gmail-запит на лист із кодом. Oracle шле його з домену роботодавця, тож
    `from:` береться з хоста вакансії (jobs.firma.de → firma); тема як запасний
    критерій, бо шаблон листа залежить від інстанції."""
    host = urlparse(url).hostname or ""
    labels = [p for p in host.split(".") if p]
    sender = labels[-2] if len(labels) >= 2 else ""
    sender_part = f"from:{sender} OR " if sender else ""
    return f"newer_than:1h ({sender_part}{_CODE_SUBJECTS})"
_CODE_POLL_SEC = 150
_CODE_RESEND_AFTER_SEC = 45


def _gmail():
    """scripts/gmail_rest.py як модуль (без дублювання OAuth-логіки)."""
    spec = importlib.util.spec_from_file_location(
        "gmail_rest", PROJECT_ROOT / "scripts" / "gmail_rest.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _code_account() -> str:
    """Скринька, куди портал шле код: та, чия адреса стоїть у формі (candidate.EMAIL)."""
    from bot import client as _client
    return _client.active().account_for_address(candidate.EMAIL)


def _extract_code(text: str) -> str | None:
    m = re.search(r"\b(\d{6})\b", text)
    return m.group(1) if m else None


def _msg_text(gm, msg_id: str) -> str:
    import base64

    msg = gm.api(_code_account(), "GET", f"messages/{msg_id}", params={"format": "full"})
    chunks: list[str] = []

    def walk(part):
        if part.get("mimeType", "").startswith("text/") and part.get("body", {}).get("data"):
            chunks.append(
                base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", "replace")
            )
        for p in part.get("parts", []):
            walk(p)

    walk(msg["payload"])
    return "\n".join(chunks) or msg.get("snippet", "")


def _poll_verification_code(page, started_at_ms: int, url: str) -> str | None:
    """Чекає СВІЖИЙ код (internalDate > старту флоу); за потреби тисне
    «Neuen Code senden». Старі коди в інбоксі ігноруються."""
    gm = _gmail()
    deadline = time.time() + _CODE_POLL_SEC
    resent = False
    while time.time() < deadline:
        try:
            acc = _code_account()
            res = gm.api(acc, "GET", "messages",
                         params={"q": _code_query(url), "maxResults": 5})
            for m in res.get("messages", []):
                meta = gm.api(acc, "GET", f"messages/{m['id']}",
                              params={"format": "minimal"})
                if int(meta.get("internalDate", 0)) <= started_at_ms:
                    continue
                code = _extract_code(_msg_text(gm, m["id"]))
                if code:
                    return code
        except Exception:
            pass
        if not resent and time.time() - started_at_ms / 1000 > _CODE_RESEND_AFTER_SEC:
            try:
                link = page.locator("text=Neuen Code senden").first
                if link.is_visible(timeout=1000):
                    link.click(timeout=2000)
            except Exception:
                pass
            resent = True
        page.wait_for_timeout(8000)
    return None


def _enter_code(page, code: str) -> bool:
    """6 окремих однознакових інпутів (скріншот 17.07) або один спільний."""
    boxes = page.locator("input[maxlength='1']")
    try:
        if boxes.count() >= 6:
            for i, digit in enumerate(code):
                boxes.nth(i).fill(digit)
                page.wait_for_timeout(150)
            return True
    except Exception:
        pass
    try:
        first = page.locator(
            "input[autocomplete='one-time-code'], input[type='tel'], input[type='text']"
        ).first
        first.click(timeout=2000)
        page.keyboard.type(code, delay=120)
        return True
    except Exception:
        return False


def _click_first(page, selectors: list[str], timeout: int = 1500):
    for sel in selectors:
        try:
            btn = page.locator(sel).first
            if btn.is_visible(timeout=timeout) and btn.is_enabled():
                btn.click(timeout=4000)
                return sel
        except Exception:
            continue
    return None


def _check_legal_boxes(page) -> list[str]:
    """Ставить ЛИШЕ datenschutz/einwilligung-чекбокси — не всі підряд."""
    checked: list[str] = []
    boxes = page.locator("input[type='checkbox']")
    for i in range(min(boxes.count(), 10)):
        box = boxes.nth(i)
        try:
            if not box.is_visible() or box.is_checked():
                continue
            meta = " ".join(filter(None, [
                box.get_attribute("name"), box.get_attribute("id"),
                box.get_attribute("aria-label"),
            ])).lower()
            try:
                meta += " " + (box.evaluate(
                    "el => el.closest('label, div')?.innerText || ''") or "").lower()[:200]
            except Exception:
                pass
            if any(k in meta for k in ("datenschutz", "einwilligung", "privacy", "einverstanden")):
                box.check(timeout=2000)
                checked.append(meta[:40])
        except Exception:
            continue
    return checked


def _upload_cv(page, app_dir: Path) -> bool:
    cv = app_dir / "to_send" / f"{CV_FILE_STEM}.pdf"
    if not cv.exists():
        return False
    try:
        inp = page.locator("input[type='file']").first
        if inp.count() == 0:
            return False
        inp.set_input_files(str(cv))
        # Oracle парсить CV у профіль — дати час
        page.wait_for_timeout(8000)
        return True
    except Exception:
        return False


def apply(page, url: str, app_dir: Path, attachments: list[Path], dry_run: bool,
          salary: str | None = None) -> ApplyResult:
    page.goto(url, wait_until="domcontentloaded", timeout=40000)
    page.wait_for_timeout(3000)
    forms.accept_cookies(page)

    body = ""
    try:
        body = page.inner_text("body").lower()
    except Exception:
        pass
    if "nicht mehr verfügbar" in body or "no longer available" in body:
        shot = forms.screenshot(page, app_dir, "apply_preflight.png")
        return ApplyResult("closed", "oracle_cx", url, [shot], reason="job page closed")

    started_at_ms = int(time.time() * 1000)

    if not _click_first(page, _APPLY_CTA, timeout=2500):
        shot = forms.screenshot(page, app_dir, "apply_no_button.png")
        return ApplyResult("form_unsupported", "oracle_cx", url, [shot],
                           reason="apply CTA not found")
    page.wait_for_timeout(3000)

    # Крок email: Oracle питає адресу, шле код
    log: list[str] = []
    try:
        email_inp = page.locator(
            "input[type='email'], input[name*='email' i], input[id*='email' i]"
        ).first
        if email_inp.is_visible(timeout=3000):
            email_inp.fill(candidate.EMAIL)
            log.append("email filled")
            _check_legal_boxes(page)
            clicked = _click_first(
                page, _NEXT_BUTTONS + ["button[type='submit']"], timeout=2000)
            log.append(f"email next: {clicked}")
            page.wait_for_timeout(3000)
    except Exception:
        pass

    # Крок верифікації («Identität bestätigen», 6 кружечків)
    try:
        body = page.inner_text("body").lower()
    except Exception:
        body = ""
    if "verifizierungscode" in body or "identität bestätigen" in body:
        forms.screenshot(page, app_dir, "apply_verify_step.png")
        code = _poll_verification_code(page, started_at_ms, url)
        if not code:
            shot = forms.screenshot(page, app_dir, "apply_verify_timeout.png")
            return ApplyResult("failed", "oracle_cx", url, [shot],
                               reason="verification code not received in time")
        if not _enter_code(page, code):
            shot = forms.screenshot(page, app_dir, "apply_verify_fail.png")
            return ApplyResult("form_unsupported", "oracle_cx", url, [shot],
                               reason="could not enter verification code")
        log.append(f"code {code} entered")
        _click_first(page, ["button:has-text('Verifizieren')",
                            "button:has-text('Verify')"], timeout=2000)
        page.wait_for_timeout(5000)

    # Анкета: до 6 кроків wizard'а; на кожному — fill + upload, далі
    # submit (кінець) або Weiter (наступний крок)
    uploaded = False
    for step in range(6):
        try:
            if not uploaded:
                uploaded = _upload_cv(page, app_dir)
                if uploaded:
                    log.append("cv uploaded")
            log += forms.fill_known_fields(page)
            log += forms.fill_extended_fields(page, salary)
            legal = _check_legal_boxes(page)
            if legal:
                log.append(f"legal={legal}")
        except Exception as e:
            log.append(f"step{step} fill error: {type(e).__name__}")

        submit_sel = None
        for sel in _SUBMIT_BUTTONS:
            try:
                btn = page.locator(sel).first
                if btn.is_visible(timeout=1200):
                    submit_sel = sel
                    break
            except Exception:
                continue

        if submit_sel:
            shot_form = forms.screenshot(page, app_dir, "apply_form_filled.png")
            if dry_run:
                return ApplyResult("dry_run", "oracle_cx", url, [shot_form],
                                   reason=f"submit ready ({submit_sel}); log={log}")
            try:
                page.locator(submit_sel).first.click(timeout=5000)
            except Exception as e:
                return ApplyResult("form_unsupported", "oracle_cx", url, [shot_form],
                                   reason=f"submit click failed: {type(e).__name__}")
            page.wait_for_timeout(7000)
            shot_conf = forms.screenshot(page, app_dir, "apply_confirmation.png")
            try:
                body = page.inner_text("body").lower()
            except Exception:
                body = ""
            hint = forms.find_confirmation(page) or next(
                (h for h in _CONFIRMATION_HINTS if h in body), "")
            if hint:
                return ApplyResult("submitted", "oracle_cx", url,
                                   [shot_form, shot_conf], confirmation_text=hint)
            return ApplyResult("failed", "oracle_cx", url, [shot_form, shot_conf],
                               reason="no confirmation after submit — verify manually")

        if not _click_first(page, _NEXT_BUTTONS, timeout=1500):
            shot = forms.screenshot(page, app_dir, f"apply_step{step}_stuck.png")
            return ApplyResult("form_unsupported", "oracle_cx", url, [shot],
                               reason=f"no submit/next button at step {step}; log={log}")
        page.wait_for_timeout(3500)

    shot = forms.screenshot(page, app_dir, "apply_too_many_steps.png")
    return ApplyResult("form_unsupported", "oracle_cx", url, [shot],
                       reason=f"wizard exceeded 6 steps; log={log}")
