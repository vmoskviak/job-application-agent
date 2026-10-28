"""claude -p як окремий джоб під спільним локом машини.

Лок той самий, що `flock` у scripts/server/run_agent.sh (JOBSEARCH_LOCK_DIR/
jobsearch-agent.lock): підписка й дисплей одні на всіх, тож LLM-джоби inbox
watcher-а не перетинаються з нічними ранами. Чекаємо на лок із опитуванням, бо
flock у python не має таймауту. Виклик claude без MCP-серверів і без вбудованих
інструментів: у промпті стоїть чужий текст листа. Без конфігу клієнта на імпорті;
для тестів інтерфейс LLM і підмінна команда замість claude.
"""
from __future__ import annotations

import contextlib
import fcntl
import json
import os
import subprocess
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Protocol


class LLM(Protocol):
    def complete(self, prompt: str) -> str: ...


class LockBusy(TimeoutError):
    """Лок машини зайнятий довше, ніж ми готові чекати (нічний ран триває)."""


def machine_lock_path() -> Path:
    """Той самий файл, що тримає run_agent.sh (exec 9>…/jobsearch-agent.lock)."""
    return Path(os.environ.get("JOBSEARCH_LOCK_DIR", "/var/lock")) / "jobsearch-agent.lock"


@contextlib.contextmanager
def machine_lock(lock_path: Path, lock_wait: int = 900) -> Iterator[None]:
    """Ексклюзивний flock на файл лока з очікуванням до lock_wait с, інакше LockBusy."""
    lock_path = Path(lock_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    # "a": відкриття не обрізає файл, який інший процес тримає під локом
    with open(lock_path, "a") as lock:
        deadline = time.monotonic() + lock_wait
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise LockBusy(f"лок машини зайнятий довше за {lock_wait} с: {lock_path}") from None
                time.sleep(min(5.0, remaining))
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def empty_mcp_config() -> Path:
    """Порожній --mcp-config: разом зі --strict-mcp-config відключає MCP-сервери машини."""
    path = Path(tempfile.gettempdir()) / "jobsearch-empty-mcp.json"
    if not path.exists():
        path.write_text(json.dumps({"mcpServers": {}}), encoding="utf-8")
    return path


class ClaudeRunner:
    """Один виклик `claude -p <prompt>`; stdout повертається як є.

    lock_path=None: лок уже тримає викликач (inbox watcher бере його на весь прохід;
    другий flock у тому самому процесі заблокував би сам себе)."""

    def __init__(self, lock_path: Path | None, model: str = "opus", timeout: int = 300,
                 lock_wait: int = 900, command: list[str] | None = None) -> None:
        self.lock_path = Path(lock_path) if lock_path else None
        self.model = model
        self.timeout = timeout
        self.lock_wait = lock_wait
        # Підмінна команда (тести): промпт іде на stdin, щоб не екранувати його в shell
        self.command = command

    def command_for(self, prompt: str) -> list[str]:
        return ["claude", "-p", prompt, "--model", self.model, "--output-format", "text",
                "--tools", "", "--strict-mcp-config", "--mcp-config", str(empty_mcp_config())]

    def _run(self, prompt: str) -> subprocess.CompletedProcess:
        env = {**os.environ, "IS_SANDBOX": "1"}
        try:
            if self.command:
                return subprocess.run(self.command, input=prompt, capture_output=True, text=True,
                                      timeout=self.timeout, env=env, check=False)
            return subprocess.run(self.command_for(prompt), stdin=subprocess.DEVNULL, capture_output=True,
                                  text=True, timeout=self.timeout, env=env, check=False,
                                  cwd=tempfile.gettempdir())
        except subprocess.TimeoutExpired:
            # не TimeoutError: без конвертації він пролітав повз обробку у watcher-і
            raise RuntimeError(f"claude -p timeout after {self.timeout} s") from None
        except OSError as e:
            raise RuntimeError(f"claude -p не запустився: {e}") from None

    def complete(self, prompt: str) -> str:
        if self.lock_path is not None:
            with machine_lock(self.lock_path, self.lock_wait):
                proc = self._run(prompt)
        else:
            proc = self._run(prompt)
        if proc.returncode != 0:
            raise RuntimeError(f"claude -p exit {proc.returncode}: {proc.stderr[-1500:].strip()}")
        return proc.stdout
