#!/usr/bin/env python3
"""Hourly pass over the contract client's mail: classes, tracker, Telegram cards, follow-ups.

    js scripts/inbox_watch.py [--days 2] [--dry-run]

Mails: replies to applications through employer_scan (tracker and ATS domains, the jobs sweep)
plus queries over platform domains (phase 2 spec, §8). Drafts are written by claude -p under the
shared machine lock. Without the bot token or chat_id the cards land in var/state/cards_dry.jsonl
and the tracker is updated all the same; --dry-run or JOBSEARCH_INBOX_DRY_RUN_<SLUG>=1 (or =1
without the suffix for every client of the machine) in the environment (shadow week) touches
neither the tracker nor the mail.
accounts.yaml → telegram.mode: notify (clients with the morning agent): text notifications to the
operator about interviews and requests only, without tracker, drafts, follow-ups and lock.
Stage 2e, after the watcher: Upwork alert mails become proposal cards (bot.upwork_intake) when
targeting.yaml → upwork.intake is true (or shadow) and search.yaml names an Upwork alert sender
(sources.upwork_alerts.sender, plus any source with `upwork: true`).
The whole pass runs under the shared machine lock, so it never overlaps the night runs; a busy
lock means skipping this hour. Before the pass the data is pulled (ff-only), after it the
tracker is committed and pushed; failures go as alerts into the summary queue.
"""
from __future__ import annotations

import argparse
import fcntl
import os
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

ENGINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE))
sys.path.insert(0, str(ENGINE / "scripts"))

from bot import client as _client
from bot.inbox_watch import WatchConfig, run_notify, run_watch
from bot.llm import ClaudeRunner, LockBusy, machine_lock, machine_lock_path
from bot.state import state_dir
from bot.telegram.api import HttpTransport
from bot.telegram.dispatch import GmailRestSender


def _today():
    return datetime.now(tz=timezone.utc).astimezone().date()


def alert_senders(c) -> list[str]:
    """Відправники алертів, які не є листами людей: загальний список рушія плюс
    власні джерела клієнта (search.yaml → sources з channel email)."""
    from alert_volume import ALL_SENDERS

    # shared_sender: the address also carries personal messages (freelance.de noreply@), so the watcher
    # reads it and tells alerts apart by subject instead of excluding the sender
    own = [str(src.get("sender")).strip()
           for src in (c.search.get("sources") or {}).values()
           if isinstance(src, dict) and src.get("channel") == "email" and src.get("sender")
           and not src.get("shared_sender")]
    return list(dict.fromkeys([*ALL_SENDERS, *own]))


def collect(c: _client.Client, days: int) -> list[dict]:
    """Листи за `days` днів: відповіді на подачі (employer_scan) і пошта платформ."""
    import employer_scan as es  # читає конфіг клієнта на імпорті

    from bot.filters.rules import parse_tracker_applications
    from bot.generators.employer_scan import scan_accounts
    from bot.generators.inbox import platform_queries

    ALL_SENDERS = alert_senders(c)
    companies = list({a["company"] for a in parse_tracker_applications(c.tracker)})
    items: list[dict] = []
    seen: set[str] = set()
    for acc in scan_accounts(c.gmail_aliases):
        for it in es.scan(acc, days, companies, c.tracker):
            if it["message_id"] not in seen:
                seen.add(it["message_id"])
                items.append(it)
    for q in platform_queries(days, ALL_SENDERS):
        for mid in es._ids(c.reply_account, q):
            if mid in seen:
                continue
            seen.add(mid)
            it = es._item(c.reply_account, mid, companies)
            if it is not None:
                items.append(it)
    return items


# An Upwork job older than two days has its proposals already, whatever --days says
UPWORK_DAYS = 2


def upwork_senders(c) -> list[tuple[str, bool]]:
    """(sender, shared) of the Upwork alert sources: search.yaml → sources.upwork_alerts and every
    other email source with `upwork: true` (04.11.2026: Freelancer Plus alerts come from
    donotreply@upwork.com, which also sends client messages, hence shared_sender there). The sources
    stay enabled: false: the evening run reads enabled email sources only, and an Upwork job must
    never become a queue lead."""
    out: list[tuple[str, bool]] = []
    for key, src in (c.search.get("sources") or {}).items():
        if not isinstance(src, dict) or not (key == "upwork_alerts" or src.get("upwork") is True):
            continue
        sender = str(src.get("sender") or "").strip()
        if sender and sender not in (s for s, _ in out):
            out.append((sender, bool(src.get("shared_sender"))))
    return out


def text_plain(payload: dict) -> str:
    """The first text/plain part of a Gmail message, whole and with its line breaks. The Upwork
    parser needs both: employer_scan._plain_body collapses whitespace and the watcher keeps 3 000
    characters, while the job block of an alert starts after about 8 000."""
    import base64

    data = (payload.get("body") or {}).get("data")
    if payload.get("mimeType") == "text/plain" and data and not payload.get("filename"):
        return base64.urlsafe_b64decode(data + "==").decode("utf-8", "replace")
    for part in payload.get("parts") or []:
        text = text_plain(part)
        if text:
            return text
    return ""


def upwork_queries(senders: list[tuple[str, bool]], days: int = UPWORK_DAYS) -> list[str]:
    """One Gmail query per sender. A shared sender also delivers client messages and invoices:
    only its "New job alert" mails go to the intake, the rest would be unknown formats."""
    return [f"in:anywhere from:{sender}" + (' subject:"new job alert"' if shared else "") + f" newer_than:{days}d"
            for sender, shared in senders]


def collect_upwork(c: _client.Client, senders: list[tuple[str, bool]], known: set[str],
                   days: int = UPWORK_DAYS) -> list[dict]:
    """Alert mails of the last `days` days in every configured mailbox (wherever Upwork delivers),
    in:anywhere like the watcher. A body is fetched only for a mail the intake has not decided on:
    a real alert weighs about 95 KB and the pass runs hourly."""
    import employer_scan as es  # reads the client config at import

    from bot.generators.employer_scan import scan_accounts
    from bot.telegram.cards import gmail_url

    seen = set(known)
    items: list[dict] = []
    for acc, query in ((a, q) for a in scan_accounts(c.gmail_aliases) for q in upwork_queries(senders, days)):
        for mid in es._ids(acc, query):
            if mid in seen:
                continue
            seen.add(mid)
            msg = es._get(acc, f"messages/{mid}", format="full")
            payload = msg.get("payload") or {}
            hdr = {h["name"]: h["value"] for h in payload.get("headers") or []}
            items.append({"message_id": mid, "account": acc, "date": hdr.get("Date", ""),
                          "subject": hdr.get("Subject", ""), "from": hdr.get("From", ""),
                          "body": text_plain(payload), "trashed": "TRASH" in (msg.get("labelIds") or []),
                          "mail_url": gmail_url(c.gmail(acc)["address"], mid)})
    return items


def run_upwork(c: _client.Client, llm, transport, chat_id: int | None, state: Path, dry_run: bool,
               alert) -> None:
    """Stage 2e after the watcher, under the same lock and LLM runner: only with
    targeting.yaml → upwork.intake true or shadow and an Upwork alert sender in search.yaml. A failure becomes an
    alert and never spoils the watcher's pass. /pause does not apply: an intake card sends nothing."""
    from bot.upwork_intake import intake_config, processed_ids, run_intake
    from bot.upwork_submit import submit_config

    cfg = intake_config(c.targeting, lang=c.report_lang, dry_run=dry_run)
    senders = upwork_senders(c)
    if cfg is None or not senders:
        return
    sub = submit_config(c.targeting, lang=c.report_lang, dry_run=dry_run)
    try:
        items = collect_upwork(c, senders, processed_ids(state, dry_run))
        res = run_intake(cfg, items, c.tracks.get("tracks") or {}, state, transport, chat_id, llm,
                         c.brief, c.profile, _today(), queue=sub is not None)
    except Exception as e:  # noqa: BLE001 — Gmail, OAuth or a parser bug: the operator hears of it
        print(f"[upwork] intake failed: {e}", file=sys.stderr)
        alert("Upwork intake failed (Gmail or parser, see the journal)", str(e))
        return
    for s in res.skipped:
        print(f"[upwork] skipped: {s}", file=sys.stderr)
    if res.unknown:
        alert(f"Upwork intake: unknown alert format in {len(res.unknown)} mail(s), check the parser",
              "\n".join(f"{u['message_id']} {u['subject']}: {u['reason']}" for u in res.unknown))
    print(f"UPWORK OK: carded={len(res.carded)} skipped={len(res.skipped)} duplicates={res.duplicates} "
          f"unknown={len(res.unknown)} llm={res.llm_calls}")
    if sub is not None:
        submit_upwork(c, sub, llm, transport, chat_id, state, alert)


def submit_upwork(c: _client.Client, sub, llm, transport, chat_id: int | None, state: Path, alert) -> None:
    """Auto-submit of the queued Upwork jobs (targeting.yaml → upwork.submit, the owner's decision of
    02.11.2026) in a headed browser on the server display with the owner's Upwork login in
    var/browser/upwork (scripts/upwork_login.py). Sent proposals get their tracker rows here."""
    from datetime import datetime

    from bot.state import JsonlStore
    from bot.telegram.dispatch import upwork_row
    from bot.upwork_submit import PlaywrightDriver, http_head, queued, run_submit

    if not queued(JsonlStore(state / "cards.jsonl")):
        return
    profile_dir = c.var / "browser" / "upwork"

    def tracker_row(card: dict) -> None:
        upwork_row(c.tracker, card, _today().isoformat(), sub.followup_days, "contract",
                   "submitted by the server")

    try:
        res = run_submit(sub, state, transport, chat_id, llm, c.brief, c.profile, tracker_row,
                         lambda: PlaywrightDriver(profile_dir, shots=state / "upwork_shots"), http_head,
                         datetime.now().astimezone())
    except Exception as e:  # noqa: BLE001 — a bug must not cost the watcher's pass
        print(f"[upwork] submit failed: {e}", file=sys.stderr)
        alert("Upwork submit failed (see the journal)", str(e))
        return
    for d in res.deferred + res.to_owner + res.declined:
        print(f"[upwork] {d}", file=sys.stderr)
    for a in res.alerts:
        alert(a)
    if res.submitted and not sub.dry_run:
        push_tracker(c.root, f"{c.slug}: Upwork proposals sent by the server: {len(res.submitted)}", alert)
    print(f"UPWORK SUBMIT OK: submitted={len(res.submitted)} to_owner={len(res.to_owner)} "
          f"declined={len(res.declined)} deferred={len(res.deferred)} llm={res.llm_calls}")


def queue_alert(message: str, tail: str = "") -> None:
    """Алерт у чергу зведеного листа (send_alert.py --queue); однакові причини там злипаються."""
    subprocess.run([sys.executable, str(ENGINE / "scripts" / "server" / "send_alert.py"),
                    "--run", "inbox", "--message", message, "--log-tail", tail[-1500:], "--queue"],
                   check=False)


def dry_run_requested(args, env, slug: str = "") -> bool:
    """--dry-run, JOBSEARCH_INBOX_DRY_RUN_<SLUG>=1 (тінь одного клієнта) або
    JOBSEARCH_INBOX_DRY_RUN=1 (усі клієнти машини) в оточенні, без правки юніта.
    28.10.2026: спільний прапорець тіньового тижня контрактного клієнта мовчки
    загасив перші живі проходи notify для двох інших."""
    if args.dry_run:
        return True
    keys = [f"JOBSEARCH_INBOX_DRY_RUN_{slug.upper()}"] if slug else []
    keys.append("JOBSEARCH_INBOX_DRY_RUN")
    return any(str(env.get(k, "")).strip().lower() in ("1", "true", "yes") for k in keys)


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=False)


SYNC_LOCK = "jobsearch-sync.lock"
SYNC_WAIT_SECONDS = 120.0


@contextmanager
def repo_lock(root: Path, wait: float = SYNC_WAIT_SECONDS) -> Iterator[None]:
    """One git sync (pull, commit, push) at a time per repository. The hourly services of several
    clients start in the same second and share one data repository: on 29.10.2026 two apply
    workers pulled at once and both failed with «Cannot fast-forward to multiple branches».
    TimeoutError when another sync holds the lock longer than `wait` seconds."""
    gitdir = _git(root, "rev-parse", "--absolute-git-dir").stdout.strip()
    path = Path(gitdir) / SYNC_LOCK if gitdir else Path(root) / f".{SYNC_LOCK}"
    with open(path, "a", encoding="utf-8") as fh:
        deadline = time.monotonic() + wait
        while True:
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"git sync lock busy for {wait:.0f}s: {path}") from None
                time.sleep(0.5)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


# The server's working tree is rarely clean between agent runs: the browser worker writes apply results,
# screenshots and resolved links into packages the morning run committed, and only the next run commits
# them. `pull --rebase` refuses such a tree outright (02.11.2026: seven failed tracker pushes in a day),
# `pull --ff-only` refuses once a push has failed. A merge leaves unrelated changes alone and stops only
# when an incoming commit touches a locally changed file, which is a conflict worth an alert.
PULL = ("pull", "--no-rebase", "--no-edit", "-q")


def _pull(root: Path) -> subprocess.CompletedProcess:
    proc = _git(root, *PULL)
    if proc.returncode != 0:
        _git(root, "merge", "--abort")  # a stopped merge must not stay half-done for the next pass
    return proc


def pull_data(root: Path, alert) -> bool:
    """Fresh data before the pass (a merge, see PULL). On failure the pass runs on local state and
    the operator gets an alert (like run_agent.sh)."""
    try:
        with repo_lock(root):
            proc = _pull(root)
    except TimeoutError as e:
        proc = subprocess.CompletedProcess(["git", "pull"], 1, "", str(e))
    if proc.returncode == 0:
        return True
    print(f"[inbox] git pull failed: {proc.stderr.strip()[-300:]}", file=sys.stderr)
    alert("Inbox-Watch: Datenpull fehlgeschlagen (git pull), Lauf auf lokalem Stand",
          _git(root, "status", "--short").stdout + proc.stderr)
    return False


def push_tracker(root: Path, message: str, alert) -> bool:
    """Commits only tracker.md, pulls (a merge, see PULL) and pushes. A stopped merge is aborted, so the
    repository is not left half-merged for the next pass or the night run.
    The whole sequence runs under repo_lock, like pull_data."""
    if not _git(root, "status", "--porcelain", "--", "tracker.md").stdout.strip():
        return True
    try:
        with repo_lock(root):
            return _commit_and_push(root, message, alert)
    except TimeoutError as e:
        alert("Inbox-Watch: git push fehlgeschlagen (sync lock)", str(e))
        return False


def _commit_and_push(root: Path, message: str, alert) -> bool:
    for cmd in (("add", "tracker.md"), ("commit", "-q", "-m", message)):
        proc = _git(root, *cmd)
        if proc.returncode != 0:
            alert("Inbox-Watch: git push fehlgeschlagen (commit)", proc.stdout + proc.stderr)
            return False
    proc = _pull(root)
    if proc.returncode != 0:
        alert("Inbox-Watch: git push fehlgeschlagen (pull, abgebrochen)",
              _git(root, "status", "--short").stdout + proc.stderr)
        return False
    proc = _git(root, "push", "-q")
    if proc.returncode != 0:
        alert("Inbox-Watch: git push fehlgeschlagen", proc.stdout + proc.stderr)
        return False
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=2)
    ap.add_argument("--dry-run", action="store_true", help="тіньовий режим: картки у файл, трекер і пошту не чіпати")
    args = ap.parse_args()
    c = _client.active()
    dry_run = dry_run_requested(args, os.environ, c.slug)
    if c.telegram["mode"] == "notify":
        # без моделі й без правок даних лок машини не потрібен: прохід не має
        # чекати нічного рану агента, а пошта читається як є
        return _pass(c, args.days, dry_run)

    try:
        with machine_lock(machine_lock_path(), int(os.environ.get("JOBSEARCH_LOCK_WAIT", "900"))):
            return _pass(c, args.days, dry_run)
    except LockBusy as e:
        print(f"[inbox] пропускаю цю годину: {e}", file=sys.stderr)
        return 0


def _pass(c: _client.Client, days: int, dry_run: bool) -> int:
    if c.telegram["mode"] != "notify":
        pull_data(c.root, queue_alert)  # у notify трекер не правиться, свіжість даних не критична
    try:
        items = collect(c, days)
    except Exception as e:  # noqa: BLE001 — OAuth, Gmail: без пошти проходу нема
        print(f"[inbox] збір пошти не вдався: {e}", file=sys.stderr)
        queue_alert("Inbox-Watch: Mailabruf fehlgeschlagen (OAuth oder Gmail prüfen)", str(e))
        return 1

    cfg = WatchConfig(
        agencies_de=list(c.targeting.get("agencies_de") or []),
        followup_days=int(c.targeting.get("followup_days") or 7),
        reply_account=c.reply_account,
        gmail_address=c.gmail(c.reply_account)["address"],
        lang=c.report_lang,
        dry_run=dry_run,
        forbidden_names=[str(n) for n in (c.targeting.get("forbidden_names") or [])],
    )
    tg = c.telegram
    token = os.environ.get(tg["token_env"])
    transport = HttpTransport(token) if token and tg["chat_id"] else None
    if transport is None:
        print(f"[inbox] без {tg['token_env']} або telegram.chat_id картки йдуть у var/state/cards_dry.jsonl",
              file=sys.stderr)
    state = state_dir(c.var)
    if tg["mode"] == "notify":
        from bot.notify import with_name

        cfg.lang = tg["lang"]
        res = run_notify(cfg, items, state, transport, tg["chat_id"], with_name(c, ""), _today())
        for s in res.skipped:
            print(f"[inbox] skipped: {s}", file=sys.stderr)
        print(f"INBOX OK: notified={len(res.carded)} system={res.system} skipped={len(res.skipped)}")
        return 0
    # лок уже наш на весь прохід, тому runner без власного лока
    llm = ClaudeRunner(None, model=os.environ.get("CLAUDE_MODEL", "opus"))
    sender = GmailRestSender(ENGINE, Path(sys.executable), c.slug)

    res = run_watch(cfg, items, c.tracker, state, transport, tg["chat_id"], llm, sender,
                    c.brief, c.profile, _today())
    if not dry_run:
        n = len(res.tracker_updates) + len(res.followups_sent)
        push_tracker(c.root, f"{c.slug}: inbox watch {_today().isoformat()}: {n} updates", queue_alert)
    for s in res.skipped:
        print(f"[inbox] skipped: {s}", file=sys.stderr)
    print(f"INBOX OK: carded={len(res.carded)} updates={len(res.tracker_updates)} "
          f"followups={len(res.followups_sent)} llm={res.llm_calls} skipped={len(res.skipped)}")
    run_upwork(c, llm, transport, tg["chat_id"], state, dry_run, queue_alert)
    return 0


if __name__ == "__main__":
    sys.exit(main())
