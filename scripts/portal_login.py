#!/usr/bin/env python3
"""Ручна перевірка автологіну в портал (сама логіка в bot/scrapers/portal_login.py).

    js scripts/portal_login.py xing              перевірити сесію, за потреби залогінити
    js scripts/portal_login.py xing --check      тільки стан, без логіну

Запускає окремий браузер на профілі клієнта, тому результат не переживе цей
процес: XING-сесія сесійна. Робочі виклики йдуть з того самого контексту, що
подає заявки (bot.apply.xing) і перевіряє сесії (session_health).
Exit: 0 live/залогінено · 2 логін не вдався · 3 немає жодного способу.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.scrapers import portal_login as pl  # noqa: E402


def _display_available() -> bool:
    d = os.environ.get("DISPLAY", "")
    return d.startswith(":") and Path(f"/tmp/.X11-unix/X{d[1:].split('.')[0]}").exists()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("portal", choices=sorted(pl.PORTALS))
    ap.add_argument("--check", action="store_true", help="тільки стан сесії")
    args = ap.parse_args(argv)

    from playwright.sync_api import sync_playwright
    from bot.scrapers.profile import PROFILE_DIR

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=not _display_available(),
            viewport={"width": 1280, "height": 900},
            locale="de-DE",
            args=["--disable-blink-features=AutomationControlled"],
        )
        try:
            if args.check:
                live = pl.is_live(ctx, pl.PORTALS[args.portal])
                print(("LIVE" if live else "EXPIRED") + f" {args.portal}")
                return 0 if live else 2
            status, why = pl.ensure_login(ctx, args.portal)
            print(f"{status.upper()} {args.portal}: {why}")
            return {"live": 0, "logged_in": 0, "failed": 2}.get(status, 3)
        finally:
            ctx.close()


if __name__ == "__main__":
    sys.exit(main())
