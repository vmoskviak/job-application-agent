#!/usr/bin/env python3
"""Експорт браузерних сесій (cookies) з Mac-профілю для переносу на сервер.

macOS Chromium шифрує cookies через Keychain — сирий копі профілю на Linux
не працює. Цей скрипт відкриває персистентний профіль і зливає розшифрований
storage_state у JSON.

    python3 scripts/export_storage_state.py            # → var/run/storage_state.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from bot.scrapers.browser import get_context, profile_lock, shutdown_browser

from bot import client as _client  # noqa: E402

OUT = _client.active().run_dir / "storage_state.json"


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with profile_lock():
        ctx = get_context()
        state = ctx.storage_state()
    shutdown_browser()

    OUT.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    domains = sorted({c.get("domain", "") for c in state.get("cookies", [])})
    print(f"OK: {OUT}")
    print(f"cookies: {len(state.get('cookies', []))}, домени: {len(domains)}")
    for key in ("linkedin", "xing", "stepstone"):
        found = any(key in d for d in domains)
        print(f"  {'✅' if found else '⚠️ '} {key}: {'є' if found else 'НЕМАЄ'}")
    print("\nПеренос: scp var/run/storage_state.json root@<server>:<clients>/<slug>/var/run/")
    print("Потім на LXC: python3 scripts/import_storage_state.py")
    print("⚠️ Файл містить живі сесії — видалити після імпорту з обох машин!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
