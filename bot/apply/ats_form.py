"""Драйвер ATS-форми (Ashby, Greenhouse, Lever, Workable): заповнення, Submit, результат.

Поля читає `FIELD_JS` (індекс `i` збігається з `page.locator(FIELD_SELECTOR).nth(i)`),
відповіді дає `ats_answers.answer_for`, модель лише для вільного тексту і не
більше `max_llm` разів. Після Submit сторінку опитує `PROBE_JS`.

Все, що не вдалося довести до підтвердження (обов'язкове поле без відповіді,
виклик reCAPTCHA, помилки валідації, тиша після Submit), стає `handoff`:
вкладка лишається відкритою, оператор доробляє руками. `apply` сторінку
ніколи не закриває, це вирішує викликач.

Попередня перевірка `detect.classify_apply_page` тут не використовується: вона
бачить «recaptcha» на кожній сторінці Ashby/Greenhouse і віддає хибне `captcha`.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field, replace
from datetime import date
from pathlib import Path
from urllib.parse import urljoin, urlparse, urlunparse

from bot.apply.ats_answers import (
    _EEO_DECLINE,
    Answer,
    AnswerContext,
    _option_like,
    answer_for,
    clean_llm_answer,
    llm_allowed,
    llm_answer_problem,
    llm_prompt,
    llm_question,
    split_street,
)
from bot.apply.ats_detect import (
    PROBE_JS,
    PageProbe,
    closed_in,
    confirmation_in,
    outcome_after_submit,
)
from bot.apply.ats_fields import (
    FIELD_JS,
    FIELD_SELECTOR,
    Field,
    accepts_file,
    classify,
    documents_label,
    fields_from,
    visible_required,
)
from bot.apply.job_boards import (
    BOARD_JS,
    blocked_page,
    board_of,
    challenge_page,
    contact_captcha,
    employer_link,
    login_wall,
    own_apply,
)
from bot.apply.models import ApplyResult
from bot.ats import ats_from_url
from bot.llm import LLM

log = logging.getLogger(__name__)

# Kept for reference by tests and callers; the opener below no longer clicks by these selectors.
GH_APPLY_SELECTOR = 'a:has-text("Apply"), button:has-text("Apply")'
APPLY_SELECTOR = ('a[href*="application"], a[href$="/apply"], a:has-text("Apply"), button:has-text("Apply"), '
                  'a:has-text("Jetzt bewerben"), button:has-text("Jetzt bewerben"), a:has-text("Online bewerben"), '
                  'a[href*="bewerb"], a:has-text("Bewerben"), button:has-text("Bewerben")')
# Kept for backwards compatibility; readiness is now decided by _form_ready, not by this selector.
FORM_READY_SELECTOR = "input[type=file], form, input, textarea, select"
# ATS whose form_url already points at the form: no button is clicked there.
_DIRECT_FORM_ATS = {"ashby", "lever", "workable"}
# How many "Apply"-like clicks the opener makes before giving up (job page -> chooser -> form).
MAX_APPLY_HOPS = 3
ENTRY_POLL_MS = 500
# A job-board page renders its apply button after domcontentloaded; its redirect to the employer
# may also run in script, so the worker waits up to BOARD_REDIRECT_MS for the URL to leave the board.
BOARD_SETTLE_MS = 1500
BOARD_REDIRECT_MS = 10000
# Identity fields: two of them visible means an application form, not a search box or newsletter.
_IDENTITY_KINDS = {"first_name", "last_name", "full_name", "email", "phone"}
# Buttons and links that open the application form, best first. Matched on normalized text.
APPLY_PHRASES = (
    "apply now", "apply for this job", "apply for this position", "apply for this role", "apply to this job",
    "apply online", "start application", "start your application", "i'm interested", "i am interested",
    "jetzt online bewerben", "jetzt bewerben", "online bewerben", "direkt bewerben", "bewerbung starten",
    "zur bewerbung", "zum bewerbungsformular", "ich bin interessiert", "ich habe interesse", "bewerben",
    "apply", "postuler", "candidater", "solliciteer", "aplicar", "candidatar",
)
# Never clicked on the way to a form: social or account logins, sharing, saving, navigation away.
_APPLY_EXCLUDE = re.compile(
    r"linkedin|xing|indeed|google|apple|facebook|github|refer|empfehl|share|teilen|save|merken|speicher|"
    r"log ?in|sign ?(in|up)|anmeld|registrier|create (an )?account|konto|continue as|weiter als|newsletter|"
    r"job ?alert|benachrichtig|cookie|similar|ähnlich|all jobs|alle jobs|zurück|back to|print|drucken|"
    r"submit|absenden|senden",
    re.IGNORECASE)
_APPLY_HREF = re.compile(r"/apply\b|/application|bewerb|/c/new|oneclick-ui|candidat", re.IGNORECASE)
_MAX_TEXT = 40   # a long text is a paragraph link, not a button
# Marks every visible button or link with data-js-cand=<n> and returns them; the mark is the click handle.
CANDIDATES_JS = r"""() => {
  document.querySelectorAll('[data-js-cand]').forEach(e => e.removeAttribute('data-js-cand'));
  const out = [];
  const els = document.querySelectorAll('a, button, [role=button], input[type=submit], input[type=button]');
  for (const el of els) {
    const r = el.getBoundingClientRect();
    const st = getComputedStyle(el);
    if (r.width === 0 || r.height === 0 || st.visibility === 'hidden' || st.display === 'none') continue;
    const text = (el.innerText || el.value || el.getAttribute('aria-label') || el.title || '')
      .replace(/\s+/g, ' ').trim();
    const href = el.tagName === 'A' ? (el.getAttribute('href') || '') : '';
    if (!text && !href) continue;
    el.setAttribute('data-js-cand', String(out.length));
    out.push({i: out.length, text: text.slice(0, 80), href: href.slice(0, 300),
              target: el.getAttribute('target') || '', tag: el.tagName.toLowerCase()});
    if (out.length >= 150) break;
  }
  return out;
}"""
_LLM_CANDIDATES = 60
FORM_WAIT_MS = 15000
# Після Submit: токен reCAPTCHA Enterprise, POST і рендер підтвердження разом часто довші за 4 с.
SUBMIT_WAIT_MS = 30000
COMBOBOX_WAIT_MS = 800
# Після дії, що може відкрити нові поля (Yes/No, check, вибір опції), DOM
# перечитується; пауза дає React дорендерити залежні питання.
REVEAL_WAIT_MS = 300
POLL_MS = 1000
NETWORK_IDLE_MS = 5000
_MAX_STEPS = 300   # запобіжник від форми, що без кінця породжує нові поля
_SKIP_TYPES = {"hidden", "submit", "button", "reset", "image"}
_CHOICE_TYPES = ("radio", "checkbox")


@dataclass
class FillReport:
    filled: list[str] = field(default_factory=list)
    uploaded: list[str] = field(default_factory=list)
    llm_calls: int = 0
    open_required: list[str] = field(default_factory=list)
    # The candidate's CV (ctx.resume) went into a file field; a cover letter alone does not count.
    cv_uploaded: bool = False


def _name(f: Field) -> str:
    """Підпис поля для звіту; для radio це питання групи, не текст опції."""
    if f.type == "radio" and f.group:
        return f.group
    return f.label or f.group or f.placeholder or f.name or f"#{f.i}"


def _exact(text: str) -> re.Pattern:
    """Регулярка на точний текст без урахування регістру й пробілів довкола."""
    return re.compile(rf"^\s*{re.escape(text.strip())}\s*$", re.IGNORECASE)


class _StaleField(Exception):
    """Елемент за індексом уже не те поле: DOM змінився після читання FIELD_JS."""


# Літерал вибірки полів у FIELD_JS; field_js підміняє його вибіркою в межах кореня.
_FIELD_QUERY = 'document.querySelectorAll("input,select,textarea")'
_SHADOW_QUERY = 'const shadow = shadowOf("input,select,textarea");'


def field_selector(root: str = "") -> str:
    """FIELD_SELECTOR у межах кореня (`dialog[open]` модалки LinkedIn); без кореня як є."""
    if not root:
        return FIELD_SELECTOR
    return ",".join(f"{root} {part}" for part in FIELD_SELECTOR.split(","))


def field_js(root: str = "") -> str:
    """FIELD_JS, що читає лише поля всередині кореня: індекс i збігається з field_selector(root)."""
    if not root:
        return FIELD_JS
    if FIELD_JS.count(_FIELD_QUERY) != 1 or FIELD_JS.count(_SHADOW_QUERY) != 1:
        raise RuntimeError("FIELD_JS змінився: вибірку полів не знайдено для звуження")
    # A scoped read (LinkedIn modal) has no shadow fields: they would lie outside the root.
    return (FIELD_JS.replace(_FIELD_QUERY, f"document.querySelectorAll({json.dumps(field_selector(root))})")
            .replace(_SHADOW_QUERY, "const shadow = [];"))


def _read(page, root: str = "") -> list[Field]:
    return fields_from(page.evaluate(field_js(root)))


def _base_key(f: Field) -> tuple:
    """Стабільний ключ поля: id, інакше name, інакше (label, group, type)."""
    if f.id:
        return ("id", f.id)
    if f.name:
        return ("name", f.name)
    return ("lgt", f.label, f.group, f.type)


def _keyed(fields: list[Field]) -> list[tuple[tuple, Field]]:
    """Поля з ключами; однакові базові ключі (radio з одним name) розрізняє номер появи."""
    seen: dict[tuple, int] = {}
    out = []
    for f in fields:
        base = _base_key(f)
        n = seen.get(base, 0)
        seen[base] = n + 1
        out.append(((*base, n), f))
    return out


def _live(page, f: Field, root: str = ""):
    """Локатор поля f з перевіркою, що за індексом стоїть саме воно; інакше _StaleField.

    Сусідні radio мають один name і часто без id, тож для radio/checkbox звіряються
    атрибут value і підпис зі свіжого читання, не лише name. `root` звужує і локатор,
    і перечитування, як у `_read`.
    """
    el = page.locator(f'[data-js-field="{f.i}"]') if f.marked else page.locator(field_selector(root)).nth(f.i)
    if f.type in _CHOICE_TYPES:
        now = _read(page, root)
        ok = f.i < len(now) and (now[f.i].name, now[f.i].attr_value, now[f.i].label) == (f.name, f.attr_value, f.label)
    elif f.id:
        ok = (el.get_attribute("id") or "") == f.id
    elif f.name:
        ok = (el.get_attribute("name") or "") == f.name
    else:
        now = _read(page, root)
        ok = f.i < len(now) and _base_key(now[f.i]) == _base_key(f)
    # Two unlabelled file inputs (an avatar and a dropzone) share a base key: their accept tells them apart.
    if ok and f.type == "file":
        ok = (el.get_attribute("accept") or "") == f.accept
    if not ok:
        raise _StaleField(_name(f))
    return el


def _click_group_button(page, group: str, label: str) -> None:
    """Ashby малює Yes/No кнопками без radio: клік по кнопці в контейнері питання.

    Контейнер — [data-field-path] з label[for=path] цього питання, інакше батько підпису;
    кнопки <button aria-pressed data-option="yes|no">, інакше за текстом.
    """
    question = page.locator("label", has_text=_exact(group))
    box = page.locator("[data-field-path]", has=question)
    if not box.count():
        box = page.get_by_text(group, exact=True).first.locator("xpath=..")
    box = box.first
    option = label.strip().lower()
    btn = box.locator(f"button[data-option={option}]") if option in ("yes", "no") else None
    if btn is None or not btn.count():
        btn = box.locator("button", has_text=_exact(label))
    btn.first.click(timeout=3000)


def _pick_option(page, value: str) -> bool:
    """Клік по опції списку: точний збіг, інакше перша з першим словом значення.

    Yes чи No лише точна опція або єдина, що з цього слова починається («No, I …»): підрядком
    «No» сидить у «…sponsorship now», а з двох «Yes, I am an EU citizen» і «Yes, I have a work
    permit» перша не обов'язково правда. Інакше False, і _fill_combobox очищує поле (hand-off).
    """
    opts = page.locator("[role=option]")
    exact = opts.filter(has_text=_exact(value)).first
    if exact.count() and exact.is_visible():
        exact.click(timeout=3000)
        return True
    if value.strip().lower() in ("yes", "no"):
        lead = opts.filter(has_text=re.compile(rf"^\s*{re.escape(value.strip())}\b", re.IGNORECASE))
        if lead.count() == 1 and lead.first.is_visible():
            lead.first.click(timeout=3000)
            return True
        return False
    first = re.split(r"[\s,]+", value.strip())[0]
    opt = opts.filter(has_text=first).first
    if first and opt.count() and opt.is_visible():
        opt.click(timeout=3000)
        return True
    return False


def _fill_combobox(page, el, value: str) -> bool:
    """Друкує значення і клікає опцію (точну, інакше з першим словом); немає опції — поле очищується."""
    el.type(value)
    page.wait_for_timeout(COMBOBOX_WAIT_MS)
    if _pick_option(page, value):
        return True
    el.fill("")  # недрукований вибір форма однаково не прийме: лишаємо відкритим
    return False


def _decline_combobox(page, el) -> bool:
    """EEO-комбобокс: відкрити список і вибрати опцію відмови; немає такої — закрити й лишити."""
    el.click(timeout=3000)
    page.wait_for_timeout(COMBOBOX_WAIT_MS)
    opt = page.locator("[role=option]").filter(has_text=re.compile(_EEO_DECLINE, re.IGNORECASE)).first
    if opt.count() and opt.is_visible():
        opt.click(timeout=3000)
        return True
    el.press("Escape")
    return False


def _click_or_force(el) -> None:
    """Кастомні radio часто приховані під підписом: звичайний клік, інакше check(force)."""
    try:
        el.click(timeout=3000)
    except Exception:  # noqa: BLE001 — клік перекрив оверлей чи лейбл: ставимо прапорець силою
        el.check(force=True, timeout=3000)


def _apply_answer(page, f: Field, ans: Answer, radios: list[Field], root: str = "", date_format: str = "") -> bool:
    """Вносить відповідь у поле; False, якщо внести не вдалося."""
    if ans.kind == "option" and f.type == "radio":
        want = ans.value.strip().lower()
        hit = next((r for r in radios if r.label.strip().lower() == want), None)
        if hit is None:
            return False
        _click_or_force(_live(page, hit, root))
        return True
    if ans.kind == "option" and f.tag == "select":
        # Нативний <select> навіть з role=combobox чи list: лише select_option.
        _live(page, f, root).select_option(label=ans.value)
        return True
    if ans.kind == "text" and f.tag == "select":
        # Email у <select> (LinkedIn): текст лише як наявна опція, fill на select не працює.
        hit = _option_like(f.options or [], ans.value)
        if hit is None:
            return False
        _live(page, f, root).select_option(label=hit)
        return True
    if ans.kind == "option" and f.combobox:
        return _fill_combobox(page, _live(page, f, root), ans.value)
    if ans.kind == "option":
        if not f.group:
            return False
        _click_group_button(page, f.group, ans.value)
        return True
    el = _live(page, f, root)
    if ans.kind == "decline":
        return f.combobox and _decline_combobox(page, el)
    if ans.kind == "text":
        if f.combobox:
            return _fill_combobox(page, el, ans.value)
        el.fill(ans.value)
        return True
    if ans.kind == "file":
        el.set_input_files(ans.files or ans.value)
        return True
    if ans.kind == "check":
        try:
            el.check(timeout=3000)
        except Exception:  # noqa: BLE001 - a styled input: its label takes the click, force as the last resort
            labelled = el.evaluate("e => { const l = e.labels && e.labels[0]; if (!l) return false; "
                                   "l.click(); return e.checked; }")
            if not labelled:
                el.check(force=True, timeout=3000)
        return True
    if ans.kind == "date":
        # No Enter: inside a <form> Enter submits implicitly, even in dry_run. Tab closes the
        # date picker and moves the focus. The page may want its own format (LinkedIn: MM/DD/YYYY).
        value = ans.value
        if date_format:
            try:
                value = date.fromisoformat(ans.value).strftime(date_format)
            except ValueError:
                value = ans.value
        el.fill(value)
        el.press("Tab")
        return True
    return False


def _open_required(fields: list[Field]) -> list[str]:
    """Видимі обов'язкові поля, що лишилися порожніми; група choice — одним записом."""
    answered = {f.group for f in fields if f.group and f.type in _CHOICE_TYPES and f.value.strip()}
    out: list[str] = []
    for f in visible_required(fields):
        if f.type in _CHOICE_TYPES and f.group:
            if f.group in answered:
                continue
            name = f.group
        else:
            if f.value.strip():
                continue
            name = _name(f)
        if name not in out:
            out.append(name)
    return out


def _package(ctx: AnswerContext, files: list[Field]) -> list[str]:
    """Everything for one upload that takes several files: the CV, the cover letter unless the form
    has its own field for it, then the other package files (the Zeugnisse marked portal: true).
    Each file once; files other than the CV only when they exist."""
    own_cover = any(classify(g) == "cover_letter_file" for g in files)
    out = [str(ctx.resume)]
    extras = ([ctx.cover_letter] if ctx.cover_letter else []) + list(ctx.attachments)
    for p in extras:
        if own_cover and ctx.cover_letter and Path(p) == Path(ctx.cover_letter):
            continue
        if str(p) not in out and Path(p).exists():
            out.append(str(p))
    return out


def _cv_upload(f: Field, kind: str, ans: Answer | None, fields: list[Field], ctx: AnswerContext,
               cv_done: bool, ats: str | None) -> Answer | None:
    """Which file a file field gets, given the whole form. German forms often have one generic upload
    («Anlagen», «Bewerbungsunterlagen») and no CV field: then the first documents field takes the CV,
    and so does a lone unlabelled upload, except on Ashby, where that is the «Autofill from resume»
    uploader. A form with a CV field keeps answer_for's per-field rules. An upload that takes the CV
    and several files, and names documents, gets the whole package (_package).

    «Lone» means the only file input on the page, hidden ones included: a web-component dropzone may
    hide its input from the visibility check, and then the unlabelled avatar next to it looked like
    the only upload (SmartRecruiters, 30.10.2026). A file the input's accept attribute rejects is
    never given: the field stays open, a required one goes to the operator."""
    if f.type != "file":
        return ans
    files = [g for g in fields if g.visible and g.type == "file"]
    if not cv_done and not any(classify(g) == "resume" for g in files):
        any_file = [g for g in fields if g.type == "file"]
        lone = kind == "unknown" and len(any_file) == 1 and ats != "ashby" and not f.ashby_autofill
        if kind == "documents" or lone:
            ans = Answer("file", str(ctx.resume))
            kind = "documents"
    if (ans is not None and ans.kind == "file" and ans.value == str(ctx.resume) and f.multiple
            and (kind == "documents" or documents_label(f))):
        ans = Answer("file", ans.value, files=_package(ctx, files))
    if ans is not None and ans.kind == "file" and f.accept:
        taken = [p for p in (ans.files or [ans.value]) if accepts_file(f.accept, p)]
        if not taken:
            log.warning("file field %r accepts only %r: nothing uploaded", _name(f), f.accept)
            return None
        if ans.files:
            ans = Answer("file", taken[0], files=taken)
    return ans


def fill_form(page, ctx: AnswerContext, llm: LLM | None, brief: str, max_llm: int = 3,
              root: str = "", date_format: str = "", ats: str | None = None) -> FillReport:
    """Прохід по видимих полях у порядку DOM, потім перечитування й open_required.

    Передзаповнене не чіпається, крім email, що не є жодною з адрес клієнта (профіль, own_emails).
    Email у select чи комбобоксі, куди адресу профілю внести не вдалося (її нема
    серед опцій), іде в open_required: викликач віддає форму оператору.
    Нотатка йде лише в перше поле cover_letter_text; друге таке поле («Why do
    you want to join…?») відповідає модель, а без неї знову нотатка.

    Поля відстежуються за стабільним ключем (`_keyed`), не за індексом: після
    дії, що може відкрити нові поля, FIELD_JS перечитується, і решта полів
    (разом із щойно відкритими) береться з нового читання. Перед кожною дією
    `_live` звіряє елемент за індексом із полем; розбіжність — перечитування
    і одна повторна спроба, потім поле пропускається.

    `root` (модалка LinkedIn) звужує читання й локатори до кореня; cookie-банери
    тоді не чіпаються: сторінка вже відкрита під логіном, клік поза модалкою зайвий.

    File fields go through `_cv_upload`: on a form without a CV field the CV goes into the generic
    documents upload; `ats` tells Ashby, whose unlabelled upload is its autofill.
    """
    from bot.apply.forms import accept_cookies

    if not root:
        accept_cookies(page)
        # Option lists that load after the form (Abacus' countries, 02.11.2026) are in before the fill.
        try:
            page.wait_for_load_state("networkidle", timeout=NETWORK_IDLE_MS)
        except Exception:  # a page that never goes idle (polling, chat widgets): fill anyway
            log.debug("no network idle before the fill", exc_info=True)
    fields = _read(page, root)
    rep = FillReport()
    email = str(ctx.profile.get("email") or "").strip().lower()
    own = {email} | {e.strip().lower() for e in ctx.own_emails if e}
    processed: set[tuple] = set()
    retried: set[tuple] = set()
    answers: dict[tuple, Answer | None] = {}
    note_used = False
    # Email у select чи комбобоксі без адреси профілю серед опцій: заявка пішла б з іншою
    # адресою, тож поле стає open_required навіть з чужим значенням.
    email_open: list[str] = []

    for _ in range(_MAX_STEPS):
        keyed = _keyed(fields)
        nxt = next(((k, f) for k, f in keyed
                    if k not in processed and f.visible and f.type not in _SKIP_TYPES), None)
        if nxt is None:
            break
        key, f = nxt
        group_keys = [key]
        radios: list[Field] = []
        if f.type == "radio":
            members = [(k, g) for k, g in keyed if g.type == "radio" and g.group == f.group] if f.group else [(key, f)]
            group_keys = [k for k, _g in members]
            radios = [g for _k, g in members]
        processed.update(group_keys)
        if f.type == "radio":
            if any(g.value.strip() for g in radios):
                continue  # група вже вибрана
            if f.options is None:
                f = replace(f, options=[g.label for g in radios])
        kind = classify(f)
        value = f.value.strip()
        if value and not (kind == "email" and email and value.lower() not in own):
            # A file field shows the chosen file as C:\fakepath\<name>: the CV may already be in it.
            if f.type == "file" and re.split(r"[\\/]", value)[-1] == Path(ctx.resume).name:
                rep.cv_uploaded = True
            continue

        if key in answers:
            ans = answers[key]   # повтор після _StaleField: модель вдруге не кличемо
        else:
            ans = _cv_upload(f, kind, answer_for(f, ctx), fields, ctx, rep.cv_uploaded, ats)
            # A form with a house number field of its own wants only the street name here.
            if (kind == "street" and ans is not None and ans.kind == "text"
                    and any(g.visible and classify(g) == "house_number" for g in fields)):
                ans = Answer("text", split_street(ans.value)[0])
            fallback = None
            if kind == "cover_letter_text" and ans is not None and note_used:
                fallback, ans = ans, None
            if ans is None and llm is not None and llm_allowed(f) and rep.llm_calls < max_llm:
                rep.llm_calls += 1
                try:
                    text = clean_llm_answer(llm.complete(llm_prompt(f, ctx, brief)))
                except Exception:
                    log.warning("LLM answer for %r failed", _name(f), exc_info=True)
                    text = ""
                # A meta reply (a question back, echoed rules, the brief's language) is never typed:
                # the field stays open, a required one goes to the operator.
                problem = llm_answer_problem(text, llm_question(f)) if text else ""
                if problem:
                    log.warning("LLM answer for %r rejected (%s): %.80r", _name(f), problem, text)
                    text = ""
                ans = Answer("text", text) if text else None
            ans = ans or fallback
            answers[key] = ans
        if ans is None or ans.kind == "skip":
            continue
        try:
            ok = _apply_answer(page, f, ans, radios, root, date_format)
        except _StaleField:
            log.warning("поле %r зсунулося в DOM, перечитую форму", _name(f))
            fields = _read(page, root)
            if key not in retried:
                retried.add(key)
                processed.difference_update(group_keys)
            continue
        except Exception:
            log.warning("не вдалося заповнити %r", _name(f), exc_info=True)
            ok = False
        if not ok and kind == "email" and (f.tag == "select" or f.combobox) and _name(f) not in email_open:
            email_open.append(_name(f))
        if ans.kind in ("option", "check", "decline") or (ans.kind == "text" and (f.combobox or f.tag == "select")):
            page.wait_for_timeout(REVEAL_WAIT_MS)
            fields = _read(page, root)
        if not ok:
            continue
        if kind == "cover_letter_text" and ans.value == ctx.note:
            note_used = True
        if ans.kind == "file" and ans.value == str(ctx.resume):
            rep.cv_uploaded = True
        (rep.uploaded if ans.kind == "file" else rep.filled).append(_name(f))

    rep.open_required = _open_required(_read(page, root))
    rep.open_required += [n for n in email_open if n not in rep.open_required]
    return rep


# Every visible button with what tells the form's own submit from an opener or another form's button,
# shadow roots included. The form that holds the filled fields is the visible one with the most filled
# values (a chosen file counts); one filled value is a search box or a newsletter, not an application form.
# Marks every button with data-js-submit=<n>, the click handle.
SUBMIT_JS = r"""() => {
  const roots = [document];
  const walk = (root) => { for (const h of root.querySelectorAll('*')) if (h.shadowRoot) { roots.push(h.shadowRoot); walk(h.shadowRoot); } };
  walk(document);
  const all = (sel) => roots.flatMap((r) => Array.from(r.querySelectorAll(sel)));
  all('[data-js-submit]').forEach((e) => e.removeAttribute('data-js-submit'));
  const filled = (form) => Array.from(form.elements || []).filter((el) => {
    const t = (el.type || '').toLowerCase();
    if (['hidden', 'submit', 'button', 'reset', 'image'].includes(t)) return false;
    if (t === 'checkbox' || t === 'radio') return el.checked;
    if (t === 'file') return !!(el.files && el.files.length);
    return !!(el.value && String(el.value).trim());
  }).length;
  // Only a visible form: a wizard keeps its finished steps as hidden forms full of values (Abacus).
  let best = null, most = 1;
  for (const f of all('form')) { if (!f.getClientRects().length) continue; const n = filled(f); if (n > most) { best = f; most = n; } }
  const out = [];
  for (const el of all('button, input[type=submit], input[type=button], [role=button]')) {
    const r = el.getBoundingClientRect();
    const st = getComputedStyle(el);
    if (r.width === 0 || r.height === 0 || st.visibility === 'hidden' || st.display === 'none') continue;
    const text = (el.innerText || el.value || el.getAttribute('aria-label') || el.title || '').replace(/\s+/g, ' ').trim();
    const form = el.form || el.closest('form');
    el.setAttribute('data-js-submit', String(out.length));
    out.push({i: out.length, text: text.slice(0, 80),
              submit_type: (el.getAttribute('type') || '').toLowerCase() === 'submit',
              in_form: !!best && form === best, other_form: !!best && !!form && form !== best,
              disabled: !!el.disabled || el.getAttribute('aria-disabled') === 'true'});
    if (out.length >= 200) break;
  }
  return out;
}"""
# Final-submit texts: a sending verb with optional «Bewerbung», «jetzt», «application» around it.
# «Code senden» or «Job per E-Mail senden» are not among them.
_FINAL_SUBMIT = re.compile(
    r"^(?:(?:jetzt|bewerbung|ihre bewerbung|deine bewerbung|unterlagen|now|your application|application)\s+)*"
    r"(?:absenden|abschicken|senden|versenden|einreichen|abschlie(?:ß|ss)en|best(?:ä|ae)tigen|submit|send)"
    r"(?:\s+(?:jetzt|bewerbung|now|application|your application|my application))*$")
# Apply texts: the same words open a form from the job page, so they come after the final texts.
_APPLY_SUBMIT = re.compile(r"^(?:jetzt\s+)?(?:online\s+)?bewerben$|^apply(?:\s+now)?$")
# Applying through an account is a sign-in, never the form's submit («Mit XING bewerben»).
_SOCIAL_SUBMIT = re.compile(r"\b(?:xing|linkedin|indeed|google|apple|facebook|github|microsoft)\b")
# A type=submit button in the filled form that still does not send the application.
_NOT_SUBMIT = re.compile(
    r"\b(?:weiter|next|continue|zurück|back|abbrechen|cancel|speichern|save|entwurf|draft|hochladen|upload|"
    r"auswählen|choose|browse|durchsuchen|suchen?|search|log ?in|sign ?(?:in|up)|anmeld\w*|registrier\w*|"
    r"newsletter|löschen|delete|entfernen|remove|hinzufügen|add|teilen|share|merken|cookies?|akzeptieren|"
    r"accept|ablehnen|reject|schließen|close)\b")


class SubmitDisabled(LookupError):
    """The page's submit button is disabled: something on the page is still missing."""


def pick_submit(cands: list[dict]) -> dict | None:
    """The button that sends the filled form, from SUBMIT_JS; None if nothing fits.

    Best first: a type=submit button inside the filled form (any text but a next-step, upload or
    similar one); a final-submit text (_FINAL_SUBMIT); an apply text (_APPLY_SUBMIT), which is also
    what the opener above a form or a sticky header says. Within a tier the filled form's own button
    wins, then an enabled one, then the first. A button of another form or one naming XING, LinkedIn,
    Indeed, Google or Apple is never taken."""
    best: tuple | None = None
    for c in cands:
        text = _norm(c.get("text") or "")
        if c.get("other_form") or _SOCIAL_SUBMIT.search(text):
            continue
        if c.get("submit_type") and c.get("in_form") and not _NOT_SUBMIT.search(text):
            tier = 0
        elif _FINAL_SUBMIT.match(text):
            tier = 1
        elif _APPLY_SUBMIT.match(text):
            tier = 2
        else:
            continue
        key = (tier, not c.get("in_form"), bool(c.get("disabled")))
        if best is None or key < best[0]:
            best = (key, c)
    return best[1] if best else None


def find_submit(page):
    """Locator of the submit button (pick_submit); LookupError if there is none, SubmitDisabled if
    the chosen one is disabled."""
    hit = pick_submit(list(page.evaluate(SUBMIT_JS) or []))
    if hit is None:
        raise LookupError("кнопку Submit не знайдено")
    if hit.get("disabled"):
        raise SubmitDisabled(hit.get("text") or "")
    return page.locator(f'[data-js-submit="{hit["i"]}"]').first


# A wizard's step button (softgarden «zum nächsten Schritt», Abacus «Weiter», «Hochladen und
# weiter», Go4Bund «Weiter zur Bewerbungsvorschau»). Anchored: «Weiterempfehlen» or «Weiter einkaufen» is none.
_NEXT_STEP = re.compile(
    r"^(?:weiter|next|continue|fortfahren|n(?:ä|ae)chste[rs]? schritt"
    r"|(?:zum|zur) (?:n(?:ä|ae)chsten|letzten) schritt|next step|continue to (?:the )?next step"
    r"|(?:hochladen|speichern|save|upload) (?:und|and) (?:weiter|continue)|weiter zu[rm]? [\w\- ]{1,40})$")


def pick_next(cands: list[dict]) -> dict | None:
    """The wizard's next-step button from SUBMIT_JS: the filled form's own first, then an enabled one."""
    best: tuple | None = None
    for c in cands:
        text = _norm(c.get("text") or "")
        if c.get("other_form") or _SOCIAL_SUBMIT.search(text) or not _NEXT_STEP.match(text):
            continue
        key = (not c.get("in_form"), bool(c.get("disabled")))
        if best is None or key < best[0]:
            best = (key, c)
    return best[1] if best else None


def find_next(page):
    """Locator of the wizard's next-step button, None without one (a disabled one counts as none)."""
    hit = pick_next(list(page.evaluate(SUBMIT_JS) or []))
    if hit is None or hit.get("disabled"):
        return None
    return page.locator(f'[data-js-submit="{hit["i"]}"]').first


def submit(page) -> None:
    """Клік по кнопці Submit (find_submit)."""
    find_submit(page).click(timeout=5000)


def probe(page) -> PageProbe:
    """Знімок сторінки для outcome_after_submit."""
    d = page.evaluate(PROBE_JS)
    return PageProbe(
        text=page.inner_text("body"),
        html=d.get("html", ""),
        submit_visible=bool(d.get("submit_visible")),
        challenge_visible=bool(d.get("challenge_visible")),
        invalid_count=int(d.get("invalid_count") or 0),
        error_texts=[str(t) for t in d.get("error_texts") or []],
    )


def _has_visible_file_input(page) -> bool:
    return any(f.visible and f.type == "file" for f in fields_from(page.evaluate(FIELD_JS)))


class _FormNotFound(Exception):
    """Полів форми так і не з'явилось після переходу й кліку Apply."""


class _OnBoard(Exception):
    """The opener landed on a job board (a redirect, or an apply link to the employer's ad there)."""

    def __init__(self, board: str):
        super().__init__(board)
        self.board = board


def _not_on_board(page) -> None:
    """_OnBoard when the page is on a job board: nothing there is clicked. StepStone's «Ich bin
    interessiert» or XING's «Einfach bewerben» may send a profile application by themselves."""
    board = board_of(getattr(page, "url", "") or "")
    if board:
        raise _OnBoard(board)


def form_url(url: str, ats: str | None) -> str:
    """Адреса форми за адресою вакансії: 2b зберігає оглядову сторінку (Ashby jobUrl, Lever
    hostedUrl, Workable url), а поля є лише на /application, /apply чи /apply/."""
    u = urlparse(url)
    path = u.path.rstrip("/")
    if ats == "ashby" and not path.endswith("/application"):
        path += "/application"
    elif ats == "lever" and not path.endswith("/apply"):
        path += "/apply"
    elif ats == "workable":
        path = path if path.endswith("/apply") else path + "/apply"
        path += "/"
    else:
        return url
    return urlunparse(u._replace(path=path))


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\u2019", "'").lower()).strip(" →>»›.!:")


def pick_apply(cands: list[dict]) -> dict | None:
    """Best button or link towards the application form by text, then by href; None if nothing fits."""
    best: tuple | None = None
    for c in cands:
        text, href = _norm(c.get("text") or ""), c.get("href") or ""
        if href.lower().startswith(("mailto:", "tel:")):
            continue
        if _APPLY_EXCLUDE.search(text) or _APPLY_EXCLUDE.search(href.split("?")[0]):
            continue
        score = None
        for k, phrase in enumerate(APPLY_PHRASES):
            if text == phrase:
                score = (0, k)
                break
            if len(text) <= _MAX_TEXT and re.search(rf"(^|\W){re.escape(phrase)}(\W|$)", text):
                score = (1, k)
                break
        if score is None and href and len(text) <= _MAX_TEXT and _APPLY_HREF.search(href):
            score = (2, 0)
        if score is not None and (best is None or score < best[0]):
            best = (score, c)
    return best[1] if best else None


def _llm_pick(cands: list[dict], llm: LLM) -> dict | None:
    """Asks the model which visible element opens the application form; None on any doubt."""
    usable = [c for c in cands if not (c.get("href") or "").lower().startswith(("mailto:", "tel:"))][:_LLM_CANDIDATES]
    if not usable:
        return None
    lines = "\n".join(f'{c["i"]}: "{c.get("text") or ""}" {c.get("href") or ""}'.rstrip() for c in usable)
    prompt = (
        "A job posting page is open in a browser. Which ONE element below opens the job application form "
        "(the form where a candidate enters name, email and uploads a CV)? Never choose an element that logs "
        "in, signs up, creates an account, applies via a social or job-board account (LinkedIn, XING, Indeed, "
        "Google, Apple), shares, saves, or leads to other jobs. Answer with the number only, or NONE.\n\n"
        + lines)
    try:
        answer = llm.complete(prompt).strip()
    except Exception:  # the model is an optional helper, never a reason to fail the lead
        log.warning("LLM pick of the apply button failed", exc_info=True)
        return None
    m = re.match(r"\s*(\d+)\b", answer)
    if not m:
        return None
    hit = next((c for c in usable if c["i"] == int(m.group(1))), None)
    if hit is None or _APPLY_EXCLUDE.search(_norm(hit.get("text") or "")):
        return None
    return hit


def _form_ready(page) -> bool:
    """An application form is on the page: a rendered CV upload or two visible identity fields.
    A lone search box, a zero-size form element or a form inside a hidden tab does not count, and
    neither does anything on a job board (Indeed, Glassdoor): its fields are search and sign-in."""
    if board_of(getattr(page, "url", "") or ""):
        return False
    try:
        fields = fields_from(page.evaluate(FIELD_JS))
    except Exception:  # noqa: BLE001 - navigation in progress: not ready yet
        return False
    if any(f.type == "file" and f.visible for f in fields):
        return True
    return sum(1 for f in fields if f.visible and classify(f) in _IDENTITY_KINDS) >= 2


def _candidates(page) -> list[dict]:
    try:
        return list(page.evaluate(CANDIDATES_JS) or [])
    except Exception:  # noqa: BLE001 - navigation in progress
        return []


def _follow(page, cand: dict) -> None:
    """Opens the chosen element in the same tab: a target=_blank link by goto, anything else by click;
    a popup that the click opened is moved into this tab, so the caller keeps a single page."""
    href = cand.get("href") or ""
    if cand.get("tag") == "a" and cand.get("target") == "_blank" and href and not href.startswith("#"):
        page.goto(urljoin(page.url, href), wait_until="domcontentloaded", timeout=30000)
        return
    context = getattr(page, "context", None)
    before = list(context.pages) if context is not None else []
    page.locator(f'[data-js-cand="{cand["i"]}"]').first.click(timeout=5000)
    page.wait_for_timeout(1500)
    if context is None:
        return
    popups = [p for p in context.pages if p not in before and p is not page]
    if popups:
        target = popups[-1].url
        for p in popups:
            p.close()
        if target and target != "about:blank":
            page.goto(target, wait_until="domcontentloaded", timeout=30000)


def _open_form(page, url: str, ats: str | None, llm: LLM | None = None) -> None:
    """Goes to the form page and gets to the form fields, clicking up to MAX_APPLY_HOPS apply-like
    buttons on the way; _FormNotFound if no form shows up, _OnBoard if the way leads to a job board."""
    from bot.apply.forms import accept_cookies

    page.goto(form_url(url, ats), wait_until="domcontentloaded", timeout=30000)
    may_click = ats not in _DIRECT_FORM_ATS
    hops, llm_used = 0, False
    while True:
        accept_cookies(page)   # a banner over the page swallows the apply click
        waited, cand = 0, None
        while waited < FORM_WAIT_MS:
            _not_on_board(page)
            if _form_ready(page):
                return
            if may_click and hops < MAX_APPLY_HOPS:
                cand = pick_apply(_candidates(page))
                if cand is not None:
                    break
            page.wait_for_timeout(ENTRY_POLL_MS)
            waited += ENTRY_POLL_MS
        if cand is None and may_click and hops < MAX_APPLY_HOPS and llm is not None and not llm_used:
            llm_used = True
            cand = _llm_pick(_candidates(page), llm)
        if cand is None:
            if _form_ready(page):
                return
            raise _FormNotFound(f"no form after {hops} click(s) on {page.url}")
        _not_on_board(page)   # a script redirect to a board while the model was choosing
        log.info("apply opener: click %r (%s)", cand.get("text"), cand.get("href"))
        _follow(page, cand)
        hops += 1


def _board_blocked(page, board: str) -> str | None:
    """A bot check ("captcha"), the board's block page ("<board>_blocked") or its sign-in page
    ("<board>_login") in place of the job page."""
    try:
        text = page.inner_text("body")
        challenge = probe(page).challenge_visible or challenge_page(text)
    except Exception:  # noqa: BLE001 - a page mid-navigation: the URL check below still applies
        text, challenge = "", False
    if challenge:
        return "captcha"
    if blocked_page(text):
        return f"{board}_blocked"
    if login_wall(page.url):
        return f"{board}_login"
    return None


def _leave_board(page, url: str, board: str) -> tuple[str | None, str | None]:
    """Opens a job-board page and goes on to the employer site by URL, never by click (a cookie
    banner covers these pages). Returns (employer URL, None) or (None, hand-off reason): captcha,
    <board>_blocked, <board>_login, <board>_captcha (the employer's contacts behind the board's captcha),
    <board>_apply (only the board's own apply flow), <board>_no_apply_link, <board>_no_employer_page
    (the redirect stayed on a board)."""
    page.goto(url, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(BOARD_SETTLE_MS)
    blocked = _board_blocked(page, board)
    if blocked:
        return None, blocked
    try:
        cands = list(page.evaluate(BOARD_JS) or [])
    except Exception:  # noqa: BLE001 - navigation in progress: the page data may still have the link
        cands = []
    html = page.content()
    link = employer_link(board, page.url, html, cands)
    if link is None:
        if contact_captcha(html):
            return None, f"{board}_captcha"
        return None, f"{board}_apply" if own_apply(html, cands) else f"{board}_no_apply_link"
    log.info("job board %s: employer link %s", board, link)
    page.goto(link, wait_until="domcontentloaded", timeout=30000)
    waited = 0
    while board_of(page.url) and waited < BOARD_REDIRECT_MS:
        page.wait_for_timeout(ENTRY_POLL_MS)
        waited += ENTRY_POLL_MS
    if board_of(page.url):
        return None, _board_blocked(page, board) or f"{board}_no_employer_page"
    return page.url, None


_MAX_WIZARD_STEPS = 6
STEP_WAIT_MS = 10000


def _step_key(page) -> tuple:
    """What tells a wizard step from the next one: the URL and the keys of the visible fields."""
    try:
        fields = _read(page)
    except Exception:  # noqa: BLE001 - a page mid-navigation: the URL alone
        return (page.url, None)
    return (page.url, tuple(sorted(str(k) for k, f in _keyed(fields) if f.visible)))


def _await_step(page, before: tuple, base_conf: str, wait_ms: int) -> tuple[str, PageProbe | None]:
    """After a next-step click: 'submitted' (a new confirmation and no submit button), 'advanced' (other
    fields or URL), 'captcha', or 'stuck' when nothing changed by wait_ms."""
    waited = 0
    pr = None
    while True:
        page.wait_for_timeout(POLL_MS)
        waited += POLL_MS
        try:
            pr = probe(page)
        except Exception:  # noqa: BLE001 - navigation in progress: look again
            pr = None
        if pr is not None:
            conf = confirmation_in(pr.text)
            if conf and conf != base_conf and not pr.submit_visible:
                return "submitted", pr
        if _step_key(page) != before:
            return "advanced", pr
        if pr is not None and pr.challenge_visible:
            return "captcha", pr
        if waited >= wait_ms:
            return "stuck", pr


def _poll_after_submit(page, baseline: str, wait_ms: int) -> tuple[PageProbe | None, str]:
    """Опитує сторінку після Submit до wait_ms; перший знімок через POLL_MS.

    Нативний POST чи редирект знищує контекст виконання: такий знімок не
    помилка, опитування триває до дедлайну. Якщо фраза підтвердження та сама,
    що була до Submit, «submitted» приймається лише з другого поспіль знімка
    без кнопки Submit.
    """
    elapsed = 0
    pr: PageProbe | None = None
    outcome = "pending"
    same_phrase_streak = 0
    while True:
        page.wait_for_timeout(POLL_MS)
        elapsed += POLL_MS
        try:
            pr = probe(page)
            outcome = outcome_after_submit(pr)
        except Exception:
            log.warning("знімок після Submit не вдався (навігація?), чекаю далі", exc_info=True)
            pr, outcome = None, "pending"
        if outcome == "submitted" and baseline and confirmation_in(pr.text) == baseline:
            same_phrase_streak += 1
            if same_phrase_streak < 2:
                outcome = "pending"
        elif outcome != "submitted":
            same_phrase_streak = 0
        if outcome != "pending" or elapsed >= wait_ms:
            return pr, outcome


def apply(page, url: str, ctx: AnswerContext, llm: LLM | None, brief: str, shots_dir: Path,
          dry_run: bool, wait_ms: int = SUBMIT_WAIT_MS) -> ApplyResult:
    """Fills and submits an ATS form; anything left unfinished comes back as a handoff with a reason.

    A job-board URL (Indeed, StepStone, Arbeitsagentur, ...: `job_boards.board_of`) is never filled:
    `_leave_board` takes the employer link from it, and the form flow runs on the employer page. A way
    to the form that ends on a board is a hand-off (<board>_redirect). The result keeps the lead's URL."""
    from bot.apply.forms import screenshot

    ats = ats_from_url(url)
    portal = ats or "ats"
    shots: list[str] = []
    clicked = False

    def result(status: str, **kw) -> ApplyResult:
        return ApplyResult(status, portal, url, list(shots), **kw)

    def shot(name: str) -> None:
        # Скріншот не має перетворити подану заявку на помилку.
        try:
            shots.append(screenshot(page, shots_dir, name))
        except Exception:
            log.warning("скріншот %s не вдався", name, exc_info=True)

    target = url
    board = board_of(url)
    if board:
        try:
            employer, why = _leave_board(page, url, board)
        except Exception as e:  # noqa: BLE001 - the board page did not load: failed, not a crash
            return result("failed", reason=f"goto: {type(e).__name__}: {e}"[:300])
        if employer is None:
            shot(f"apply_{board}.png")
            return result("handoff", reason=why)
        target, ats = employer, ats_from_url(employer)
        portal = ats or "ats"

    try:
        _open_form(page, target, ats, llm)
    except _OnBoard as e:
        shot(f"apply_{e.board}.png")
        return result("handoff", reason=f"{e.board}_redirect")
    except _FormNotFound:
        try:
            closed = closed_in(page.inner_text("body"))
        except Exception:  # мертва сторінка: все одно hand-off, не виняток
            log.warning("тіло сторінки без форми не читається", exc_info=True)
            closed = False
        if closed:
            shot("apply_closed.png")
            return result("closed", reason="posting closed")
        shot("apply_form_not_found.png")
        try:
            challenge = probe(page).challenge_visible
        except Exception:  # noqa: BLE001 - dead page: still a hand-off
            challenge = False
        if challenge:
            shot("apply_captcha.png")
            return result("handoff", reason="captcha")
        return result("handoff", reason="form_not_found")
    except Exception as e:  # noqa: BLE001 — сторінка не відкрилась (мережа, DNS, впав браузер): failed, не crash
        return result("failed", reason=f"goto: {type(e).__name__}: {e}"[:300])

    try:
        if closed_in(page.inner_text("body")):
            shot("apply_closed.png")
            return result("closed", reason="posting closed")

        # A wizard (softgarden, Abacus, …) is filled step by step: no submit button but a next-step one
        # means the step is done. The CV may sit on a later step, so its check waits for the last one.
        rep = FillReport()
        step = 1
        while True:
            part = fill_form(page, ctx, llm, brief, max_llm=max(0, 3 - rep.llm_calls), ats=ats)
            rep.filled += part.filled
            rep.uploaded += part.uploaded
            rep.llm_calls += part.llm_calls
            rep.cv_uploaded = rep.cv_uploaded or part.cv_uploaded
            rep.open_required = part.open_required
            shot("apply_form_filled.png" if step == 1 else f"apply_form_step{step}.png")
            if rep.open_required:
                return result("handoff", reason="open_required: " + "; ".join(rep.open_required))
            try:
                find_submit(page)
                break
            except SubmitDisabled:
                break
            except LookupError:
                pass
            nxt = find_next(page)
            if nxt is None:
                break
            if dry_run:
                return result("dry_run", reason=(
                    f"filled {len(rep.filled)}, uploaded {len(rep.uploaded)}, llm {rep.llm_calls}, "
                    f"wizard step {step}: the next button is not clicked in the shadow"))
            if step >= _MAX_WIZARD_STEPS:
                return result("handoff", reason="wizard_too_long")
            from bot.apply.forms import wait_friendly_captcha

            if wait_friendly_captcha(page) in ("failed", "pending"):
                return result("handoff", reason="captcha")
            before = _step_key(page)
            base_conf = confirmation_in(page.inner_text("body"))
            # A next button may send (Abacus' last «Weiter»): from here an exception is error_after_submit.
            clicked = True
            nxt.click(timeout=5000)
            state, pr = _await_step(page, before, base_conf, min(wait_ms, STEP_WAIT_MS))
            if state == "submitted":
                shot("apply_confirmation.png")
                if not rep.cv_uploaded:
                    log.warning("a wizard step sent the application without the CV: %s", url)
                return result("submitted", confirmation_text=confirmation_in(pr.text))
            if state == "captcha":
                shot("apply_after_submit.png")
                return result("handoff", reason="captcha")
            if state == "stuck":
                shot(f"apply_form_step{step}.png")
                errors = "; ".join(pr.error_texts) if pr is not None and pr.error_texts else ""
                return result("handoff", reason=("validation: " + errors) if errors else "wizard_no_progress")
            clicked = False
            step += 1
        # An application without the CV is worthless, and a page with no CV field is usually not
        # the form at all (a job board's search box, a login wall). Checked before dry_run, so the
        # shadow pass reports the same hand-off the live pass would produce.
        if not rep.cv_uploaded:
            return result("handoff", reason="no_cv_uploaded")
        if dry_run:
            return result("dry_run", reason=(
                f"filled {len(rep.filled)}, uploaded {len(rep.uploaded)}, llm {rep.llm_calls}"))

        # FriendlyCaptcha computes after the form is filled; a submit before its token comes back as a
        # fresh empty form (softgarden 02.11.2026). Unsolved: the operator gets the filled form.
        from bot.apply.forms import wait_friendly_captcha

        if wait_friendly_captcha(page) in ("failed", "pending"):
            return result("handoff", reason="captcha")
        # Фраза підтвердження, що вже є на сторінці до Submit (опис вакансії
        # Greenhouse на тій самій сторінці), сама по собі не доказ подачі.
        baseline = confirmation_in(page.inner_text("body"))
        try:
            btn = find_submit(page)
        except SubmitDisabled:
            return result("handoff", reason="submit_disabled")
        except LookupError:
            return result("handoff", reason="no_submit_button")
        # Прапорець до кліку: клік, що пішов і кинув виняток, уже міг подати заявку.
        clicked = True
        btn.click(timeout=5000)

        pr, outcome = _poll_after_submit(page, baseline, wait_ms)
        if outcome == "submitted":
            shot("apply_confirmation.png")
            return result("submitted", confirmation_text=confirmation_in(pr.text))
        shot("apply_after_submit.png")
        if outcome == "challenge":
            return result("handoff", reason="captcha")
        if outcome == "validation" and pr is not None:
            errors = "; ".join(pr.error_texts) or f"{pr.invalid_count} invalid"
            return result("handoff", reason="validation: " + errors)
        return result("handoff", reason="no_confirmation")
    except Exception as e:
        # Недозаповнена чи незрозуміла сторінка: оператор розбереться у відкритій вкладці.
        # Після кліку Submit заявка могла піти: окрема причина, щоб не подати вдруге.
        log.exception("ATS-форма %s", url)
        prefix = "error_after_submit" if clicked else "error"
        return result("handoff", reason=f"{prefix}: {type(e).__name__}: {e}"[:300])
