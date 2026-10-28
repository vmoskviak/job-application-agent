#!/usr/bin/env python3
"""After an agent run: new interviews, offers and requests from the tracker go to the operator in Telegram.

    notify_run.py --base <sha before the run> --run morning|evening

Compares tracker.md before the run (git show <sha>:tracker.md) with the current one and
sends a single text about the rows that became actionable. Does nothing when the client
has no Telegram: the operator then reads the summary mail. On 28.10.2026 the morning run
at 03:11 recorded an interview at 17:30 the same day, and the operator would only have
learned of it at 20:40 from the summary.
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
    ap.add_argument("--base", required=True, help="data commit before the run")
    ap.add_argument("--run", required=True, choices=("morning", "evening"))
    args = ap.parse_args(argv)
    c = _client.active()
    if not _notify.configured(c):
        print("[notify] telegram not configured, skipping")
        return 0
    text = _notify.run_changes_text(rows_at(c.root, args.base), rep.parse_rows(c.tracker),
                                    args.run, c.telegram["lang"])
    if text is None:
        print("[notify] no new interviews or requests")
        return 0
    _notify.notify(c, text)
    print(f"[notify] telegram: {text.splitlines()[0]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
