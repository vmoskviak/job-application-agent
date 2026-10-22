"""Shared form helpers for portal apply flows."""

from __future__ import annotations

import re
from pathlib import Path

from bot.apply import candidate

CONFIRMATION_PATTERNS = [
    # StepStone smart-apply 2026: фінальна сторінка після «Bewerbung abschicken».
    # Інтерстишл «Bewerbung wird vorbereitet/geprüft/gesendet» сюди НЕ входить:
    # після нього StepStone може віддати «…auf der Unternehmensseite
    # weiterführen», і заявка не подана (будівельний концерн, 08.10.2026).
    "deine bewerbung ist auf dem weg",
    "bewerbung versendet",   # «Bewerbung versendet! Du bist jetzt offiziell im Rennen»
    "bewerbung wurde versendet",
    "bewerbung wurde erfolgreich",
    "erfolgreich beworben",
    "erfolgreich übermittelt",
    "erfolgreich versendet",
    "vielen dank für ihre bewerbung",
    "vielen dank für deine bewerbung",
    "bewerbung ist eingegangen",
    "application submitted",
    "application sent",
    # Stepstone success-сторінка 2026: «Geschafft, deine Bewerbung ist raus!»
    # (перевірено 22.07 на 4 подачах). УВАГА: «Eckdaten zur Bewerbung» тут НЕ
    # можна — цей блок є і на «Hat es geklappt?»-сторінці зовнішньої подачі,
    # де заявка ще НЕ відправлена (перевірено 22.07 на корпоративному ATS)
    "bewerbung ist raus",
    "glückwunsch",
    "bewerbung wurde übermittelt",
    # XING-сторінки подачі 2026 («Bewerbung senden» / «Bewerbung versenden»).
    "bewerbung gesendet",
    "bewerbung wurde gesendet",
    "bewerbung erfolgreich gesendet",
    "bewerbung abgeschickt",
    "bewerbung wurde abgeschickt",
    "bewerbung ist unterwegs",
]


def accept_cookies(page) -> None:
    """Клікає типові cookie-банери; відсутність — не помилка."""
    selectors = [
        "#ccmgt_explicit_accept",
        "button#onetrust-accept-btn-handler",
        "[data-testid='uc-accept-all-button']",
        "button:has-text('Alle akzeptieren')",
        "button:has-text('Alles akzeptieren')",
        "button:has-text('Akzeptieren')",
        "button:has-text('Accept all')",
        # Корпоративні ATS: «Nur notwendige Cookies» (мінімальна згода) / «Geht klar»
        "text=Nur notwendige Cookies",
        "button:has-text('Geht klar')",
    ]
    for sel in selectors:
        try:
            btn = page.locator(sel).first
            if btn.is_visible(timeout=800):
                btn.click(timeout=2000)
                page.wait_for_timeout(400)
                return
        except Exception:
            continue


_PHONE_HINTS = ("telefon", "phone", "mobil")


def _has_country_code_select(page) -> bool:
    """Форма з окремим кодом країни: select/combobox із «+49» або підпис
    «Landesvorwahl». Тоді в поле номера йде лише національна частина."""
    try:
        if page.locator("select, [role='combobox'], button").filter(has_text="+49").count():
            return True
        return page.get_by_text("Landesvorwahl", exact=False).count() > 0
    except Exception:
        return False


def fill_known_fields(page) -> list[str]:
    """Заповнює порожні текстові інпути за FIELD_HINTS. Повертає список
    заповнених полів (для логу). Незнайомі поля не чіпає."""
    filled: list[str] = []
    national_phone = _has_country_code_select(page)
    inputs = page.locator("input[type='text'], input[type='email'], input[type='tel'], input:not([type])")
    count = min(inputs.count(), 40)
    for i in range(count):
        inp = inputs.nth(i)
        try:
            if not inp.is_visible() or not inp.is_enabled():
                continue
            if (inp.input_value() or "").strip():
                continue  # передзаповнене профілем портала не чіпаємо
            meta = " ".join(
                filter(
                    None,
                    [
                        inp.get_attribute("name"),
                        inp.get_attribute("id"),
                        inp.get_attribute("placeholder"),
                        inp.get_attribute("aria-label"),
                    ],
                )
            ).lower()
            for hint, value in candidate.FIELD_HINTS.items():
                if hint in meta:
                    if national_phone and hint in _PHONE_HINTS:
                        value = candidate.PHONE_NATIONAL
                    inp.fill(value)
                    filled.append(hint)
                    break
        except Exception:
            continue
    return filled


def _labels_by_input_id(page) -> dict[str, str]:
    """id інпута → текст його <label for=...> (lowercase)."""
    try:
        pairs = page.evaluate(
            "Array.from(document.querySelectorAll('label[for]'))"
            ".map(l => [l.htmlFor, l.innerText])"
        )
        return {pid: (text or "").lower() for pid, text in pairs if pid}
    except Exception:
        return {}


def _el_meta(el, labels: dict[str, str]) -> str:
    parts = [
        el.get_attribute("name"),
        el.get_attribute("id"),
        el.get_attribute("placeholder"),
        el.get_attribute("aria-label"),
    ]
    el_id = el.get_attribute("id")
    if el_id and el_id in labels:
        parts.append(labels[el_id])
    labelled_by = el.get_attribute("aria-labelledby")
    if labelled_by:
        # XING-майстер (14.10.2026): питання лежить в елементі з aria-labelledby,
        # <label for> немає; сам id теж говорить («…country_of_residence»).
        parts.append(labelled_by)
        try:
            parts.append(el.evaluate(
                "(e, ids) => ids.split(/\\s+/).map(i => (document.getElementById(i) || {}).innerText || '').join(' ')",
                labelled_by))
        except Exception:
            pass
    return " ".join(filter(None, parts)).lower()


def _is_required(el, meta: str) -> bool:
    if el.get_attribute("required") is not None:
        return True
    if (el.get_attribute("aria-required") or "").lower() == "true":
        return True
    return "*" in meta


_LANG_KEYS = {"Deutsch": ("deutsch", "german"), "Englisch": ("englisch", "english")}
# Рівень із profile.yaml → підрядки опцій у порядку переваги.
_LEVEL_NEEDLES = {
    "C2": ("muttersprach", "c2", "verhandlungssicher", "fließend"),
    "C1": ("c1", "fließend", "verhandlungssicher", "sehr gut"),
    "B1-B2": ("b2", "gut", "fortgeschritten", "b1"),
    "A1-A2": ("a2", "grundkenntnis", "a1"),
}


def pick_language_level(meta: str, option_labels: list[str], levels: dict) -> str | None:
    """Опція select-а «Bitte stufen Sie Ihre Deutschkenntnisse ein» за рівнем із
    профілю. None, якщо це не мовне питання, рівня в профілі нема або жодна
    опція не підходить: тоді поле лишається порожнім, а не вгадується
    (08.10.2026 бот вибрав «Keine» для німецької як першу опцію)."""
    low = meta.lower()
    for lang, keys in _LANG_KEYS.items():
        if not any(k in low for k in keys):
            continue
        needles = _LEVEL_NEEDLES.get(str(levels.get(lang, "")).strip().upper().replace(" ", ""))
        if not needles:
            return None
        for needle in needles:
            exact = [lab for lab in option_labels if lab.strip().lower() == needle]
            if exact:
                return exact[0]
            part = [lab for lab in option_labels
                    if needle in lab.lower() and not (needle == "gut" and "sehr gut" in lab.lower())]
            if part:
                return part[0]
        return None
    return None


def text_answer(meta: str, answers: list[dict] | None = None) -> str | None:
    """Текстова відповідь із profile.yaml → form_answers за regex по тексту питання."""
    for rule in (candidate.FORM_ANSWERS if answers is None else answers):
        if rule.get("answer") and re.search(str(rule["pattern"]), meta, re.IGNORECASE):
            return str(rule["answer"])
    return None


_CHOICE_LABELS = {"no": ("no", "nein"), "yes": ("yes", "ja")}


def choose_radio(question: str, options: list[str], answers: list[dict] | None = None) -> int | None:
    """Індекс radio-опції для питання Ja/Nein за form_answers → choice. None = не чіпати."""
    for rule in (candidate.FORM_ANSWERS if answers is None else answers):
        wanted = _CHOICE_LABELS.get(str(rule.get("choice", "")).lower())
        if not wanted or not re.search(str(rule["pattern"]), question, re.IGNORECASE):
            continue
        for i, label in enumerate(options):
            if label.strip().lower() in wanted:
                return i
    return None


_RADIO_GROUPS_JS = """() => {
  const groups = {};
  const all = [...document.querySelectorAll("input[type=radio]")];
  all.forEach((r, i) => {
    // StepStone 2026 не ставить name: група = data-testid без хвоста «-radio-N»,
    // інакше найближчий radiogroup/fieldset.
    const tid = (r.getAttribute("data-testid") || "").replace(/-radio-\\d+$/, "");
    const box = r.closest("[role=radiogroup], fieldset");
    const key = r.name || tid || (box ? "box" + all.indexOf(box.querySelector("input[type=radio]")) : "");
    if (!key) return;
    r.__gi = i;
    (groups[key] ||= []).push(r);
  });
  return Object.entries(groups).map(([name, rs]) => {
    const fs = rs[0].closest("fieldset");
    let q = fs && fs.querySelector("legend") ? fs.querySelector("legend").innerText : "";
    if (!q) { const rg = rs[0].closest("[aria-labelledby]");
      const lab = rg ? document.getElementById(rg.getAttribute("aria-labelledby").split(" ")[0]) : null;
      if (lab) q = lab.innerText || ""; }
    if (!q) { let p = rs[0].parentElement;   // кастомні radio сидять глибоко: до 12 рівнів
      for (let d = 0; p && d < 12; d++) { const t = (p.innerText || "").trim(); if (t.length > 25) { q = t; break; } p = p.parentElement; } }
    return { name, question: (q || "").slice(0, 400), checked: rs.some(r => r.checked),
      indices: rs.map(r => r.__gi),
      options: rs.map(r => { const l = r.id ? document.querySelector('label[for="' + CSS.escape(r.id) + '"]') : null;
        const own = r.closest("label");
        return ((l ? l.innerText : (own ? own.innerText : r.value)) || "").trim(); }) };
  });
}"""


def answer_radio_questions(page) -> list[str]:
    """Відповідає на radio-питання Ja/Nein, описані в form_answers. Решту не чіпає."""
    done: list[str] = []
    if not any(r.get("choice") for r in candidate.FORM_ANSWERS):
        return done
    try:
        groups = page.evaluate(_RADIO_GROUPS_JS)
    except Exception:
        return done
    for g in groups:
        if g.get("checked"):
            continue
        idx = choose_radio(g.get("question", ""), g.get("options", []))
        if idx is None:
            continue
        try:
            radio = page.locator("input[type='radio']").nth(g["indices"][idx])
            rid = radio.get_attribute("id")
            label = page.locator(f'label[for="{rid}"]').first if rid else None
            if label is not None and label.count() and label.is_visible():
                label.click(timeout=3000)   # кастомні radio: клік по підпису надійніший
            else:
                radio.check(force=True, timeout=3000)
            if not radio.is_checked():
                radio.check(force=True, timeout=3000)
            done.append(f"radio:{g['options'][idx]}")
        except Exception:
            continue
    return done


def earliest_start_iso(today=None) -> str:
    """Реалістичний найближчий Eintrittstermin для полів дати: 1-ше число
    наступного місяця, якщо до нього ще ≥ 14 днів, інакше 1-ше через місяць.
    «Сьогодні» у формі виглядало як помилка (XING-майстер, 14.10.2026)."""
    from datetime import date as _date, timedelta

    today = today or _date.today()
    first_next = (today.replace(day=1) + timedelta(days=32)).replace(day=1)
    if (first_next - today).days < 14:
        first_next = (first_next + timedelta(days=32)).replace(day=1)
    return first_next.isoformat()


def fill_extended_fields(page, salary: str | None = None) -> list[str]:
    """Другий прохід після fill_known_fields: label-матчинг (Stepstone кладе
    назву поля лише в <label for>), дати (Geburtsdatum/Eintritt), Gehalt і
    селекти. Незнайомі селекти чіпає лише якщо вони обов'язкові (інакше
    опційний Anredetitel отримав би 'Dr.'). Повертає список для логу."""
    filled: list[str] = []
    labels = _labels_by_input_id(page)
    start_iso = earliest_start_iso()

    inputs = page.locator(
        "input[type='text'], input[type='email'], input[type='tel'], "
        "input[type='number'], input[type='date'], input:not([type])"
    )
    for i in range(min(inputs.count(), 40)):
        inp = inputs.nth(i)
        try:
            if not inp.is_visible() or not inp.is_enabled():
                continue
            if (inp.input_value() or "").strip():
                continue
            meta = _el_meta(inp, labels)
            if not meta:
                continue
            is_date = (inp.get_attribute("type") or "") == "date"
            if "geburt" in meta or "birth" in meta:
                inp.fill(candidate.BIRTH_DATE_ISO)
                filled.append("geburtsdatum")
            elif is_date and any(h in meta for h in ("eintritt", "verfügbar", "start", "beginn")):
                inp.fill(start_iso)
                filled.append("eintrittsdatum")
            elif any(h in meta for h in ("gehalt", "salary")):
                inp.fill(str(salary or candidate.DEFAULT_SALARY))
                filled.append("gehalt")
            elif not is_date:
                answer = text_answer(meta)
                if answer:
                    inp.fill(answer)
                    filled.append("answer:" + meta[:24])
                    continue
                for hint, value in candidate.FIELD_HINTS.items():
                    if hint in meta:
                        inp.fill(value)
                        filled.append(f"label:{hint}")
                        break
        except Exception:
            continue

    selects = page.locator("select")
    for i in range(min(selects.count(), 20)):
        sel = selects.nth(i)
        try:
            if not sel.is_visible() or not sel.is_enabled():
                continue
            if (sel.input_value() or "").strip():
                continue  # вже вибрано (профілем чи нами)
            meta = _el_meta(sel, labels)
            options = sel.evaluate(
                "el => Array.from(el.options).map(o => [o.value, o.label || o.text])"
            )
            real = [(v, (t or "").strip()) for v, t in options if (v or "").strip()]
            if not real:
                continue

            def pick(*needles: str) -> str | None:
                for v, t in real:
                    if any(n in t.lower() for n in needles):
                        return v
                return None

            value = None
            label_tag = None
            if "anrede" in meta and "titel" not in meta:
                value, label_tag = pick("frau"), "anrede"
            elif "land" in meta or "country" in meta:
                value, label_tag = pick("deutschland", "germany"), "land"
            elif "kündigungsfrist" in meta or "notice" in meta:
                value = pick("sofort", "keine") or real[0][0]
                label_tag = "kündigungsfrist"
            elif "umzug" in meta or "reloc" in meta:
                value, label_tag = pick("nein", "no"), "umzugsbereit"
            elif any(k in meta for keys in _LANG_KEYS.values() for k in keys):
                label = pick_language_level(meta, [t for _, t in real], candidate.LANGUAGE_LEVELS)
                value = next((v for v, t in real if t == label), None) if label else None
                label_tag = f"sprache:{label}" if value else None
                if not value:
                    filled.append(f"select-offen:{meta[:30]}")
            elif _is_required(sel, meta):
                # Обов'язковий незнайомий селект НЕ вгадуємо: перша опція буває
                # «Keine» / «Nein» (Deutschkenntnisse → «Keine», 08.10.2026).
                # Поле лишається порожнім, submit неактивний, заявка йде в manual.
                filled.append(f"select-offen:{meta[:30]}")
            if value:
                sel.select_option(value)
                filled.append(label_tag or "select")
        except Exception:
            continue
    return filled


def _button_context(btn) -> str:
    """НАЙБЛИЖЧИЙ змістовний текст довкола кнопки (перший предок з innerText
    15–250 симв.) — ширший контекст захоплює цілу картку Berufserfahrung, де
    «cv» перебиває справжню секцію «Zusätzliche Dateien» (22.07)."""
    try:
        return (btn.evaluate(
            "el => { const own = (el.innerText || '').trim(); let p = el.parentElement;"
            " for (let d = 0; p && d < 8; d++) {"
            "   const t = (p.innerText || '').trim();"
            # обгортка самої кнопки («Datei auswählen», 15 симв.) контекстом не є:
            # нова StepStone-форма (08.10.2026) тримає підпис «Lebenslauf hierhin
            # ziehen…» у сусідньому вузлі, і без цього пропуску CV-слот не впізнавався
            "   if (t.length >= 15 && t.length < 400 && t !== own && t.length > own.length + 3) return t;"
            "   if (t.length >= 400) return '';"
            "   p = p.parentElement; }"
            " return ''; }"
        ) or "").lower()
    except Exception:
        return ""


def _upload_via_chooser(page, btn, file: Path) -> bool:
    """Клік по кнопці секції → file chooser (одразу або через попап
    «Datei hochladen»). True = файл передано у chooser."""
    try:
        with page.expect_file_chooser(timeout=2500) as fc_info:
            btn.click(timeout=3000)
        fc_info.value.set_files(str(file))
        return True
    except Exception:
        pass
    # кнопка відкрила попап-меню, а не chooser
    try:
        # НЕ мішати text= у css-union — невалідний селектор тихо валить спробу
        item = page.locator("text=Datei hochladen").first
        if item.is_visible(timeout=1500):
            with page.expect_file_chooser(timeout=4000) as fc_info:
                item.click(timeout=3000)
            fc_info.value.set_files(str(file))
            return True
    except Exception:
        pass
    return False


def _replace_profile_cv(page, log: list[str]) -> None:
    """Форма StepStone 2026 показує в CV-слоті профільний CV з попередньої
    подачі (картка «Hochgeladen am …» з меню «…»: Vorschau / Herunterladen /
    Löschen, кнопки «Ersetzen» нема). Профільний CV тейлорений під минулу
    вакансію, тому його видаляємо, і далі звичайний маппінг вантажить CV цієї."""
    try:
        menu = page.locator("button[aria-label='attachmentMenuDisclosure']").first
        if not menu.is_visible(timeout=1500):
            return
        menu.click(timeout=3000)
        page.wait_for_timeout(800)
        delete = page.locator("[data-testid='deleteFileButton'], [role='menuitem']:has-text('Löschen')").first
        if not delete.is_visible(timeout=2000):
            page.keyboard.press("Escape")
            return
        delete.click(timeout=3000)
        page.wait_for_timeout(1200)
        # можливий діалог підтвердження
        try:
            confirm = page.locator("[role='dialog'] button:has-text('Löschen'), "
                                   "[role='dialog'] button:has-text('Bestätigen')").first
            if confirm.is_visible(timeout=1500):
                confirm.click(timeout=3000)
                page.wait_for_timeout(1200)
        except Exception:
            pass
        log.append("cv-profile:gelöscht")
    except Exception:
        return


def upload_documents_mapped(
    page,
    cv: Path | None,
    anschreiben: Path | None,
    zeugnisse: list[Path],
) -> list[str]:
    """Вантажить документи ЧЕРЕЗ КНОПКИ секцій + file chooser.

    КРИТИЧНО: на Stepstone file-input один, висить на <body> без контексту і
    спільний для всіх секцій; прямий set_input_files доставляє файл у
    ДЕФОЛТНИЙ обробник — CV-слот, який пише у ПРОФІЛЬ кандидата (інцидент
    22.07: Anschreiben перезаписав профільний CV). Тому: клік по кнопці
    конкретної секції → expect_file_chooser → set_files. Ніколи не сетити
    body-level input напряму. Повертає лог "секція:файл"."""
    log: list[str] = []
    if cv and cv.exists():
        _replace_profile_cv(page, log)
    buttons = page.locator(
        "button:has-text('Hinzufügen'), button:has-text('Datei auswählen'), "
        "button:has-text('Ersetzen')"
    )
    anschreiben_placed = False
    zeugnis_placed = False
    cv_placed = False
    # Знімок кнопок один раз: після завантаження CV кнопка «Datei auswählen»
    # зникає, nth(i) по живому локатору з'їжджає, і «Hinzufügen» пропускається
    # (dry-run 08.10.2026: CV є, Anschreiben нема).
    try:
        handles = buttons.element_handles()[:10]
    except Exception:
        handles = []
    for btn in handles:
        try:
            if not btn.is_visible():
                continue
            ctx = _button_context(btn)
            file: Path | None = None
            tag = ""
            # порядок: специфічні секції ПЕРЕД cv — ширший контекст може
            # містити «cv» від сусіднього CV-блока тієї ж картки
            try:
                own = (btn.inner_text() or "").strip().lower()
            except Exception:
                own = ""
            # Кнопка «Hinzufügen» = секція «Zusätzliche Dateien» (форма 2026),
            # навіть якщо ширший контекст зачепив сусідню CV-картку з «Lebenslauf».
            is_zusatz_btn = "hinzufügen" in own
            is_cv_slot = (not is_zusatz_btn) and (
                "lebenslauf" in ctx or bool(re.search(r"\bcv\b", ctx)))
            if "anschreiben" in ctx and anschreiben and not anschreiben_placed:
                file, tag = anschreiben, "anschreiben"
            elif "zeugnis" in ctx and zeugnisse and not zeugnis_placed:
                file, tag = zeugnisse[0], "zeugnis"
            elif is_cv_slot and cv and not cv_placed:
                # CV-слот перед «zusätzliche Datei»: у формі 2026 підпис дропзони
                # «Lebenslauf hierhin ziehen…», і Anschreiben туди не можна
                # (слот пише в профільний CV, інцидент 22.07 і 08.10.2026)
                file, tag = cv, "cv"
            elif (
                (is_zusatz_btn or any(k in ctx for k in ("zusätzliche datei", "weitere anlage")))
                and anschreiben and not anschreiben_placed
            ):
                file, tag = anschreiben, "zusatz→anschreiben"
            if file is None or not file.exists():
                continue
            if not _upload_via_chooser(page, btn, file):
                continue
            page.wait_for_timeout(2500)
            # попап «Wähle Elemente aus…»: файл треба ще підтвердити кнопкою
            # «N Datei(en) zu deiner Bewerbung hinzufügen»
            try:
                confirm = page.locator(
                    "button:has-text('zu deiner Bewerbung hinzufügen')"
                ).first
                if confirm.is_visible(timeout=2000):
                    confirm.click(timeout=3000)
                    page.wait_for_timeout(1500)
            except Exception:
                pass
            log.append(f"{tag}:{file.name}")
            if tag == "cv":
                cv_placed = True
            elif tag == "zeugnis":
                zeugnis_placed = True
            else:
                anschreiben_placed = True
        except Exception:
            continue
    # Форма з однією загальною секцією «Zusätzliche Dateien» (StepStone 2026):
    # окремого слота для Zeugnisse нема, а роботодавці пишуть «nur Bewerbungen
    # mit Lebenslauf und Zeugnissen». Додаємо їх тією ж кнопкою «Hinzufügen».
    if anschreiben_placed and not zeugnis_placed and zeugnisse:
        for z in zeugnisse[:2]:
            if not z.exists():
                continue
            try:
                add = page.locator("button:has-text('Hinzufügen')").first
                if not add.is_visible(timeout=1500) or not _upload_via_chooser(page, add, z):
                    break
                page.wait_for_timeout(2500)
                try:
                    confirm = page.locator("button:has-text('zu deiner Bewerbung hinzufügen')").first
                    if confirm.is_visible(timeout=1500):
                        confirm.click(timeout=3000)
                        page.wait_for_timeout(1500)
                except Exception:
                    pass
                log.append(f"zusatz→zeugnis:{z.name}")
            except Exception:
                break
    # Єдина кнопка на формі без розпізнаного підпису: у Schnelle-Bewerbung-формі
    # 2026 це завжди CV-слот («CV» як заголовок секції стоїть далеко від кнопки).
    if not log and cv and cv.exists() and buttons.count() == 1:
        try:
            btn = buttons.first
            if btn.is_visible() and _upload_via_chooser(page, btn, cv):
                page.wait_for_timeout(2500)
                log.append(f"cv:{cv.name}")
        except Exception:
            pass
    return log


def js_fill_by_label(page, label_sub: str, value: str) -> str:
    """Заповнює input/textarea за фрагментом тексту <label> через JS
    (native setter + input/change події — працює з React-формами)."""
    return page.evaluate("""([labelSub, value]) => {
      const labs = Array.from(document.querySelectorAll('label'));
      for (const l of labs) {
        if (!(l.innerText || '').toLowerCase().includes(labelSub.toLowerCase())) continue;
        const el = document.getElementById(l.htmlFor);
        if (!el) continue;
        const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
        Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, value);
        el.dispatchEvent(new Event('input', {bubbles: true}));
        el.dispatchEvent(new Event('change', {bubbles: true}));
        return `${labelSub} -> ok`;
      }
      return `${labelSub} -> label not found`;
    }""", [label_sub, value])


def js_set_select_by_label(page, label_sub: str, *needles: str) -> str:
    """Ставить value <select> (навіть схованого за кастомним віджетом) за
    фрагментами тексту опції; перший збіг з needles перемагає."""
    return page.evaluate("""([labelSub, needles]) => {
      const sels = Array.from(document.querySelectorAll('select'));
      for (const el of sels) {
        const lab = (document.querySelector(`label[for="${el.id}"]`)?.innerText || '').toLowerCase();
        if (!lab.includes(labelSub.toLowerCase())) continue;
        for (const n of needles) {
          const opt = Array.from(el.options).find(o => o.text.toLowerCase().includes(n.toLowerCase()));
          if (opt) {
            el.value = opt.value;
            el.dispatchEvent(new Event('input', {bubbles: true}));
            el.dispatchEvent(new Event('change', {bubbles: true}));
            return `${lab.slice(0,30)} -> ${opt.text}`;
          }
        }
        return `${lab.slice(0,30)} -> NO MATCH`;
      }
      return `label not found: ${labelSub}`;
    }""", [label_sub, list(needles)])


def upload_documents(page, files: list[Path]) -> int:
    """Вантажить файли у file-input (останній на сторінці — на Stepstone це
    «Weitere Anlage»; перший — заміна CV з профілю). Повертає кількість."""
    existing = [f for f in files if f.exists()]
    if not existing:
        return 0
    file_inputs = page.locator("input[type='file']")
    if file_inputs.count() == 0:
        # інпут може з'являтись після кліку на кнопку вибору файлу
        for sel in ("button:has-text('Datei auswählen')", "button:has-text('Hinzufügen')"):
            try:
                btn = page.locator(sel).first
                if btn.is_visible(timeout=800):
                    btn.click(timeout=2000)
                    page.wait_for_timeout(800)
                    break
            except Exception:
                continue
        if file_inputs.count() == 0:
            return 0
    target = file_inputs.last
    uploaded = 0
    try:
        if target.get_attribute("multiple") is not None:
            target.set_input_files([str(f) for f in existing])
            page.wait_for_timeout(1500)
            return len(existing)
        for f in existing:
            target.set_input_files([str(f)])
            page.wait_for_timeout(1500)
            uploaded += 1
    except Exception:
        pass
    return uploaded


def find_confirmation(page) -> str:
    """Шукає текст підтвердження подачі. Порожній рядок = не знайдено."""
    try:
        body = page.inner_text("body").lower()
    except Exception:
        return ""
    for pattern in CONFIRMATION_PATTERNS:
        if pattern in body:
            return pattern
    return ""


def screenshot(page, app_dir: Path, name: str) -> str:
    """Скріншот у working/ папку заявки; повертає відносний шлях."""
    working = app_dir / "working"
    working.mkdir(parents=True, exist_ok=True)
    path = working / name
    try:
        page.screenshot(path=str(path), full_page=True)
    except Exception:
        page.screenshot(path=str(path))
    return str(path)
