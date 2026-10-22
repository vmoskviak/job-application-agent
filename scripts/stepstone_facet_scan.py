#!/usr/bin/env python3
"""Скан StepStone за Berufsfeld-фасетом → файл URL для discover.

Чому окремий вхід, а не алерт: StepStone зберігає в Job-E-Mail лише
what/where/radius, фасети `di=`/`sdi=` губляться (пілот 25.07 вийшов підпискою
на всі вакансії в 50 км). Тому фасет читаємо самі.

  DISPLAY=:99 .venv/bin/python scripts/stepstone_facet_scan.py --max-pages 3
  DISPLAY=:99 .venv/bin/python scripts/stepstone_facet_scan.py --dry-run
  .venv/bin/python bot/generators/discover.py var/run/urls_facet_2026-07-25.txt

Стан «що вже бачили» — var/state/stepstone_facet_seen.json (job_id зі StepStone),
тому повторні прогони віддають лише нові позиції.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from bot.scrapers.browser import shutdown_browser  # noqa: E402
from bot.scrapers.stepstone_facet import (  # noqa: E402
    load_seen,
    save_seen,
    scan_facet,
)

from bot import client as _client  # noqa: E402

STATE = _client.active().var / "state" / "stepstone_facet_seen.json"
RUN_DIR = _client.active().run_dir


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--facet", default=_client.active().search.get("stepstone_facet"),
                    help="URL фасета (дефолт: search.yaml → stepstone_facet)")
    ap.add_argument("--max-pages", type=int, default=3, help="сторінок за прогін (25 позицій кожна)")
    ap.add_argument("--state", type=Path, default=STATE)
    ap.add_argument("--out", type=Path, default=None, help="куди писати URL (типово var/run/urls_facet_<дата>.txt)")
    ap.add_argument("--dry-run", action="store_true", help="не писати ні стан, ні файл URL")
    ap.add_argument("--json", action="store_true", help="вивід машинно-читний")
    args = ap.parse_args()

    if not args.facet:
        # клієнт без фасета в search.yaml: кроку просто нема
        print(json.dumps({"new": 0, "skipped": "no stepstone_facet"}) if args.json
              else "stepstone_facet не задано — пропускаю")
        return 0
    seen_before = load_seen(args.state)
    # Лок профілю бере сам fetch_with_browser на кожен фетч; flock не реентрантний,
    # тому обгортати цикл ще одним profile_lock() = гарантований дедлок.
    try:
        hits = scan_facet(args.facet, max_pages=args.max_pages, seen=seen_before)
    finally:
        shutdown_browser()

    out_path = args.out or RUN_DIR / f"urls_facet_{date.today():%Y-%m-%d}.txt"
    existing = (
        out_path.read_text(encoding="utf-8").splitlines() if out_path.exists() else []
    )
    if not args.dry_run and hits:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        merged = existing + [h.url for h in hits if h.url not in existing]
        out_path.write_text("\n".join(merged) + "\n", encoding="utf-8")
        save_seen(args.state, [h.job_id for h in hits])
    # Повторний ран того ж дня: нових нема (усі вже «бачені»), але сьогоднішній
    # файл із ранковими знахідками є, і його треба віддати в discover, інакше
    # вечірній батч втрачає StepStone (08.10.2026).
    today_urls = len(existing) + (len(hits) if not args.dry_run else 0)
    urls_file = str(out_path) if (today_urls and not args.dry_run) else None

    payload = {
        "facet": args.facet,
        "pages_scanned_max": args.max_pages,
        "seen_before": len(seen_before),
        "new": len(hits),
        "known_today": len(existing),
        "urls_file": urls_file,
        "dry_run": args.dry_run,
        "hits": [
            {"job_id": h.job_id, "title": h.title, "company": h.company,
             "location": h.location, "url": h.url}
            for h in hits
        ],
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=1))
    else:
        print(f"Фасет: {args.facet}")
        print(f"Відомо раніше: {len(seen_before)} · нових: {len(hits)}")
        for h in hits:
            print(f"  {h.job_id}  {h.title[:52]:52}  {h.company[:26]:26}  {h.location[:22]}")
        if hits and not args.dry_run:
            print(f"URL записано: {out_path}")
            print(f"Далі: .venv/bin/python bot/generators/discover.py {out_path}")
        elif not hits:
            print("Нових позицій немає." + (f" Сьогоднішній файл: {out_path} ({len(existing)} URL)" if existing else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
