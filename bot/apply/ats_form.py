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
from pathlib import Path
from urllib.parse import urlparse, urlunparse

from bot.apply.ats_answers import (
    _EEO_DECLINE,
    Answer,
    AnswerContext,
    _option_like,
    answer_for,
    clean_llm_answer,
    llm_allowed,
    llm_prompt,
)
from bot.apply.ats_detect import (
    PROBE_JS,
    SUBMIT_TEXTS,
    PageProbe,
    closed_in,
    confirmation_in,
    outcome_after_submit,
)
from bot.apply.ats_fields import (
    FIELD_JS,
    FIELD_SELECTOR,
    Field,
    classify,
    fields_from,
    visible_required,
)
from bot.apply.models import ApplyResult
from bot.ats import ats_from_url
from bot.llm import LLM

log = logging.getLogger(__name__)

# Greenhouse інколи показує спершу опис вакансії, форма з'являється після «Apply».
GH_APPLY_SELECTOR = 'a:has-text("Apply"), button:has-text("Apply")'
# Невідомий ATS: перше видиме посилання чи кнопка на форму.
APPLY_SELECTOR = ('a[href*="application"], a[href$="/apply"], a:has-text("Apply"), button:has-text("Apply"), '
                  'a:has-text("Jetzt bewerben"), button:has-text("Jetzt bewerben"), a:has-text("Online bewerben"), '
                  'a[href*="bewerb"], a:has-text("Bewerben"), button:has-text("Bewerben")')
# Сторінка рендериться клієнтом: до першого читання чекаємо хоч якесь поле форми.
FORM_READY_SELECTOR = "input[type=file], form, input, textarea, select"
FORM_WAIT_MS = 15000
# Після Submit: токен reCAPTCHA Enterprise, POST і рендер підтвердження разом часто довші за 4 с.
SUBMIT_WAIT_MS = 30000
COMBOBOX_WAIT_MS = 800
# Після дії, що може відкрити нові поля (Yes/No, check, вибір опції), DOM
# перечитується; пауза дає React дорендерити залежні питання.
REVEAL_WAIT_MS = 300
POLL_MS = 1000
_MAX_STEPS = 300   # запобіжник від форми, що без кінця породжує нові поля
_SKIP_TYPES = {"hidden", "submit", "button", "reset", "image"}
_CHOICE_TYPES = ("radio", "checkbox")


@dataclass
class FillReport:
    filled: list[str] = field(default_factory=list)
    uploaded: list[str] = field(default_factory=list)
    llm_calls: int = 0
    open_required: list[str] = field(default_factory=list)


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


def field_selector(root: str = "") -> str:
    """FIELD_SELECTOR у межах кореня (`dialog[open]` модалки LinkedIn); без кореня як є."""
    if not root:
        return FIELD_SELECTOR
    return ",".join(f"{root} {part}" for part in FIELD_SELECTOR.split(","))


def field_js(root: str = "") -> str:
    """FIELD_JS, що читає лише поля всередині кореня: індекс i збігається з field_selector(root)."""
    if not root:
        return FIELD_JS
    if FIELD_JS.count(_FIELD_QUERY) != 1:
        raise RuntimeError("FIELD_JS змінився: вибірку полів не знайдено для звуження")
    return FIELD_JS.replace(_FIELD_QUERY, f"document.querySelectorAll({json.dumps(field_selector(root))})")


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
    el = page.locator(field_selector(root)).nth(f.i)
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


def _apply_answer(page, f: Field, ans: Answer, radios: list[Field], root: str = "") -> bool:
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
        el.set_input_files(ans.value)
        return True
    if ans.kind == "check":
        try:
            el.check(timeout=3000)
        except Exception:  # noqa: BLE001 — кастомний чекбокс без клікабельного input: силою
            el.check(force=True, timeout=3000)
        return True
    if ans.kind == "date":
        # Не Enter: усередині <form> Enter — неявна відправка, навіть у dry_run.
        # Tab закриває датапікер і знімає фокус.
        el.fill(ans.value)
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


def fill_form(page, ctx: AnswerContext, llm: LLM | None, brief: str, max_llm: int = 3,
              root: str = "") -> FillReport:
    """Прохід по видимих полях у порядку DOM, потім перечитування й open_required.

    Передзаповнене не чіпається, крім email, що не збігається з профілем.
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
    """
    from bot.apply.forms import accept_cookies

    if not root:
        accept_cookies(page)
    fields = _read(page, root)
    rep = FillReport()
    email = str(ctx.profile.get("email") or "").strip().lower()
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
        if value and not (kind == "email" and email and value.lower() != email):
            continue

        if key in answers:
            ans = answers[key]   # повтор після _StaleField: модель вдруге не кличемо
        else:
            ans = answer_for(f, ctx)
            fallback = None
            if kind == "cover_letter_text" and ans is not None and note_used:
                fallback, ans = ans, None
            if ans is None and llm is not None and llm_allowed(f) and rep.llm_calls < max_llm:
                rep.llm_calls += 1
                try:
                    text = clean_llm_answer(llm.complete(llm_prompt(f, ctx, brief)))
                except Exception:
                    log.warning("LLM-відповідь для %r не вдалася", _name(f), exc_info=True)
                    text = ""
                ans = Answer("text", text) if text else None
            ans = ans or fallback
            answers[key] = ans
        if ans is None or ans.kind == "skip":
            continue
        try:
            ok = _apply_answer(page, f, ans, radios, root)
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
        (rep.uploaded if ans.kind == "file" else rep.filled).append(_name(f))

    rep.open_required = _open_required(_read(page, root))
    rep.open_required += [n for n in email_open if n not in rep.open_required]
    return rep


def find_submit(page):
    """Перша видима кнопка за SUBMIT_TEXTS у їхньому порядку; немає — LookupError.

    Короткі слова («submit», «apply») лише точним збігом, як у PROBE_JS:
    «Apply for another role» не кнопка відправки.
    """
    for s in SUBMIT_TEXTS:
        rx = re.compile(re.escape(s), re.IGNORECASE) if " " in s else _exact(s)
        btns = page.get_by_role("button", name=rx)
        for k in range(btns.count()):
            btn = btns.nth(k)
            if btn.is_visible():
                return btn
    raise LookupError("кнопку Submit не знайдено")


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


def _click_apply(page, selector: str) -> None:
    """Greenhouse і невідомий ATS: форма за кнопкою «Apply», один клік по першій видимій,
    якщо файлового поля ще не видно. Чекаємо або поле форми, або саме посилання."""
    try:
        page.wait_for_selector(f"{FORM_READY_SELECTOR}, {selector}", timeout=FORM_WAIT_MS)
    except Exception:  # нічого не з'явилось: вирішить очікування форми нижче
        log.debug("ні форми, ні кнопки Apply за %d мс", FORM_WAIT_MS, exc_info=True)
        return
    if _has_visible_file_input(page):
        return
    links = page.locator(selector)
    for k in range(links.count()):
        link = links.nth(k)
        if link.is_visible():
            link.click(timeout=5000)
            return


def _open_form(page, url: str, ats: str | None) -> None:
    """Перехід на сторінку форми й очікування її полів; _FormNotFound, якщо їх нема."""
    page.goto(form_url(url, ats), wait_until="domcontentloaded", timeout=30000)
    if ats == "greenhouse":
        _click_apply(page, GH_APPLY_SELECTOR)
    elif ats is None:
        _click_apply(page, APPLY_SELECTOR)
    try:
        page.wait_for_selector(FORM_READY_SELECTOR, timeout=FORM_WAIT_MS)
    except Exception as e:  # таймаут Playwright: полів нема
        raise _FormNotFound(str(e)) from e


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
    """Заповнює і подає ATS-форму; все незавершене повертає як handoff з причиною."""
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

    try:
        _open_form(page, url, ats)
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
        return result("handoff", reason="form_not_found")
    except Exception as e:  # noqa: BLE001 — сторінка не відкрилась (мережа, DNS, впав браузер): failed, не crash
        return result("failed", reason=f"goto: {type(e).__name__}: {e}"[:300])

    try:
        if closed_in(page.inner_text("body")):
            shot("apply_closed.png")
            return result("closed", reason="posting closed")

        rep = fill_form(page, ctx, llm, brief)
        shot("apply_form_filled.png")
        if rep.open_required:
            return result("handoff", reason="open_required: " + "; ".join(rep.open_required))
        if dry_run:
            return result("dry_run", reason=(
                f"filled {len(rep.filled)}, uploaded {len(rep.uploaded)}, llm {rep.llm_calls}"))

        # Фраза підтвердження, що вже є на сторінці до Submit (опис вакансії
        # Greenhouse на тій самій сторінці), сама по собі не доказ подачі.
        baseline = confirmation_in(page.inner_text("body"))
        try:
            btn = find_submit(page)
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
