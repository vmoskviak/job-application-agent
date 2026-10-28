"""Адаптер LinkedIn під логіном: Easy Apply покроково, зовнішній Apply, маркери стану.

Селектори й тексти з живого захоплення 28.10.2026 (план 2d, розділ «Captured»).
Класи LinkedIn обфусковані, тож усе знаходиться за `data-view-name`, `aria-*`
і тегами. Модалка Easy Apply це нативний `<dialog open>`: поля читаються лише в
її межах (`ats_form.fill_form(..., root=MODAL_SELECTOR)`), відповіді дає той самий
`ats_answers.answer_for`, що й для ATS-форм.

Все незавершене (обов'язкове поле без відповіді, помилка валідації, тиша після
Submit, виняток) стає `handoff`: модалка лишається відкритою, оператор доробляє
руками. `easy_apply` ніколи не закриває ні модалку, ні сторінку. Тиха подача:
заявка не йде, поки прапорець Follow компанії увімкнений (`follow_still_checked`),
і не йде зі збереженим резюме замість щойно завантаженого (`resume_not_selected`,
а коли кнопки Upload resume так і не було до Submit, `resume_not_uploaded`). На
кожному кроці спершу резюме й поля, лише потім пошук кнопки Submit.

`page_state` сканує весь текст body, разом з описом вакансії, тож маркери
рестрикції це лише повні фрази самого LinkedIn, не окремі слова.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Literal

from bot.apply.ats_answers import AnswerContext
from bot.apply.ats_detect import PROBE_JS
from bot.apply.ats_form import FillReport, fill_form
from bot.apply.models import ApplyResult
from bot.ats import decode_safety_url
from bot.llm import LLM

log = logging.getLogger(__name__)

PORTAL = "linkedin"

# Контрол Apply на сторінці вакансії, єдиний на сторінці: <button> для Easy Apply,
# <a href=".../safety/go/?url=…"> для подачі на сайті компанії.
EASY_APPLY_SELECTOR = 'button[data-view-name="job-apply-button"]'
OFFSITE_SELECTOR = 'a[data-view-name="job-apply-button"]'
MODAL_SELECTOR = "dialog[open]"

# Кнопки модалки за data-view-name; тексти лише запасний шлях.
NEXT_VIEW = "continue-unify"
REVIEW_VIEW = "review-unify"
SUBMIT_VIEW = "submit-unify"
NEXT_TEXTS = ("Next", "Weiter")
REVIEW_TEXTS = ("Review", "Überprüfen")
SUBMIT_TEXTS = ("Submit application", "Bewerbung senden")
# Збереженого резюме не вибираємо: input[type=file] нема, кнопка відкриває file chooser.
UPLOAD_RESUME_SELECTOR = f'{MODAL_SELECTOR} button[data-view-name="upload-document-unify"]'
# «Follow <company>…» на кроці Review, увімкнено за замовчуванням: клік вимикає.
FOLLOW_CHECKBOX_SELECTOR = (
    f'{MODAL_SELECTOR} div[role=checkbox][data-view-name="follow-company-unify"][aria-checked="true"]'
)

# Підтвердження після Submit ще не бачене наживо: широкий список (спека §5.4).
CONFIRMATION_RE = re.compile(
    r"application was sent|application sent|your application has been submitted"
    r"|bewerbung wurde gesendet|application submitted",
    re.IGNORECASE,
)
# Сторінка вакансії, на яку вже подано: «Application status» і під ним «Application submitted».
APPLIED_MARKER_RE = re.compile(r"Application status\s+Application submitted")
APPLIED_SLOT_SELECTOR = "#AppliedHowYouFitSlot"
APPLIED_PHRASE = "Application submitted"
# Сторінка вакансії відрендерилась: контрол Apply (будь-який) або слот «Applied».
APPLY_READY_SELECTOR = f'[data-view-name="job-apply-button"], {APPLIED_SLOT_SELECTOR}'
CLOSED_RE = re.compile(r"no longer accepting applications|nimmt keine bewerbungen mehr", re.IGNORECASE)
RESTRICTED_RE = re.compile(
    r"we(?:'|’)ve restricted your account|your account has been restricted"
    r"|we(?:'|’)ve detected unusual activity|ungewöhnliche aktivität festgestellt",
    re.IGNORECASE,
)
CHECKPOINT_URL = "linkedin.com/checkpoint/"
LOGIN_URLS = ("linkedin.com/login", "/authwall", "/uas/login")
# Глобальна навігація під логіном має пункт «Me»; «Sign in» без нього це стіна логіну.
_SIGN_IN_RE = re.compile(r"\bSign in\b")
_NAV_ME_RE = re.compile(r"\bMe\b")

# Текст помилки під полем модалки («Invalid input») разом із підписом поля.
HELPER_ERRORS_JS = r"""
(() => {
  const out = [];
  document.querySelectorAll('dialog[open] [data-testid="text-input-helper-text"]').forEach((h) => {
    const text = (h.innerText || "").replace(/\s+/g, " ").trim();
    if (!text) return;
    let box = h.parentElement, el = null;
    while (box && box.tagName !== "DIALOG" && !(el = box.querySelector("input,select,textarea"))) box = box.parentElement;
    let label = el ? (el.getAttribute("aria-label") || "") : "";
    if (!label && el && el.labels && el.labels.length) label = el.labels[0].innerText || "";
    out.push({label: label.replace(/\s+/g, " ").trim(), text});
  });
  return out;
})()
"""

GOTO_TIMEOUT_MS = 30000
APPLY_WAIT_MS = 15000
SETTLE_MS = 2000
STEP_SETTLE_MS = 800
MODAL_WAIT_MS = 15000
SUBMIT_WAIT_MS = 30000
POLL_MS = 1000
RESUME_WAIT_MS = 10000
RESUME_POLL_MS = 500
MAX_LLM = 3


def page_state(url: str, text: str) -> Literal["restricted", "login_required", "closed", "open"]:
    """Стан сторінки вакансії за адресою після переходу й текстом body."""
    url = url or ""
    text = text or ""
    if CHECKPOINT_URL in url or RESTRICTED_RE.search(text):
        return "restricted"
    if any(marker in url for marker in LOGIN_URLS):
        return "login_required"
    if _SIGN_IN_RE.search(text) and not _NAV_ME_RE.search(text):
        return "login_required"
    if CLOSED_RE.search(text):
        return "closed"
    return "open"


def _open(page, url: str) -> None:
    """Перехід і очікування контролу Apply чи маркера Applied: SPA під логіном рендерить їх
    пізніше за domcontentloaded. Таймаут не помилка: стан визначать page_state і локатори."""
    page.goto(url, wait_until="domcontentloaded", timeout=GOTO_TIMEOUT_MS)
    try:
        page.wait_for_selector(APPLY_READY_SELECTOR, timeout=APPLY_WAIT_MS)
    except Exception:  # noqa: BLE001, таймаут: закрита вакансія, стіна логіну чи сторінка без Apply
        log.info("нема контролу Apply на %s за %d мс", url, APPLY_WAIT_MS)
    page.wait_for_timeout(SETTLE_MS)


def resolve_offsite(page, url: str) -> tuple[str, str | None]:
    """Стан вакансії і зовнішнє посилання Apply (лише якір job-apply-button, ніколи
    перше safety/go на сторінці); посилання None, якщо стан не open чи якоря нема."""
    _open(page, url)
    state = page_state(page.url, page.inner_text("body"))
    if state != "open":
        return state, None
    anchor = page.locator(OFFSITE_SELECTOR)
    href = anchor.first.get_attribute("href") if anchor.count() else None
    return state, decode_safety_url(href) if href else None


def _modal_text(page) -> str:
    modal = page.locator(MODAL_SELECTOR)
    return modal.first.inner_text() if modal.count() else ""


def _already_applied(page, text: str) -> bool:
    """Подано раніше: маркери Applied і жодного контролу Easy Apply."""
    marked = bool(APPLIED_MARKER_RE.search(text)) or page.locator(APPLIED_SLOT_SELECTOR).count() > 0
    return marked and page.locator(EASY_APPLY_SELECTOR).count() == 0


def _button(page, view: str, texts: tuple[str, ...]):
    """Кнопка модалки за data-view-name, інакше за точним текстом; None, якщо нема."""
    loc = page.locator(f'{MODAL_SELECTOR} button[data-view-name="{view}"]')
    if loc.count():
        return loc.first
    rx = re.compile(rf"^\s*(?:{'|'.join(map(re.escape, texts))})\s*$", re.IGNORECASE)
    loc = page.locator(f"{MODAL_SELECTOR} button", has_text=rx)
    return loc.first if loc.count() else None


def probe_submitted(page) -> str:
    """Фраза підтвердження після Submit або "".

    Модалка відкрита: CONFIRMATION_RE у її тексті і кнопки Submit уже нема.
    Модалки нема: сторінка вакансії з маркерами Applied.
    """
    if page.locator(MODAL_SELECTOR).count():
        if _button(page, SUBMIT_VIEW, SUBMIT_TEXTS) is not None:
            return ""
        m = CONFIRMATION_RE.search(_modal_text(page))
        return m.group(0) if m else ""
    text = page.inner_text("body")
    if APPLIED_MARKER_RE.search(text) or page.locator(APPLIED_SLOT_SELECTOR).count():
        return APPLIED_PHRASE
    return ""


def _probe_errors(page) -> tuple[list[str], int]:
    d = page.evaluate(PROBE_JS)
    return [str(t) for t in d.get("error_texts") or []], int(d.get("invalid_count") or 0)


def _helper_texts(page) -> list[str]:
    """Тексти під полями модалки, з підписом поля, якщо він є."""
    return [f'{e["label"]}: {e["text"]}' if e.get("label") else str(e.get("text", ""))
            for e in page.evaluate(HELPER_ERRORS_JS) or []]


def _validation(page, base_helpers: list[str], base_errors: list[str], base_invalid: int) -> list[str]:
    """Помилки після Next/Review: нові тексти під полями (підказки й лічильники, що були
    до кліку, не рахуються), інакше нові помилки чи більше aria-invalid з PROBE_JS."""
    helper = [t for t in _helper_texts(page) if t not in base_helpers]
    if helper:
        return helper
    errors, invalid = _probe_errors(page)
    fresh = [t for t in errors if t not in base_errors]
    if fresh:
        return fresh
    return [f"{invalid} invalid"] if invalid > base_invalid else []


def _css_string(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def resume_radio_selector(file_name: str) -> str:
    """Вибраний файл у списку резюме модалки: div[role=radio] з aria-label = ім'я файлу."""
    return f'{MODAL_SELECTOR} div[role=radio][aria-label={_css_string(file_name)}][aria-checked="true"]'


def _upload_resume(page, resume: Path) -> Literal["", "selected", "not_selected"]:
    """Завантажує резюме пакета через file chooser кнопки Upload resume.

    "" коли кнопки нема (крок без резюме). Інакше чекає до RESUME_WAIT_MS, поки
    завантажений файл стане вибраним radio: за замовчуванням вибране збережене
    резюме 2018 року, і подача з ним гірша за hand-off.
    """
    btn = page.locator(UPLOAD_RESUME_SELECTOR)
    if not btn.count():
        return ""
    with page.expect_file_chooser() as fc:
        btn.first.click(timeout=5000)
    fc.value.set_files(str(resume))
    radio = resume_radio_selector(Path(resume).name)
    waited = 0
    while True:
        if page.locator(radio).count():
            return "selected"
        if waited >= RESUME_WAIT_MS:
            return "not_selected"
        page.wait_for_timeout(RESUME_POLL_MS)
        waited += RESUME_POLL_MS


def _unfollow(page) -> bool:
    """Вимикає Follow компанії; False, якщо після кліку прапорець досі увімкнений."""
    box = page.locator(FOLLOW_CHECKBOX_SELECTOR)
    if not box.count():
        return True
    box.first.click(timeout=3000)
    page.wait_for_timeout(STEP_SETTLE_MS)
    return page.locator(FOLLOW_CHECKBOX_SELECTOR).count() == 0


def easy_apply(page, url: str, ctx: AnswerContext, llm: LLM | None, brief: str, shots_dir: Path,
               dry_run: bool, max_steps: int = 8, wait_ms: int = SUBMIT_WAIT_MS) -> ApplyResult:
    """Easy Apply покроково: заповнення кожного кроку модалки, Review, Submit, підтвердження."""
    from bot.apply.forms import screenshot

    shots: list[str] = []
    clicked = False
    resume_uploaded = False   # Submit лише після резюме, завантаженого в цьому проході
    total = FillReport()

    def result(status: str, **kw) -> ApplyResult:
        return ApplyResult(status, PORTAL, url, list(shots), **kw)

    def shot(name: str) -> None:
        try:
            shots.append(screenshot(page, shots_dir, name))
        except Exception:
            log.warning("скріншот %s не вдався", name, exc_info=True)

    try:
        _open(page, url)
    except Exception as e:  # noqa: BLE001, сторінка не відкрилась: failed, не crash
        return result("failed", reason=f"goto: {type(e).__name__}: {e}"[:300])

    try:
        text = page.inner_text("body")
        state = page_state(page.url, text)
        if state == "restricted":
            shot("apply_restricted.png")
            return result("restricted", reason="account restricted or checkpoint")
        if state == "login_required":
            shot("apply_login_required.png")
            return result("login_required", reason="linkedin login required")
        if _already_applied(page, text):
            return result("closed", reason="already applied")
        if state == "closed":
            shot("apply_closed.png")
            return result("closed", reason="posting closed")

        apply_btn = page.locator(EASY_APPLY_SELECTOR)
        if not apply_btn.count():
            shot("apply_no_easy_apply.png")
            return result("handoff", reason="no_easy_apply")
        apply_btn.first.click(timeout=5000)
        page.wait_for_selector(MODAL_SELECTOR, timeout=MODAL_WAIT_MS)

        for n in range(1, max_steps + 1):
            page.wait_for_timeout(STEP_SETTLE_MS)
            # Резюме й поля до пошуку Submit: модалка з одного кроку чи крок резюме з іншою
            # кнопкою інакше пішли б із збереженим резюме 2018 року й незаповненими полями.
            upload = _upload_resume(page, ctx.resume)
            if upload == "not_selected":
                shot(f"apply_modal_step{n}.png")
                return result("handoff", reason="resume_not_selected")
            if upload == "selected":
                total.uploaded.append("Resume")
                resume_uploaded = True
            rep = fill_form(page, ctx, llm, brief, max_llm=max(0, MAX_LLM - total.llm_calls), root=MODAL_SELECTOR)
            total.filled += rep.filled
            total.uploaded += rep.uploaded
            total.llm_calls += rep.llm_calls
            submit_btn = _button(page, SUBMIT_VIEW, SUBMIT_TEXTS)
            if rep.open_required:
                shot(f"apply_modal_step{n}.png")
                return result("handoff", reason="open_required: " + "; ".join(rep.open_required))
            if submit_btn is not None:
                if not resume_uploaded:
                    shot("apply_form_filled.png")
                    return result("handoff", reason="resume_not_uploaded")
                unfollowed = _unfollow(page)
                shot("apply_form_filled.png")
                if not unfollowed:
                    return result("handoff", reason="follow_still_checked")
                if dry_run:
                    return result("dry_run", reason=(
                        f"filled {len(total.filled)}, uploaded {len(total.uploaded)}, "
                        f"llm {total.llm_calls}, steps {n}"))
                # Прапорець до кліку: клік, що кинув виняток, уже міг подати заявку.
                clicked = True
                submit_btn.click(timeout=5000)
                return _await_confirmation(page, wait_ms, shot, result)
            shot(f"apply_modal_step{n}.png")

            nxt = _button(page, NEXT_VIEW, NEXT_TEXTS) or _button(page, REVIEW_VIEW, REVIEW_TEXTS)
            if nxt is None:
                return result("handoff", reason="no_next_button")
            base_helpers = _helper_texts(page)
            base_errors, base_invalid = _probe_errors(page)
            nxt.click(timeout=5000)
            page.wait_for_timeout(STEP_SETTLE_MS)
            errors = _validation(page, base_helpers, base_errors, base_invalid)
            if errors:
                return result("handoff", reason="validation: " + "; ".join(errors))
        return result("handoff", reason="too_many_steps")
    except Exception as e:
        # Модалка лишається відкритою: оператор розбереться. Після кліку Submit заявка
        # могла піти: окрема причина, щоб не подати вдруге.
        log.exception("LinkedIn Easy Apply %s", url)
        prefix = "error_after_submit" if clicked else "error"
        return result("handoff", reason=f"{prefix}: {type(e).__name__}: {e}"[:300])


def _await_confirmation(page, wait_ms: int, shot, result) -> ApplyResult:
    """Опитує сторінку після Submit до wait_ms; знімок, що впав (перерендер), не помилка."""
    elapsed = 0
    while elapsed < wait_ms:
        page.wait_for_timeout(POLL_MS)
        elapsed += POLL_MS
        try:
            phrase = probe_submitted(page)
        except Exception:
            log.warning("знімок після Submit не вдався, чекаю далі", exc_info=True)
            phrase = ""
        if phrase:
            shot("apply_confirmation.png")
            return result("submitted", confirmation_text=phrase)
    shot("apply_after_submit.png")
    return result("handoff", reason="no_confirmation")
