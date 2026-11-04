"""scripts/inbox_watch.py: тіньовий режим з оточення, pull перед проходом, push трекера, алерт на збій пошти."""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ENGINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE / "scripts"))

import inbox_watch as iw


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com",
                    "-c", "init.defaultBranch=main", *args], cwd=cwd, check=True, capture_output=True)


def _git_out(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


def _repo_with_origin(tmp: Path, name: str, files: dict[str, str] | None = None) -> Path:
    src = tmp / f"{name}-src"
    src.mkdir()
    for rel, text in {"tracker.md": "# tracker\n", **(files or {})}.items():
        (src / rel).parent.mkdir(parents=True, exist_ok=True)
        (src / rel).write_text(text, encoding="utf-8")
    _git(src, "init", "-q")
    _git(src, "add", "-A")
    _git(src, "commit", "-q", "-m", "init")
    _git(tmp, "clone", "-q", "--bare", str(src), f"{name}.git")
    _git(tmp, "clone", "-q", str(tmp / f"{name}.git"), name)
    # push_tracker runs plain `git commit`: the CI runner has no global identity
    _git(tmp / name, "config", "user.name", "t")
    _git(tmp / name, "config", "user.email", "t@example.com")
    return tmp / name


def _remote_change(tmp: Path, name: str, text: str, path: str = "tracker.md") -> None:
    """A commit pushed from elsewhere (the Mac)."""
    other = tmp / f"{name}-other"
    _git(tmp, "clone", "-q", str(tmp / f"{name}.git"), other.name)
    (other / path).parent.mkdir(parents=True, exist_ok=True)
    (other / path).write_text(text, encoding="utf-8")
    _git(other, "add", "-A")
    _git(other, "commit", "-qm", "remote")
    _git(other, "push", "-q")


def _no_sync_in_progress(root: Path) -> bool:
    git = root / ".git"
    return not any((git / p).exists() for p in ("rebase-merge", "rebase-apply", "MERGE_HEAD"))


def _origin_files(tmp: Path, name: str) -> str:
    return subprocess.run(["git", "-C", str(tmp / f"{name}.git"), "ls-tree", "-r", "--name-only", "main"],
                          capture_output=True, text=True, check=True).stdout


def _origin_log(tmp: Path, name: str) -> str:
    return subprocess.run(["git", "-C", str(tmp / f"{name}.git"), "log", "--format=%s", "main"],
                          capture_output=True, text=True, check=True).stdout


# a package file the morning run committed and the browser worker rewrites later (resolved apply link)
PACKAGE = "applications/2026-11-02/Firma_Stelle/lead.json"


def test_dry_run_requested_from_env():
    ns = argparse.Namespace(dry_run=False)
    assert iw.dry_run_requested(ns, {"JOBSEARCH_INBOX_DRY_RUN": "1"}) is True
    assert iw.dry_run_requested(ns, {"JOBSEARCH_INBOX_DRY_RUN": "0"}) is False
    # тінь одного клієнта не гасить інших
    assert iw.dry_run_requested(ns, {"JOBSEARCH_INBOX_DRY_RUN_ERIKA": "1"}, "erika") is True
    assert iw.dry_run_requested(ns, {"JOBSEARCH_INBOX_DRY_RUN_JORDAN": "1"}, "erika") is False
    assert iw.dry_run_requested(ns, {"JOBSEARCH_INBOX_DRY_RUN": "1"}, "erika") is True
    assert iw.dry_run_requested(ns, {}) is False
    assert iw.dry_run_requested(argparse.Namespace(dry_run=True), {}) is True


def test_pull_data_ok(tmp_path):
    root = _repo_with_origin(tmp_path, "data")
    _remote_change(tmp_path, "data", "# tracker\nremote line\n")
    alerts: list[str] = []
    assert iw.pull_data(root, lambda m, tail="": alerts.append(m)) is True
    assert "remote line" in (root / "tracker.md").read_text(encoding="utf-8") and alerts == []


def test_pull_data_conflict_keeps_local_and_alerts(tmp_path):
    root = _repo_with_origin(tmp_path, "data")
    _remote_change(tmp_path, "data", "# tracker\nremote line\n")
    (root / "tracker.md").write_text("# tracker\nlocal line\n", encoding="utf-8")
    _git(root, "commit", "-qam", "local")
    alerts: list[str] = []
    assert iw.pull_data(root, lambda m, tail="": alerts.append(m)) is False
    assert _no_sync_in_progress(root) and "local line" in (root / "tracker.md").read_text(encoding="utf-8")
    assert len(alerts) == 1 and "Datenpull" in alerts[0]


def test_pull_data_merges_after_an_earlier_push_failed(tmp_path):
    """A tracker commit that never reached origin (02.11.2026) plus a change pushed from the Mac:
    `pull --ff-only` refused, and every later pass would run on stale data with an alert."""
    root = _repo_with_origin(tmp_path, "data")
    _remote_change(tmp_path, "data", "rules: []\n", path="profile.yaml")
    (root / "tracker.md").write_text("# tracker\nlocal line\n", encoding="utf-8")
    _git(root, "commit", "-qam", "local")
    alerts: list[str] = []
    assert iw.pull_data(root, lambda m, tail="": alerts.append(m)) is True
    assert alerts == [] and (root / "profile.yaml").exists() and _no_sync_in_progress(root)
    assert "local line" in (root / "tracker.md").read_text(encoding="utf-8")


def test_push_tracker_commits_and_pushes(tmp_path):
    root = _repo_with_origin(tmp_path, "data")
    (root / "tracker.md").write_text("# tracker\n| 1 | row |\n", encoding="utf-8")
    alerts: list[str] = []
    assert iw.push_tracker(root, "jordan: inbox watch 2026-10-26: 1 updates", lambda m, tail="": alerts.append(m)) is True
    log = subprocess.run(["git", "-C", str(tmp_path / "data.git"), "log", "--oneline", "-1"],
                         capture_output=True, text=True, check=True).stdout
    assert "inbox watch" in log and alerts == []


def test_push_tracker_nothing_to_commit(tmp_path):
    root = _repo_with_origin(tmp_path, "data")
    assert iw.push_tracker(root, "msg", lambda m, tail="": None) is True


def test_push_tracker_conflict_aborts_and_alerts(tmp_path):
    root = _repo_with_origin(tmp_path, "data")
    _remote_change(tmp_path, "data", "# tracker\nremote line\n")
    (root / "tracker.md").write_text("# tracker\nlocal line\n", encoding="utf-8")
    alerts: list[str] = []
    assert iw.push_tracker(root, "msg", lambda m, tail="": alerts.append(m)) is False
    assert _no_sync_in_progress(root) and len(alerts) == 1 and "push" in alerts[0].lower()
    assert "local line" in (root / "tracker.md").read_text(encoding="utf-8")


def test_push_tracker_pushes_while_the_worker_left_a_package_file_modified(tmp_path):
    """02.11.2026: the worker rewrote a committed lead.json; `pull --rebase` refused the whole tree
    («You have unstaged changes») and every tracker push of the day failed with an alert."""
    root = _repo_with_origin(tmp_path, "data", {PACKAGE: "{}\n"})
    (root / PACKAGE).write_text('{"apply_url": "https://example.com/apply"}\n', encoding="utf-8")
    (root / "tracker.md").write_text("# tracker\n| 1 | row |\n", encoding="utf-8")
    alerts: list[str] = []
    assert iw.push_tracker(root, "jordan: apply 2026-11-02: 1 sent", lambda m, tail="": alerts.append(m)) is True
    assert alerts == [] and "jordan: apply 2026-11-02: 1 sent" in _origin_log(tmp_path, "data")
    # the worker's file is neither committed nor lost: the next agent run commits it
    assert "apply_url" in (root / PACKAGE).read_text(encoding="utf-8")
    assert _git_out(root, "status", "--porcelain") == f" M {PACKAGE}\n"


def test_push_tracker_merges_a_change_from_the_mac_while_a_package_file_is_modified(tmp_path):
    root = _repo_with_origin(tmp_path, "data", {PACKAGE: "{}\n"})
    _remote_change(tmp_path, "data", "rules: []\n", path="profile.yaml")
    (root / PACKAGE).write_text('{"apply_url": "https://example.com/apply"}\n', encoding="utf-8")
    (root / "tracker.md").write_text("# tracker\n| 1 | row |\n", encoding="utf-8")
    alerts: list[str] = []
    assert iw.push_tracker(root, "msg", lambda m, tail="": alerts.append(m)) is True
    assert alerts == [] and _no_sync_in_progress(root) and (root / "profile.yaml").exists()
    assert "profile.yaml" in _origin_files(tmp_path, "data") and "msg" in _origin_log(tmp_path, "data")
    assert "apply_url" in (root / PACKAGE).read_text(encoding="utf-8")


def test_main_alerts_when_collect_fails(monkeypatch, tmp_path):
    from bot import client as _client
    monkeypatch.setenv("JOBSEARCH_CLIENT", "jordan")
    monkeypatch.setenv("JOBSEARCH_LOCK_DIR", str(tmp_path))
    _client.active.cache_clear()
    alerts: list[str] = []

    def failing_collect(c, days):
        raise RuntimeError("oauth expired")

    monkeypatch.setattr(iw, "collect", failing_collect)
    monkeypatch.setattr(iw, "queue_alert", lambda message, tail="": alerts.append(message))
    monkeypatch.setattr(iw, "pull_data", lambda root, alert: True)
    monkeypatch.setattr(sys, "argv", ["inbox_watch.py", "--dry-run"])
    assert iw.main() == 1
    assert alerts and "Mailabruf" in alerts[0]


def test_alert_senders_include_client_sources():
    """Власні алерти клієнта (search.yaml → sources) не є звернення: 26.10 два GULP-алерти стали картками."""
    from types import SimpleNamespace

    from alert_volume import ALL_SENDERS
    c = SimpleNamespace(search={"sources": {
        "gulp_alerts": {"enabled": True, "channel": "email", "sender": "alerts@example-agency.de"},
        "hays_alerts": {"enabled": False, "channel": "email", "sender": ""},
        "hn": {"enabled": True},
    }})
    senders = iw.alert_senders(c)
    assert "alerts@example-agency.de" in senders and "" not in senders
    assert all(a in senders for a in ALL_SENDERS)


def test_alert_senders_skip_shared_sender():
    """A sender that also carries personal messages (shared_sender) stays readable for the watcher:
    its alerts are told apart by subject (SYSTEM_RE), its messages become cards."""
    from types import SimpleNamespace
    c = SimpleNamespace(search={"sources": {
        "board_alerts": {"enabled": True, "channel": "email", "sender": "noreply@example-board.de",
                         "shared_sender": True},
        "agency_alerts": {"enabled": True, "channel": "email", "sender": "alerts@example-agency.de"},
    }})
    senders = iw.alert_senders(c)
    assert "noreply@example-board.de" not in senders
    assert "alerts@example-agency.de" in senders


def test_repo_lock_is_exclusive_per_repository(tmp_path):
    """29.10.2026: the apply workers of two clients started in the same millisecond and both pulled the
    shared data repo; one sync at a time per repository, the second waits."""
    import pytest

    root = _repo_with_origin(tmp_path, "data")
    sub = root / "clients" / "erika"   # services pass the client folder, the lock belongs to the repo
    sub.mkdir(parents=True)
    with iw.repo_lock(root), pytest.raises(TimeoutError), iw.repo_lock(sub, wait=0.3):
        pass
    with iw.repo_lock(sub, wait=0.3):
        pass


def test_pull_and_push_run_inside_the_repo_lock(tmp_path, monkeypatch):
    from contextlib import contextmanager

    root = _repo_with_origin(tmp_path, "data")
    held: list[bool] = [False]
    seen: list[tuple[str, bool]] = []

    @contextmanager
    def recording_lock(path, wait=iw.SYNC_WAIT_SECONDS):
        held[0] = True
        yield
        held[0] = False

    real_git = iw._git

    def spy_git(path, *args):
        if args and args[0] in ("pull", "push", "commit"):
            seen.append((args[0], held[0]))
        return real_git(path, *args)

    monkeypatch.setattr(iw, "repo_lock", recording_lock)
    monkeypatch.setattr(iw, "_git", spy_git)
    iw.pull_data(root, lambda m, tail="": None)
    (root / "tracker.md").write_text("# tracker\n| 1 | row |\n", encoding="utf-8")
    iw.push_tracker(root, "msg", lambda m, tail="": None)
    assert seen and all(inside for _, inside in seen)


def test_pull_data_alerts_when_the_lock_stays_busy(tmp_path, monkeypatch):
    from contextlib import contextmanager

    @contextmanager
    def busy(path, wait=iw.SYNC_WAIT_SECONDS):
        raise TimeoutError("git sync lock busy")
        yield

    monkeypatch.setattr(iw, "repo_lock", busy)
    alerts: list[str] = []
    assert iw.pull_data(_repo_with_origin(tmp_path, "data"), lambda m, tail="": alerts.append(m)) is False
    assert len(alerts) == 1 and "Datenpull" in alerts[0]


# ── Upwork intake hook (stage 2e) ────────────────────────────────────────────

def _b64(text: str) -> str:
    import base64
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii").rstrip("=")


def _message(body: str, subject: str = "New job alert: Example Fitness Companion App", labels=("UNREAD",)) -> dict:
    return {"labelIds": list(labels), "payload": {
        "mimeType": "multipart/alternative",
        "headers": [{"name": "From", "value": "Upwork <upwork@t.upwork.com>"},
                    {"name": "Subject", "value": subject},
                    {"name": "Date", "value": "Thu, 29 Oct 2026 09:12:00 +0000"}],
        "parts": [{"mimeType": "text/plain", "filename": "", "body": {"data": _b64(body)}},
                  {"mimeType": "text/html", "filename": "", "body": {"data": _b64("<p>html</p>")}}]}}


def test_text_plain_keeps_the_whole_part_with_its_line_breaks():
    body = "Fixed\nprice:\n$2,000\n" + "x" * 20000
    assert iw.text_plain(_message(body)["payload"]) == body
    nested = {"mimeType": "multipart/mixed", "parts": [_message("inner\ntext")["payload"]]}
    assert iw.text_plain(nested) == "inner\ntext"
    html_only = {"mimeType": "text/html", "body": {"data": _b64("<p>x</p>")}}
    attachment = {"mimeType": "multipart/mixed", "parts": [
        {"mimeType": "text/plain", "filename": "notes.txt", "body": {"data": _b64("attached")}}]}
    assert iw.text_plain(html_only) == "" and iw.text_plain(attachment) == ""


def test_collect_reads_platform_mail_from_every_mailbox(monkeypatch, tmp_path):
    """04.11.2026: a client's LinkedIn account is registered with the jobs address while employers
    reply to main; the platform queries ran on main only, so her LinkedIn and ATS mail on jobs was
    invisible to the hourly watcher (the morning agent found it a day later)."""
    from types import SimpleNamespace

    import employer_scan as es
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(es, "scan", lambda acc, days, companies, tracker: [])
    monkeypatch.setattr(es, "_ids", lambda acc, q: calls.append((acc, q)) or [f"{acc}-1", "shared"])
    monkeypatch.setattr(es, "_item", lambda acc, mid, companies: {"message_id": mid, "account": acc})
    tracker = tmp_path / "tracker.md"
    tracker.write_text("# Tracker\n", encoding="utf-8")
    c = SimpleNamespace(gmail_aliases=["jobs", "main"], reply_account="main", tracker=tracker, search={})
    items = iw.collect(c, 2)
    assert {acc for acc, _ in calls} == {"jobs", "main"}
    assert all("in:anywhere" in q for _, q in calls)
    # one mailbox behind two aliases: a message id seen on jobs is not fetched again for main
    assert [(i["message_id"], i["account"]) for i in items] == [("jobs-1", "jobs"), ("shared", "jobs"), ("main-1", "main")]


def test_upwork_senders_come_from_the_disabled_sources():
    from types import SimpleNamespace
    c = SimpleNamespace(search={"sources": {
        "upwork_alerts": {"enabled": False, "channel": "email", "sender": " upwork@t.upwork.com "},
        "upwork_plus_alerts": {"enabled": False, "channel": "email", "sender": "donotreply@upwork.com",
                               "shared_sender": True, "upwork": True},
        "board_alerts": {"enabled": True, "channel": "email", "sender": "alerts@example-board.de"},
    }})
    assert iw.upwork_senders(c) == [("upwork@t.upwork.com", False), ("donotreply@upwork.com", True)]
    assert iw.upwork_senders(SimpleNamespace(search={})) == []
    assert iw.upwork_senders(SimpleNamespace(search={"sources": {"upwork_alerts": {"enabled": False}}})) == []


def test_shared_upwork_sender_is_read_for_alerts_only():
    """donotreply@upwork.com also sends client messages: the intake asks Gmail for its alerts only,
    and the watcher keeps reading the sender (shared_sender), so messages still become cards."""
    from types import SimpleNamespace
    assert iw.upwork_queries([("upwork@t.upwork.com", False), ("donotreply@upwork.com", True)]) == [
        "in:anywhere from:upwork@t.upwork.com newer_than:2d",
        'in:anywhere from:donotreply@upwork.com subject:"new job alert" newer_than:2d']
    c = SimpleNamespace(search={"sources": {"upwork_plus_alerts": {
        "enabled": False, "channel": "email", "sender": "donotreply@upwork.com", "shared_sender": True, "upwork": True}}})
    assert "donotreply@upwork.com" not in iw.alert_senders(c)


def test_collect_upwork_reads_every_mailbox_and_fetches_only_new_mails(monkeypatch):
    from types import SimpleNamespace

    import employer_scan as es
    queries, fetched = [], []

    def fake_ids(account, query):
        queries.append((account, query))
        return ["m1", "m2"]   # jobs and main are one mailbox here: the same ids twice

    def fake_get(account, path, **params):
        fetched.append((account, path, params))
        return _message("body of " + path, labels=("TRASH",))

    monkeypatch.setattr(es, "_ids", fake_ids)
    monkeypatch.setattr(es, "_get", fake_get)
    c = SimpleNamespace(gmail_aliases=["jobs", "main"], gmail=lambda alias: {"address": f"{alias}@example.com"})
    items = iw.collect_upwork(c, [("upwork@t.upwork.com", False)], known={"m1"})
    assert queries == [("jobs", "in:anywhere from:upwork@t.upwork.com newer_than:2d"),
                       ("main", "in:anywhere from:upwork@t.upwork.com newer_than:2d")]
    assert fetched == [("jobs", "messages/m2", {"format": "full"})]
    assert items == [{"message_id": "m2", "account": "jobs", "date": "Thu, 29 Oct 2026 09:12:00 +0000",
                      "subject": "New job alert: Example Fitness Companion App",
                      "from": "Upwork <upwork@t.upwork.com>", "body": "body of messages/m2", "trashed": True,
                      "mail_url": "https://mail.google.com/mail/u/0/?authuser=jobs@example.com#all/m2"}]


UPWORK_ON = {"intake": True, "max_cards_per_pass": 3}
SOURCES = {"upwork_alerts": {"enabled": False, "channel": "email", "sender": "upwork@t.upwork.com"}}


def _contract_client(tmp_path, monkeypatch, upwork=UPWORK_ON, sources=SOURCES):
    """jordan with the Upwork switches, rooted in tmp_path so no state lands in examples/."""
    from dataclasses import replace

    from bot import client as _client
    monkeypatch.delenv("JOBSEARCH_TELEGRAM_TOKEN_JORDAN", raising=False)
    c = _client.load("jordan", base=ENGINE / "examples" / "clients")
    (tmp_path / "jordan").mkdir(parents=True)
    return replace(c, root=tmp_path / "jordan", targeting={**c.targeting, "upwork": upwork},
                   search={**c.search, "sources": {**(c.search.get("sources") or {}), **sources}})


def _quiet_watch(monkeypatch, alerts: list[str]) -> None:
    from bot.inbox_watch import WatchResult
    monkeypatch.setattr(iw, "collect", lambda c, days: [])
    monkeypatch.setattr(iw, "run_watch", lambda *a, **k: WatchResult())
    monkeypatch.setattr(iw, "pull_data", lambda root, alert: True)
    monkeypatch.setattr(iw, "push_tracker", lambda root, message, alert: True)
    monkeypatch.setattr(iw, "queue_alert", lambda message, tail="": alerts.append(message))


def test_pass_runs_upwork_intake_and_alerts_unknown_format(tmp_path, monkeypatch, capsys):
    import json

    from tests.test_llm import FakeLLM
    from tests.upwork_fixtures import alert_body
    alerts: list[str] = []
    _quiet_watch(monkeypatch, alerts)
    answer = json.dumps({"fit": "high", "reason": "Android work", "proposal": "Your app needs offline sync.\n"
                         "I would start with the data layer.\n\nWhich screens matter most?"})
    monkeypatch.setattr(iw, "ClaudeRunner", lambda *a, **k: FakeLLM(lambda p: answer))
    good = {"message_id": "19a0000000000002", "account": "main", "date": "Thu, 29 Oct 2026 10:00:00 +0000",
            "subject": "New job alert: Example Fitness Companion App", "body": alert_body(), "trashed": False,
            "mail_url": "https://mail.google.com/mail/u/0/#all/19a0000000000002"}
    digest = {**good, "message_id": "19a0000000000001", "subject": "Jobs picked for you"}
    seen_known: list[set] = []

    def fake_collect_upwork(c, sender, known):
        seen_known.append(set(known))
        return [good, digest]

    monkeypatch.setattr(iw, "collect_upwork", fake_collect_upwork)
    c = _contract_client(tmp_path, monkeypatch)
    assert iw._pass(c, 2, True) == 0
    state = c.var / "state"
    dry_cards = [json.loads(line) for line in (state / "cards_dry.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [d["kind"] for d in dry_cards] == ["upwork"] and "Your app needs offline sync." in dry_cards[0]["text"]
    statuses = sorted(json.loads(line)["status"]
                      for line in (state / "upwork_jobs_dry.jsonl").read_text(encoding="utf-8").splitlines())
    assert statuses == ["carded", "unknown_format"] and not (state / "upwork_jobs.jsonl").exists()
    assert alerts == ["Upwork intake: unknown alert format in 1 mail(s), check the parser"]
    assert "UPWORK OK: carded=1 skipped=1 duplicates=0 unknown=1 llm=1" in capsys.readouterr().out
    assert iw._pass(c, 2, True) == 0
    assert seen_known[1] == {"19a0000000000001", "19a0000000000002"}   # the second pass fetches nothing again


def test_upwork_shadow_writes_only_dry_files_in_a_live_pass(tmp_path, monkeypatch):
    import json

    from tests.test_llm import FakeLLM
    from tests.upwork_fixtures import alert_body
    alerts: list[str] = []
    _quiet_watch(monkeypatch, alerts)
    answer = json.dumps({"fit": "high", "reason": "Android", "proposal": "Your app needs offline sync."})
    monkeypatch.setattr(iw, "ClaudeRunner", lambda *a, **k: FakeLLM(lambda p: answer))
    mail = {"message_id": "19a0000000000003", "account": "main", "date": "Thu, 29 Oct 2026 10:00:00 +0000",
            "subject": "New job alert: Example Fitness Companion App", "body": alert_body(), "trashed": False}
    monkeypatch.setattr(iw, "collect_upwork", lambda c, sender, known: [mail])
    c = _contract_client(tmp_path, monkeypatch, upwork={"intake": "shadow"})
    assert iw._pass(c, 2, False) == 0
    state = c.var / "state"
    assert (state / "upwork_jobs_dry.jsonl").exists() and not (state / "upwork_jobs.jsonl").exists()
    assert "Your app needs offline sync." in (state / "cards_dry.jsonl").read_text(encoding="utf-8")


def test_pass_leaves_upwork_alone_when_off_or_without_sender(tmp_path, monkeypatch):
    alerts: list[str] = []
    _quiet_watch(monkeypatch, alerts)
    monkeypatch.setattr(iw, "ClaudeRunner", lambda *a, **k: None)

    def must_not_run(*a, **k):
        raise AssertionError("collect_upwork called")

    monkeypatch.setattr(iw, "collect_upwork", must_not_run)
    off = _contract_client(tmp_path / "a", monkeypatch, upwork={"intake": False})
    no_sender = _contract_client(tmp_path / "b", monkeypatch, sources={"upwork_alerts": {"enabled": False}})
    assert iw._pass(off, 2, False) == 0 and iw._pass(no_sender, 2, False) == 0 and alerts == []


def test_upwork_failure_is_an_alert_and_the_pass_still_succeeds(tmp_path, monkeypatch):
    alerts: list[str] = []
    _quiet_watch(monkeypatch, alerts)
    monkeypatch.setattr(iw, "ClaudeRunner", lambda *a, **k: None)

    def broken(*a, **k):
        raise RuntimeError("gmail down")

    monkeypatch.setattr(iw, "collect_upwork", broken)
    assert iw._pass(_contract_client(tmp_path, monkeypatch), 2, False) == 0
    assert alerts == ["Upwork intake failed (Gmail or parser, see the journal)"]
