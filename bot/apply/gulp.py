"""Адаптер GULP під логіном: мотивація, ставка, валюта, CV, підтвердження.

Селектори з живого захоплення 28.10.2026 (план 2d, розділ «Captured», GULP).
Сторінка проєкту має кнопку `button[data-testid="applyButton"]`, яка веде на
`<проєкт>/bewerbung-mit-profil`; ця адреса відкривається і напряму, тож після
перевірки стану проєкту адаптер іде туди сам, без кліку.

Форма CXS (Salesforce) у `#cxsFormHolder`. Підписи залежать від мови сайту
(EN/DE), тому поля шукаємо лише за `name` чи класом контейнера, ніколи за
підписом. Обов'язковість позначає клас `cxsRequired` на контейнері, не атрибут.
Ім'я, прізвище й email GULP заповнює сам, адаптер їх не чіпає.

Поле CV сидить у крос-доменному iframe, і його `onchange` одразу вантажить файл
у GULP. Тож dry run файл не ставить взагалі: пробний прогін нічого не передає.
Справжній прогін натискає Submit лише тоді, коли ім'я файлу CV з'явилось в
iframe чи на сторінці; інакше `handoff cv_upload_unconfirmed`.

Текст підтвердження живцем не перевірено (кнопку не натискали), тому все, що
не збіглося з фразою подяки чи маркером «ви вже подались», стає `handoff`.
Сторінка лишається відкритою для оператора, `apply` її не закриває.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Literal

from bot.apply.models import ApplyResult
from bot.scrapers.browser import settle_body

log = logging.getLogger(__name__)

PORTAL = "gulp"

# Сторінка проєкту. Кнопку шукаємо за data-testid: текст залежить від мови.
APPLY_BUTTON_SELECTOR = 'button[data-testid="applyButton"]'
APPLY_TEXTS = ("Apply now", "Jetzt bewerben")
APPLY_PATH = "/bewerbung-mit-profil"
# Блок «N freelancers have applied for this project!»; тут шукаємо «ви вже подались».
APPLICATIONS_INFO_SELECTOR = '[data-testid="applications-info"]'

# Форма CXS (`#cxsFormHolder`): її наявність перевіряємо полем мотивації.
MOTIVATION_SELECTOR = "textarea[name=cxsrec__motivation__c]"
MOTIVATION_MAX = 4000
RATE_SELECTOR = "input[name=rsd_purchaseprice__c]"
# У select валюти нема name: беремо його за класом контейнера.
CURRENCY_SELECTOR = ".cxsFieldName_rsd_currency__c select"
CV_FRAME_SELECTOR = "iframe.cxsFileUploadFrame"
CV_INPUT_SELECTOR = "input[type=file]"
CV_BODY_SELECTOR = "body"
SUBMIT_SELECTOR = "input#cxsApplyButton[type=button]"
SUBMIT_TEXTS = ("Submit", "Absenden")
# Підписи лише для довідки й діагностики; адаптер за ними не шукає.
MOTIVATION_LABEL_RE = r"Motivation|Anschreiben"
RATE_LABEL_RE = r"Hourly pay rate|Stundensatz"
CURRENCY_LABEL_RE = r"Currency|Währung"

LOGGED_IN_MARKER = "Dashboard"
LOGIN_RE = re.compile(r"Anmelden|Registrieren|Login|Sign in")
CLOSED_RE = re.compile(r"nicht mehr verfügbar|no longer available|ist bereits vergeben")
CONFIRMATION_RE = re.compile(
    r"thank you for your application|vielen dank für ihre bewerbung"
    r"|ihre bewerbung wurde (?:erfolgreich )?(?:gesendet|übermittelt)"
    r"|application (?:has been )?(?:sent|submitted)",
    re.IGNORECASE,
)
# Маркер «ви вже подались», теж не перевірений живцем. Заякорений навмисно:
# «17 freelancers have applied for this project!» стоїть на кожному проєкті, а
# опис проєкту буває з фразами на кшталт «Ideally you have applied Kotlin …».
# На сторінці проєкту шукаємо лише в блоці applications-info.
APPLIED_RE = re.compile(
    r"\byou(?:'ve| have) already applied"
    r"|\byou(?:'ve| have) applied (?:for|to) this (?:project|job)"
    r"|\bsie haben sich (?:bereits|schon) (?:auf dieses projekt )?beworben",
    re.IGNORECASE,
)

GOTO_TIMEOUT_MS = 30000
SETTLE_MS = 8000
FORM_WAIT_MS = 15000
CV_CONFIRM_MS = 15000
SUBMIT_WAIT_MS = 30000
POLL_MS = 1000


def page_state(url: str, text: str) -> Literal["login_required", "closed", "open"]:
    """Стан сторінки проєкту за текстом body.

    Слова входу рахуються лише без «Dashboard»: залогінений хедер його має, а
    «Login» може трапитись і в тексті проєкту.
    """
    text = text or ""
    if LOGGED_IN_MARKER not in text and LOGIN_RE.search(text):
        return "login_required"
    if CLOSED_RE.search(text):
        return "closed"
    return "open"


def form_url(url: str) -> str:
    """Адреса форми для адреси проєкту; вже готову адресу форми не подовжує."""
    base = url.rstrip("/")
    return base if base.endswith(APPLY_PATH) else base + APPLY_PATH


def project_url(url: str) -> str:
    base = url.rstrip("/")
    return base[: -len(APPLY_PATH)] if base.endswith(APPLY_PATH) else url


def cut_note(note: str, limit: int = MOTIVATION_MAX) -> str:
    """Обрізає текст до limit символів по межі слова (maxlength поля мотивації)."""
    if len(note) <= limit:
        return note
    cut = note[: limit + 1]
    idx = max(cut.rfind(" "), cut.rfind("\n"))
    return (cut[:idx] if idx > 0 else note[:limit]).rstrip()


def project_ready(text: str) -> bool:
    """Проєкт відрендерився: є номер проєкту, а не лише оболонка SPA."""
    return "Job ID" in text or "Projekt-ID" in text


def _open(page, url: str, selector: str, settle_ms: int, ready=None) -> str:
    """Перехід, очікування селектора (таймаут не помилка), текст body після стабілізації."""
    page.goto(url, wait_until="domcontentloaded", timeout=GOTO_TIMEOUT_MS)
    try:
        page.wait_for_selector(selector, timeout=FORM_WAIT_MS)
    except Exception:  # noqa: BLE001, таймаут: стан визначить page_state чи перевірка форми
        log.info("нема %s на %s за %d мс", selector, url, FORM_WAIT_MS)
    return settle_body(page, settle_ms, page.inner_text("body"), ready=ready)


def _applications_info(page) -> str:
    """Текст блоку applications-info; без блоку порожньо."""
    info = page.locator(APPLICATIONS_INFO_SELECTOR)
    return info.first.inner_text(timeout=3000) if info.count() else ""


def _cv_confirmed(page, cv: Path, wait_ms: int) -> bool:
    """Чекає, поки ім'я файлу CV з'явиться в iframe завантаження чи на сторінці.

    CXS показує завантажений файл назвою; без неї Submit пішов би без CV.
    """
    names = (cv.name, cv.stem)
    frame_body = page.frame_locator(CV_FRAME_SELECTOR).locator(CV_BODY_SELECTOR)
    elapsed = 0
    while True:
        for read in (lambda: frame_body.first.inner_text(timeout=2000), lambda: page.inner_text("body")):
            try:
                text = read()
            except Exception:
                log.debug("текст для перевірки CV не читається", exc_info=True)
                continue
            if any(n and n in text for n in names):
                return True
        if elapsed >= wait_ms:
            return False
        page.wait_for_timeout(POLL_MS)
        elapsed += POLL_MS


def apply(page, url: str, note: str, rate: str, currency: str, cv: Path, shots_dir: Path,
          dry_run: bool, wait_ms: int = SUBMIT_WAIT_MS) -> ApplyResult:
    """Заповнює і подає форму GULP; все незавершене повертає як handoff з причиною."""
    from bot.apply.forms import screenshot

    shots: list[str] = []
    clicked = False

    def result(status: str, **kw) -> ApplyResult:
        return ApplyResult(status, PORTAL, url, list(shots), **kw)

    def shot(name: str) -> None:
        # Скріншот не має перетворити подану заявку на помилку.
        try:
            shots.append(screenshot(page, shots_dir, name))
        except Exception:
            log.warning("скріншот %s не вдався", name, exc_info=True)

    try:
        text = _open(page, project_url(url), APPLY_BUTTON_SELECTOR, SETTLE_MS, project_ready)
    except Exception as e:  # noqa: BLE001, сторінка не відкрилась: failed, не crash
        return result("failed", reason=f"goto: {type(e).__name__}: {e}"[:300])

    try:
        state = page_state(page.url, text)
        if state == "login_required":
            shot("apply_login_required.png")
            return result("login_required", reason="gulp login required")
        if state == "closed":
            shot("apply_closed.png")
            return result("closed", reason="posting closed")
        # Той самий маркер, що підтверджує подачу: друга заявка не має піти ніколи.
        if APPLIED_RE.search(_applications_info(page)):
            return result("closed", reason="already applied")
        # Відрендерений проєкт під логіном без кнопки: подати нікуди.
        if LOGGED_IN_MARKER in text and not page.locator(APPLY_BUTTON_SELECTOR).count():
            shot("apply_no_apply_button.png")
            return result("handoff", reason="form_not_found: no apply button")

        # Форму чекаємо селектором поля, settle тут лише марнував би час.
        text = _open(page, form_url(url), MOTIVATION_SELECTOR, 0)
        if APPLIED_RE.search(text):
            return result("closed", reason="already applied")
        motivation = page.locator(MOTIVATION_SELECTOR)
        rate_input = page.locator(RATE_SELECTOR)
        currency_select = page.locator(CURRENCY_SELECTOR)
        submit_btn = page.locator(SUBMIT_SELECTOR)
        cv_input = page.frame_locator(CV_FRAME_SELECTOR).locator(CV_INPUT_SELECTOR)
        if motivation.count():
            # iframe завантаження підтягується окремо від форми і в body не видно.
            try:
                cv_input.first.wait_for(state="attached", timeout=FORM_WAIT_MS)
            except Exception:  # noqa: BLE001, нема поля CV: нижче це form_not_found
                log.info("поле CV в iframe не з'явилось за %d мс", FORM_WAIT_MS)
        if not motivation.count():
            if page_state(page.url, text) == "login_required":
                shot("apply_login_required.png")
                return result("login_required", reason="gulp login required")
            shot("apply_form_not_found.png")
            return result("handoff", reason="form_not_found")
        missing = [name for name, loc in (("rate", rate_input), ("currency", currency_select),
                                          ("submit", submit_btn), ("cv", cv_input)) if not loc.count()]
        if missing:
            log.warning("форма GULP без полів %s на %s", ", ".join(missing), page.url)
            shot("apply_form_not_found.png")
            return result("handoff", reason="form_not_found")

        motivation.first.fill(cut_note(note), timeout=5000)
        rate_input.first.fill(rate, timeout=5000)
        currency_select.first.select_option(currency, timeout=5000)
        if not dry_run:
            # onchange вантажить файл одразу: це вже передача в GULP.
            cv_input.first.set_input_files(str(cv), timeout=15000)
            if not _cv_confirmed(page, cv, CV_CONFIRM_MS):
                shot("apply_cv_unconfirmed.png")
                return result("handoff", reason="cv_upload_unconfirmed")
        shot("apply_form_filled.png")
        if dry_run:
            return result("dry_run", reason="filled 3, uploaded 0 (dry run skips the CV upload)")

        # Фраза, що вже є на сторінці до кліку, сама по собі не доказ подачі.
        baseline = _confirmation(page.inner_text("body"), _applications_info(page)) is not None
        # Прапорець до кліку: клік, що кинув виняток, уже міг подати заявку.
        clicked = True
        submit_btn.first.click(timeout=5000)
        return _await_confirmation(page, baseline, wait_ms, shot, result)
    except Exception as e:
        # Сторінка лишається відкритою: оператор розбереться. Після кліку заявка
        # могла піти: окрема причина, щоб не подати вдруге.
        log.exception("gulp %s", url)
        prefix = "error_after_submit" if clicked else "error"
        return result("handoff", reason=f"{prefix}: {type(e).__name__}: {e}"[:300])


def _confirmation(text: str, info: str = "") -> str | None:
    """Фраза подяки в body або маркер «ви вже подались» у блоці applications-info."""
    m = CONFIRMATION_RE.search(text or "") or APPLIED_RE.search(info or "")
    return m.group(0) if m else None


def _await_confirmation(page, baseline: bool, wait_ms: int, shot, result) -> ApplyResult:
    """Опитує body після кліку до wait_ms: фраза подяки чи маркер «ви вже подались»."""
    elapsed = 0
    while elapsed < wait_ms:
        page.wait_for_timeout(POLL_MS)
        elapsed += POLL_MS
        try:
            text = page.inner_text("body")
            info = _applications_info(page)
        except Exception:  # перерендер чи навігація: чекаємо далі
            log.warning("body після кліку не читається, чекаю далі", exc_info=True)
            continue
        found = _confirmation(text, info)
        if found and not baseline:
            shot("apply_confirmation.png")
            return result("submitted", confirmation_text=found)
    shot("apply_after_submit.png")
    return result("handoff", reason="no_confirmation")
