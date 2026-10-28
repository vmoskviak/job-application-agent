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


def _repo_with_origin(tmp: Path, name: str) -> Path:
    src = tmp / f"{name}-src"
    src.mkdir()
    (src / "tracker.md").write_text("# tracker\n", encoding="utf-8")
    _git(src, "init", "-q")
    _git(src, "add", "-A")
    _git(src, "commit", "-q", "-m", "init")
    _git(tmp, "clone", "-q", "--bare", str(src), f"{name}.git")
    _git(tmp, "clone", "-q", str(tmp / f"{name}.git"), name)
    return tmp / name


def _remote_change(tmp: Path, name: str, text: str) -> None:
    other = tmp / f"{name}-other"
    _git(tmp, "clone", "-q", str(tmp / f"{name}.git"), other.name)
    (other / "tracker.md").write_text(text, encoding="utf-8")
    _git(other, "commit", "-qam", "remote")
    _git(other, "push", "-q")


def _no_rebase_in_progress(root: Path) -> bool:
    return not (root / ".git" / "rebase-merge").exists() and not (root / ".git" / "rebase-apply").exists()


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
    assert _no_rebase_in_progress(root) and "local line" in (root / "tracker.md").read_text(encoding="utf-8")
    assert len(alerts) == 1 and "Datenpull" in alerts[0]


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


def test_push_tracker_conflict_aborts_rebase_and_alerts(tmp_path):
    root = _repo_with_origin(tmp_path, "data")
    _remote_change(tmp_path, "data", "# tracker\nremote line\n")
    (root / "tracker.md").write_text("# tracker\nlocal line\n", encoding="utf-8")
    alerts: list[str] = []
    assert iw.push_tracker(root, "msg", lambda m, tail="": alerts.append(m)) is False
    assert _no_rebase_in_progress(root) and len(alerts) == 1 and "push" in alerts[0].lower()


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
