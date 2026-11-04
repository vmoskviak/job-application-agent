#!/usr/bin/env python3
"""Selected leads of the evening run → packages → checks → queue (stage 2b).

    js scripts/enqueue_leads.py --date YYYY-MM-DD [--filtered PATH] [--notes DIR]
    js scripts/enqueue_leads.py --date YYYY-MM-DD --rechannel
    js scripts/enqueue_leads.py --date YYYY-MM-DD            (employment market: manual_link and
                                                            linkedin leads with packages in
                                                            applications/<date>/, plus StepStone and
                                                            XING leads the runner found external)

For every selected lead in var/run/filtered_<date>.json that has a note in
var/run/notes_<date>/<slug>.txt: the channel (bot.ats, without a LinkedIn login), the
folder var/run/packages_<date>/<slug>/, the bot.presend checks (with the platform
switches and daily limits from targeting.yaml), a record in var/state/queue.jsonl
(queued or held). --rechannel: the date's finished packages go through the resolver
again (channel from lead.source_url), lead.json is rewritten, checks and queue repeat;
packages whose record is already queued, sending, sent, handoff or failed are left alone
(a failed one may already be an application: the summary lists them in skipped_failed).
Prints one JSON line with the summary. Sends nothing: the dispatcher sends
(scripts/dispatch_leads.py), the browser worker applies (scripts/apply_leads.py).
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import os
import re
import sys
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

ENGINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE))

from bot import client as _client
from bot.apply.job_boards import board_of, employer_url_ok
from bot.apply.models import ApplyResult, external_application, load_result
from bot.ats import BoardClient, HttpxBoardClient, Resolution, ats_from_url, resolve_channel
from bot.generators.cover_note import _agency_match, build_note, note_language, slugged
from bot.packages import SKIPPED_FILE, Package, build_packages, load_packages
from bot.platforms import declared
from bot.presend import check_package
from bot.sendqueue import APPLICATION_STATUSES, QUEUE_FILE, enqueue, lead_id, limits_from
from bot.state import JsonlStore, state_dir

USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")
EXIT_NO_INPUT = 2
# Keys the resolver writes into lead.json; without them the lead is a selected record again.
RESOLVER_KEYS = ("channel", "apply_url", "ats", "resolver_note", "board_workplace", "board_location")
# Employment market: filter channels (bot.filters.rules.classify_channel) the browser worker applies on.
WORKER_CHANNELS = ("manual_link", "linkedin")
# Filter channels the portal runner (bot.apply.runner) applies on; an external posting there goes to the worker.
PORTAL_CHANNELS = ("stepstone_auto", "xing_auto")
LOOKUP_NOTE = "employer page by lookup (board posting without an employer link)"
_URL_IN_TEXT = re.compile(r"https?://[^\s\"'<>]+")


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


# ── Ринок найму: пакети вже відрендерив --batch-render у applications/<дата>/ ──

def _folder_stem(company: str, title: str) -> str:
    """Та сама назва папки, що в new_app.make_folder_name (без імпорту new_app: він читає клієнта)."""
    import re

    def clean(text: str) -> str:
        text = re.sub(r"\s*\(m/w/d\)\s*", "", text or "", flags=re.IGNORECASE)
        return re.sub(r"[^a-zA-Z0-9äöüÄÖÜß]", "", text)[:25].strip()
    return f"{clean(company)}_{clean(title)}"


def find_application_dir(day_dir: Path, entry: dict) -> Path | None:
    """Папка пакета ліда: за Source-рядком working/job_description.txt, інакше за назвою папки."""
    from bot.filters.rules import normalize_url

    if not Path(day_dir).is_dir():
        return None
    want = normalize_url(entry.get("source_url") or "")
    dirs = sorted(d for d in Path(day_dir).iterdir() if d.is_dir())
    if want:
        for d in dirs:
            jd = d / "working" / "job_description.txt"
            if jd.exists():
                first = jd.read_text(encoding="utf-8", errors="ignore").splitlines()[:1]
                if first and first[0].startswith("Source:") and normalize_url(first[0][7:].strip()) == want:
                    return d
    stem = _folder_stem(entry.get("company") or "", entry.get("title") or "")
    return next((d for d in dirs if d.name == stem or d.name.startswith(stem + "_v")), None)


def employment_attachments(app_dir: Path, cv_cfg: dict, attachments_dir: Path) -> list[Path]:
    """CV, Anschreiben з to_send/ і Zeugnisse з cv.yaml → attachments (portal: true)."""
    to_send = Path(app_dir) / "to_send"
    out = sorted(to_send.glob("*_CV.pdf")) + sorted(to_send.glob("*Anschreiben*.pdf"))
    for a in (cv_cfg or {}).get("attachments") or []:
        if isinstance(a, dict) and a.get("portal") and a.get("file"):
            out.append(Path(attachments_dir) / str(a["file"]))
    return out


def batch_notes(paths: list[Path]) -> dict[tuple[str, str], str]:
    """Anschreiben з batch_<дата>.json за (компанія, посада): текст для поля «Anschreiben» у формі."""
    from bot.filters.rules import normalize_company

    out: dict[tuple[str, str], str] = {}
    for path in paths:
        if not Path(path).exists():
            continue
        try:
            items = json.loads(Path(path).read_text(encoding="utf-8"))
        except ValueError:
            continue
        for it in items if isinstance(items, list) else []:
            job, tailored = it.get("job") or {}, it.get("tailored") or {}
            key = (normalize_company(job.get("company") or ""), (job.get("title") or "").strip().lower())
            if tailored.get("anschreiben"):
                out[key] = str(tailored["anschreiben"])
    return out


def run_employment(c: _client.Client, date: str, filtered: list[Path], batches: list[Path], applications_dir: Path,
                   attachments_dir: Path, fetch: Callable[[str], str | None], today_text: str,
                   lookup: Callable[[str, str, str], object] | None = None) -> dict:
    """Employment-market leads for the browser worker (apply_leads) with packages from applications/<date>/.

    apply_channel manual_link (the employer's form) and linkedin. The channel comes from
    resolve_channel(market=de_employment): a LinkedIn posting becomes linkedin_easy (Easy Apply) or
    linkedin_offsite (the worker reads the external Apply link under the login and fills that form),
    both with platform linkedin, so targeting.platforms.linkedin and daily_limits.linkedin apply.
    StepStone/XING/Interamt leads are the runner's (bot.apply.runner, earlier in the morning run). A
    StepStone or XING lead comes here only when the runner met an external application
    (`external_application` of its apply_result.json): the employer's form goes to the worker at the
    URL the runner recorded, else the one in the posting page (`resolve_channel`). Without one the
    lead is held as no_employer_url; a board page is never queued. A lead the runner submitted, or
    stopped inside the portal's own form, is not taken."""
    from bot.filters.rules import normalize_company

    selected: list[dict] = []
    portal: list[dict] = []
    for path in filtered:
        if Path(path).exists():
            for e in _selected(Path(path)):
                if e.get("apply_channel") in WORKER_CHANNELS:
                    selected.append(e)
                elif e.get("apply_channel") in PORTAL_CHANNELS:
                    portal.append(e)
    notes = batch_notes(batches)
    store = JsonlStore(state_dir(c.var) / QUEUE_FILE)
    existing = {r.get("id") for r in store.read()}
    check = _checker(c, list(c.targeting.get("agencies_de") or []))
    queued: dict[str, int] = {}
    held: list[dict] = []
    skipped: list[dict] = []
    created = today_text
    # (slug, entry, package dir, resolution or None for the resolver); worker slugs keep their
    # numbering, a portal lead with a slug already taken is the same job and is left out.
    leads: list[tuple[str, dict, Path | None, object]] = [
        (slug, entry, find_application_dir(Path(applications_dir) / date, entry), None)
        for slug, entry in slugged(selected)]
    taken = {slug for slug, *_ in leads}
    for slug, entry in slugged(portal):
        app_dir = find_application_dir(Path(applications_dir) / date, entry)
        result = load_result(app_dir) if app_dir is not None else None
        if slug not in taken and external_application(result):
            leads.append((slug, entry, app_dir, _portal_resolution(entry, result, fetch)))
    for slug, entry, app_dir, res in leads:
        if app_dir is None:
            skipped.append({"slug": slug, "reason": "no_package"})
            continue
        if res is None:
            res = resolve_channel(entry, fetch, HttpxBoardClient(), market="de_employment")
        if res.channel == "none" and board_of(res.apply_url) and lookup is not None:
            # A board posting without the employer's link: the employer lookup may find the job's own page.
            posting = lookup(str(entry.get("company") or ""), str(entry.get("title") or ""),
                             str(entry.get("location") or ""))
            if posting is not None:
                res = Resolution("form", posting.url, ats_from_url(posting.url), LOOKUP_NOTE)
        attachments = employment_attachments(app_dir, c.cv, attachments_dir)
        note = notes.get((normalize_company(entry.get("company") or ""), (entry.get("title") or "").strip().lower()), "")
        lead = {**entry, "channel": res.channel, "apply_url": res.apply_url, "ats": res.ats,
                "resolver_note": res.note, "board_workplace": None, "board_location": None}
        (app_dir / "lead.json").write_text(json.dumps(lead, ensure_ascii=False, indent=1), encoding="utf-8")
        (app_dir / "note.txt").write_text(note + "\n", encoding="utf-8")
        (app_dir / "meta.json").write_text(json.dumps(
            {"slug": slug, "lang": "de", "subject": "", "attachments": [str(p) for p in attachments],
             "created": created}, ensure_ascii=False, indent=1), encoding="utf-8")
        pkg = Package(slug, app_dir, lead, res.channel, res.apply_url, res.ats, "de", "", note, attachments)
        reasons = check(pkg, "de")
        if res.channel == "none" and board_of(res.apply_url):
            # a board posting without the employer's apply URL: say so instead of channel_none
            reasons = ["no_employer_url"] + [r for r in reasons if r != "channel_none"]
        rec = enqueue(store, pkg, reasons, date, today_text)
        if rec["id"] not in existing:
            _count(rec, slug, queued, held)
    return {"date": date, "packages": len(leads) - len(skipped), "queued": queued, "held": held,
            "skipped": skipped}


def _portal_resolution(entry: dict, result: ApplyResult, fetch: Callable[[str], str | None]) -> Resolution:
    """The employer's form of an external StepStone or XING posting: the URL the runner recorded,
    else a URL in its reason (StepStone's hand-off), else the one on the posting page."""
    for url in [result.employer_url, *_URL_IN_TEXT.findall(result.reason or "")]:
        if url and employer_url_ok(url):
            return Resolution("form", url, ats_from_url(url), f"{result.portal} external application, "
                                                              "employer url from the runner")
    # without the email: the runner's lead is an application through a form, not a mail
    return resolve_channel({k: v for k, v in entry.items() if k != "email"}, fetch, HttpxBoardClient(),
                           market="de_employment")


def _checker(c: _client.Client, agencies: list[str]) -> Callable[..., list[str]]:
    """check_package with the client's parameters; the package and the expected language remain."""
    hourly_min = (c.profile.get("rate") or {}).get("hourly_min")
    forbidden = [str(n) for n in (c.targeting.get("forbidden_names") or [])]
    platforms = declared(c.targeting)
    limits = limits_from(c.targeting)

    def check(pkg, lang: str) -> list[str]:
        return check_package(pkg, tracker_path=c.tracker, expected_lang=lang, forbidden_names=forbidden,
                             hourly_min=int(hourly_min) if hourly_min else None,
                             is_agency=_agency_match(pkg.lead, agencies), platforms=platforms, limits=limits)
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
    if c.market == "de_employment":
        import datetime as _dt

        prev = (_dt.date.fromisoformat(args.date) - _dt.timedelta(days=1)).isoformat()
        # вечірній batch (учора) і нічні алерти (сьогодні) рендеряться в один ранок
        filtered_paths = [args.filtered] if args.filtered else [
            c.run_dir / f"filtered_{prev}.json", c.run_dir / f"filtered_alerts_{args.date}.json",
            c.run_dir / f"filtered_{args.date}.json"]
        batches = [c.run_dir / f"batch_{prev}.json", c.run_dir / f"batch_alerts_{args.date}.json",
                   c.run_dir / f"batch_{args.date}.json"]
        from bot.apply.employer_lookup import KEY_ENV, find_posting

        lookup = find_posting if os.environ.get(KEY_ENV) else None
        out = run_employment(c, args.date, filtered_paths, batches, c.applications, c.attachments, fetch_html, now,
                             lookup=lookup)
        print(json.dumps(out, ensure_ascii=False))
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
