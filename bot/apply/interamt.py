"""Interamt (interamt.de) auto-apply flow — öffentlicher Dienst.

Кодифіковано 28.07.2026 після живої подачі Musterbehörde (Stellen-ID 1234567,
в останній день Frist). Wicket-візард з пʼяти кроків:
Anschreiben → Bewerbungsbogen → Lebenslauf → Anlagen → Abschluss.

Особливості платформи:
- Сесія серверна (JSESSIONID) і гине з рестартом браузера. Логін уміє лише
  людина (scripts/interamt_repl.py + VNC, див. docs/14_server_setup.md).
  Без живої сесії модуль повертає login_required — це очікуваний стан
  нічних ранів, вакансія їде в manual-список звіту.
- Bewerbungsbogen і Lebenslauf заповнює кнопка «Daten aus Bewerbungsprofil
  übernehmen»: профіль наповнено подачею 28.07 (там-таки виправлено
  переплутані Vor-/Nachname). Behörde-специфічні додаткові питання завалять
  save — тоді form_unsupported зі скріншотом і текстом feedback-панелі.
- Anschreiben — CKEditor: текст через CKEDITOR.instances API. Шапку портал
  додає сам; Anrede і Grußformel додаємо ми.
- Anlagen: кожна Behörde задає власні слоти і хоче ОКРЕМІ файли. Мапимо по
  ключових словах на attachments/interamt/ + пакет заявки. Незаповнені
  обовʼязкові слоти не блокують подачу (портал дозволяє надіслати неповний
  комплект) — список іде в reason.
- Wicket-селекти реагують лише на value + change-event; залежні поля
  (Fachgebiet, Sonstige Branche) зʼявляються ПІСЛЯ вибору. У профільних даних
  вони вже збережені, тож у щасливому шляху цим займатись не треба.

Fail-safe: незнайома сторінка/помилка валідації → form_unsupported зі
скріншотом, наосліп не сабмітить.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from bot import client as _client
from bot.apply import forms
from bot.apply.candidate import FULL_NAME
from bot.apply.models import ApplyResult
from bot.generators.cv_engine import CV_FILE_STEM, ATTACHMENTS_DIR

PROJECT_ROOT = Path(__file__).parent.parent.parent
BUNDLE_DIR = ATTACHMENTS_DIR / "interamt"

# Interamt приймає по одному PDF на слот, тому документи клієнта зведені в
# тематичні файли в attachments/interamt/. Імена можна перевизначити в
# cv.yaml → interamt_bundle: {arbeitszeugnis, studium, schule, sprache}.
_BUNDLE = {
    "arbeitszeugnis": "Arbeitszeugnisse.pdf",
    "studium": "Studienabschluesse.pdf",
    "schule": "Abschlusszeugnis_Schule.pdf",
    "sprache": "Sprachnachweis.pdf",
    **(_client.active().cv.get("interamt_bundle") or {}),
}

# (ключові слова в label слота, файл) — порядок важливий: перший матч виграє.
# None у другій позиції = слот свідомо ігноруємо (не стосується кандидата).
_SLOT_RULES: list[tuple[tuple[str, ...], str | None]] = [
    (("anschreiben", "motivationsschreiben"), "@anschreiben"),
    (("lebenslauf",), "@cv"),
    (("arbeitszeugnis", "beurteilung"), _BUNDLE["arbeitszeugnis"]),
    (("abschlusszeugnis studium", "staatsexamen"), _BUNDLE["studium"]),
    (("abschlusszeugnis schule",), _BUNDLE["schule"]),
    (("sprach",), _BUNDLE["sprache"]),
    (("lichtbild", "schwerbehinder", "wehr-", "zivildienst", "impfnachweis",
      "ausbildungsnachweis", "zwischenzeugnis"), None),
]


def read_anschreiben_text(app_dir: Path) -> str | None:
    """Текст anschreiben з data-only generate.py пакета (ast, не regex)."""
    gen = app_dir / "generate.py"
    if not gen.exists():
        return None
    try:
        tree = ast.parse(gen.read_text(encoding="utf-8"))
    except SyntaxError:
        return None
    for node in ast.walk(tree):
        if isinstance(node, ast.keyword) and node.arg == "anschreiben":
            if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                return node.value.value
    return None


def letter_html(text: str) -> str:
    """Anrede + абзаци + Grußformel як HTML для CKEditor.

    Хвости (Grußformel, підпис, Anlagen-блок) зрізає _letter_body з cv_engine —
    та сама зачистка, що для PDF/DOCX, щоб лист не дублював шапку.
    """
    from bot.generators.cv_engine import _letter_body

    paras = _letter_body(text)
    html = ["<p>Sehr geehrte Damen und Herren,</p>"]
    html += [f"<p>{p}</p>" for p in paras]
    html.append(f"<p>Mit freundlichen Grüßen<br>{FULL_NAME}</p>")
    return "".join(html)


def slot_file(label: str, app_dir: Path) -> Path | None:
    """Файл для слота Anlagen за його label; None = слот пропускаємо."""
    low = (label or "").lower()
    for needles, target in _SLOT_RULES:
        if any(n in low for n in needles):
            if target is None:
                return None
            if target == "@anschreiben":
                return app_dir / "to_send" / "01_Anschreiben.pdf"
            if target == "@cv":
                return app_dir / "to_send" / f"{CV_FILE_STEM}.pdf"
            return BUNDLE_DIR / target
    return None


def _body(page) -> str:
    try:
        return page.inner_text("body")
    except Exception:
        return ""


def _click_action(page, label: str, settle_ms: int = 5000) -> bool:
    """Кнопки Interamt — input[type=submit] зі значенням-текстом або лінки."""
    for sel in (f"input[type=submit][value='{label}']",
                f"input[type=submit][value*='{label}']",
                f"a:has-text('{label}')",
                f"button:has-text('{label}')"):
        try:
            loc = page.locator(sel).first
            if loc.count() and loc.is_visible(timeout=1500):
                loc.click(timeout=8000)
                page.wait_for_timeout(settle_ms)
                return True
        except Exception:
            continue
    return False


def _feedback(page) -> str:
    m = re.search(r"Bitte überprüfen Sie die Eingabe[^\n]*(?:\n[^\n]+){0,6}", _body(page))
    return m.group(0).strip() if m else ""


def _active_step(page) -> str:
    """Назва активного кроку візарда (з document.title 'Крок | ...')."""
    try:
        return (page.title() or "").split("|")[0].strip()
    except Exception:
        return ""


def _save_and_advance(page, settle_ms: int = 6000) -> str:
    """«speichern und weiter»; повертає текст feedback-панелі ('' = ok)."""
    if not _click_action(page, "speichern und weiter", settle_ms):
        return "кнопку «speichern und weiter» не знайдено"
    return _feedback(page)


def _upload_anlagen(page, app_dir: Path) -> tuple[list[str], list[str]]:
    """Заповнює слоти Anlagen. Повертає (uploaded, skipped_labels)."""
    rows = page.evaluate(
        "() => [...document.querySelectorAll('input[type=file]')]"
        ".filter(e => !!e.offsetParent)"
        ".map((e, i) => { const tr = e.closest('tr');"
        " const label = tr ? (tr.querySelector('td')||{}).innerText || '' : '';"
        " const done = tr ? /hochgeladen|aus Profil/i.test(tr.innerText) : false;"
        " return { i: i, name: e.name, label: label.trim(), done: done }; })"
    )
    uploaded, skipped = [], []
    for row in rows:
        if row.get("done"):
            continue
        target = slot_file(row.get("label", ""), app_dir)
        if target is None:
            if row.get("label"):
                skipped.append(row["label"][:40])
            continue
        if not target.exists():
            skipped.append(f"{row['label'][:40]} (файл відсутній: {target.name})")
            continue
        try:
            sel = f"input[type=file][name='{row['name']}']"
            page.locator(sel).first.set_input_files(str(target))
            page.wait_for_timeout(4000)
            uploaded.append(f"{row['label'][:40]} <- {target.name}")
        except Exception as e:
            skipped.append(f"{row['label'][:40]} (upload fail: {type(e).__name__})")
    return uploaded, skipped


def apply(page, url: str, app_dir: Path, attachments: list[Path], dry_run: bool,
          salary: str | None = None) -> ApplyResult:
    page.goto(url, wait_until="domcontentloaded", timeout=45000)
    page.wait_for_timeout(3500)
    forms.accept_cookies(page)

    # «Mein INTERAMT» у меню видно і анонімно; надійний маркер сесії —
    # лише Logout/Abmelden у шапці.
    body = _body(page)
    if not re.search(r"\bLogout\b|\bAbmelden\b", body):
        shot = forms.screenshot(page, app_dir, "apply_preflight.png")
        return ApplyResult(
            "login_required", "interamt", url, [shot],
            reason="Interamt-сесія мертва (серверний JSESSIONID). "
                   "Re-login: DISPLAY=:99 scripts/interamt_repl.py + VNC.",
        )

    anschreiben_text = read_anschreiben_text(app_dir)
    if not anschreiben_text:
        return ApplyResult(
            "form_unsupported", "interamt", url, [],
            reason="anschreiben-текст не знайдено в generate.py пакета",
        )

    if not _click_action(page, "Online Bewerben"):
        shot = forms.screenshot(page, app_dir, "apply_no_button.png")
        return ApplyResult(
            "form_unsupported", "interamt", url, [shot],
            reason="кнопки «Online Bewerben» нема (Frist минув або nur schriftlich?)",
        )

    filled: list[str] = []

    # ── Anschreiben (CKEditor) ────────────────────────────────────────────────
    if _active_step(page) == "Anschreiben":
        _click_action(page, "Anschreiben neu erstellen", settle_ms=4000)
        ok = page.evaluate(
            "(html) => { const K = window.CKEDITOR;"
            " if (!K) return false;"
            " const keys = Object.keys(K.instances);"
            " if (!keys.length) return false;"
            " K.instances[keys[0]].setData(html); return true; }",
            letter_html(anschreiben_text),
        )
        if not ok:
            shot = forms.screenshot(page, app_dir, "apply_form_blocked.png")
            return ApplyResult("form_unsupported", "interamt", url, [shot],
                               reason="CKEditor не знайдено на кроці Anschreiben")
        page.wait_for_timeout(1500)
        filled.append("anschreiben=ckeditor")
        fb = _save_and_advance(page)
        if fb:
            shot = forms.screenshot(page, app_dir, "apply_form_blocked.png")
            return ApplyResult("form_unsupported", "interamt", url, [shot],
                               reason=f"Anschreiben: {fb[:300]}")

    # ── Bewerbungsbogen і Lebenslauf: профіль + save ──────────────────────────
    for step in ("Bewerbungsbogen", "Lebenslauf"):
        if _active_step(page) != step:
            continue
        _click_action(page, "Daten aus Bewerbungsprofil übernehmen", settle_ms=5000)
        filled.append(f"{step.lower()}=profil")
        fb = _save_and_advance(page)
        if fb:
            # Behörde-специфічні поля, яких нема в профілі — не вгадуємо
            shot = forms.screenshot(page, app_dir, "apply_form_blocked.png")
            return ApplyResult("form_unsupported", "interamt", url, [shot],
                               reason=f"{step}: {fb[:300]}")

    # ── Anlagen ──────────────────────────────────────────────────────────────
    if _active_step(page) == "Anlagen":
        _click_action(page, "Anlagen aus Bewerbungs-Profil übernehmen", settle_ms=4000)
        uploaded, skipped = _upload_anlagen(page, app_dir)
        filled.append(f"anlagen={uploaded}")
        if skipped:
            filled.append(f"anlagen_skipped={skipped}")
        fb = _save_and_advance(page)
        if fb:
            shot = forms.screenshot(page, app_dir, "apply_form_blocked.png")
            return ApplyResult("form_unsupported", "interamt", url, [shot],
                               reason=f"Anlagen: {fb[:300]}")

    # ── Abschluss ────────────────────────────────────────────────────────────
    if _active_step(page) != "Abschluss":
        shot = forms.screenshot(page, app_dir, "apply_form_blocked.png")
        return ApplyResult(
            "form_unsupported", "interamt", url, [shot],
            reason=f"очікував крок Abschluss, а на сторінці «{_active_step(page)}»",
        )

    missing = ""
    m = re.search(r"Folgende Pflichtanlagen fehlen noch:[^\n]*", _body(page))
    if m:
        missing = m.group(0).strip()
        filled.append(missing)
    shot_form = forms.screenshot(page, app_dir, "apply_form_filled.png")

    if dry_run:
        return ApplyResult("dry_run", "interamt", url, [shot_form],
                           reason=f"filled={filled}")

    if not _click_action(page, "Online absenden", settle_ms=7000):
        return ApplyResult("form_unsupported", "interamt", url, [shot_form],
                           reason="кнопки «Online absenden» нема")
    # Проміжне питання «Möchten Sie Ihre Bewerbung jetzt absenden?»
    if "absenden?" in _body(page):
        _click_action(page, "ja", settle_ms=8000)

    shot_conf = forms.screenshot(page, app_dir, "apply_confirmation.png")
    body_after = _body(page)
    m_id = re.search(r"[?&]id=(\d+)", url)
    stellen_id = m_id.group(1) if m_id else ""
    listed = "Bewerbungen" in body_after and (not stellen_id or stellen_id in body_after)
    if listed:
        return ApplyResult(
            "submitted", "interamt", url, [shot_form, shot_conf],
            confirmation_text=f"у списку Bewerbungen (Stellen-ID {stellen_id or '?'})",
            reason=missing,
        )
    return ApplyResult(
        "failed", "interamt", url, [shot_form, shot_conf],
        reason="після absenden не бачу заявку в списку Bewerbungen — перевірити руками",
    )
