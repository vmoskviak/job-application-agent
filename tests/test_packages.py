"""Пакети лідів: папка на лід з каналом, нотаткою і вкладеннями за треком."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from bot.ats import Resolution
from bot.packages import attachments_for, build_packages, load_packages

TRACKS = {"tracks": {"android": {"cv_file": "CV_Android.pdf"}, "ai": {"cv_file": "CV_AI.pdf"}}}
TARGETING = {"agencies_de": ["hays.de"], "agency_attachments": ["Project_List_DE.pdf"]}
SLUG = "contract-co-senior-android-engineer"


def test_attachments_for_track_and_agency(tmp_path):
    plain = {"category": "android", "source_url": "https://x.example"}
    assert attachments_for(plain, TRACKS, tmp_path, TARGETING) == [tmp_path / "CV_Android.pdf"]
    agency = {"category": "ai", "email": "job@hays.de", "source_url": "https://hays.de/j/1"}
    assert attachments_for(agency, TRACKS, tmp_path, TARGETING) == [
        tmp_path / "CV_AI.pdf", tmp_path / "Project_List_DE.pdf"]


def test_attachments_for_unknown_track_is_empty(tmp_path):
    assert attachments_for({"category": "other"}, TRACKS, tmp_path, TARGETING) == []


def _selected():
    return [
        {"title": "Senior Android Engineer", "company": "Contract Co", "category": "android",
         "source_url": "https://www.linkedin.com/jobs/view/1/"},
        {"title": "Mobile Dev", "company": "No Note Inc", "category": "android",
         "source_url": "https://x.example/2"},
    ]


def test_build_and_load_packages(tmp_path):
    notes = tmp_path / "notes_2026-10-26"
    notes.mkdir()
    (notes / f"{SLUG}.txt").write_text(
        "Hello Contract Co team,\n\nBody.\n\nBest regards\nJordan Example\n", encoding="utf-8")
    res = {SLUG: Resolution("form", "https://boards.greenhouse.io/contractco/jobs/123", "greenhouse", "board contractco")}
    pkgs = build_packages(_selected(), res, notes, {SLUG: "en"}, tmp_path / "packages", TRACKS,
                          tmp_path, TARGETING, lambda e, lang: f"Contract: {e['title']}")
    assert [p.slug for p in pkgs] == [SLUG]
    p = pkgs[0]
    assert p.channel == "form" and p.ats == "greenhouse" and p.lang == "en"
    assert p.subject == "Contract: Senior Android Engineer"
    assert p.apply_url == "https://boards.greenhouse.io/contractco/jobs/123"
    assert p.dir == tmp_path / "packages" / SLUG
    lead = json.loads((p.dir / "lead.json").read_text(encoding="utf-8"))
    assert lead["company"] == "Contract Co" and lead["channel"] == "form" and lead["resolver_note"] == "board contractco"
    assert json.loads((tmp_path / "packages" / "skipped.json").read_text(encoding="utf-8")) == [
        {"slug": "no-note-inc-mobile-dev", "reason": "no_note"}]
    again = load_packages(tmp_path / "packages")
    assert len(again) == 1 and again[0].note.startswith("Hello Contract Co team")
    assert again[0].attachments == [tmp_path / "CV_Android.pdf"]
    assert again[0].subject == p.subject and again[0].lead["title"] == "Senior Android Engineer"


def test_build_strips_subject_line_from_note_file(tmp_path):
    """write_notes кладе тему першим рядком; у тілі листа її бути не повинно."""
    notes = tmp_path / "notes_2026-10-26"
    notes.mkdir()
    (notes / f"{SLUG}.txt").write_text(
        "Contract: Senior Android Engineer\n\nHello Contract Co team,\n\nBody.\n", encoding="utf-8")
    res = {SLUG: Resolution("none", "https://www.linkedin.com/jobs/view/1/", None, "board not found")}
    pkgs = build_packages(_selected()[:1], res, notes, {SLUG: "en"}, tmp_path / "packages", TRACKS,
                          tmp_path, TARGETING, lambda e, lang: f"Contract: {e['title']}")
    assert pkgs[0].note == "Hello Contract Co team,\n\nBody."
    assert (pkgs[0].dir / "note.txt").read_text(encoding="utf-8") == "Hello Contract Co team,\n\nBody.\n"


def test_build_missing_resolution_is_channel_none(tmp_path):
    notes = tmp_path / "notes_2026-10-26"
    notes.mkdir()
    (notes / f"{SLUG}.txt").write_text("Hello,\n\nBody.\n", encoding="utf-8")
    pkgs = build_packages(_selected()[:1], {}, notes, {}, tmp_path / "packages", TRACKS,
                          tmp_path, TARGETING, lambda e, lang: "S")
    assert pkgs[0].channel == "none" and pkgs[0].lang == "en"


def test_board_workplace_and_location_go_to_lead_json(tmp_path):
    """Формат роботи й локація з дошки лягають у lead.json поруч із channel і ats."""
    notes = tmp_path / "notes_2026-10-26"
    notes.mkdir()
    (notes / f"{SLUG}.txt").write_text("Hello,\n\nBody.\n", encoding="utf-8")
    res = {SLUG: Resolution("form", "https://jobs.ashbyhq.com/contractco/abc", "ashby", "board contractco",
                            "hybrid", "San Francisco, United States")}
    pkgs = build_packages(_selected()[:1], res, notes, {SLUG: "en"}, tmp_path / "packages", TRACKS,
                          tmp_path, TARGETING, lambda e, lang: "S")
    lead = json.loads((pkgs[0].dir / "lead.json").read_text(encoding="utf-8"))
    assert lead["board_workplace"] == "hybrid" and lead["board_location"] == "San Francisco, United States"
    assert pkgs[0].lead["board_workplace"] == "hybrid"
    assert load_packages(tmp_path / "packages")[0].lead["board_location"] == "San Francisco, United States"
    pkgs = build_packages(_selected()[:1], {}, notes, {}, tmp_path / "packages2", TRACKS,
                          tmp_path, TARGETING, lambda e, lang: "S")
    assert pkgs[0].lead["board_workplace"] is None and pkgs[0].lead["board_location"] is None
