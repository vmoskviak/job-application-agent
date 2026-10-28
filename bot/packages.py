"""Пакет на лід: папка var/run/packages_<дата>/<slug>/ з лідом, каналом,
нотаткою і списком вкладень за треком.

lead.json: запис ліда плюс channel, apply_url, ats, resolver_note, board_workplace і
board_location (формат роботи й локація з дошки ATS, None без пошуку дошки).
note.txt: тіло листа без теми (write_notes кладе тему першим рядком, тут її знімаємо).
meta.json: slug, lang, subject, attachments, created.
Жодного конфігу клієнта на імпорті: треки, вкладення й таргетинг приходять параметрами.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from bot.ats import Resolution
from bot.generators.cover_note import _agency_match, slugged

SKIPPED_FILE = "skipped.json"


@dataclass
class Package:
    slug: str
    dir: Path
    lead: dict
    channel: str
    apply_url: str
    ats: str | None
    lang: str
    subject: str
    note: str
    attachments: list[Path] = field(default_factory=list)


def attachments_for(entry: dict, tracks: dict, attachments_dir: Path, targeting: dict) -> list[Path]:
    """CV треку (tracks.yaml → tracks.<category>.cv_file) плюс файли для німецьких
    агентур (targeting.yaml → agency_attachments). Існування не перевіряється."""
    track = ((tracks.get("tracks") or {}).get(entry.get("category") or "") or {})
    out: list[Path] = []
    if track.get("cv_file"):
        out.append(Path(attachments_dir) / str(track["cv_file"]))
    if _agency_match(entry, list(targeting.get("agencies_de") or [])):
        out.extend(Path(attachments_dir) / str(name) for name in (targeting.get("agency_attachments") or []))
    return out


def _strip_subject(text: str, subject: str) -> str:
    """Перший абзац з одного рядка, рівний темі, це тема з write_notes, не тіло."""
    body = text.strip()
    head, sep, rest = body.partition("\n\n")
    if sep and "\n" not in head and head.strip() == subject.strip():
        return rest.strip()
    return body


def build_packages(selected: list[dict], resolutions: dict[str, Resolution], notes_dir: Path,
                   langmap: dict[str, str], packages_dir: Path, tracks: dict, attachments_dir: Path,
                   targeting: dict, subject_for: Callable[[dict, str], str]) -> list[Package]:
    """Папка на кожен відібраний лід, що має нотатку; решта в skipped.json."""
    packages_dir = Path(packages_dir)
    packages_dir.mkdir(parents=True, exist_ok=True)
    created = datetime.now(tz=timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M")
    out: list[Package] = []
    skipped: list[dict] = []
    for slug, entry in slugged(selected):
        note_path = Path(notes_dir) / f"{slug}.txt"
        if not note_path.exists():
            skipped.append({"slug": slug, "reason": "no_note"})
            continue
        lang = langmap.get(slug) or "en"
        subject = subject_for(entry, lang)
        note = _strip_subject(note_path.read_text(encoding="utf-8"), subject)
        res = resolutions.get(slug) or Resolution("none", entry.get("source_url") or "", None, "unresolved")
        attachments = attachments_for(entry, tracks, attachments_dir, targeting)
        d = packages_dir / slug
        d.mkdir(parents=True, exist_ok=True)
        lead = {**entry, "channel": res.channel, "apply_url": res.apply_url, "ats": res.ats,
                "resolver_note": res.note, "board_workplace": res.board_workplace,
                "board_location": res.board_location}
        (d / "lead.json").write_text(json.dumps(lead, ensure_ascii=False, indent=1), encoding="utf-8")
        (d / "note.txt").write_text(note + "\n", encoding="utf-8")
        (d / "meta.json").write_text(json.dumps(
            {"slug": slug, "lang": lang, "subject": subject, "attachments": [str(p) for p in attachments],
             "created": created}, ensure_ascii=False, indent=1), encoding="utf-8")
        out.append(Package(slug, d, lead, res.channel, res.apply_url, res.ats, lang, subject, note, attachments))
    (packages_dir / SKIPPED_FILE).write_text(json.dumps(skipped, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


def load_packages(packages_dir: Path) -> list[Package]:
    """Пакети з диска, за назвою папки."""
    out: list[Package] = []
    for d in sorted(Path(packages_dir).iterdir() if Path(packages_dir).is_dir() else []):
        if not (d / "lead.json").exists() or not (d / "meta.json").exists():
            continue
        lead = json.loads((d / "lead.json").read_text(encoding="utf-8"))
        meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
        note = (d / "note.txt").read_text(encoding="utf-8").strip() if (d / "note.txt").exists() else ""
        out.append(Package(meta["slug"], d, lead, lead.get("channel") or "none", lead.get("apply_url") or "",
                           lead.get("ats"), meta.get("lang") or "en", meta.get("subject") or "", note,
                           [Path(p) for p in meta.get("attachments") or []]))
    return out
