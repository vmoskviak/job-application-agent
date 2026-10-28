"""Обгортка scripts/server/run_agent.sh: збій git pull даних має дійти до оператора.

22.10.2026 сервер не зміг підтягнути дані (той самий файл створено і на сервері,
і на Mac), обгортка лише дописала рядок у лог, і всі рани того дня йшли на
застарілих трекерах. Тут обгортка ганяється в пісочниці: підроблений python
записує, з чим його викликали, а render_run.py «відповідає» кодом 3 (ринок без
цього режиму), тож скрипт зупиняється одразу після обох pull.
"""
from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
RUN_AGENT = ROOT / "scripts" / "server" / "run_agent.sh"

FAKE_PYTHON = """#!/bin/bash
# Записує кожен виклик одним рядком (хвіст лога в --log-tail багаторядковий);
# render_run.py --check відповідає «ринок без режиму» (exit 3)
args="$*"
printf '%s\\n' "${args//$'\\n'/ }" >> "$FAKE_PY_LOG"
case "$1" in
  *render_run.py) exit 3 ;;
esac
exit 0
"""


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com",
                    "-c", "init.defaultBranch=main", *args],
                   cwd=cwd, check=True, capture_output=True)


def _executable(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _repo_with_origin(tmp: Path, name: str, files: dict[str, str]) -> Path:
    """Клон із upstream: `git pull` тут проходить («Already up to date»)."""
    src = tmp / f"{name}-src"
    src.mkdir()
    for rel, text in files.items():
        (src / rel).parent.mkdir(parents=True, exist_ok=True)
        (src / rel).write_text(text, encoding="utf-8")
    _git(src, "init", "-q")
    _git(src, "add", "-A")
    _git(src, "commit", "-q", "-m", "init")
    _git(tmp, "clone", "-q", "--bare", str(src), f"{name}.git")
    _git(tmp, "clone", "-q", str(tmp / f"{name}.git"), name)
    return tmp / name


@pytest.fixture
def sandbox(tmp_path):
    engine = _repo_with_origin(tmp_path, "engine", {"README.md": "engine\n"})
    (engine / "scripts" / "server").mkdir(parents=True)
    (engine / "scripts" / "server" / "run_agent.sh").write_text(
        RUN_AGENT.read_text(encoding="utf-8"), encoding="utf-8")
    _executable(engine / ".venv" / "bin" / "python", FAKE_PYTHON)
    # macOS без flock: підробка в $ENGINE/bin, який обгортка ставить на початок PATH;
    # аргументи лягають у той самий журнал, що й виклики підробленого python
    _executable(engine / "bin" / "flock",
                "#!/bin/bash\nprintf 'flock %s\\n' \"$*\" >> \"$FAKE_PY_LOG\"\nexit 0\n")
    clients = tmp_path / "clients"
    clients.mkdir()
    env = {
        **os.environ,
        "JOBSEARCH_ENV_FILE": str(tmp_path / "no-such-env"),
        "JOBSEARCH_CLIENTS_DIR": str(clients),
        "JOBSEARCH_LOCK_DIR": str(tmp_path),
        "FAKE_PY_LOG": str(tmp_path / "python_calls.log"),
    }

    def run(slug: str) -> tuple[subprocess.CompletedProcess, list[str], str]:
        proc = subprocess.run(["bash", str(engine / "scripts" / "server" / "run_agent.sh"),
                               slug, "evening"], env=env, capture_output=True, text=True,
                              check=False)
        calls_path = tmp_path / "python_calls.log"
        calls = calls_path.read_text(encoding="utf-8").splitlines() if calls_path.exists() else []
        logs = sorted((clients / slug / "var" / "logs").glob("evening_*.log"))
        return proc, calls, logs[0].read_text(encoding="utf-8") if logs else ""

    return tmp_path, clients, run


def _alerts(calls: list[str]) -> list[str]:
    return [c for c in calls if "send_alert.py" in c]


def test_failed_data_pull_alerts_operator(sandbox):
    _, clients, run = sandbox
    root = clients / "jordan"
    root.mkdir()
    _git(root, "init", "-q")            # без upstream: `git pull --rebase` падає

    proc, calls, log = run("jordan")

    assert proc.returncode == 0, proc.stderr
    assert "git pull даних failed" in log
    alerts = _alerts(calls)
    assert len(alerts) == 1, calls
    assert "--run evening" in alerts[0]
    assert "Datenpull fehlgeschlagen" in alerts[0]
    assert "--queue" in alerts[0]       # evening-режим пошти: у зведений лист, не окремо


def test_successful_data_pull_sends_no_alert(sandbox):
    tmp, clients, run = sandbox
    _repo_with_origin(tmp, "jordan", {"tracker.md": "# tracker\n"})
    (tmp / "jordan").rename(clients / "jordan")

    proc, calls, log = run("jordan")

    assert proc.returncode == 0, proc.stderr
    assert "git pull даних failed" not in log
    assert _alerts(calls) == []
    assert any(c.endswith("render_run.py evening --check") for c in calls), calls


def test_failed_engine_pull_alerts_operator(sandbox):
    tmp, clients, run = sandbox
    _git(tmp / "engine", "remote", "remove", "origin")   # pull --ff-only рушія падає
    _repo_with_origin(tmp, "jordan", {"tracker.md": "# tracker\n"})
    (tmp / "jordan").rename(clients / "jordan")

    proc, calls, log = run("jordan")

    assert proc.returncode == 0, proc.stderr
    assert "git pull рушія failed" in log
    alerts = _alerts(calls)
    assert len(alerts) == 1, calls
    assert "Engine-Pull fehlgeschlagen" in alerts[0]


def test_lock_is_awaited_not_skipped(sandbox):
    """flock -w замість -n: короткий LLM-джоб inbox watcher-а не має збивати нічний ран."""
    tmp, clients, run = sandbox
    _repo_with_origin(tmp, "jordan", {"tracker.md": "# tracker\n"})
    (tmp / "jordan").rename(clients / "jordan")

    proc, calls, _ = run("jordan")

    assert proc.returncode == 0, proc.stderr
    flock_calls = [c for c in calls if c.startswith("flock ")]
    assert flock_calls == ["flock -w 900 9"]
