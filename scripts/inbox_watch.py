#!/usr/bin/env python3
"""Погодинний прохід по пошті контрактного клієнта: класи, трекер, картки в Telegram, follow-ups.

    js scripts/inbox_watch.py [--days 2] [--dry-run]

Листи: відповіді на подачі через employer_scan (домени трекера й ATS, прочісування
jobs) плюс запити по доменах платформ (спека фази 2, §8). Чернетки пише claude -p
під спільним локом машини. Без токена бота або chat_id картки лягають у
var/state/cards_dry.jsonl, трекер оновлюється однаково; --dry-run або
JOBSEARCH_INBOX_DRY_RUN_<SLUG>=1 (або =1 без суфікса для всіх клієнтів) в оточенні
(тіньовий тиждень) не чіпає ні трекер, ні пошту.
accounts.yaml → telegram.mode: notify (клієнти з ранковим агентом): лише текстові
сповіщення оператору про співбесіди й запити, без трекера, чернеток, follow-up і лока.
Увесь прохід іде під спільним локом машини, тож із нічними ранами не перетинається;
зайнятий лок означає пропуск цієї години. Перед проходом дані підтягуються (ff-only),
після нього трекер комітиться й пушиться; збої йдуть алертом у чергу зведеного листа.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
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

    own = [str(src.get("sender")).strip()
           for src in (c.search.get("sources") or {}).values()
           if isinstance(src, dict) and src.get("channel") == "email" and src.get("sender")]
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


def pull_data(root: Path, alert) -> bool:
    """Свіжі дані перед проходом: ff-only, щоб нічого не ребейзити й не ховати в stash.
    Збій → прохід на локальному стані й алерт (як у run_agent.sh)."""
    proc = _git(root, "pull", "--ff-only", "-q")
    if proc.returncode == 0:
        return True
    print(f"[inbox] git pull failed: {proc.stderr.strip()[-300:]}", file=sys.stderr)
    alert("Inbox-Watch: Datenpull fehlgeschlagen (git pull --ff-only), Lauf auf lokalem Stand",
          _git(root, "status", "--short").stdout + proc.stderr)
    return False


def push_tracker(root: Path, message: str, alert) -> bool:
    """Коміт лише tracker.md, pull --rebase, push. Невдалий rebase відкочується, щоб репозиторій
    не лишався в недоребейзеному стані для наступного проходу чи нічного рану."""
    if not _git(root, "status", "--porcelain", "--", "tracker.md").stdout.strip():
        return True
    for cmd in (("add", "tracker.md"), ("commit", "-q", "-m", message)):
        proc = _git(root, *cmd)
        if proc.returncode != 0:
            alert("Inbox-Watch: git push fehlgeschlagen (commit)", proc.stdout + proc.stderr)
            return False
    proc = _git(root, "pull", "--rebase", "-q")
    if proc.returncode != 0:
        _git(root, "rebase", "--abort")
        alert("Inbox-Watch: git push fehlgeschlagen (pull --rebase, abgebrochen)",
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
    return 0


if __name__ == "__main__":
    sys.exit(main())
