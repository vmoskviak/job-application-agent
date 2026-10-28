#!/usr/bin/env python3
"""Job-алерти на порталах із `search.yaml → alerts` активного клієнта.

    js scripts/portal_alerts.py --dry-run                 # лише URL-и, без браузера
    js scripts/portal_alerts.py --portal stepstone        # один портал
    js scripts/portal_alerts.py                           # stepstone, linkedin, xing
    js scripts/portal_alerts.py --list                    # сторінки керування алертами

Indeed тут навмисно немає: він блокує автоматизацію, алерти на Indeed ставляться
з Chrome-профілю клієнта на Mac (memory: portal-alerts-mechanics).

search.yaml:
    alerts:
    - {query: Scrum Master, location: Düsseldorf, radius: 15}
    - {query: Scrum Master, remote: true}            # без міста, лише віддалено
    - {query: Dolmetscher Ukrainisch, location: Düsseldorf, radius: 15, portals: [linkedin]}

Кожен запис ставиться на всіх трьох порталах, якщо не звужено `portals`.
Механіка (станом на 07.10.2026): StepStone — кнопка «Schickt mir neue
Jobs per E-Mail.» відкриває модал, у ньому `[data-testid=ja-submit-button]`;
LinkedIn — перемикач `.artdeco-toggle__text` («Benachrichtigung: Ein»); XING —
`label:has-text("Benachrichtigungen für neue Jobs")`. Скріншоти після кожного
кроку в `var/run/alerts_shots/`.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import quote, quote_plus

sys.path.insert(0, str(Path(__file__).parent.parent))

from bot import client as _client  # noqa: E402

PORTALS = ("stepstone", "linkedin", "xing")

MANAGE_URLS = {
    "stepstone": "https://www.stepstone.de/candidate/notification-settings",
    "linkedin": "https://www.linkedin.com/jobs/jam/",
    "xing": "https://www.xing.com/jobs/my-jobs?active_view=SEARCH_ALERTS",
}

# LinkedIn міряє відстань милями; 10 миль ≈ 16 км
_KM_TO_MILES = 0.621371


def _slug(text: str) -> str:
    """StepStone-слаг: «Scrum Master» → scrum-master, «Düsseldorf» → düsseldorf."""
    return re.sub(r"[^\w]+", "-", text.strip().lower(), flags=re.UNICODE).strip("-")


def alert_url(portal: str, spec: dict) -> str:
    query = str(spec["query"]).strip()
    remote = bool(spec.get("remote"))
    location = str(spec.get("location") or "").strip()
    radius = int(spec.get("radius") or 15)
    if portal == "stepstone":
        if remote:
            # wfh=1 — фасет «Homeoffice» StepStone; ключове слово страхує, якщо
            # параметр перестане працювати
            return f"https://www.stepstone.de/jobs/{_slug(query + ' remote')}?wfh=1"
        return f"https://www.stepstone.de/jobs/{_slug(query)}/in-{_slug(location)}?radius={radius}"
    if portal == "linkedin":
        base = f"https://www.linkedin.com/jobs/search/?keywords={quote(query)}"
        if remote:
            return f"{base}&location={quote('Deutschland')}&f_WT=2"
        miles = max(5, round(radius * _KM_TO_MILES))
        return f"{base}&location={quote(location)}&distance={miles}"
    if portal == "xing":
        if remote:
            return f"https://www.xing.com/jobs/search?keywords={quote_plus(query + ' remote')}"
        return (f"https://www.xing.com/jobs/search?keywords={quote_plus(query)}"
                f"&location={quote_plus(location)}&radius={radius}")
    raise ValueError(portal)


def plan(specs: list[dict], portals: tuple[str, ...]) -> list[tuple[str, dict, str]]:
    out = []
    for spec in specs:
        if not spec.get("remote") and not spec.get("location"):
            raise SystemExit(f"alerts: запис без location і без remote: {spec}")
        for portal in portals:
            if spec.get("portals") and portal not in spec["portals"]:
                continue
            out.append((portal, spec, alert_url(portal, spec)))
    return out


def _shot(page, shots: Path, name: str) -> None:
    try:
        page.screenshot(path=str(shots / f"{name}.png"), full_page=False)
    except Exception:  # noqa: BLE001 — скріншот не має валити алерт
        pass


def _text(page) -> str:
    try:
        return page.inner_text("body")
    except Exception:  # noqa: BLE001
        return ""


def create_stepstone(page, shots: Path, name: str) -> str:
    btn = page.locator('button:has-text("Schickt mir neue Jobs per E-Mail.")').first
    if btn.count() == 0:
        if re.search(r"Job-?Alarm|Benachrichtigung aktiv|bereits", _text(page), re.I):
            return "exists"
        return "no_button"
    btn.click()
    page.wait_for_timeout(1500)
    _shot(page, shots, f"{name}_modal")
    submit = page.locator("[data-testid=ja-submit-button]").first
    if submit.count() == 0:
        return "no_submit"
    submit.click()
    page.wait_for_timeout(2500)
    _shot(page, shots, f"{name}_done")
    body = _text(page)
    # «Geschafft! Du erhältst nun Jobs per E-Mail für diese Suche.» (14.10.2026)
    if re.search(r"Geschafft|Jobs per E-Mail für diese Suche|erfolgreich|aktiviert|Job-?Alarm", body, re.I):
        return "created"
    return "unclear"


def create_linkedin(page, shots: Path, name: str) -> str:
    toggle = page.locator(".artdeco-toggle__text").first
    if toggle.count() == 0:
        return "no_toggle"
    # «Benachrichtigung: Ein» (DE) або «Alert on» (EN-інтерфейс, 14.10.2026)
    on = re.compile(r"\b(Ein|On|an)\s*$", re.I)
    label = (toggle.inner_text() or "").strip()
    if on.search(label):
        return "exists"
    toggle.click()
    page.wait_for_timeout(2500)
    _shot(page, shots, f"{name}_done")
    label = (page.locator(".artdeco-toggle__text").first.inner_text() or "").strip()
    return "created" if on.search(label) else f"unclear:{label}"


def create_xing(page, shots: Path, name: str) -> str:
    body = _text(page)
    if "Benachrichtigungen für diese Job-Suche aktiviert" in body:
        return "exists"
    lab = page.locator('label:has-text("Benachrichtigungen für neue Jobs")').first
    if lab.count() == 0:
        return "no_label"
    lab.click()
    page.wait_for_timeout(2500)
    _shot(page, shots, f"{name}_done")
    body = _text(page)
    return "created" if "aktiviert" in body else "unclear"


def create_stepstone_settings(page, shots: Path, name: str, spec: dict) -> str:
    """Через діалог «Erstellen Sie eine Job-E-Mail» на сторінці налаштувань:
    сторінку пошуку StepStone після першого алерту блокує антибот
    (ERR_HTTP2_PROTOCOL_ERROR), сторінка налаштувань відкривається далі."""
    title = str(spec["query"]) + (" Remote" if spec.get("remote") else "")
    location = str(spec.get("location") or "Deutschland")
    body = _text(page)
    if title in body:
        return "exists"
    page.locator("[data-testid=ja-create-new-job-alert-btn]").first.click()
    page.wait_for_timeout(1500)
    page.get_by_placeholder("z. B. Technikerin oder Designer").fill(title)
    page.wait_for_timeout(800)
    loc = page.get_by_placeholder("Stadt oder Postleitzahl")
    loc.fill(location)
    page.wait_for_timeout(1500)
    # автодоповнення міста: перший пункт списку, інакше Tab
    option = page.locator('[role="option"]').first
    if option.count():
        option.click()
    else:
        loc.press("Tab")
    try:
        page.locator("select").last.select_option(label="Täglich")
    except Exception:  # noqa: BLE001 — частота не критична
        pass
    _shot(page, shots, f"{name}_form")
    page.locator('button:has-text("Erstellen Sie eine Job-E-Mail")').last.click()
    page.wait_for_timeout(3000)
    _shot(page, shots, f"{name}_done")
    body = _text(page)
    # тост «Job-E-Mail geändert.»; у згорнутому списку нового запису не видно
    if title in body or re.search(r"Job-E-Mail (geändert|erstellt|gespeichert)", body):
        return "created"
    return "unclear"


CREATORS = {"stepstone": create_stepstone, "linkedin": create_linkedin, "xing": create_xing}


def run(items: list[tuple[str, dict, str]], shots: Path, pause: float = 2.0) -> list[dict]:
    from bot.scrapers.browser import get_context, profile_lock, shutdown_browser
    from bot.scrapers.portal_login import PORTALS as LOGIN_PORTALS, ensure_login, _dismiss_consent

    results: list[dict] = []
    with profile_lock():
        ctx = get_context()
        try:
            page = ctx.new_page()
            for n, (portal, spec, url) in enumerate(items, 1):
                row = {"portal": portal, "query": spec["query"],
                       "where": "remote" if spec.get("remote") else spec.get("location"), "url": url}
                # StepStone після створення алерту блокує наступні переходи в тому
                # самому браузері (ERR_HTTP2_PROTOCOL_ERROR, 14.10.2026, і з сервера,
                # і з Mac): кожен StepStone-алерт у свіжому браузері
                if portal == "stepstone" and n > 1:
                    shutdown_browser()
                    ctx = get_context()
                    page = ctx.new_page()
                # LinkedIn у ensure_login немає: сесія живе в профілі (session_health)
                status, detail = ensure_login(ctx, portal) if portal in LOGIN_PORTALS else ("live", "профіль")
                if status not in ("live", "logged_in"):
                    row["result"] = f"login:{status}:{detail[:80]}"
                    results.append(row)
                    print(json.dumps(row, ensure_ascii=False))
                    continue
                name = f"{n:02d}_{portal}_{_slug(spec['query'])[:30]}"
                try:
                    target = MANAGE_URLS["stepstone"] if portal == "stepstone" else url
                    page.goto(target, wait_until="domcontentloaded", timeout=45000)
                    page.wait_for_timeout(3500)
                    _dismiss_consent(page)
                    _shot(page, shots, f"{name}_page")
                    if portal == "stepstone":
                        row["result"] = create_stepstone_settings(page, shots, name, spec)
                    else:
                        row["result"] = CREATORS[portal](page, shots, name)
                except Exception as e:  # noqa: BLE001 — один алерт не валить решту
                    row["result"] = f"error:{type(e).__name__}:{str(e)[:100]}"
                results.append(row)
                print(json.dumps(row, ensure_ascii=False))
                # StepStone після кількох швидких переходів віддає
                # net::ERR_HTTP2_PROTOCOL_ERROR (антибот, 14.10.2026): пауза між алертами
                time.sleep(pause)
        finally:
            shutdown_browser()
    return results


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="лише URL-и, без браузера")
    ap.add_argument("--portal", choices=PORTALS, action="append", help="обмежити порталами")
    ap.add_argument("--list", action="store_true", help="показати сторінки керування алертами")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--pause", type=float, default=2.0, help="секунд між алертами (StepStone: 15)")
    ap.add_argument("--start", type=int, default=1, help="почати з N-го алерту плану (повтор після збою)")
    args = ap.parse_args(argv)

    c = _client.active()
    if args.list:
        for portal, url in MANAGE_URLS.items():
            print(f"{portal}: {url}")
        return 0
    specs = list(c.search.get("alerts") or [])
    if not specs:
        print("search.yaml: alerts порожній — нема що ставити", file=sys.stderr)
        return 2
    portals = tuple(args.portal) if args.portal else PORTALS
    items = plan(specs, portals)
    if args.dry_run:
        for portal, spec, url in items:
            print(f"{portal:9} {spec['query']!r:32} {('remote' if spec.get('remote') else spec.get('location')):14} {url}")
        print(f"{len(items)} алертів, {len(specs)} записів, портали: {', '.join(portals)}")
        return 0
    shots = c.run_dir / "alerts_shots"
    shots.mkdir(parents=True, exist_ok=True)
    results = run(items[args.start - 1:], shots, pause=args.pause)
    bad = [r for r in results if not r["result"].startswith(("created", "exists"))]
    if args.json:
        print(json.dumps({"results": results, "failed": len(bad)}, ensure_ascii=False))
    print(f"готово: {len(results) - len(bad)} ок, {len(bad)} проблемних; скріншоти в {shots}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
