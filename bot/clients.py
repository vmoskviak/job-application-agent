"""`python -m bot.clients list [--status active] [--slugs]` — огляд клієнтів."""

from __future__ import annotations

import sys

from bot.client import STATUSES, list_clients


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Клієнти рушія")
    sub = parser.add_subparsers(dest="cmd", required=True)
    ls = sub.add_parser("list")
    ls.add_argument("--status", choices=STATUSES)
    ls.add_argument("--slugs", action="store_true", help="лише slug-и, по одному на рядок")
    args = parser.parse_args(argv)

    for c in list_clients():
        if args.status and c.status != args.status:
            continue
        print(c.slug if args.slugs else f"{c.slug:<20} {c.status:<8} {c.full_name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
