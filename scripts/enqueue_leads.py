#!/usr/bin/env python3
"""Відібрані ліди вечірнього рану → пакети → перевірки → черга (етап 2b).

    js scripts/enqueue_leads.py --date YYYY-MM-DD [--filtered PATH] [--notes DIR]
    js scripts/enqueue_leads.py --date YYYY-MM-DD --rechannel

Для кожного selected з var/run/filtered_<дата>.json, що має нотатку в
var/run/notes_<дата>/<slug>.txt: канал подачі (bot.ats, без логіну LinkedIn),
папка var/run/packages_<дата>/<slug>/, перевірки bot.presend (з вимикачами
платформ з targeting.yaml), запис у var/state/queue.jsonl (queued або held).
--rechannel: готові пакети дати заново проходять резолвер (канал з
lead.source_url), lead.json переписується, перевірки й черга повторюються;
пакети, чий запис уже queued, sending, sent, handoff чи failed, не чіпаються
(failed міг уже стати подачею: зведення перелічує їх у skipped_failed).
Друкує один JSON-рядок зі зведенням. Нічого не шле: відправляє диспетчер
(scripts/dispatch_leads.py).
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

ENGINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE))

from bot import client as _client
from bot.ats import BoardClient, HttpxBoardClient, resolve_channel
from bot.generators.cover_note import _agency_match, build_note, note_language, slugged
from bot.packages import SKIPPED_FILE, build_packages, load_packages
from bot.platforms import declared
from bot.presend import check_package
from bot.sendqueue import APPLICATION_STATUSES, QUEUE_FILE, enqueue, lead_id
from bot.state import JsonlStore, state_dir

USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")
EXIT_NO_INPUT = 2
# Ключі, які пише резолвер у lead.json; без них лід знову є записом selected.
RESOLVER_KEYS = ("channel", "apply_url", "ats", "resolver_note", "board_workplace", "board_location")


def fetch_html(url: str) -> str | None:
    """Гостьова сторінка без логіну; None на будь-яку помилку."""
    import httpx

    try:
        r = httpx.get(url, headers={"User-Agent": USER_AGENT}, follow_redirects=True, timeout=20)
        return r.text if r.status_code == 200 else None
    except Exception:  # noqa: BLE001 — сторінка просто недоступна
        return None


def _selected(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("selected", []) if isinstance(data, dict) else list(data)


def run(c: _client.Client, date: str, filtered: Path, notes_dir: Path, run_dir: Path, attachments_dir: Path,
        fetch: Callable[[str], str | None], board: BoardClient, today_text: str) -> dict:
    """Чистий прохід: усе I/O через параметри, щоб тест ганяв його на фейках."""
    agencies = list(c.targeting.get("agencies_de") or [])
    selected = _selected(filtered)
    resolutions = {slug: resolve_channel(entry, fetch, board) for slug, entry in slugged(selected)}
    langmap = {slug: note_language(entry, agencies) for slug, entry in slugged(selected)}
    packages_dir = Path(run_dir) / f"packages_{date}"
    pkgs = build_packages(selected, resolutions, notes_dir, langmap, packages_dir, c.tracks, attachments_dir,
                          c.targeting, lambda entry, lang: build_note(entry, "", c.profile, c.targeting).subject)
    store = JsonlStore(state_dir(c.var) / QUEUE_FILE)
    existing = {r.get("id") for r in store.read()}   # повторний запуск того ж вечора нічого не рахує двічі
    check = _checker(c, agencies)
    queued: dict[str, int] = {}
    held: list[dict] = []
    for pkg in pkgs:
        rec = enqueue(store, pkg, check(pkg, langmap.get(pkg.slug, "en")), date, today_text)
        if rec["id"] not in existing:
            _count(rec, pkg.slug, queued, held)
    return {"date": date, "packages": len(pkgs), "queued": queued, "held": held, "skipped": _skipped(packages_dir)}


def rechannel(c: _client.Client, date: str, run_dir: Path, fetch: Callable[[str], str | None],
              board: BoardClient, today_text: str) -> dict:
    """Пакети дати заново через резолвер: lead.json з новим каналом, перевірки, enqueue.
    Пакет, чий запис уже є подачею (APPLICATION_STATUSES) або failed, лишається як був, без
    запитів: failed («process died during the form fill», «handoff_expired») міг уже подати
    заявку, і повернення в queued дало б другу. Такі id ідуть у skipped_failed, оператор
    розбирає їх руками. held переоцінює наявний шлях оновлення enqueue. Зведення як у run,
    лише по переоцінених пакетах."""
    agencies = list(c.targeting.get("agencies_de") or [])
    packages_dir = Path(run_dir) / f"packages_{date}"
    pkgs = load_packages(packages_dir)
    store = JsonlStore(state_dir(c.var) / QUEUE_FILE)
    status = {r.get("id"): r.get("status") for r in store.read()}
    check = _checker(c, agencies)
    queued: dict[str, int] = {}
    held: list[dict] = []
    skipped_failed: list[str] = []
    for pkg in pkgs:
        rid = lead_id(date, pkg.slug)
        if status.get(rid) in APPLICATION_STATUSES:
            continue
        if status.get(rid) == "failed":
            skipped_failed.append(rid)
            continue
        entry = {k: v for k, v in pkg.lead.items() if k not in RESOLVER_KEYS}
        res = resolve_channel(entry, fetch, board)
        lead = {**entry, "channel": res.channel, "apply_url": res.apply_url, "ats": res.ats,
                "resolver_note": res.note, "board_workplace": res.board_workplace,
                "board_location": res.board_location}
        (pkg.dir / "lead.json").write_text(json.dumps(lead, ensure_ascii=False, indent=1), encoding="utf-8")
        pkg = dataclasses.replace(pkg, lead=lead, channel=res.channel, apply_url=res.apply_url, ats=res.ats)
        _count(enqueue(store, pkg, check(pkg, pkg.lang), date, today_text), pkg.slug, queued, held)
    return {"date": date, "packages": len(pkgs), "queued": queued, "held": held, "skipped": _skipped(packages_dir),
            "skipped_failed": skipped_failed}


def _checker(c: _client.Client, agencies: list[str]) -> Callable[..., list[str]]:
    """check_package з параметрами клієнта; лишаються пакет і очікувана мова."""
    hourly_min = (c.profile.get("rate") or {}).get("hourly_min")
    forbidden = [str(n) for n in (c.targeting.get("forbidden_names") or [])]
    platforms = declared(c.targeting)

    def check(pkg, lang: str) -> list[str]:
        return check_package(pkg, tracker_path=c.tracker, expected_lang=lang, forbidden_names=forbidden,
                             hourly_min=int(hourly_min) if hourly_min else None,
                             is_agency=_agency_match(pkg.lead, agencies), platforms=platforms)
    return check


def _count(rec: dict, slug: str, queued: dict[str, int], held: list[dict]) -> None:
    if rec["status"] == "held":
        held.append({"slug": slug, "reasons": rec["reasons"]})
    elif rec["status"] == "queued":
        queued[rec["channel"]] = queued.get(rec["channel"], 0) + 1


def _skipped(packages_dir: Path) -> list:
    skipped_file = packages_dir / SKIPPED_FILE
    return json.loads(skipped_file.read_text(encoding="utf-8")) if skipped_file.exists() else []


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--date", required=True, help="YYYY-MM-DD вечірнього рану")
    ap.add_argument("--filtered", type=Path, help="дефолт var/run/filtered_<дата>.json")
    ap.add_argument("--notes", type=Path, help="дефолт var/run/notes_<дата>/")
    ap.add_argument("--rechannel", action="store_true",
                    help="заново визначити канал готових пакетів дати й переоцінити їхні записи")
    args = ap.parse_args(argv)
    c = _client.active()
    now = datetime.now(tz=timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M")
    if args.rechannel:
        print(json.dumps(rechannel(c, args.date, c.run_dir, fetch_html, HttpxBoardClient(), now),
                         ensure_ascii=False))
        return 0
    filtered = args.filtered or c.run_dir / f"filtered_{args.date}.json"
    notes = args.notes or c.run_dir / f"notes_{args.date}"
    if not filtered.exists():
        print(f"enqueue_leads: нема {filtered}", file=sys.stderr)
        return EXIT_NO_INPUT
    out = run(c, args.date, filtered, notes, c.run_dir, c.attachments, fetch_html, HttpxBoardClient(), now)
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
