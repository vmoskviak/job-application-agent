"""StepStone "Schnelle Bewerbung" auto-apply flow.

Fail-safe за задумом: будь-який незнайомий стан → form_unsupported зі
скріншотом, ніколи не сабмітить наосліп.
"""

from __future__ import annotations

import time
from pathlib import Path

from bot.apply import detect, forms
from bot.apply.models import ApplyResult
from bot.generators.cv_engine import CV_FILE_STEM, portal_zeugnisse

_APPLY_BUTTON_SELECTORS = [
    "[data-testid*='harmonised-apply']",
    "[data-at='button-apply']",
    "button:has-text('Schnelle Bewerbung')",
    "a:has-text('Schnelle Bewerbung')",
    "button:has-text('Jetzt bewerben')",
    "a:has-text('Jetzt bewerben')",
]

_SUBMIT_SELECTORS = [
    "button[type='submit']:has-text('Bewerbung')",
    "button:has-text('Bewerbung abschicken')",
    "button:has-text('Bewerbung absenden')",
    "button:has-text('Bewerbung senden')",
    "button:has-text('Abschicken')",
    "button:has-text('Absenden')",
    "button[type='submit']",
]


_HANDOFF_MARKERS = ("auf der unternehmensseite weiterführen", "bewerbung fortführen")


def is_external_handoff(body: str) -> bool:
    low = body.lower()
    return any(m in low for m in _HANDOFF_MARKERS)


def external_handoff_url(page) -> str | None:
    """None, якщо це не сторінка передачі; інакше URL сайту роботодавця ('' якщо не знайдено)."""
    try:
        if not is_external_handoff(page.inner_text("body")):
            return None
    except Exception:  # noqa: BLE001
        return None
    try:
        link = page.locator("a:has-text('Bewerbung fortführen')").first
        href = link.get_attribute("href", timeout=2000) if link.count() else None
        if href:
            return href
    except Exception:  # noqa: BLE001
        pass
    # лінка в розмітці нема: це <button data-testid="continueFromATSiError">,
    # клік веде на сайт роботодавця в тій самій вкладці (роботодавець → його власна Jobbörse)
    try:
        btn = page.locator("[data-testid='continueFromATSiError'], "
                           "button:has-text('Bewerbung fortführen')").first
        if btn.is_visible(timeout=1500):
            btn.click(timeout=4000)
    except Exception:  # noqa: BLE001
        pass
    deadline = time.time() + 15
    while time.time() < deadline:
        if "stepstone.de" not in page.url:
            return page.url
        page.wait_for_timeout(1500)
    return ""


_APPLIED_MARKERS = ("schon beworben", "bereits beworben")


def shows_already_applied(page) -> bool:
    """Кнопка подачі на сторінці вакансії каже «Schon beworben». Це найнадійніша
    ознака факту подачі: тексти фінальної сторінки StepStone міняє, а кнопка
    відображає стан на їхньому боці (перевірено 08.10.2026 на п'яти заявках)."""
    try:
        loc = page.locator("[data-testid*='harmonised-apply'], [data-at*='apply']")
        for i in range(min(loc.count(), 4)):
            if any(m in (loc.nth(i).inner_text() or "").lower() for m in _APPLIED_MARKERS):
                return True
    except Exception:  # noqa: BLE001
        pass
    return False


def apply(page, url: str, app_dir: Path, attachments: list[Path], dry_run: bool,
          salary: str | None = None) -> ApplyResult:
    # Сесія StepStone теж може протухнути між процесами: логінимось у цьому ж
    # контексті (Google-OAuth профілю або облікові дані), один раз на процес.
    from bot.scrapers.portal_login import ensure_login
    ctx = getattr(page, "context", None)  # тестові FakePage без контексту
    if ctx is not None:
        ensure_login(ctx, "stepstone")
    kind = detect.detect(page, url)
    if ctx is not None and shows_already_applied(page):
        shot = forms.screenshot(page, app_dir, "apply_already_applied.png")
        return ApplyResult(
            "submitted", "stepstone", url, [shot],
            confirmation_text="schon beworben (StepStone вже має цю заявку)",
        )
    if kind in ("login_required", "captcha", "closed"):
        shot = forms.screenshot(page, app_dir, "apply_preflight.png")
        return ApplyResult(kind, "stepstone", url, [shot], reason=f"pre-flight: {kind}")
    if kind != "stepstone_quick":
        shot = forms.screenshot(page, app_dir, "apply_preflight.png")
        return ApplyResult(
            "form_unsupported", "stepstone", url, [shot],
            reason=f"pre-flight classified as {kind}",
        )

    forms.accept_cookies(page)

    clicked = False
    for sel in _APPLY_BUTTON_SELECTORS:
        try:
            btn = page.locator(sel).first
            if btn.is_visible(timeout=1500):
                btn.click(timeout=5000)
                clicked = True
                break
        except Exception:
            continue
    if not clicked:
        shot = forms.screenshot(page, app_dir, "apply_no_button.png")
        return ApplyResult(
            "form_unsupported", "stepstone", url, [shot],
            reason="apply button not found",
        )

    page.wait_for_timeout(2500)

    # Повторний pre-flight вже на формі (login-wall може вилізти тут)
    form_kind = detect.classify_apply_page(page.url, page.content())
    if form_kind in ("login_required", "captcha"):
        shot = forms.screenshot(page, app_dir, "apply_form_blocked.png")
        return ApplyResult(form_kind, "stepstone", url, [shot], reason="blocked on form")

    # Зовнішня подача: клік веде на сайт компанії, Stepstone показує
    # інтерстишл «Hat es geklappt?» — форми нема, авто-подача неможлива
    try:
        body = page.inner_text("body").lower()
    except Exception:
        body = ""
    if "hat es geklappt" in body or "unternehmenswebsite" in body:
        shot = forms.screenshot(page, app_dir, "apply_external.png")
        return ApplyResult(
            "form_unsupported", "stepstone", url, [shot],
            reason="external application — redirects to company website",
        )

    filled = forms.fill_known_fields(page)
    filled += forms.fill_extended_fields(page, salary)
    filled += forms.answer_radio_questions(page)
    # Мапимо документи по секціях (CV-інпут пише в ПРОФІЛЬ — тільки CV туди!).
    # Anschreiben — DOCX, не PDF: reportlab-PDF (3КБ, шрифти без embedding)
    # Stepstone-віджет відкидає як «Datei ist beschädigt» (перевірено 22.07).
    to_send = app_dir / "to_send"
    docx = sorted(to_send.glob("Anschreiben_*.docx"))
    zeugnisse = portal_zeugnisse()
    upload_log = forms.upload_documents_mapped(
        page,
        cv=to_send / f"{CV_FILE_STEM}.pdf",
        anschreiben=docx[0] if docx else None,
        zeugnisse=zeugnisse,
    )
    uploaded = len(upload_log)
    filled.append(f"upload={upload_log}")
    page.wait_for_timeout(2500)
    shot_form = forms.screenshot(page, app_dir, "apply_form_filled.png")

    # Профільний quick-apply інколи сабмітить сам одразу після кліку/заповнення
    # (Chemhandel 28.07: на екрані вже «Gut gemacht, deine Bewerbung ist raus!», а
    # модуль шукав submit-кнопку і записав false negative form_unsupported).
    early_confirmation = forms.find_confirmation(page)
    if early_confirmation:
        return ApplyResult(
            "submitted", "stepstone", url, [shot_form],
            confirmation_text=early_confirmation,
            reason="auto-submitted by portal right after fill"
                   + (" (dry-run intended!)" if dry_run else ""),
        )

    if dry_run:
        return ApplyResult(
            "dry_run", "stepstone", url, [shot_form],
            reason=f"filled={filled} uploaded={uploaded}",
        )

    submit_btn = None
    for sel in _SUBMIT_SELECTORS:
        try:
            btn = page.locator(sel).first
            if btn.is_visible(timeout=1500):
                submit_btn = btn
                break
        except Exception:
            continue
    if submit_btn is None:
        return ApplyResult(
            "form_unsupported", "stepstone", url, [shot_form],
            reason="submit button not found",
        )
    # disabled submit = невидимі для філера обов'язкові поля; не сабмітимо наосліп
    for _ in range(3):
        if submit_btn.is_enabled():
            break
        page.wait_for_timeout(1000)
    if not submit_btn.is_enabled():
        return ApplyResult(
            "form_unsupported", "stepstone", url, [shot_form],
            reason=f"submit disabled — required fields left: filled={filled}",
        )
    try:
        submit_btn.click(timeout=8000)
    except Exception:
        # DRAWER_OVERLAY перехоплює pointer events і валить звичайний клік
        # (Pax-Bank 28.07). JS-клік іде повз hit-testing — той самий трюк, що
        # з LinkedIn-тумблерами.
        submit_btn.evaluate("el => el.click()")

    page.wait_for_timeout(4000)
    # Smart-apply 2026 після сабміту крутить інтерстишл «Bewerbung wird
    # vorbereitet/geprüft/gesendet» до 20–30 с; закрити браузер на ньому =
    # заявка може не дійти (будівельний концерн 08.10.2026: інтерстишл є, листа «ist raus»
    # нема). Чекаємо, доки він зникне або з'явиться фінальний текст.
    deadline = time.time() + 45
    while time.time() < deadline:
        try:
            body = page.inner_text("body").lower()
        except Exception:  # noqa: BLE001
            break
        if "bewerbung wird" not in body or "auf dem weg" in body or "ist raus" in body:
            break
        page.wait_for_timeout(2000)

    # Передача на сайт роботодавця: «Du musst diese Bewerbung auf der
    # Unternehmensseite weiterführen» + кнопка «Bewerbung fortführen». Заявка
    # НЕ подана; повертаємо лінк, куди StepStone веде, щоб оператор подав там.
    handoff = external_handoff_url(page)
    if handoff is not None:
        shot_conf = forms.screenshot(page, app_dir, "apply_external_handoff.png")
        return ApplyResult(
            "form_unsupported", "stepstone", url, [shot_form, shot_conf],
            reason=f"StepStone передає на сайт роботодавця, не подано: {handoff or 'лінк не визначено'}",
        )

    confirmation = forms.find_confirmation(page)
    shot_conf = forms.screenshot(page, app_dir, "apply_confirmation.png")

    if not confirmation:
        # Тексту підтвердження не впізнали: питаємо сам StepStone через кнопку
        # на сторінці вакансії.
        # Кнопка міняється на «Schon beworben» не одразу: одна із заявок 10.10.2026
        # пішла в трекер як failed, хоча заявка дійшла. Тому до трьох спроб.
        try:
            for _ in range(3):
                page.goto(url, wait_until="domcontentloaded", timeout=45000)
                page.wait_for_timeout(3500)
                if shows_already_applied(page):
                    confirmation = "schon beworben (перевірено на сторінці вакансії)"
                    break
                page.wait_for_timeout(8000)
        except Exception:  # noqa: BLE001
            pass

    if confirmation:
        return ApplyResult(
            "submitted", "stepstone", url, [shot_form, shot_conf],
            confirmation_text=confirmation,
        )
    return ApplyResult(
        "failed", "stepstone", url, [shot_form, shot_conf],
        reason="no confirmation text after submit — verify manually",
    )
