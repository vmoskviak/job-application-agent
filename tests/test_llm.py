"""claude -p під спільним локом машини (bot.llm) і FakeLLM для тестів етапу 2a."""
from __future__ import annotations

import fcntl
from collections.abc import Callable
from pathlib import Path

import pytest

from bot.llm import ClaudeRunner, LockBusy, machine_lock_path


class FakeLLM:
    """Відповіді за списком або функцією; записує промпти."""

    def __init__(self, answers: list[str] | Callable[[str], str]) -> None:
        self._answers = answers
        self.prompts: list[str] = []

    def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if callable(self._answers):
            return self._answers(prompt)
        if not self._answers:
            raise RuntimeError("FakeLLM: відповіді скінчились")
        return self._answers.pop(0)


def test_runner_calls_command_and_returns_stdout(tmp_path):
    r = ClaudeRunner(tmp_path / "lock",
                     command=["bash", "-c", "cat >/dev/null; echo '{\"class\": \"outreach\", \"draft\": \"ok\"}'"])
    assert '"draft": "ok"' in r.complete("prompt text")


def test_runner_passes_prompt_on_stdin_for_custom_command(tmp_path):
    r = ClaudeRunner(tmp_path / "lock", command=["bash", "-c", "cat"])
    assert r.complete("prompt text").strip() == "prompt text"


def test_runner_waits_for_busy_lock_then_times_out(tmp_path):
    lock = tmp_path / "lock"
    with open(lock, "w") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        r = ClaudeRunner(lock, lock_wait=1, command=["true"])
        with pytest.raises(LockBusy):
            r.complete("p")
    assert issubclass(LockBusy, TimeoutError)


def test_runner_releases_lock_after_run(tmp_path):
    lock = tmp_path / "lock"
    ClaudeRunner(lock, command=["true"]).complete("p")
    with open(lock, "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)   # вільний, інакше BlockingIOError


def test_runner_raises_on_failure(tmp_path):
    r = ClaudeRunner(tmp_path / "lock", command=["bash", "-c", "echo boom >&2; exit 2"])
    with pytest.raises(RuntimeError, match="boom"):
        r.complete("p")


def test_machine_lock_path_follows_env(monkeypatch, tmp_path):
    monkeypatch.setenv("JOBSEARCH_LOCK_DIR", str(tmp_path))
    assert machine_lock_path() == tmp_path / "jobsearch-agent.lock"


def test_fake_llm_records_prompts():
    llm = FakeLLM(["a", "b"])
    assert llm.complete("x") == "a" and llm.complete("y") == "b" and llm.prompts == ["x", "y"]


def test_runner_converts_process_timeout(tmp_path):
    """subprocess.TimeoutExpired не є TimeoutError: без конвертації він убивав увесь прохід."""
    r = ClaudeRunner(tmp_path / "lock", timeout=1, command=["sleep", "5"])
    with pytest.raises(RuntimeError, match="timeout"):
        r.complete("p")


def test_runner_missing_binary_is_runtime_error(tmp_path):
    r = ClaudeRunner(tmp_path / "lock", command=["/nonexistent/claude-xyz"])
    with pytest.raises(RuntimeError):
        r.complete("p")


def test_default_command_is_hardened(tmp_path):
    """claude -p без MCP-серверів і вбудованих інструментів: у промпті чужий текст листа."""
    r = ClaudeRunner(tmp_path / "lock", model="opus")
    cmd = r.command_for("hello")
    assert cmd[:3] == ["claude", "-p", "hello"]
    assert "--strict-mcp-config" in cmd and "--mcp-config" in cmd
    assert cmd[cmd.index("--tools") + 1] == ""
    assert cmd[cmd.index("--output-format") + 1] == "text"
    assert cmd[cmd.index("--model") + 1] == "opus"
    mcp = Path(cmd[cmd.index("--mcp-config") + 1])
    assert mcp.read_text(encoding="utf-8").strip() == '{"mcpServers": {}}'
