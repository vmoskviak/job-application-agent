"""CLI orchestrator for portal auto-apply.

Використання:
    python -m bot.apply.runner --app-dir applications/2026-07-08/Foo_Bar \
        --url https://www.stepstone.de/... [--dry-run]
    python -m bot.apply.runner --batch var/run/apply_queue.json [--dry-run]

Формат batch-файла: [{"app_dir": "...", "url": "...", "portal": "stepstone"}]
(portal опційний — виводиться з URL).

Exit codes: 0 = ok (всі результати в summary), 2 = помилка вводу,
3 = спрацював runaway guard (нічого не подано).

Tracker НЕ редагує — статуси оновлює morning-агент за summary JSON
(stdout, останній рядок).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from bot.apply import interamt, oracle_cx, stepstone, xing
from bot.apply.models import ApplyResult, already_submitted
from bot.filters.rules import MAX_SUBMISSIONS_PER_RUN
from bot.generators.cv_engine import CV_FILE_STEM

_PORTAL_MODULES = {"stepstone": stepstone, "xing": xing,
                   "oracle_cx": oracle_cx,
                   "interamt": interamt}


def portal_from_url(url: str) -> str | None:
    u = (url or "").lower()
    if "stepstone.de" in u:
        return "stepstone"
    if "xing.com" in u:
        return "xing"
    # Oracle CX Recruiting (корпоративні портали): <host>/<lang>/sites/<SITE>/job/<id>
    if re.search(r"/sites/[^/]+/job/\d+", u):
        return "oracle_cx"
    if "interamt.de" in u:
        return "interamt"
    return None


def default_attachments(app_dir: Path) -> list[Path]:
    to_send = app_dir / "to_send"
    return [
        to_send / f"{CV_FILE_STEM}.pdf",
        to_send / "01_Anschreiben.pdf",
    ]


def salary_from_package(app_dir: Path) -> str | None:
    """Зарплата пакета з його generate.py (TailoredData(... salary='46000')).

    Черга apply_queue несе лише app_dir і url, а дефолт профілю для
    Assistenz-пакета зависокий (14.10.2026: 55000 замість 46000 у листі).
    """
    try:
        text = (app_dir / "generate.py").read_text(encoding="utf-8")
    except OSError:
        return None
    m = re.search(r"\bsalary='(\d+)'", text)
    return m.group(1) if m else None


def guard_limit() -> int:
    """APPLY_GUARD_LIMIT env перекриває дефолт (trial-режим = 10)."""
    try:
        return int(os.environ.get("APPLY_GUARD_LIMIT", MAX_SUBMISSIONS_PER_RUN))
    except ValueError:
        return MAX_SUBMISSIONS_PER_RUN


def apply_one(item: dict, dry_run: bool) -> ApplyResult:
    app_dir = Path(item["app_dir"])
    url = item["url"]
    portal = item.get("portal") or portal_from_url(url)

    if portal not in _PORTAL_MODULES:
        return ApplyResult("skipped", portal or "unknown", url,
                           reason="portal not supported for auto-apply")
    if not app_dir.exists():
        return ApplyResult("failed", portal, url, reason=f"app_dir missing: {app_dir}")
    if already_submitted(app_dir):
        return ApplyResult("skipped", portal, url,
                           reason="apply_result.json: already submitted")

    from bot.scrapers.browser import get_context, profile_lock

    module = _PORTAL_MODULES[portal]
    with profile_lock(timeout_sec=120):
        ctx = get_context()
        page = ctx.new_page()
        try:
            result = module.apply(page, url, app_dir,
                                  default_attachments(app_dir), dry_run,
                                  salary=item.get("salary") or salary_from_package(app_dir))
        except Exception as e:  # будь-який неочікуваний збій — failed, не crash
            result = ApplyResult("failed", portal, url, reason=f"exception: {e}")
        finally:
            page.close()

    result.save(app_dir)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Portal auto-apply runner")
    parser.add_argument("--app-dir", type=Path, help="папка однієї заявки")
    parser.add_argument("--url", help="URL вакансії (для --app-dir)")
    parser.add_argument("--batch", type=Path, help="JSON-черга заявок")
    parser.add_argument("--dry-run", action="store_true",
                        help="заповнити форму + скріншот, НЕ сабмітити")
    args = parser.parse_args(argv)

    if args.batch:
        queue = json.loads(args.batch.read_text(encoding="utf-8"))
        if isinstance(queue, dict):
            queue = queue.get("selected", [])
    elif args.app_dir and args.url:
        queue = [{"app_dir": str(args.app_dir), "url": args.url}]
    else:
        parser.print_help()
        return 2

    limit = guard_limit()
    if len(queue) > limit:
        print(
            f"[apply] RUNAWAY GUARD: {len(queue)} заявок у черзі > ліміт {limit}. "
            f"Нічого не подано — перевірте фільтр.",
            file=sys.stderr,
        )
        print(json.dumps({"guard_triggered": True, "queue_size": len(queue),
                          "limit": limit, "results": []}, ensure_ascii=False))
        return 3

    results = []
    for i, item in enumerate(queue, 1):
        print(f"[apply {i}/{len(queue)}] {item.get('url', '')[:80]}", file=sys.stderr)
        result = apply_one(item, dry_run=args.dry_run)
        print(f"  → {result.status} ({result.reason or result.confirmation_text})",
              file=sys.stderr)
        results.append({"app_dir": item.get("app_dir"), **asdict(result)})

    from bot.scrapers.browser import shutdown_browser
    shutdown_browser()

    counts: dict[str, int] = {}
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    print(json.dumps({"guard_triggered": False, "counts": counts,
                      "results": results}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
