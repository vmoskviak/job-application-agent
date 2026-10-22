#!/usr/bin/env python3
"""Golden URL acceptance runner — runs intake_with_meta on fixture URLs."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

FIXTURES_PATH = Path(__file__).parent / "fixtures" / "test_urls.json"
MIN_LIVE_RATE = 0.80


def _short_url(url: str, max_len: int = 48) -> str:
    if len(url) <= max_len:
        return url
    return url[: max_len - 3] + "..."


def run_acceptance(
    fixtures_path: Path = FIXTURES_PATH,
    use_playwright: bool = True,
    verbose: bool = True,
) -> int:
    fixtures = json.loads(fixtures_path.read_text(encoding="utf-8"))
    from bot.generators.intake import intake_with_meta
    from bot.scrapers.browser import shutdown_browser

    rows: list[tuple[str, str, str, str, str]] = []
    failures = 0
    live_expected = 0
    live_matched = 0

    try:
        for item in fixtures:
            url = item["url"]
            expected = item["expect_liveness"]
            note = item.get("note", "")

            result = intake_with_meta(url, use_playwright=use_playwright)
            actual = result.liveness
            ok = actual == expected
            status = "PASS" if ok else "FAIL"
            if not ok:
                failures += 1
            if expected == "live":
                live_expected += 1
                if actual == "live":
                    live_matched += 1

            rows.append((status, _short_url(url), expected, actual, note))
    finally:
        shutdown_browser()

    if verbose:
        col_w = (6, 50, 10, 10, 24)
        header = ("RESULT", "URL", "EXPECTED", "ACTUAL", "NOTE")
        print(f"{header[0]:<{col_w[0]}}  {header[1]:<{col_w[1]}}  {header[2]:<{col_w[2]}}  {header[3]:<{col_w[3]}}  {header[4]}")
        print("-" * (sum(col_w) + 8))
        for status, url, expected, actual, note in rows:
            print(
                f"{status:<{col_w[0]}}  {url:<{col_w[1]}}  {expected:<{col_w[2]}}  {actual:<{col_w[3]}}  {note[:col_w[4]]}"
            )

    live_rate = (live_matched / live_expected) if live_expected else 1.0
    if verbose:
        print()
        print(f"Summary: {len(fixtures) - failures}/{len(fixtures)} liveness match")
        if live_expected:
            print(
                f"Live-expected: {live_matched}/{live_expected} "
                f"({live_rate * 100:.0f}%, min {MIN_LIVE_RATE * 100:.0f}%)"
            )

    exit_code = 0
    if failures:
        exit_code = 1
    elif live_expected and live_rate < MIN_LIVE_RATE:
        if verbose:
            print(f"FAIL: live rate {live_rate:.0%} < {MIN_LIVE_RATE:.0%}", file=sys.stderr)
        exit_code = 1

    return exit_code


def main() -> None:
    parser = argparse.ArgumentParser(description="Golden URL intake acceptance runner")
    parser.add_argument(
        "--fixtures",
        type=Path,
        default=FIXTURES_PATH,
        help="Path to test_urls.json",
    )
    parser.add_argument(
        "--no-playwright",
        action="store_true",
        help="httpx only (skip Playwright fallback)",
    )
    args = parser.parse_args()
    sys.exit(run_acceptance(fixtures_path=args.fixtures, use_playwright=not args.no_playwright))


if __name__ == "__main__":
    main()
