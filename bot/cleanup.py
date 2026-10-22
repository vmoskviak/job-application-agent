"""Автоматична чистка: auto-ghost, видалення папок термінальних заявок,
дедуп статичних PDF, ротація runtime-файлів.

Python вирішує (детерміністично, з guards) — агент лише викликає
`new_app.py --cleanup` і читає JSON-звіт. Git-історія зберігає все,
тому видалення з робочої копії відновлюване.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

TERMINAL_DELETE_STATUSES = {"rejected", "ghosted", "withdrew", "skipped"}
AUTO_GHOST_STATUSES = {"sent", "confirmed", "portal_submitted"}
GHOST_AFTER_DAYS = 45
RUN_RETENTION_DAYS = 14
LOG_RETENTION_DAYS = 30
MAX_DELETIONS_PER_RUN = 15
MIN_TRACKER_ROWS = 50  # sanity за замовчуванням: менше — трекер битий, нічого не чіпати

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DATE_ANY_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
_STATUS_WORD_RE = re.compile(r"[a-z_]+")
_NOTES_PATH_RE = re.compile(r"applications/[^\s|`·,;)]+")


@dataclass
class TrackerRow:
    line_no: int
    kind: str  # "main" | "legacy"
    date: str
    company: str
    position: str
    status: str        # нормалізоване слово: rejected / sent / ...
    status_raw: str
    last_update: str   # legacy: збігається з date
    notes: str


def _status_word(raw: str) -> str:
    m = _STATUS_WORD_RE.search(raw.strip().strip("*").lower())
    return m.group(0) if m else ""


def _parse_date(s: str) -> Optional[date]:
    m = _DATE_ANY_RE.search(s or "")
    if not m:
        return None
    try:
        return datetime.strptime(m.group(0), "%Y-%m-%d").date()
    except ValueError:
        return None


def parse_tracker_rows(tracker_path: Path) -> list[TrackerRow]:
    """Обидва формати таблиць — та сама логіка клітинок, що в
    bot.generators.responses.update_tracker_status."""
    rows: list[TrackerRow] = []
    date_re = re.compile(r"^\d{4}-\d{2}-\d{2}")
    for i, line in enumerate(tracker_path.read_text(encoding="utf-8").splitlines()):
        if not line.startswith("|"):
            continue
        cells = line.split("|")
        if len(cells) < 5:
            continue
        stripped = [c.strip() for c in cells]
        if date_re.match(stripped[1]):
            # legacy: | date | title | company, location | status |
            rows.append(TrackerRow(
                line_no=i, kind="legacy", date=stripped[1],
                company=stripped[3].split(",")[0].strip(), position=stripped[2],
                status=_status_word(stripped[4]), status_raw=stripped[4],
                last_update=stripped[1], notes="",
            ))
        elif len(cells) >= 11 and (date_re.match(stripped[2]) or stripped[2] in ("—", "2026-05-??")):
            # основна: | # | Date | Company | Position | Source | Salary | Status | Last update | Next | Notes |
            rows.append(TrackerRow(
                line_no=i, kind="main", date=stripped[2],
                company=stripped[3], position=stripped[4],
                status=_status_word(stripped[7]), status_raw=stripped[7],
                last_update=stripped[8],
                notes=stripped[10] if len(stripped) > 10 else "",
            ))
    return rows


def auto_ghost(tracker_path: Path, today: date,
               days: int = GHOST_AFTER_DAYS, dry_run: bool = False) -> list[dict]:
    """sent/confirmed/portal_submitted без оновлень > days днів → ghosted.
    Редагує рядок за line_no (не за компанією — вона може повторюватись)."""
    rows = parse_tracker_rows(tracker_path)
    lines = tracker_path.read_text(encoding="utf-8").splitlines(keepends=True)
    ghosted: list[dict] = []
    changed = False

    for row in rows:
        if row.status not in AUTO_GHOST_STATUSES:
            continue
        last = _parse_date(row.last_update) or _parse_date(row.date)
        if last is None:
            continue
        silent_days = (today - last).days
        if silent_days <= days:
            continue

        eol = "\n" if lines[row.line_no].endswith("\n") else ""
        cells = lines[row.line_no].rstrip("\n").split("|")
        if row.kind == "main":
            cells[7] = " ghosted "
            cells[8] = f" {today.isoformat()} "
        else:
            cells[4] = f" ghosted ({today.isoformat()}) "
        if not dry_run:
            lines[row.line_no] = "|".join(cells) + eol
            changed = True
        ghosted.append({
            "company": row.company, "position": row.position,
            "date": row.date, "old_status": row.status,
            "silent_days": silent_days,
        })

    if changed:
        tracker_path.write_text("".join(lines), encoding="utf-8")
    return ghosted


def _clean(s: str) -> str:
    """Дзеркало clean() з new_app.make_folder_name (паритет — тестом)."""
    s = re.sub(r"\s*\(m/w/d\)\s*", "", s, flags=re.IGNORECASE)
    s = re.sub(r"[^a-zA-Z0-9äöüÄÖÜß]", "", s)
    return s[:25].strip()


def _company_matches(folder_name: str, company: str) -> bool:
    comp_part = folder_name.split("_", 1)[0].lower()
    cand = _clean(company).lower()
    if len(comp_part) < 4 or len(cand) < 4:
        return comp_part == cand
    return comp_part.startswith(cand) or cand.startswith(comp_part)


def _title_hits(position: str, folder_name: str) -> int:
    """Скільки токенів позиції входить у склеєну title-частину назви папки.
    Назви папок — слова без пробілів (make_folder_name), тому шукаємо
    підрядки, а не порівнюємо токен-сети."""
    parts = folder_name.split("_", 1)
    if len(parts) < 2:
        return 0
    haystack = parts[1].lower()
    tokens = set(re.findall(r"[\wäöüß]{4,}", position.lower()))
    return sum(1 for t in tokens if t in haystack)


def resolve_app_dir(row: TrackerRow, applications_dir: Path) -> tuple[Optional[Path], str]:
    """Знаходить папку заявки для рядка трекера. Сумнів → (None, причина)."""
    project_root = applications_dir.parent

    # 1) явний шлях у Notes
    m = _NOTES_PATH_RE.search(row.notes)
    if m:
        rel = m.group(0).split("/to_send")[0].rstrip("/")
        candidate = project_root / rel
        if candidate.is_dir() and applications_dir in candidate.parents:
            return candidate, "notes"

    # 2) applications/<date>/<slug-prefix>
    date_dir = applications_dir / row.date
    if date_dir.is_dir():
        candidates = [p for p in date_dir.iterdir()
                      if p.is_dir() and _company_matches(p.name, row.company)]
        if len(candidates) > 1:
            scored = [(_title_hits(row.position, p.name), p) for p in candidates]
            best = max(s for s, _ in scored)
            top = [p for s, p in scored if s == best]
            if best > 0 and len(top) == 1:
                return top[0], "date_slug_title"
            return None, "ambiguous"
        if len(candidates) == 1:
            return candidates[0], "date_slug"

    # 3) глобальний скан: приймаємо лише рівно один company+title матч
    matches = []
    for top in applications_dir.iterdir():
        if not top.is_dir():
            continue
        for p in top.iterdir():
            if p.is_dir() and _company_matches(p.name, row.company) \
                    and _title_hits(row.position, p.name) > 0:
                matches.append(p)
    if len(matches) == 1:
        return matches[0], "global_scan"
    return None, "ambiguous" if matches else "not_found"


def _remove_empty_date_dirs(applications_dir: Path, dry_run: bool) -> None:
    for top in applications_dir.iterdir():
        if not top.is_dir():
            continue
        leftovers = [p for p in top.iterdir() if p.name != ".DS_Store"]
        if not leftovers:
            if not dry_run:
                shutil.rmtree(top)


def delete_terminal_folders(tracker_path: Path, applications_dir: Path, today: date,
                            dry_run: bool = False,
                            max_delete: int = MAX_DELETIONS_PER_RUN) -> dict:
    rows = parse_tracker_rows(tracker_path)
    terminal = [r for r in rows if r.status in TERMINAL_DELETE_STATUSES]
    active = [r for r in rows if r.status not in TERMINAL_DELETE_STATUSES]

    protected: set[Path] = set()
    for row in active:
        p, _ = resolve_app_dir(row, applications_dir)
        if p is not None:
            protected.add(p)
    today_dir = applications_dir / today.isoformat()

    to_delete: list[tuple[Path, TrackerRow]] = []
    seen: set[Path] = set()
    unmatched: list[dict] = []
    for row in terminal:
        p, reason = resolve_app_dir(row, applications_dir)
        if p is None:
            unmatched.append({"company": row.company, "date": row.date,
                              "status": row.status, "reason": reason})
            continue
        if p in seen:
            continue
        if p in protected or p.parent == today_dir:
            continue
        seen.add(p)
        to_delete.append((p, row))

    capped = len(to_delete) > max_delete
    deleted: list[dict] = []
    for p, row in to_delete[:max_delete]:
        if not dry_run:
            shutil.rmtree(p)
        deleted.append({"company": row.company, "status": row.status,
                        "dir": str(p.relative_to(applications_dir.parent))})

    _remove_empty_date_dirs(applications_dir, dry_run)
    return {"deleted": deleted, "unmatched": unmatched, "capped": capped}


SIZE_TOLERANCE = 0.2  # історичні копії різняться метаданими PDF на кілька байтів


def dedupe_static_pdfs(applications_dir: Path, canonical_dir: Path,
                       dry_run: bool = False) -> dict:
    """Прибирає копії Zeugnisse/дипломів з папок заявок: канонічне ім'я файлу
    + розмір у межах ±20% від attachments/ = та сама статична PDF (байти
    гуляють через метадані при історичних перегенераціях). Ідемпотентно —
    одноразова міграція і щоденний прибиральник це один код."""
    from bot.generators.cv_engine import ZEUGNIS_FILES

    removed = 0
    bytes_freed = 0
    mismatched: list[str] = []
    for fname in ZEUGNIS_FILES:
        canonical = canonical_dir / fname
        if not canonical.exists():
            mismatched.append(f"canonical missing: {fname}")
            continue
        want_size = canonical.stat().st_size
        for copy in applications_dir.rglob(fname):
            size = copy.stat().st_size
            if abs(size - want_size) <= want_size * SIZE_TOLERANCE:
                bytes_freed += size
                if not dry_run:
                    copy.unlink()
                removed += 1
            else:
                mismatched.append(str(copy.relative_to(applications_dir.parent)))
    return {"removed": removed, "bytes_freed": bytes_freed, "mismatched": mismatched}


def rotate_dir(directory: Path, patterns: list[str], days: int, today: date,
               dry_run: bool = False) -> list[str]:
    """Видаляє старі runtime-файли: дата з імені файлу, fallback — mtime."""
    rotated: list[str] = []
    if not directory.is_dir():
        return rotated
    for pattern in patterns:
        for f in directory.rglob(pattern):
            if not f.is_file():
                continue
            stamp = _parse_date(f.name)
            if stamp is None:
                stamp = date.fromtimestamp(f.stat().st_mtime)
            if (today - stamp).days > days:
                if not dry_run:
                    f.unlink()
                rotated.append(f.name)
    return rotated


def _merge_daily_reports(prev: dict, cur: dict) -> dict:
    """Повторний запуск того ж дня — no-op по діях, але без merge він
    перезаписував денний JSON порожніми ghosted/deleted (регресія 2026-07-16)."""
    merged = dict(cur)
    for key in ("ghosted", "deleted"):
        seen = {json.dumps(e, sort_keys=True, ensure_ascii=False)
                for e in prev.get(key, [])}
        merged[key] = list(prev.get(key, [])) + [
            e for e in cur.get(key, [])
            if json.dumps(e, sort_keys=True, ensure_ascii=False) not in seen]
    merged["capped"] = bool(prev.get("capped")) or bool(cur.get("capped"))

    d_prev, d_cur = prev.get("dedupe", {}), cur.get("dedupe", {})
    merged["dedupe"] = {
        "removed": d_prev.get("removed", 0) + d_cur.get("removed", 0),
        "bytes_freed": d_prev.get("bytes_freed", 0) + d_cur.get("bytes_freed", 0),
        "mismatched": d_cur.get("mismatched", []),
    }
    r_prev, r_cur = prev.get("rotated", {}), cur.get("rotated", {})
    merged["rotated"] = {k: r_prev.get(k, 0) + r_cur.get(k, 0)
                         for k in set(r_prev) | set(r_cur)}
    merged["runs"] = prev.get("runs", 1) + 1
    return merged


def compact_report(report: dict) -> dict:
    """Версія для stdout: unmatched згортається до лічильників, повний список
    лишається у var/run/cleanup_<date>.json. Інакше JSON на сотні рядків
    провокує пагінацію через перезапуск команди."""
    compact = dict(report)
    unmatched = compact.get("unmatched")
    if isinstance(unmatched, list):
        by_reason: dict = {}
        for u in unmatched:
            reason = u.get("reason", "?")
            by_reason[reason] = by_reason.get(reason, 0) + 1
        compact["unmatched"] = {"count": len(unmatched), "by_reason": by_reason}
    return compact


def run_cleanup(project_root: Path, today: Optional[date] = None,
                dry_run: bool = False,
                max_delete: int = MAX_DELETIONS_PER_RUN,
                min_rows: int = MIN_TRACKER_ROWS) -> dict:
    today = today or date.today()
    tracker = project_root / "tracker.md"
    applications = project_root / "applications"
    report: dict = {"date": today.isoformat(), "dry_run": dry_run, "errors": []}

    rows = parse_tracker_rows(tracker) if tracker.exists() else []
    if len(rows) < min_rows:
        report["errors"].append(
            f"sanity: трекер дав {len(rows)} рядків (< {min_rows}) — нічого не чіпаю")
        return report

    # без канонічних attachments/ не видаляємо нічого — інакше можна
    # втратити останню копію Zeugnisse
    try:
        from bot.generators.cv_engine import zeugnis_paths, ATTACHMENTS_DIR, VARIANTS
        # частина вкладень прив'язана до варіантів CV — guard має бачити повний набір
        for variant in VARIANTS:
            zeugnis_paths(variant)
    except FileNotFoundError as e:
        report["errors"].append(str(e))
        return report

    report["ghosted"] = auto_ghost(tracker, today, dry_run=dry_run)
    report.update(delete_terminal_folders(
        tracker, applications, today, dry_run=dry_run, max_delete=max_delete))
    report["dedupe"] = dedupe_static_pdfs(applications, ATTACHMENTS_DIR, dry_run=dry_run)
    report["rotated"] = {
        "var_run": len(rotate_dir(project_root / "var" / "run",
                                  ["*.json", "*.txt"], RUN_RETENTION_DAYS, today, dry_run)),
        "var_logs": len(rotate_dir(project_root / "var" / "logs",
                                   ["*.log"], LOG_RETENTION_DAYS, today, dry_run)),
        "logs": len(rotate_dir(project_root / "logs",
                               ["*.log"], LOG_RETENTION_DAYS, today, dry_run)),
        "playwright_mcp": len(rotate_dir(project_root / ".playwright-mcp",
                                         ["*"], LOG_RETENTION_DAYS, today, dry_run)),
    }

    out = project_root / "var" / "run" / f"cleanup_{today.isoformat()}.json"
    if not dry_run:
        prev: Optional[dict] = None
        if out.exists():
            try:
                prev = json.loads(out.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                prev = None
        if prev and not prev.get("dry_run") and "ghosted" in prev:
            report = _merge_daily_reports(prev, report)
        else:
            report["runs"] = 1
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Чистка applications/ + runtime")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--max-delete", type=int, default=MAX_DELETIONS_PER_RUN)
    parser.add_argument("--project-root", type=Path, default=None,
                        help="корінь даних клієнта (дефолт: активний клієнт)")
    args = parser.parse_args(argv)

    min_rows = MIN_TRACKER_ROWS
    if args.project_root is None:
        from bot import client
        args.project_root = client.active().root
        # у нового клієнта трекер порожній: поріг задається в targeting.yaml
        # і підіймається, коли трекер виросте
        min_rows = int(client.active().targeting.get("cleanup_min_tracker_rows", 0))
    report = run_cleanup(args.project_root, dry_run=args.dry_run,
                         max_delete=args.max_delete, min_rows=min_rows)
    print(json.dumps(compact_report(report), ensure_ascii=False, indent=2))
    return 1 if report["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
