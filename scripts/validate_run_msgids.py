#!/usr/bin/env python3
"""Валідатор Msg-ID у трекері — ловить сфабриковані ID.

Кожен `<16-hex>` Msg-ID, який агент вписав у трекер, має реально існувати
в пошті (jobs або main) як message АБО thread. Якщо ні — це фабрикація
(інцидент 24.07: Sonnet вигадав `19f91e390bece17c`, якого не існує).

Використання:
  validate_run_msgids.py --base <git-ref>     # перевіряти лише ID, ДОДАНІ
                                              # в diff <ref>..HEAD (для ранера)
  validate_run_msgids.py                      # ID з рядків трекера з
                                              # сьогоднішньою датою (fallback)
  validate_run_msgids.py <id> [<id> ...]      # перевірити конкретні ID (тест/ручне)
  опційно: --tracker <path>  (дефолт tracker.md)

Exit 1, якщо є хоч один відсутній/сфабрикований ID.

Увага: у режимі `--base <pre-run HEAD>` (ранер) перевіряються лише ID, додані
цим раном — свіжий jobs-Eingangsbestätigung, навіть якщо бот його одразу
видалив, ще резолвиться (Gmail тримає у кошику 30 днів). У широкому/сьогодні-
режимі старі вже-вичищені jobs-ID можуть дати false-positive MISSING — це не
фабрикація, а закінчення 30-денного вікна кошика.
"""
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gmail_rest as g  # noqa: E402
import httpx  # noqa: E402

MSGID_RE = re.compile(r"`([0-9a-f]{16})`")
TRACKER_DEFAULT = "tracker.md"


def _added_ids_from_diff(base: str, tracker: str) -> set[str]:
    out = subprocess.run(["git", "diff", base, "HEAD", "--", tracker],
                         capture_output=True, text=True).stdout
    ids: set[str] = set()
    for line in out.splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            ids.update(MSGID_RE.findall(line))
    return ids


def _ids_from_today(tracker: str) -> set[str]:
    d = date.today()
    iso, dm = d.isoformat(), f"{d.day:02d}.{d.month:02d}"
    ids: set[str] = set()
    for line in Path(tracker).read_text(encoding="utf-8").splitlines():
        if iso in line or dm in line:
            ids.update(MSGID_RE.findall(line))
    return ids


def _exists(msg_id: str):
    """Real, якщо ID резолвиться як message або thread у будь-якій скриньці клієнта."""
    for acc in g.CREDS:
        for kind in ("messages", "threads"):
            try:
                g.api(acc, "GET", f"{kind}/{msg_id}", params={"format": "minimal"})
                return f"{acc}/{kind[:-1]}"
            except httpx.HTTPStatusError as e:
                if e.response.status_code in (403, 404):
                    continue
                raise
    return None


def main() -> int:
    args = sys.argv[1:]
    base = tracker = None
    positional: list[str] = []
    i = 0
    while i < len(args):
        if args[i] == "--base":
            base = args[i + 1]; i += 2
        elif args[i] == "--tracker":
            tracker = args[i + 1]; i += 2
        else:
            positional.append(args[i]); i += 1
    tracker = tracker or TRACKER_DEFAULT

    if positional:
        ids = set(positional)
    elif base:
        ids = _added_ids_from_diff(base, tracker)
    else:
        ids = _ids_from_today(tracker)

    if not ids:
        print("MSGID-CHECK OK: немає нових Msg-ID для перевірки")
        return 0

    missing = []
    for mid in sorted(ids):
        loc = _exists(mid)
        if loc:
            print(f"OK       {mid}  ({loc})")
        else:
            print(f"MISSING  {mid}  — НЕ існує в jobs/main (можлива фабрикація)")
            missing.append(mid)

    if missing:
        print(f"MSGID-CHECK FAIL: {len(missing)}/{len(ids)} відсутні: {', '.join(missing)}")
        return 1
    print(f"MSGID-CHECK OK: усі {len(ids)} Msg-ID реальні")
    return 0


if __name__ == "__main__":
    sys.exit(main())
