#!/usr/bin/env python3
"""Знімок живої сторінки подачі в профілі клієнта (фікстури для адаптерів 2d).

    DISPLAY=:99 JOBSEARCH_CLIENT=<slug> .venv/bin/python \\
        scripts/capture_apply_pages.py <url> --out DIR [--steps] [--click-apply] [--click CSS]

Відкриває URL у видимому браузері профілю клієнта (`get_context(headed=True)`)
під `profile_lock`, чекає `settle_body` і пише в DIR: `page.html`, `body.txt`,
`fields.json` (`FIELD_JS`), `shot.png`, `url.txt`, `frame_<n>.html` на кожен iframe.

`--steps` (LinkedIn Easy Apply): клікає кнопку Easy Apply, на кожному кроці
модалки пише `step_<n>_fields.json` (FIELD_JS у межах діалогу), `step_<n>.html`
(outerHTML діалогу), `step_<n>.json` (заголовок, кнопки, помилки) і
`step_<n>.png`, клікає лише Next / Review (або «Weiter» / «Überprüfen»), поки
така кнопка є і крок змінюється. «Submit application» не клікає НІКОЛИ: цикл
зупиняється, щойно вона з'являється. Закриває модалку через Dismiss і Discard.

`--click CSS`: клікає вкладку чи фільтр лише для читання (трекер LinkedIn
«Applied») і знімає результат у DIR/clicked/.

`--click-apply` (GULP): після знімка сторінки проєкту клікає «Apply now» /
«Jetzt bewerben» і знімає форму `/bewerbung-mit-profil` у DIR/apply_form/.
Відправку форми не клікає.

Сторінку-проміжок freelance.de (`promotion/postlogin.php`) проходить за
посиланням «Weiter zur vorherigen Seite». Нічого не заповнює, нічого не
завантажує, не логіниться. Операторський інструмент, без юніт-тесту.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ENGINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE))

from bot.apply.ats_fields import FIELD_JS
from bot.scrapers.browser import get_context, profile_lock, settle_body, shutdown_browser

SETTLE_MS = 20000
# LinkedIn 10.2026: Easy Apply це нативний <dialog open>, не div[role=dialog].
DIALOG = "dialog[open], div[role=dialog]"
# Кнопки, які цикл кроків має право клікати; усе інше (Submit) лише читається.
STEP_BUTTONS = re.compile(r"^(Next|Review|Continue|Weiter|Überprüfen|Bewerbung prüfen)$", re.I)
SUBMIT = re.compile(r"submit|absenden|senden|bewerbung verschicken", re.I)
EASY_APPLY = re.compile(r"^\s*(Easy Apply|Einfach bewerben)", re.I)
APPLY_NOW = re.compile(r"^\s*(Apply now|Jetzt bewerben)\s*$", re.I)
INTERSTITIAL = "Weiter zur vorherigen Seite"
MAX_STEPS = 8

# FIELD_JS читає весь документ; для модалки рахуємо лише поля діалогу.
_DOC_FIELDS = 'document.querySelectorAll("input,select,textarea")'
DIALOG_FIELD_JS = FIELD_JS.replace(
    _DOC_FIELDS,
    '(document.querySelector("dialog[open], div[role=dialog]") || document).querySelectorAll("input,select,textarea")',
)
assert DIALOG_FIELD_JS != FIELD_JS, "FIELD_JS changed: update the dialog scope"

DIALOG_INFO_JS = r"""
(() => {
  const d = document.querySelector("dialog[open], div[role=dialog]");
  if (!d) return null;
  const t = (n) => (n ? (n.innerText || n.textContent || "").replace(/\s+/g, " ").trim() : "");
  const heads = Array.from(d.querySelectorAll("h1,h2,h3")).map(t).filter(Boolean);
  const buttons = Array.from(d.querySelectorAll("button")).map((b) => ({
    text: t(b), aria: b.getAttribute("aria-label") || "", view: b.getAttribute("data-view-name") || "", disabled: b.disabled,
  }));
  const errors = Array.from(d.querySelectorAll(
    "[role=alert], .artdeco-inline-feedback--error, [data-test-form-element-error-messages]"
  )).map(t).filter(Boolean);
  const progress = d.querySelector("progress, [role=progressbar]");
  return {
    heads, buttons, errors,
    progress: progress ? (progress.getAttribute("value") || progress.getAttribute("aria-valuenow") || "") : "",
    aria_label: d.getAttribute("aria-labelledby") ? t(document.getElementById(d.getAttribute("aria-labelledby"))) : (d.getAttribute("aria-label") || ""),
  };
})()
"""


# Де шукати модалку, якщо div[role=dialog] не знайшовся: <dialog>, aria-modal,
# відкриті shadow root (LinkedIn interop-outlet).
PROBE_JS = r"""
(() => {
  const hits = [];
  const walk = (root, path) => {
    root.querySelectorAll("dialog, [role=dialog], [aria-modal=true], [data-test-modal], #interop-outlet").forEach((n) => {
      hits.push({path, tag: n.tagName, id: n.id, role: n.getAttribute("role"), cls: (n.className || "").toString().slice(0, 120),
                 text: (n.innerText || "").replace(/\s+/g, " ").slice(0, 200), shadow: !!n.shadowRoot});
    });
    root.querySelectorAll("*").forEach((n) => { if (n.shadowRoot) walk(n.shadowRoot, path + ">" + n.tagName + "#" + n.id); });
  };
  walk(document, "doc");
  return hits;
})()
"""


def snapshot(page, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / "url.txt").write_text(page.url + "\n")
    (out / "page.html").write_text(page.content())
    (out / "body.txt").write_text(page.inner_text("body"))
    (out / "fields.json").write_text(json.dumps(page.evaluate(FIELD_JS), ensure_ascii=False, indent=1))
    # Вкладені iframe (поле CV у GULP живе в iframe Salesforce): frame_<n>.html.
    for n, frame in enumerate(page.frames[1:], 1):
        try:
            (out / f"frame_{n}.html").write_text(f"<!-- {frame.url} -->\n" + frame.content())
        except Exception as e:
            print("frame", n, frame.url, "unreadable:", e)
    try:
        page.screenshot(path=str(out / "shot.png"), full_page=True)
    except Exception:
        page.screenshot(path=str(out / "shot.png"))
    print("captured", page.url, "->", out)


def open_settled(page, url: str) -> None:
    page.goto(url, wait_until="domcontentloaded", timeout=45000)
    page.wait_for_timeout(1500)
    settle_body(page, SETTLE_MS, page.inner_text("body"))
    link = page.get_by_role("link", name=INTERSTITIAL)
    if "promotion/postlogin" in page.url and link.count():
        print("interstitial:", page.url)
        link.first.click()
        page.wait_for_load_state("domcontentloaded")
        page.wait_for_timeout(1500)
        settle_body(page, SETTLE_MS, page.inner_text("body"))


def step_button(page):
    """Перша кнопка діалогу з дозволеним текстом, ніколи не Submit."""
    for b in page.locator(DIALOG).first.locator("button").all():
        text = " ".join((b.inner_text() or "").split())
        if SUBMIT.search(text):
            continue
        if STEP_BUTTONS.match(text) and b.is_visible() and b.is_enabled():
            return b, text
    return None, ""


def easy_apply_steps(page, out: Path) -> None:
    btn = page.get_by_role("button", name=EASY_APPLY)
    if not btn.count():
        btn = page.get_by_role("link", name=EASY_APPLY)
    if not btn.count():
        print("NO Easy Apply button on", page.url)
        return
    btn.first.click()
    try:
        page.wait_for_selector(DIALOG, timeout=15000)
    except Exception:
        # Модалка не з'явилась у DOM: знімок того, що є, для розбору.
        print("no dialog after click; url:", page.url)
        print("probe:", json.dumps(page.evaluate(PROBE_JS), ensure_ascii=False)[:3000])
        snapshot(page, out / "after_click")
        return
    page.wait_for_timeout(2000)
    prev = None
    for n in range(1, MAX_STEPS + 1):
        info = page.evaluate(DIALOG_INFO_JS) or {}
        fields = page.evaluate(DIALOG_FIELD_JS)
        (out / f"step_{n}.json").write_text(json.dumps(info, ensure_ascii=False, indent=1))
        (out / f"step_{n}_fields.json").write_text(json.dumps(fields, ensure_ascii=False, indent=1))
        (out / f"step_{n}.html").write_text(page.locator(DIALOG).first.evaluate("d => d.outerHTML"))
        page.screenshot(path=str(out / f"step_{n}.png"))
        texts = [b["text"] for b in info.get("buttons", [])]
        print(f"step {n}: heads={info.get('heads')} buttons={texts} errors={info.get('errors')}")
        if any(SUBMIT.search(t) for t in texts):
            print("submit button reached: stop (never clicked)")
            break
        key = (tuple(info.get("heads", [])), json.dumps(fields, sort_keys=True), tuple(info.get("errors", [])))
        if key == prev:
            print("step did not change (validation?): stop")
            break
        prev = key
        b, text = step_button(page)
        if b is None:
            print("no Next/Review button: stop")
            break
        print("click", text)
        b.click()
        page.wait_for_timeout(2500)
    close_modal(page, out)


def close_modal(page, out: Path) -> None:
    dismiss = page.locator("dialog[open] button[aria-label='Dismiss'], div[role=dialog] button[aria-label='Dismiss'], dialog[open] button[aria-label='Schließen']")
    if not dismiss.count():
        print("no Dismiss button found")
        return
    dismiss.first.click()
    page.wait_for_timeout(1500)
    info = page.evaluate(DIALOG_INFO_JS)
    (out / "discard_dialog.json").write_text(json.dumps(info, ensure_ascii=False, indent=1))
    if page.locator(DIALOG).count():
        (out / "discard_dialog.html").write_text(page.locator(DIALOG).first.evaluate("d => d.outerHTML"))
    page.screenshot(path=str(out / "discard_dialog.png"))
    discard = page.get_by_role("button", name=re.compile(r"^\s*(Discard|Verwerfen)\s*$", re.I))
    if discard.count():
        discard.first.click()
        page.wait_for_timeout(1500)
        print("modal closed with Discard")
    else:
        print("no Discard prompt:", info)


def click_apply(page, out: Path) -> None:
    target = page.get_by_role("link", name=APPLY_NOW)
    if not target.count():
        target = page.get_by_role("button", name=APPLY_NOW)
    if not target.count():
        print("NO Apply now / Jetzt bewerben on", page.url)
        return
    print("click", target.first.inner_text().strip())
    target.first.click()
    try:
        page.wait_for_url(re.compile(r"bewerbung-mit-profil"), timeout=20000)
    except Exception:
        print("url after click:", page.url)
    page.wait_for_timeout(1500)
    settle_body(page, SETTLE_MS, page.inner_text("body"))
    snapshot(page, out / "apply_form")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("url")
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--steps", action="store_true", help="LinkedIn Easy Apply: walk the modal, never submit")
    ap.add_argument("--click-apply", action="store_true", help="GULP: open the application form, never submit")
    ap.add_argument("--click", metavar="CSS", help="click a read-only tab or filter, then capture DIR/clicked/")
    args = ap.parse_args(argv)
    with profile_lock(timeout_sec=5):
        try:
            page = get_context(headed=True).new_page()
            open_settled(page, args.url)
            snapshot(page, args.out)
            if args.steps:
                easy_apply_steps(page, args.out)
            if args.click_apply:
                click_apply(page, args.out)
            if args.click:
                page.locator(args.click).first.click()
                page.wait_for_timeout(3000)
                settle_body(page, SETTLE_MS, page.inner_text("body"))
                snapshot(page, args.out / "clicked")
        finally:
            shutdown_browser()
    return 0


if __name__ == "__main__":
    sys.exit(main())
