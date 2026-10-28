"""Стан етапу 2a у <client>/var/state/: картки, черга кнопок, бачені листи, пауза.

Файли маленькі (десятки записів), тому JSONL переписується цілком при оновленні,
а множина бачених листів живе в пам'яті після першого читання. Жодного конфігу
клієнта на імпорті: шляхи передаються явно.
"""
from __future__ import annotations

import json
import os
from pathlib import Path


def state_dir(client_var: Path) -> Path:
    """var/state клієнта, створюється на вимогу."""
    d = Path(client_var) / "state"
    d.mkdir(parents=True, exist_ok=True)
    return d


class JsonlStore:
    """Записи з полем id, по одному JSON-об'єкту на рядок."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def read(self) -> list[dict]:
        if not self.path.exists():
            return []
        out: list[dict] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(rec, dict):
                out.append(rec)
        return out

    def append(self, rec: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def get(self, rec_id: str) -> dict | None:
        for rec in self.read():
            if rec.get("id") == rec_id:
                return rec
        return None

    def update(self, rec_id: str, **fields) -> bool:
        recs = self.read()
        found = False
        for rec in recs:
            if rec.get("id") == rec_id:
                rec.update(fields)
                found = True
        if found:   # тимчасовий файл + os.replace: обрив посеред запису не лишає обрізаного файла
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in recs), encoding="utf-8")
            try:
                os.replace(tmp, self.path)
            finally:
                tmp.unlink(missing_ok=True)
        return found


class SeenSet:
    """Ключі (Gmail message id), які вже оброблено; JSON-список на диску."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._keys: set[str] | None = None

    def _load(self) -> set[str]:
        if self._keys is None:
            if self.path.exists():
                try:
                    self._keys = set(json.loads(self.path.read_text(encoding="utf-8")))
                except (json.JSONDecodeError, TypeError):
                    self._keys = set()
            else:
                self._keys = set()
        return self._keys

    def __contains__(self, key: str) -> bool:
        return key in self._load()

    def add(self, key: str) -> None:
        keys = self._load()
        keys.add(key)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(sorted(keys), ensure_ascii=False), encoding="utf-8")


def _pause_path(state: Path) -> Path:
    return Path(state) / "pause"


def is_paused(state: Path) -> bool:
    return _pause_path(state).exists()


def set_paused(state: Path, on: bool) -> None:
    p = _pause_path(state)
    if on:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.touch()
    else:
        p.unlink(missing_ok=True)
