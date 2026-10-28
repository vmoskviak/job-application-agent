"""Що з трекера кандидатка ще не бачила (report_mode: candidate).

Повний звіт кандидатці показує все нове з минулого звіту: заявки цього рану,
заявки, які оператор подав руками вдень, і всі відповіді фірм. Пам'ять
агента для цього ненадійна, тому події рахуються з трекера детерміновано:
кожна пара «статус дата» у клітинці статусу (`sent 2026-10-08`,
`portal_submitted (StepStone) 2026-10-08`, `rejected 2026-10-12`, ...) є
подією. Подію, яку вже показали, записано у `var/candidate_report_state.json`.

    js scripts/candidate_report.py --json            нове з минулого звіту
    js scripts/candidate_report.py --mark-reported   після відправки звіту
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path

from bot import operator_report as rep

STATE_FILE = "candidate_report_state.json"
# Старіші події при першому звіті (або після довгої паузи) не вивалюються пачкою.
MAX_AGE_DAYS = 14

_EVENT_RE = re.compile(
    r"(?<![\w-])(sent|portal_submitted|confirmed|rejected|offer|"
    r"interview(?:_invited|_scheduled)?)\b(?:\s*\([^)]*\))?\s+(\d{4}-\d{2}-\d{2})")
_KIND = {"sent": "submitted", "portal_submitted": "submitted", "confirmed": "confirmed",
         "rejected": "rejected", "offer": "offer"}
_CHANNEL_RE = re.compile(r"portal_submitted\s*\(([^)]*)\)")
# Внутрішні сліди, яких кандидатка бачити не повинна
_NOISE_RES = (
    re.compile(r"\(?(?:Bew\.\s*|Best\.\s*)?Msg\s+[0-9a-f]{8,}\)?"),
    re.compile(r"\(?Screenshot[^)]*\)?"),
    re.compile(r"applications/\S+"),
)


@dataclass
class Event:
    key: str
    kind: str           # submitted | confirmed | rejected | interview | offer
    event_date: str
    company: str
    title: str
    applied_on: str
    channel: str
    note: str           # текст статусу без Msg-ID і шляхів, для формулювання деталей


def _clean(text: str, limit: int = 400) -> str:
    for rx in _NOISE_RES:
        text = rx.sub("", text)
    text = re.sub(r"\s+([,;.])", r"\1", text)
    text = re.sub(r"\s{2,}", " ", text).strip(" ;·—")
    return text[:limit].rstrip() + ("…" if len(text) > limit else "")


def events(rows: list[rep.Row]) -> list[Event]:
    out: list[Event] = []
    for r in rows:
        m_ch = _CHANNEL_RE.search(r.status)
        channel = m_ch.group(1).strip() if m_ch else ("E-Mail" if re.search(r"\bsent\b", r.status) else "")
        seen: set[str] = set()
        for m in _EVENT_RE.finditer(r.status):
            word, day = m.group(1), m.group(2)
            kind = "interview" if word.startswith("interview") else _KIND[word]
            key = f"{r.day}|{r.company}|{r.title}|{kind}|{day}"
            if key in seen:
                continue
            seen.add(key)
            out.append(Event(key, kind, day, r.company, r.title, r.submitted_on,
                             channel, _clean(r.status)))
    return out


def _state_path(var: Path) -> Path:
    return var / STATE_FILE


def load_state(var: Path) -> dict:
    path = _state_path(var)
    if not path.exists():
        return {"reported": [], "last_sent": ""}
    return json.loads(path.read_text(encoding="utf-8"))


def pending(rows: list[rep.Row], var: Path, today: str) -> list[Event]:
    """Події, яких ще не було в жодному звіті, не старші MAX_AGE_DAYS."""
    reported = set(load_state(var).get("reported", []))
    floor = (date.fromisoformat(today) - timedelta(days=MAX_AGE_DAYS)).isoformat()
    return [e for e in events(rows) if e.key not in reported and floor <= e.event_date <= today]


def mark_reported(rows: list[rep.Row], var: Path, today: str) -> int:
    """Усі поточні події позначаються показаними. Повертає кількість нових."""
    state = load_state(var)
    reported = set(state.get("reported", []))
    keys = {e.key for e in events(rows)}
    added = len(keys - reported)
    var.mkdir(parents=True, exist_ok=True)
    _state_path(var).write_text(json.dumps(
        {"reported": sorted(reported | keys), "last_sent": today},
        ensure_ascii=False, indent=1), encoding="utf-8")
    return added


def digest(rows: list[rep.Row], var: Path, today: str) -> dict:
    """Дані для повного звіту: нові події за типом і загальний стан."""
    new = pending(rows, var, today)
    by_kind: dict[str, list[dict]] = {}
    for e in sorted(new, key=lambda e: (e.event_date, e.company)):
        by_kind.setdefault(e.kind, []).append(asdict(e))
    s = rep.summarize(rows, today)
    waiting = [r for r in rows if r.kind == "submitted"]
    return {
        "today": today,
        "since": load_state(var).get("last_sent", ""),
        "new_count": len(new),
        "submitted": by_kind.get("submitted", []),
        "confirmed": by_kind.get("confirmed", []),
        "rejected": by_kind.get("rejected", []),
        "interview": by_kind.get("interview", []),
        "offer": by_kind.get("offer", []),
        "totals": {
            "beworben_insgesamt": s["submitted_total"],
            "warten_auf_antwort": len(waiting),
            "gespraeche": len(s["interviews"]),
            "absagen": len(s["rejected"]),
        },
    }
