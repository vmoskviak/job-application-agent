#!/usr/bin/env python3
"""Headed-логін у персистентний Playwright-профіль (через Xvfb+VNC на LXC).

    DISPLAY=:99 .venv/bin/python scripts/relogin.py [url]
    DISPLAY=:99 .venv/bin/python scripts/relogin.py https://login.xing.com \\
        --wait-for xing.com --timeout 1200

Відкриває вікно браузера у віртуальному дисплеї; логінишся через VNC.
Без `--wait-for` — підтвердження по Enter у терміналі (як раніше).
З `--wait-for <домен>` скрипт сам чекає успішного логіну (можна запускати у
фоні й дивитись у лог), пише знімок куків і закриває контекст, щоб куки
гарантовано злилися на диск.

ВАЖЛИВО в VNC: ставити галочку «Eingeloggt bleiben» / «Angemeldet bleiben».
Без неї сайт видає session-кук, який зникає разом із процесом браузера, і
профіль лишається розлогіненим (XING, 24.07.2026).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from bot.scrapers.profile import PROFILE_DIR

from playwright.sync_api import sync_playwright

# Як дістатись до дисплея сервера: env JOBSEARCH_VNC_HINT (напр. vnc://host:5900)
VNC_HINT = __import__("os").environ.get("JOBSEARCH_VNC_HINT", "VNC сервера, див. docs/server_setup.md")

QUIT = Path("/tmp/relogin_quit")


def auth_cookies(ctx, host: str) -> set[str]:
    """Персистентні httpOnly-куки домену: саме такими бувають сесійні токени.

    Трекери майже завжди не httpOnly, тому цей фільтр відсіює шум. XING після
    логіну кладе `login` (httpOnly, +6 місяців) на www.xing.com і login.xing.com.
    """
    return {
        c["name"]
        for c in ctx.cookies()
        if host in c.get("domain", "")
        and c.get("httpOnly")
        and c.get("expires", -1) > 0
    }


def logged_in(page, host: str) -> tuple[bool, str]:
    """Чи сторінка виглядає як залогінена для цього хоста."""
    url = page.url
    if "/login" in url or "login." in url.split("//")[-1].split("/")[0]:
        return False, f"на сторінці логіну: {url[:80]}"
    if host not in url:
        return False, f"інший хост: {url[:80]}"
    try:
        body = page.inner_text("body")[:4000]
    except Exception as e:  # noqa: BLE001
        return False, f"body недоступний: {type(e).__name__}"
    # XING з 2026 звертається на «Du»: «Deine Jobs», «Dein Profil» (07.10.2026).
    markers = ("Abmelden", "Sign out", "Logout", "Mein Profil", "Meine Jobs", "My jobs",
               "Deine Jobs", "Dein Profil", "Mitteilungen", "Nachrichten")
    anon = ("Einloggen", "Anmelden", "Bei XING anmelden", "Sign in")
    if any(m in body for m in markers):
        return True, "знайдено маркер залогіненості"
    if any(a in body for a in anon):
        return False, "на сторінці ще анонімні маркери"
    return False, "маркерів не видно"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("url", nargs="?", default="https://www.linkedin.com/login")
    ap.add_argument("--wait-for", metavar="DOMAIN",
                    help="чекати логіну на цьому домені замість Enter (напр. xing.com)")
    ap.add_argument("--timeout", type=int, default=1200, help="секунд на логін")
    args = ap.parse_args()

    QUIT.unlink(missing_ok=True)
    p = sync_playwright().start()
    ctx = p.chromium.launch_persistent_context(
        user_data_dir=str(PROFILE_DIR),
        headless=False,
        viewport={"width": 1280, "height": 900},
        locale="de-DE",
        # без цього деякі сайти (LinkedIn) бачать автоматизацію ще до логіну
        args=["--disable-blink-features=AutomationControlled"],
    )
    page = ctx.new_page()
    page.goto(args.url)

    rc = 0
    if not args.wait_for:
        input(f"Відкрито {args.url}. Залогінься через VNC, потім Enter тут... ")
    else:
        host = args.wait_for
        print(f"Відкрито {args.url}. Логінься через VNC "
              f"({VNC_HINT}) — ГАЛОЧКА «Eingeloggt bleiben»!", flush=True)
        print(f"Чекаю логіну на {host}, до {args.timeout} с. "
              f"Скасувати: touch {QUIT}", flush=True)
        deadline = time.time() + args.timeout
        last = ""
        # Логін може відбутись в іншій вкладці — тоді page.url лишається на
        # сторінці логіну, і дивитись лише на сторінку недостатньо (25.07.2026).
        cookies_before = auth_cookies(ctx, host)
        while time.time() < deadline:
            if QUIT.exists():
                print(">>> скасовано файлом-сигналом", flush=True)
                rc = 3
                break
            ok, why = logged_in(page, host)
            fresh = auth_cookies(ctx, host) - cookies_before
            if not ok and fresh:
                ok, why = True, f"новий httpOnly-кук домену: {sorted(fresh)}"
            if ok and "xing" in host:
                # XING: лише httpOnly-кук `login` (+6 міс.) переживає закриття
                # браузера. Залогінений без нього = сесія згорить з процесом.
                names = {c["name"]: c for c in ctx.cookies() if "xing.com" in c.get("domain", "")}
                if "login" not in names or names["login"].get("expires", -1) <= 0:
                    why = ("залогінена, але кука `login` не персистентна: сесія згорить. "
                           "Вийди (Abmelden) і зайди ще раз із галочкою «Angemeldet bleiben»")
                    if why != last:
                        print(f"…{why}", flush=True)
                        last = why
                    ok = False
                    time.sleep(5)
                    continue
            if ok:
                print(f"LOGIN OK: {why} · url={page.url[:90]}", flush=True)
                cookies = [c["name"] for c in ctx.cookies() if host in c.get("domain", "")]
                persistent = [c["name"] for c in ctx.cookies()
                              if host in c.get("domain", "") and c.get("expires", -1) > 0]
                print("куків домену:", len(cookies), flush=True)
                print("персистентних:", len(persistent), flush=True)
                print(json.dumps(sorted(set(cookies) - set(persistent)),
                                 ensure_ascii=False), "← session-only (зникнуть!)", flush=True)
                break
            if why != last:
                print(f"…{why}", flush=True)
                last = why
            time.sleep(5)
        else:
            print("TIMEOUT: логіну не дочекався", flush=True)
            rc = 4

    ctx.close()
    p.stop()
    print("Профіль:", PROFILE_DIR, flush=True)
    return rc


if __name__ == "__main__":
    sys.exit(main())
