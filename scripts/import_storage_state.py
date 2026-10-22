#!/usr/bin/env python3
"""Імпорт браузерних сесій у профіль на сервері + перевірка логінів.

    python3 scripts/import_storage_state.py [var/run/storage_state.json]

Після імпорту відвідує LinkedIn/XING/StepStone і друкує статус логіна.
Якщо LOGIN REQUIRED — headed re-login за runbook у docs/14_server_setup.md.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from bot.scrapers.browser import get_context, profile_lock, shutdown_browser

from bot import client as _client  # noqa: E402

DEFAULT_STATE = _client.active().run_dir / "storage_state.json"

_CHECKS = [
    ("linkedin", "https://www.linkedin.com/feed/", ["feed-identity", "global-nav"]),
    # XING (виправлено 25.07): /jobs віддається і анонімно, тому розлогінена
    # сесія показувала "live". /jobs/find під анонімом редіректить на
    # login.xing.com — це єдиний надійний сигнал.
    ("xing", "https://www.xing.com/jobs/find", ["profile", "abmelden", "logout"]),
    # StepStone (07.10.2026): головна показує «Profil» і анонімам → хибний live.
    # /candidate/notification-settings під анонімом редіректить на /candidate/login.
    ("stepstone", "https://www.stepstone.de/candidate/notification-settings",
     ["benachrichtigung", "job-e-mail", "abmelden"]),
    # Indeed (25.07): вхід через Google One Tap, панель алертів — subscriptions.indeed.com.
    # Розлогінений myjobs.indeed.com/saved редіректить на secure.indeed.com/auth.
    ("indeed", "https://myjobs.indeed.com/saved", ["meine jobs", "abmelden"]),
]


def main() -> int:
    state_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_STATE
    if not state_path.exists():
        print(f"Немає файла: {state_path}")
        return 1

    state = json.loads(state_path.read_text(encoding="utf-8"))
    cookies = state.get("cookies", [])
    print(f"Імпортую {len(cookies)} cookies...")

    exit_code = 0
    with profile_lock():
        ctx = get_context()
        ctx.add_cookies(cookies)

        for name, url, markers in _CHECKS:
            page = ctx.new_page()
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(2500)
                content = (page.content() + page.url).lower()
                login_wall = any(
                    m in content for m in ("login", "anmelden", "signin", "sign-in")
                ) and not any(m in content for m in markers)
                logged_in = any(m in content for m in markers) and not login_wall
                print(f"  {'✅' if logged_in else '❌'} {name}: "
                      f"{'logged in' if logged_in else 'LOGIN REQUIRED'}")
                if not logged_in:
                    exit_code = 2
            except Exception as e:
                print(f"  ⚠️  {name}: перевірка не вдалась ({e})")
                exit_code = 2
            finally:
                page.close()
    shutdown_browser()

    if exit_code:
        print("\n→ Re-login runbook: docs/14_server_setup.md")
    else:
        print("\nВсе ок. Видаліть storage_state.json з обох машин.")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
