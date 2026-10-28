#!/usr/bin/env python3
"""Після рану агента: нові співбесіди, оферти й запити з трекера → оператору в Telegram.

    notify_run.py --base <sha до рану> --run morning|evening

Порівнює tracker.md до рану (git show <sha>:tracker.md) з поточним і шле один текст
про рядки, що стали actionable. Без Telegram у клієнта нічого не робить: тоді
оператор читає зведений лист. 28.10.2026 ранковий ран о 03:11 записав співбесіду
на 17:30 того ж дня, а оператор дізнався б о 20:40 зі зведення.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from bot import client as _client
from bot import notify as _notify
from bot import operator_report as rep


def rows_at(root: Path, sha: str) -> list:
    proc = subprocess.run(["git", "-C", str(root), "show", f"{sha}:tracker.md"],
                          capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        return []
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as f:
        f.write(proc.stdout)
    try:
        return rep.parse_rows(Path(f.name))
    finally:
        Path(f.name).unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", required=True, help="коміт даних до рану")
    ap.add_argument("--run", required=True, choices=("morning", "evening"))
    args = ap.parse_args(argv)
    c = _client.active()
    if not _notify.configured(c):
        print("[notify] telegram не налаштовано, пропускаю")
        return 0
    text = _notify.run_changes_text(rows_at(c.root, args.base), rep.parse_rows(c.tracker),
                                    args.run, c.telegram["lang"])
    if text is None:
        print("[notify] нових співбесід чи запитів нема")
        return 0
    _notify.notify(c, text)
    print(f"[notify] telegram: {text.splitlines()[0]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
