"""Адаптер freelancermap під логіном: Anschreiben, вкладення, місячна квота, підтвердження.

Селектори й тексти з живого захоплення 28.10.2026 (план 2d, розділ «Captured»).
Форма стоїть прямо на сторінці проєкту під `<h2 id="project-contact-form">Bewerben</h2>`,
без елемента `<form>`. Заповнюємо лише Anschreiben, файли й два чекбокси
контактів; візитку (`vcard[]`) і кнопку генерації тексту (`ai-application-button`)
адаптер не чіпає взагалі.

Подача витрачає одну з місячних заявок платформи (лічильник «Restliche
Bewerbungen für diesen Monat: n/total»), тож підтвердженням рахується або фраза
успіху, або лічильник, що зменшився рівно на одиницю, і лише тоді, коли кнопки
відправки на сторінці вже нема. Все незавершене стає `handoff`, сторінка
лишається відкритою для оператора. `apply` її не закриває.

Без вкладень заявка не йде (`no_attachments`). Після `set_input_files` код
перевіряє лише `files.length` самого input; відрендерений список вкладень (його
DOM не захоплено) перевіряється очима на скріншоті першого тіньового проходу.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Literal

from bot.apply.models import ApplyResult
from bot.scrapers.browser import settle_body

log = logging.getLogger(__name__)

PORTAL = "freelancermap"

COVER_SELECTOR = "textarea#cover-letter"
# Прихований input усередині кнопки «Dateien hochladen»: set_input_files без кліку.
FILE_INPUT_SELECTOR = "input.inputfile[type=file]"
EMAIL_CHECKBOX_SELECTOR = "input[name=sendEmail]"
PHONE_CHECKBOX_SELECTOR = "input[name=sendPhone]"
SUBMIT_SELECTOR = 'button[data-id="contact-send-application"]'
SUBMIT_TEXT = "Bewerbung verschicken"
# Ніколи не чіпати: візитка лишається як є, текст генерує не платформа.
VCARD_SELECTOR = 'input[name="vcard[]"]'
AI_BUTTON_SELECTOR = 'button[data-id="ai-application-button"]'
# Банер закритого проєкту; ним же чекаємо рендеру сторінки разом із формою.
CLOSED_BANNER_SELECTOR = 'div.banner[data-testid="applicationsDisabled"]'

QUOTA_RE = re.compile(r"Restliche Bewerbungen für diesen Monat:\s*(\d+)\s*/\s*(\d+)")
QUOTA_LIMIT_RE = re.compile(r"Bewerbungslimit erreicht")
LOGIN_URL_MARKER = "/login"
LOGIN_TEXT_RE = re.compile(r"Das Bewerbungsformular ist nur für eingeloggte Nutzer verfügbar")
CLOSED_RE = re.compile(r"Keine Kontaktanfrage möglich|bereits geschlossen|Bewerbungsphase wurde beendet")
# Проєкт, на який уже подано: друга заявка не має піти ніколи.
ALREADY_APPLIED_RE = re.compile(r"Bewerbung erfolgreich abgeschickt am")
CONFIRMATION_RE = re.compile(r"Ihre Bewerbung wurde erfolgreich versendet|Bewerbung erfolgreich abgeschickt am")
PLATFORM_ERROR_RE = re.compile(r"Es ist ein Fehler aufgetreten")

GOTO_TIMEOUT_MS = 30000
SETTLE_MS = 5000
FORM_WAIT_MS = 10000
UPLOAD_SETTLE_MS = 2000
SUBMIT_WAIT_MS = 30000
POLL_MS = 1000


def _quota(text: str) -> tuple[int, int] | None:
    m = QUOTA_RE.search(text or "")
    return (int(m.group(1)), int(m.group(2))) if m else None


def remaining_quota(text: str) -> int | None:
    """Скільки заявок лишилось цього місяця; None, якщо лічильника на сторінці нема."""
    q = _quota(text)
    return q[0] if q else None


def page_state(url: str, text: str, has_form: bool) -> Literal["login_required", "closed", "quota", "open"]:
    """Стан сторінки проєкту за адресою після переходу, текстом body і наявністю форми.

    Порядок важливий: сторінка без логіну теж без форми, тож логін першим;
    закритий чи вже поданий проєкт важливіший за вичерпану квоту, квота
    важливіша за відсутню форму.
    """
    url = url or ""
    text = text or ""
    if LOGIN_URL_MARKER in url or LOGIN_TEXT_RE.search(text):
        return "login_required"
    if CLOSED_RE.search(text) or ALREADY_APPLIED_RE.search(text):
        return "closed"
    # Вичерпана квота може ховати форму: перевіряємо до запасного «нема форми».
    if QUOTA_LIMIT_RE.search(text) or remaining_quota(text) == 0:
        return "quota"
    if not has_form:
        return "closed"
    return "open"


def _open(page, url: str) -> str:
    """Перехід, очікування форми чи банера, текст body після стабілізації."""
    page.goto(url, wait_until="domcontentloaded", timeout=GOTO_TIMEOUT_MS)
    try:
        page.wait_for_selector(f"{COVER_SELECTOR}, {CLOSED_BANNER_SELECTOR}", timeout=FORM_WAIT_MS)
    except Exception:  # noqa: BLE001, таймаут: логін чи сторінка без форми, стан визначить page_state
        log.info("ні форми, ні банера закриття на %s за %d мс", url, FORM_WAIT_MS)
    return settle_body(page, SETTLE_MS, page.inner_text("body"))


def _tick(page, selector: str) -> None:
    """Відмічає чекбокс, лише якщо він є і ще не відмічений (клік зняв би позначку)."""
    box = page.locator(selector)
    if box.count() and not box.first.is_checked():
        box.first.check(timeout=3000)


def apply(page, url: str, note: str, attachments: list[Path], shots_dir: Path,
          dry_run: bool, wait_ms: int = SUBMIT_WAIT_MS) -> ApplyResult:
    """Заповнює і подає форму проєкту; все незавершене повертає як handoff з причиною."""
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
        text = _open(page, url)
    except Exception as e:  # noqa: BLE001, сторінка не відкрилась: failed, не crash
        return result("failed", reason=f"goto: {type(e).__name__}: {e}"[:300])

    try:
        state = page_state(page.url, text, page.locator(COVER_SELECTOR).count() > 0)
        if state == "login_required":
            shot("apply_login_required.png")
            return result("login_required", reason="freelancermap login required")
        if state == "closed":
            if ALREADY_APPLIED_RE.search(text):
                return result("closed", reason="already applied")
            shot("apply_closed.png")
            reason = "posting closed" if CLOSED_RE.search(text) else "closed: no application form"
            return result("closed", reason=reason)
        if state == "quota":
            shot("apply_quota.png")
            return result("quota", reason="platform_quota")

        if not attachments:
            # Заявка без CV гірша за ручну подачу: нічого не заповнюємо.
            return result("handoff", reason="no_attachments")
        submit_btn = page.locator(SUBMIT_SELECTOR)
        if not submit_btn.count():
            shot("apply_no_submit.png")
            return result("handoff", reason="no_submit_button")
        file_input = page.locator(FILE_INPUT_SELECTOR)
        if not file_input.count():
            shot("apply_no_file_input.png")
            return result("handoff", reason="no_file_input")

        page.locator(COVER_SELECTOR).first.fill(note, timeout=5000)
        file_input.first.set_input_files([str(p) for p in attachments], timeout=10000)
        attached = file_input.first.evaluate("el => el.files ? el.files.length : 0")
        if attached != len(attachments):
            shot("apply_upload_not_attached.png")
            return result("handoff", reason="upload_not_attached")
        page.wait_for_timeout(UPLOAD_SETTLE_MS)
        _tick(page, EMAIL_CHECKBOX_SELECTOR)
        _tick(page, PHONE_CHECKBOX_SELECTOR)
        shot("apply_form_filled.png")
        if dry_run:
            return result("dry_run", reason=f"filled 1, uploaded {len(attachments)}")

        before = _quota(page.inner_text("body"))
        # Прапорець до кліку: клік, що кинув виняток, уже міг подати заявку.
        clicked = True
        submit_btn.first.click(timeout=5000)
        return _await_confirmation(page, before, wait_ms, shot, result)
    except Exception as e:
        # Сторінка лишається відкритою: оператор розбереться. Після кліку заявка
        # могла піти: окрема причина, щоб не подати вдруге.
        log.exception("freelancermap %s", url)
        prefix = "error_after_submit" if clicked else "error"
        return result("handoff", reason=f"{prefix}: {type(e).__name__}: {e}"[:300])


def _submit_gone(page) -> bool:
    """Кнопки відправки вже нема; локатор, що впав на перерендері, рахується як «ще є»."""
    try:
        return page.locator(SUBMIT_SELECTOR).count() == 0
    except Exception:  # перерендер: спробуємо на наступному кроці
        log.warning("кнопка відправки не перевіряється, чекаю далі", exc_info=True)
        return False


def _await_confirmation(page, before: tuple[int, int] | None, wait_ms: int, shot, result) -> ApplyResult:
    """Опитує body після кліку до wait_ms: фраза успіху, лічильник мінус один чи помилка платформи.

    Лічильник мінус один сам по собі не доказ (міг оновитись після іншої заявки):
    приймається лише разом із тим, що кнопки відправки на сторінці вже нема.
    """
    elapsed = 0
    while elapsed < wait_ms:
        page.wait_for_timeout(POLL_MS)
        elapsed += POLL_MS
        try:
            text = page.inner_text("body")
        except Exception:  # перерендер чи навігація: чекаємо далі
            log.warning("body після кліку не читається, чекаю далі", exc_info=True)
            continue
        m = CONFIRMATION_RE.search(text)
        if m:
            shot("apply_confirmation.png")
            return result("submitted", confirmation_text=m.group(0))
        after = _quota(text)
        if before and after and after[0] == before[0] - 1 and _submit_gone(page):
            shot("apply_confirmation.png")
            return result("submitted", confirmation_text=f"quota {after[0]}/{after[1]}")
        if PLATFORM_ERROR_RE.search(text):
            shot("apply_after_submit.png")
            return result("handoff", reason="error_after_submit: platform error")
    shot("apply_after_submit.png")
    return result("handoff", reason="no_confirmation")
