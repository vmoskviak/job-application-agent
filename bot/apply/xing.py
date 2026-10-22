"""XING Easy Apply auto-apply flow (fail-safe, дзеркалить stepstone.py)."""

from __future__ import annotations

import re
import shutil
import tempfile
from pathlib import Path

from bot.apply import candidate, detect, forms
from bot.apply.models import ApplyResult

# Нові сторінки подачі XING (14.10.2026) замість модала на сторінці вакансії:
# /jobs/apply/<slug> — класична форма (контакти, Dokument-Hub, «Bewerbung
# versenden»); /jobs/custom-apply/<slug> — майстер «Schritt N von 4» з
# питаннями роботодавця; /jobs/quick-apply/<slug> — «Mit Lebenslauf bewerben»
# зі збереженим CV, «Bewerbung bearbeiten» веде на класичну форму.
_APPLY_PAGE_MARKERS = ("/jobs/apply/", "/jobs/quick-apply/", "/jobs/custom-apply/")
_WEITER_RE = re.compile(r"^\s*Weiter\s*$")
_SUBMIT_RE = re.compile(r"Bewerbung\s+(versenden|abschicken|absenden|senden)", re.I)
_EDIT_RE = re.compile(r"^\s*Bewerbung bearbeiten\s*$")
_ADD_DOC_RE = re.compile(r"^\s*Neues Dokument hinzufügen\s*$")
_MORE_DOCS_RE = re.compile(r"^\s*Weitere Dokumente hinzufügen")
_HUB_ADD_RE = re.compile(r"^\s*Dokument hinzufügen\s*$")
_DELETE_DOC_SEL = "[data-testid='delete-upload-button']"

_APPLY_BUTTON_SELECTORS = [
    "[data-testid*='apply-button']",
    "button:has-text('Easy Apply')",
    "button:has-text('Einfach bewerben')",
    "a:has-text('Einfach bewerben')",
    "button:has-text('Jetzt bewerben')",
    "a:has-text('Jetzt bewerben')",
]

_SUBMIT_SELECTORS = [
    "button[type='submit']:has-text('Bewerbung')",
    "button:has-text('Bewerbung absenden')",
    "button:has-text('Bewerbung senden')",
    "button:has-text('Absenden')",
    "button:has-text('Senden')",
    "button[type='submit']",
]


# Рівні мов для pre-screening XING («Diese Infos brauchen wir zuerst»):
# profile.yaml → language_levels: {Deutsch: C2, Englisch: B1-B2}. Значення =
# префікс опції XING: A1-A2 · B1-B2 · C1 · C2.
_PRESCREEN_SELECTS = {
    "Deutsch": "[data-testid='pre-screening-germanSkills-dropdown']",
    "Englisch": "[data-testid='pre-screening-englishSkills-dropdown']",
}


def pick_level_option(options: list[str], wanted: str) -> str | None:
    """Опція XING за префіксом рівня («C2» → «C2 (Verhandlungssicher / …)»)."""
    w = (wanted or "").strip().upper()
    if not w:
        return None
    for o in options:
        if o.strip().upper().startswith(w):
            return o
    return None


_PRESCREEN_SAVE_SEL = "button:has-text('Speichern und weiter zur Bewerbung')"


def _answer_eu_permit(page) -> str:
    """Радіо «Bist Du berechtigt, in der EU zu arbeiten?» у модалі → «Ja».

    Спершу справжній input (value yes/true), бо кастомне радіо XING ховає
    його за лейблом; якщо check() не проходить, клік по тексту «Ja».
    """
    dlg = page.locator("[role='dialog']").first
    for sel in ("input[value='yes']", "input[value='true']", "input[type='radio'][id*='yes' i]"):
        try:
            radio = dlg.locator(sel).first
            if not radio.count():
                continue
            if not radio.is_checked():
                radio.check()
            return "eu=yes"
        except Exception:  # noqa: BLE001
            continue
    try:
        ja = dlg.get_by_text("Ja", exact=True).first
        if ja.count():
            ja.click(timeout=2000)
            return "eu=yes(label)"
    except Exception:  # noqa: BLE001
        pass
    return "eu=?"


def answer_prescreening(page) -> list[str]:
    """Закриває модал «Diese Infos brauchen wir zuerst» перед формою Easy Apply.

    Модал упізнається за кнопкою «Speichern und weiter zur Bewerbung», а не за
    дропдауном німецької: 14.10.2026 XING спитав лише дозвіл на роботу в ЄС,
    без мов, і детектор за дропдауном пропустив модал (BeispielPersonal, apply_wrong_job).
    Дозвіл на роботу в ЄС завжди «Ja» (клієнти рушія працюють у Німеччині
    легально, інакше їх тут нема); рівні мов з profile.yaml → language_levels,
    лише якщо відповідний дропдаун є. Повертає список заповненого; порожній
    список = модала не було.
    """
    try:
        if not page.locator(_PRESCREEN_SAVE_SEL).first.is_visible(timeout=1500):
            return []
    except Exception:  # noqa: BLE001
        return []
    from bot import client as _client
    levels = _client.active().profile.get("language_levels") or {}
    done: list[str] = [_answer_eu_permit(page)]
    for lang, sel in _PRESCREEN_SELECTS.items():
        try:
            select = page.locator(sel).first
            if not select.count():
                continue
            options = select.locator("option").all_inner_texts()
            choice = pick_level_option(options, str(levels.get(lang, "")))
            if choice:
                select.select_option(label=choice)
                done.append(f"{lang}={choice.split(' ')[0]}")
            else:
                done.append(f"{lang}=? (profile.yaml → language_levels)")
        except Exception as e:  # noqa: BLE001
            done.append(f"{lang}=err:{type(e).__name__}")
    try:
        page.locator(_PRESCREEN_SAVE_SEL).first.click(timeout=5000)
        page.wait_for_timeout(2500)
        done.append("saved")
    except Exception as e:  # noqa: BLE001
        done.append(f"save=err:{type(e).__name__}")
    return done


def national_phone(phone: str, prefix: str) -> str:
    """Збережено для сумісності; логіка в candidate.national_phone."""
    return candidate.national_phone(phone, prefix)


def pick_header_button(h1_top: float, candidates: list[tuple[float, str]]) -> tuple[int | None, str]:
    """Кнопка дії в шапці вакансії: найближча ПІД заголовком h1.

    Повертає (індекс у candidates, kind): kind = easy (Easy Apply), external
    («Zur Arbeitgeber-Website»), none. Стрічки «Ähnliche Jobs» над і під
    вакансією мають свої кнопки «Einfach bewerben», тому «перша на сторінці»
    і «перша після h1 у DOM» обидві брали чужу (07.10.2026).
    """
    best: tuple[float, int] | None = None
    for i, (top, _text) in enumerate(candidates):
        if top >= h1_top - 2 and (best is None or top < best[0]):
            best = (top, i)
    if best is None:
        return None, "none"
    text = candidates[best[1]][1].lower()
    if "arbeitgeber" in text or "website" in text or "extern" in text:
        return best[1], "external"
    return best[1], "easy"


_HEADER_BTN_SEL = ("[data-testid*='apply-button'], a:has-text('Zur Arbeitgeber-Website'), "
                   "button:has-text('Einfach bewerben'), a:has-text('Einfach bewerben')")


def _main_apply_button(page):
    """(locator | None, kind) для кнопки саме цієї вакансії, див. pick_header_button."""
    try:
        h1 = page.locator("h1").first
        box = h1.bounding_box(timeout=2000) if h1.count() else None
        if not box:
            return None, "none"
        loc = page.locator(_HEADER_BTN_SEL)
        cands: list[tuple[float, str]] = []
        n = min(loc.count(), 30)
        for i in range(n):
            b = loc.nth(i).bounding_box()
            cands.append((b["y"] if b else -1.0, loc.nth(i).inner_text(timeout=1000) or ""))
        idx, kind = pick_header_button(box["y"], cands)
        return (loc.nth(idx) if idx is not None else None), kind
    except Exception:  # noqa: BLE001
        return None, "none"


def _dialog_matches_job(page, title: str) -> bool:
    """Форма Easy Apply показує картку вакансії; її заголовок має збігатись з h1."""
    if not title:
        return True
    try:
        dialog = page.locator("[role='dialog']").first
        text = dialog.inner_text(timeout=3000) if dialog.count() else page.inner_text("body")
    except Exception:  # noqa: BLE001
        return True
    probe = title.strip()[:30].lower()
    return probe in text.lower()


def job_id_from_url(url: str) -> str:
    """Числовий id вакансії зі slug-а XING («…-verwaltung-100000101»)."""
    clean = (url or "").split("?")[0].split("#")[0].rstrip("/")
    m = re.search(r"-(\d{6,})$", clean)
    return m.group(1) if m else ""


def is_apply_page(url: str) -> bool:
    """Чи це одна з окремих сторінок подачі XING (див. _APPLY_PAGE_MARKERS)."""
    return any(m in (url or "") for m in _APPLY_PAGE_MARKERS)


def split_attachments(attachments: list[Path]) -> tuple[Path | None, Path | None]:
    """(CV, Anschreiben) з переліку вкладень пакета за назвою файлу."""
    cv = next((a for a in attachments if "anschreiben" not in a.name.lower()), None)
    letter = next((a for a in attachments if "anschreiben" in a.name.lower()), None)
    return cv, letter


def upload_name(app_dir: Path, attachment: Path) -> str:
    """Унікальна назва файлу для Dokument-Hub: <stem>_<Firma><suffix>.

    Hub дедуплікує за вмістом і показує збережені документи лише за назвою;
    однакові «01_Anschreiben.pdf» різних пакетів у списку не розрізнити
    (14.10.2026 dry-run причепив до одного роботодавця лист для іншого).
    Firma = префікс папки пакета до першого «_».
    """
    tag = re.sub(r"[^A-Za-z0-9]", "", app_dir.name.split("_")[0])[:40] or "Bewerbung"
    stem = "Anschreiben" if "anschreiben" in attachment.name.lower() else attachment.stem
    return f"{stem}_{tag}{attachment.suffix}"


def upload_copies(app_dir: Path, attachments: list[Path], tmp_dir: Path) -> list[Path]:
    """Копії вкладень під назвами upload_name у тимчасовій папці."""
    copies: list[Path] = []
    for a in attachments:
        if not a.exists():
            continue
        dst = tmp_dir / upload_name(app_dir, a)
        shutil.copyfile(a, dst)
        copies.append(dst)
    return copies


def _first_enabled(loc):
    for i in range(loc.count()):
        try:
            if loc.nth(i).is_visible() and loc.nth(i).is_enabled():
                return loc.nth(i)
        except Exception:  # noqa: BLE001
            continue
    return None


def _doc_listed(page, name: str) -> bool:
    try:
        return page.get_by_text(name, exact=True).count() > 0
    except Exception:  # noqa: BLE001
        return False


_HUB_DUPLICATE_TEXT = "bereits hochgeladen"
_BTN_CONTEXT_JS = """e => { let p = e.parentElement;
  for (let k = 0; k < 5 && p; k++) {
    const t = (p.innerText || '').trim();
    if (/Anschreiben|Weitere Dokumente|Lebenslauf/.test(t)) return t.slice(0, 120);
    p = p.parentElement;
  }
  return ''; }"""


def _hub_dialog(page):
    return page.locator("[role='dialog']").last


def _hub_select(page, name: str) -> bool:
    """Відмітити у Dokument-Hub збережений документ із назвою name."""
    dlg = _hub_dialog(page)
    boxes = dlg.locator("input[type='checkbox'], input[type='radio']")
    for i in range(boxes.count()):
        box = boxes.nth(i)
        try:
            ctx = box.evaluate("e => (e.closest('label') || e.parentElement).innerText || ''")
            if name not in ctx:
                continue
            if not box.is_checked():
                try:
                    box.check(timeout=2000)
                except Exception:  # noqa: BLE001  кастомний чекбокс ховає input
                    box.evaluate("e => (e.closest('label') || e).click()")
                page.wait_for_timeout(500)
            return True
        except Exception:  # noqa: BLE001
            continue
    return False


def _hub_cancel(page) -> None:
    btn = _hub_dialog(page).locator("button:has-text('Abbrechen')")
    try:
        if btn.count():
            btn.last.click(timeout=3000)
            page.wait_for_timeout(800)
    except Exception:  # noqa: BLE001
        pass


def _hub_pick_or_upload(page, file: Path, alias: str | None = None) -> str:
    """Відкритий Dokument-Hub: збережений документ з такою назвою відмітити
    (назва унікальна на пакет, див. upload_name, тож це повтор тієї самої
    заявки), інакше завантажити через прихований input; потім «Dokument
    hinzufügen».

    XING дедуплікує за вмістом: повторне завантаження того самого файлу дає
    «Dieses Dokument wurde bereits hochgeladen…» без нового рядка. Тоді
    вибирається збережена копія за назвою або за alias (оригінальна назва
    файлу пакета), а якщо в цьому Hub її нема (CV-Hub показує лише «Deine
    Lebensläufe»), модал закривається і файл іде додатковим документом.
    """
    status = ""
    if _hub_select(page, file.name):
        status = "picked"
    else:
        inp = page.locator("input[type='file']")
        if not inp.count():
            _hub_cancel(page)
            return "no-file-input"
        inp.last.set_input_files(str(file))
        page.wait_for_timeout(3500)
        if _hub_dialog(page).get_by_text(_HUB_DUPLICATE_TEXT).count():
            if _hub_select(page, file.name) or (alias and _hub_select(page, alias)):
                status = "picked-duplicate"
            else:
                _hub_cancel(page)
                return "duplicate-unlisted"
        else:
            status = "uploaded"
            _hub_select(page, file.name)
    add = page.get_by_role("button", name=_HUB_ADD_RE)
    if not add.count() or not add.first.is_enabled():
        _hub_cancel(page)
        return status + ":add-disabled"
    add.first.click(timeout=5000)
    page.wait_for_timeout(2500)
    return status + ":attached"


def _add_button_for(page, kind: str):
    """Кнопка «Neues Dokument hinzufügen» потрібного слота (cv | letter).

    У майстрі слоти «Dein Lebenslauf» і «Dein Anschreiben» мають однакові
    кнопки; розрізняються за текстом навколо. Для letter у класичній формі
    перевага «Weitere Dokumente hinzufügen».
    """
    if kind == "letter":
        more = _first_enabled(page.get_by_role("button", name=_MORE_DOCS_RE))
        if more is not None:
            return more
    want = re.compile(r"Lebenslauf" if kind == "cv" else r"Anschreiben|Weitere Dokumente")
    loc = page.get_by_role("button", name=_ADD_DOC_RE)
    fallback = None
    for i in range(loc.count()):
        btn = loc.nth(i)
        try:
            if not (btn.is_visible() and btn.is_enabled()):
                continue
            ctx = btn.evaluate(_BTN_CONTEXT_JS) or ""
            if want.search(ctx):
                return btn
            if fallback is None:
                fallback = btn
        except Exception:  # noqa: BLE001
            continue
    return fallback


def _attach_documents(page, cv: Path | None, letter: Path | None,
                      aliases: dict[str, str] | None = None) -> list[str]:
    """Секція «Dokumente» нових сторінок XING: CV-слот і додаткові документи.

    Збережений у профілі CV («Zuletzt hochgeladen 4. Mai 2026») прибирається
    з заявки кнопкою «Datei … löschen», замість нього іде свіжий з пакета;
    Anschreiben іде як додатковий документ. Файл, що не став у CV-слот
    (дублікат у «Deine Dokumente»), теж іде додатковим. aliases: назва копії
    → оригінальна назва файлу пакета (для вибору дубліката в Hub). Порожній
    список = на цьому кроці документів нема.
    """
    log: list[str] = []
    aliases = aliases or {}
    add_btn = page.get_by_role("button", name=_ADD_DOC_RE)
    more_btn = page.get_by_role("button", name=_MORE_DOCS_RE)
    if not (add_btn.count() or more_btn.count()):
        return log
    pending: list[Path] = []
    if cv and cv.exists() and not _doc_listed(page, cv.name):
        dels = page.locator(_DELETE_DOC_SEL)
        for i in range(dels.count()):
            aria = dels.nth(i).get_attribute("aria-label") or ""
            if cv.name in aria or (letter is not None and letter.name in aria):
                continue
            try:
                dels.nth(i).click(timeout=3000)
                page.wait_for_timeout(1500)
                log.append(f"cv-slot-removed:{aria[:60]}")
            except Exception as e:  # noqa: BLE001
                log.append(f"cv-slot-remove-err:{type(e).__name__}")
            break
        btn = _add_button_for(page, "cv")
        if btn is None:
            log.append("cv:no-add-button")
            pending.append(cv)
        else:
            btn.click(timeout=5000)
            page.wait_for_timeout(1500)
            status = _hub_pick_or_upload(page, cv, aliases.get(cv.name))
            log.append("cv:" + status)
            if not status.endswith(":attached"):
                pending.append(cv)
    if letter and letter.exists() and not _doc_listed(page, letter.name):
        pending.append(letter)
    for f in pending:
        btn = _add_button_for(page, "letter")
        if btn is None:
            log.append(f"{f.name}:no-add-button")
            continue
        btn.click(timeout=5000)
        page.wait_for_timeout(1500)
        log.append(f"{f.name}:" + _hub_pick_or_upload(page, f, aliases.get(f.name)))
    return log


def _step_marker(page) -> str:
    """«Schritt N von M» майстра; порожній рядок на сторінках без кроків."""
    try:
        m = re.search(r"Schritt \d+ von \d+", page.inner_text("body"))
        return m.group(0) if m else ""
    except Exception:  # noqa: BLE001
        return ""


def _apply_page_flow(page, url: str, app_dir: Path, attachments: list[Path],
                     dry_run: bool, salary: str | None, prescreen: list[str]) -> ApplyResult:
    """Подача на окремій сторінці XING: поля, документи, «Weiter» до кнопки відправки."""
    log: list[str] = []
    if prescreen:
        log.append(f"prescreen={prescreen}")
    if "/jobs/quick-apply/" in page.url:
        # Свіжий CV і Anschreiben з пакета замість «zuletzt verwendeter Lebenslauf».
        edit = page.get_by_role("button", name=_EDIT_RE)
        if edit.count() and edit.first.is_visible():
            edit.first.click(timeout=5000)
            page.wait_for_timeout(3000)
            log.append("quick-apply:bearbeiten")
    if job_id_from_url(page.url) != job_id_from_url(url):
        shot = forms.screenshot(page, app_dir, "apply_wrong_job.png")
        return ApplyResult("form_unsupported", "xing", url, [shot],
                           reason=f"сторінка подачі іншої вакансії: {page.url[:100]}")
    tmp_dir = Path(tempfile.mkdtemp(prefix="xing_upload_"))
    try:
        copies = upload_copies(app_dir, attachments, tmp_dir)
        aliases = {c.name: a.name for c, a in zip(copies, [a for a in attachments if a.exists()])}
        cv, letter = split_attachments(copies)
        return _walk_apply_steps(page, url, app_dir, cv, letter, aliases, dry_run, salary, log)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _walk_apply_steps(page, url: str, app_dir: Path, cv: Path | None, letter: Path | None,
                      aliases: dict[str, str], dry_run: bool, salary: str | None,
                      log: list[str]) -> ApplyResult:
    """Кроки сторінки подачі: поля, документи, «Weiter», кнопка відправки."""
    for step in range(1, 9):
        try:
            filled = forms.fill_known_fields(page) + forms.fill_extended_fields(page, salary)
            if filled:
                log.append(f"step{step}:{filled}")
            docs = _attach_documents(page, cv, letter, aliases)
            if docs:
                log.append(f"step{step}:docs={docs}")
        except Exception as e:  # noqa: BLE001
            shot = forms.screenshot(page, app_dir, "apply_step_error.png")
            return ApplyResult("form_unsupported", "xing", url, [shot],
                               reason=f"крок {step}: {type(e).__name__}: {str(e)[:160]}; {' '.join(log)}")
        submit = page.get_by_role("button", name=_SUBMIT_RE)
        if submit.count() and submit.first.is_visible():
            shot_form = forms.screenshot(page, app_dir, "apply_form_filled.png")
            if dry_run:
                return ApplyResult("dry_run", "xing", url, [shot_form], reason=" ".join(log))
            submit.first.click(timeout=8000)
            page.wait_for_timeout(4000)
            confirmation = forms.find_confirmation(page)
            shot_conf = forms.screenshot(page, app_dir, "apply_confirmation.png")
            if confirmation:
                return ApplyResult("submitted", "xing", url, [shot_form, shot_conf],
                                   confirmation_text=confirmation, reason=" ".join(log))
            return ApplyResult("failed", "xing", url, [shot_form, shot_conf],
                               reason="no confirmation text after submit — verify manually; " + " ".join(log))
        marker = _step_marker(page)
        weiter = page.get_by_role("button", name=_WEITER_RE)
        if not (weiter.count() and weiter.first.is_visible()):
            break
        weiter.first.click(timeout=5000)
        page.wait_for_timeout(2500)
        if marker and _step_marker(page) == marker:
            # «Weiter» не пустив далі: обов'язкове поле лишилось порожнім.
            shot = forms.screenshot(page, app_dir, "apply_step_blocked.png")
            return ApplyResult("form_unsupported", "xing", url, [shot],
                               reason=f"крок не пройдено ({marker}): {' '.join(log)}")
    shot = forms.screenshot(page, app_dir, "apply_no_submit.png")
    return ApplyResult("form_unsupported", "xing", url, [shot],
                       reason=f"кнопки відправки не знайдено: {' '.join(log)}")


def _fill_xing_phone(page) -> bool:
    """Поле Telefonnummer поруч із select Landesvorwahl: лише національний номер."""
    try:
        has_prefix_select = page.locator("text=Landesvorwahl").count() > 0
        inp = page.locator("input[name*='phone' i], input[aria-label*='Telefon' i], "
                           "input[id*='phone' i]").first
        if not has_prefix_select or not inp.is_visible(timeout=1500):
            return False
        value = national_phone(candidate.PHONE, candidate.PHONE_PREFIX)
        inp.fill(value)
        return True
    except Exception:  # noqa: BLE001
        return False


def apply(page, url: str, app_dir: Path, attachments: list[Path], dry_run: bool,
          salary: str | None = None) -> ApplyResult:
    # XING-сесія не переживає рестарт браузера: логінимось у цьому ж контексті
    # (один раз на процес, результат кешується на контексті).
    from bot.scrapers.portal_login import ensure_login
    ctx = getattr(page, "context", None)  # тестові FakePage без контексту
    if ctx is not None:
        ensure_login(ctx, "xing")
    kind = detect.detect(page, url)
    if kind in ("login_required", "captcha", "closed"):
        shot = forms.screenshot(page, app_dir, "apply_preflight.png")
        return ApplyResult(kind, "xing", url, [shot], reason=f"pre-flight: {kind}")
    if kind != "xing_easy":
        shot = forms.screenshot(page, app_dir, "apply_preflight.png")
        return ApplyResult(
            "form_unsupported", "xing", url, [shot],
            reason=f"pre-flight classified as {kind}",
        )

    forms.accept_cookies(page)

    try:
        job_title = page.locator("h1").first.inner_text(timeout=2000).strip()
    except Exception:  # noqa: BLE001
        job_title = ""

    clicked = False
    main_btn, btn_kind = _main_apply_button(page)
    if btn_kind == "external" or (btn_kind == "none" and job_title):
        # Під заголовком «Zur Arbeitgeber-Website» або взагалі нічого: детектор
        # бачив «Einfach bewerben» зі стрічки «Ähnliche Jobs» (07.10.2026).
        # Нічого не клікати, лінк іде оператору.
        shot = forms.screenshot(page, app_dir, "apply_preflight.png")
        return ApplyResult(
            "form_unsupported", "xing", url, [shot],
            reason="під заголовком немає Easy Apply: зовнішня подача на сайті роботодавця",
        )
    if main_btn is not None:
        try:
            main_btn.click(timeout=5000)
            clicked = True
        except Exception:  # noqa: BLE001
            clicked = False
    if not clicked:
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
            "form_unsupported", "xing", url, [shot],
            reason="apply button not found",
        )

    page.wait_for_timeout(2500)

    # XING інколи спершу питає дозвіл на роботу в ЄС і рівні мов (07.10.2026:
    # обидві XING-форми репетиції лишились порожніми саме через цей модал).
    prescreen = answer_prescreening(page)

    if is_apply_page(page.url):
        return _apply_page_flow(page, url, app_dir, attachments, dry_run, salary, prescreen)

    form_kind = detect.classify_apply_page(page.url, page.content())
    if form_kind in ("login_required", "captcha"):
        shot = forms.screenshot(page, app_dir, "apply_form_blocked.png")
        return ApplyResult(form_kind, "xing", url, [shot], reason="blocked on form")

    if not _dialog_matches_job(page, job_title):
        shot = forms.screenshot(page, app_dir, "apply_wrong_job.png")
        return ApplyResult(
            "form_unsupported", "xing", url, [shot],
            reason=f"форма відкрилась для іншої вакансії (очікував «{job_title[:40]}»)",
        )

    phone_fixed = _fill_xing_phone(page)
    filled = forms.fill_known_fields(page)
    if phone_fixed:
        filled.append("phone=national")
    uploaded = forms.upload_documents(page, attachments)
    shot_form = forms.screenshot(page, app_dir, "apply_form_filled.png")

    if dry_run:
        return ApplyResult(
            "dry_run", "xing", url, [shot_form],
            reason=f"filled={filled} uploaded={uploaded}" + (f" prescreen={prescreen}" if prescreen else ""),
        )

    for sel in _SUBMIT_SELECTORS:
        try:
            btn = page.locator(sel).first
            if btn.is_visible(timeout=1500) and btn.is_enabled():
                btn.click(timeout=8000)
                break
        except Exception:
            continue
    else:
        return ApplyResult(
            "form_unsupported", "xing", url, [shot_form],
            reason="submit button not found",
        )

    page.wait_for_timeout(4000)
    confirmation = forms.find_confirmation(page)
    shot_conf = forms.screenshot(page, app_dir, "apply_confirmation.png")

    if confirmation:
        return ApplyResult(
            "submitted", "xing", url, [shot_form, shot_conf],
            confirmation_text=confirmation,
        )
    return ApplyResult(
        "failed", "xing", url, [shot_form, shot_conf],
        reason="no confirmation text after submit — verify manually",
    )
