#!/usr/bin/env python3
"""Нове для повного звіту кандидатці (report_mode: candidate).

    js scripts/candidate_report.py --json            що кандидатка ще не бачила
    js scripts/candidate_report.py --mark-reported   позначити все показаним
                                                     (після відправки звіту або
                                                     для старту без старих подій)
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot import candidate_report as cr  # noqa: E402
from bot import client as _client  # noqa: E402
from bot import operator_report as rep  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--json", action="store_true", help="нові події й загальний стан")
    g.add_argument("--mark-reported", action="store_true", help="позначити всі події показаними")
    ap.add_argument("--day", default=date.today().isoformat())
    args = ap.parse_args(argv)

    c = _client.active()
    rows = rep.parse_rows(c.tracker)
    if args.mark_reported:
        print(f"[candidate_report] позначено показаними: {cr.mark_reported(rows, c.var, args.day)} нових подій")
        return 0
    print(json.dumps(cr.digest(rows, c.var, args.day), ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
