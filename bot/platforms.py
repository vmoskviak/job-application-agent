"""Платформи подачі: до якої платформи належить лід, вимикачі й ліміти.

Декларований стан платформи (on, off, shadow) живе в targeting.yaml, секція
`platforms`, і є постійним. Файл var/state/platforms.json тримає лише тимчасові
записи (off_until, shadow_until): збій входу, обмеження акаунта, добовий
ліміт. Тимчасовий запис чинний, доки не мине його час, і перекриває
декларацію: off завжди, shadow лише коли платформа не оголошена off.
Прострочені записи ігноруються, але не видаляються. Жодного конфігу клієнта
на імпорті, усі функції чисті: targeting, path і now приходять параметрами.
Позначки у файлі: місцевий настінний час без зони; час із зоною (CLI передає
datetime.now(tz=utc).astimezone()) зводиться до нього перед записом і порівнянням.
"""
from __future__ import annotations

import contextlib
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

Switch = Literal["on", "off", "shadow"]

STATE_FILE = "platforms.json"
TS_FORMAT = "%Y-%m-%d %H:%M"
PLATFORM_HOSTS: dict[str, tuple[str, ...]] = {
    "freelancermap": ("freelancermap.de", "freelancermap.com"),
    "gulp": ("gulp.de",),
    "freelance_de": ("freelance.de",),
    "hays": ("hays.de",),
    "xing": ("xing.com",),
    "malt": ("malt.de", "malt.com", "malt.fr"),
    "upwork": ("upwork.com",),
    "arc": ("arc.dev",),
    "ateam": ("a.team",),
    "solcom": ("solcom.de",),
}
_LINKEDIN_CHANNELS = ("linkedin_easy", "linkedin_offsite")


def platform_of(channel: str, url: str) -> str | None:
    """Платформа ліда: linkedin за каналом, platform_login за хостом URL."""
    if channel in _LINKEDIN_CHANNELS:
        return "linkedin"
    if channel != "platform_login":
        return None
    host = (urlparse(url or "").hostname or "").lower()
    for name, hosts in PLATFORM_HOSTS.items():
        if any(host == h or host.endswith("." + h) for h in hosts):
            return name
    return None


def _local(dt: datetime) -> datetime:
    """Місцевий час без зони; наївний лишається як є (вважається місцевим)."""
    return dt.astimezone().replace(tzinfo=None) if dt.tzinfo is not None else dt


def _switch(value: object) -> Switch:
    if value is True or value == "on":
        return "on"
    if value == "shadow":
        return "shadow"
    return "off"


def declared(targeting: dict) -> dict[str, Switch]:
    """targeting.yaml → platforms, значення нормалізовані до on/off/shadow."""
    return {str(k): _switch(v) for k, v in (targeting.get("platforms") or {}).items()}


def read_state(path: Path) -> dict[str, dict]:
    """Тимчасові записи; порожньо, коли файла немає або він не читається."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write(path: Path, state: dict[str, dict]) -> None:
    """Атомарний запис: тимчасовий файл у тій самій папці й os.replace. Прохід воркера і
    /platform пишуть той самий файл; обірваний запис інакше лишив би напівфайл, а read_state
    віддав би {} і зняв усі тимчасові off."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(state, ensure_ascii=False, indent=1))
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def set_off(path: Path, platform: str, reason: str, until: datetime, at: datetime) -> None:
    state = read_state(path)
    state[platform] = {"off_until": _local(until).strftime(TS_FORMAT), "reason": reason,
                       "at": _local(at).strftime(TS_FORMAT)}
    _write(path, state)


def set_shadow(path: Path, platform: str, until: datetime, at: datetime) -> None:
    state = read_state(path)
    state[platform] = {"shadow_until": _local(until).strftime(TS_FORMAT), "at": _local(at).strftime(TS_FORMAT)}
    _write(path, state)


def clear(path: Path, platform: str) -> None:
    state = read_state(path)
    if state.pop(platform, None) is not None:
        _write(path, state)


def _active(entry: dict, key: str, now: datetime) -> bool:
    raw = entry.get(key)
    if not raw:
        return False
    try:
        return datetime.strptime(str(raw), TS_FORMAT) > _local(now)
    except ValueError:
        return False


def _resolve(targeting: dict, path: Path, now: datetime) -> dict[str, tuple[Switch, str | None]]:
    """Платформа → (ефективний стан, причина тимчасового запису або None)."""
    state = read_state(path)
    out: dict[str, tuple[Switch, str | None]] = {}
    for name, sw in declared(targeting).items():
        entry = state.get(name)
        entry = entry if isinstance(entry, dict) else {}
        if _active(entry, "off_until", now):
            out[name] = ("off", str(entry.get("reason") or "temporary"))
        elif sw != "off" and _active(entry, "shadow_until", now):
            out[name] = ("shadow", "shadow")
        else:
            out[name] = (sw, None)
    return out


def effective(targeting: dict, path: Path, now: datetime) -> dict[str, Switch]:
    """Декларація з накладеними чинними тимчасовими записами."""
    return {k: v[0] for k, v in _resolve(targeting, path, now).items()}


def end_of_day(now: datetime) -> datetime:
    return now.replace(hour=23, minute=59, second=0, microsecond=0)


def first_of_next_month(now: datetime) -> datetime:
    year, month = (now.year + 1, 1) if now.month == 12 else (now.year, now.month + 1)
    return datetime(year, month, 1, 0, 0)


def limit_for(limits: dict[str, int], platform: str) -> int:
    return limits.get(platform, 0)


def status_lines(targeting: dict, path: Path, now: datetime, sent: dict[str, int],
                 limits: dict[str, int]) -> list[str]:
    """Рядок на оголошену платформу: «<назва>: <декларація>[ → <ефект> (<причина>)] <надіслано>/<ліміт>»."""
    decl = declared(targeting)
    lines = []
    for name, (eff, reason) in _resolve(targeting, path, now).items():
        mid = f"{decl[name]}" + (f" → {eff} ({reason})" if reason else "")
        lines.append(f"{name}: {mid} {sent.get(name, 0)}/{limit_for(limits, name)}")
    return lines
