import json
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from bot.cleanup import (
    MIN_TRACKER_ROWS, TrackerRow, auto_ghost, dedupe_static_pdfs,
    delete_terminal_folders, parse_tracker_rows, resolve_app_dir,
    rotate_dir, run_cleanup, _clean,
)

TODAY = date(2026, 7, 14)

HEADER = "| # | Date | Company | Position | Source | Salary range | Status | Last update | Next action | Notes |\n" \
         "|---|------|---------|----------|--------|--------------|--------|-------------|-------------|-------|\n"


def row(n, d, company, position, status, last, notes=""):
    return f"| {n} | {d} | {company} | {position} | Indeed | ~42K | {status} | {last} | — | {notes} |\n"


def make_tracker(tmp_path, lines):
    tracker = tmp_path / "tracker.md"
    tracker.write_text(HEADER + "".join(lines), encoding="utf-8")
    return tracker


def make_app(tmp_path, d, folder, files=("to_send/01_Anschreiben.pdf",)):
    app = tmp_path / "applications" / d / folder
    for f in files:
        p = app / f
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"pdf")
    return app


# ── auto_ghost ────────────────────────────────────────────────────────────────

def test_auto_ghost_after_45_days(tmp_path):
    tracker = make_tracker(tmp_path, [
        row(1, "2026-05-01", "Alte GmbH", "Junior BA", "sent", "2026-05-20"),
    ])
    ghosted = auto_ghost(tracker, TODAY)
    assert len(ghosted) == 1
    assert ghosted[0]["company"] == "Alte GmbH"
    assert ghosted[0]["silent_days"] == 55
    text = tracker.read_text()
    assert " ghosted " in text and " sent " not in text
    assert " 2026-07-14 " in text


def test_auto_ghost_recent_untouched(tmp_path):
    tracker = make_tracker(tmp_path, [
        row(1, "2026-06-20", "Fresh AG", "Junior BA", "confirmed", "2026-06-25"),
    ])
    assert auto_ghost(tracker, TODAY) == []
    assert "confirmed" in tracker.read_text()


def test_auto_ghost_interview_never(tmp_path):
    tracker = make_tracker(tmp_path, [
        row(1, "2026-04-01", "Slow GmbH", "BA", "interview_1", "2026-04-10"),
        row(2, "2026-04-01", "Offer AG", "BA", "offer", "2026-04-10"),
    ])
    assert auto_ghost(tracker, TODAY) == []


def test_auto_ghost_portal_submitted_with_suffix(tmp_path):
    tracker = make_tracker(tmp_path, [
        row(1, "2026-05-01", "Portal AG", "BA", "portal_submitted (портал)", "2026-05-10"),
    ])
    ghosted = auto_ghost(tracker, TODAY)
    assert len(ghosted) == 1 and ghosted[0]["old_status"] == "portal_submitted"


def test_auto_ghost_legacy_row(tmp_path):
    tracker = tmp_path / "tracker.md"
    tracker.write_text(
        "| 2026-05-01 | Junior BA | Legacy GmbH, Köln | ✅ confirmed |\n",
        encoding="utf-8")
    ghosted = auto_ghost(tracker, TODAY)
    assert len(ghosted) == 1
    assert "ghosted (2026-07-14)" in tracker.read_text()


def test_auto_ghost_dry_run_keeps_tracker(tmp_path):
    tracker = make_tracker(tmp_path, [
        row(1, "2026-05-01", "Alte GmbH", "BA", "sent", "2026-05-20"),
    ])
    before = tracker.read_text()
    ghosted = auto_ghost(tracker, TODAY, dry_run=True)
    assert len(ghosted) == 1
    assert tracker.read_text() == before


# ── resolve_app_dir ───────────────────────────────────────────────────────────

def _row(d, company, position, notes=""):
    return TrackerRow(0, "main", d, company, position, "rejected",
                      "rejected", d, notes)


def test_resolve_from_notes_path(tmp_path):
    app = make_app(tmp_path, "2026-05-28", "Silbertax_Teamassistenz")
    r = _row("2026-05-28", "Silbertax GmbH", "Teamassistenz",
             notes="пакет: applications/2026-05-28/Silbertax_Teamassistenz/to_send/ · ще текст")
    resolved, reason = resolve_app_dir(r, tmp_path / "applications")
    assert resolved == app and reason == "notes"


def test_resolve_by_date_and_slug(tmp_path):
    app = make_app(tmp_path, "2026-06-08", "MusterITGmbH_JuniorConsultantHealth")
    r = _row("2026-06-08", "MusterIT GmbH", "Junior Consultant Healthcare IT")
    resolved, reason = resolve_app_dir(r, tmp_path / "applications")
    assert resolved == app and reason == "date_slug"


def test_resolve_ambiguous_same_company_twice(tmp_path):
    make_app(tmp_path, "2026-05-12", "Beispielwerk_AIAnalyst")
    make_app(tmp_path, "2026-05-12", "Beispielwerk_BAConsultantERP")
    r = _row("2026-05-12", "Beispielwerk", "BA Consultant ERP-Systeme")
    resolved, reason = resolve_app_dir(r, tmp_path / "applications")
    assert resolved is not None and resolved.name == "Beispielwerk_BAConsultantERP"

    r2 = _row("2026-05-12", "Beispielwerk", "щось без перетину токенів")
    resolved2, reason2 = resolve_app_dir(r2, tmp_path / "applications")
    assert resolved2 is None and reason2 == "ambiguous"


def test_resolve_not_found(tmp_path):
    (tmp_path / "applications").mkdir()
    r = _row("2026-05-12", "Unbekannt AG", "BA")
    resolved, reason = resolve_app_dir(r, tmp_path / "applications")
    assert resolved is None and reason == "not_found"


def test_slug_parity_with_make_folder_name():
    from new_app import make_folder_name
    company, title = "Test & Söhne GmbH (m/w/d)", "Junior Büro-Assistenz (m/w/d)"
    assert make_folder_name(company, title) == f"{_clean(company)}_{_clean(title)}"


# ── delete_terminal_folders ───────────────────────────────────────────────────

def test_delete_terminal_folder(tmp_path):
    app = make_app(tmp_path, "2026-05-01", "AlteGmbH_JuniorBA")
    tracker = make_tracker(tmp_path, [
        row(1, "2026-05-01", "Alte GmbH", "Junior BA", "rejected", "2026-06-01"),
    ])
    result = delete_terminal_folders(tracker, tmp_path / "applications", TODAY)
    assert len(result["deleted"]) == 1
    assert not app.exists()
    assert not (tmp_path / "applications" / "2026-05-01").exists()  # порожня date-тека


def test_never_deletes_active(tmp_path):
    app = make_app(tmp_path, "2026-06-01", "AktivAG_ScrumMaster")
    tracker = make_tracker(tmp_path, [
        row(1, "2026-06-01", "Aktiv AG", "Scrum Master", "interview_1", "2026-07-01"),
    ])
    result = delete_terminal_folders(tracker, tmp_path / "applications", TODAY)
    assert result["deleted"] == [] and app.exists()


def test_never_deletes_today(tmp_path):
    app = make_app(tmp_path, TODAY.isoformat(), "HeuteAG_BA")
    tracker = make_tracker(tmp_path, [
        row(1, TODAY.isoformat(), "Heute AG", "BA", "rejected", TODAY.isoformat()),
    ])
    result = delete_terminal_folders(tracker, tmp_path / "applications", TODAY)
    assert result["deleted"] == [] and app.exists()


def test_delete_cap_respected(tmp_path):
    lines = []
    for i in range(5):
        make_app(tmp_path, "2026-05-01", f"Firma{i}xx_BA")
        lines.append(row(i, "2026-05-01", f"Firma{i}xx", "BA", "rejected", "2026-06-01"))
    tracker = make_tracker(tmp_path, lines)
    result = delete_terminal_folders(tracker, tmp_path / "applications", TODAY,
                                     max_delete=2)
    assert len(result["deleted"]) == 2 and result["capped"] is True


def test_delete_dry_run_changes_nothing(tmp_path):
    app = make_app(tmp_path, "2026-05-01", "AlteGmbH_JuniorBA")
    tracker = make_tracker(tmp_path, [
        row(1, "2026-05-01", "Alte GmbH", "Junior BA", "rejected", "2026-06-01"),
    ])
    result = delete_terminal_folders(tracker, tmp_path / "applications", TODAY,
                                     dry_run=True)
    assert len(result["deleted"]) == 1 and app.exists()


def test_unmatched_reported_not_deleted(tmp_path):
    (tmp_path / "applications").mkdir()
    tracker = make_tracker(tmp_path, [
        row(1, "2026-05-01", "Verschwunden AG", "BA", "rejected", "2026-06-01"),
    ])
    result = delete_terminal_folders(tracker, tmp_path / "applications", TODAY)
    assert result["deleted"] == []
    assert result["unmatched"][0]["reason"] == "not_found"


# ── dedupe_static_pdfs ────────────────────────────────────────────────────────

def test_dedupe_removes_exact_copy_keeps_mismatch(tmp_path, monkeypatch):
    import bot.generators.cv_engine as cv_engine
    monkeypatch.setattr(cv_engine, "ZEUGNIS_FILES", ["03_Zeugnis_Test.pdf"])
    canonical_dir = tmp_path / "attachments"
    canonical_dir.mkdir()
    (canonical_dir / "03_Zeugnis_Test.pdf").write_bytes(b"CANON")

    exact = make_app(tmp_path, "2026-05-01", "A_BA",
                     files=("to_send/03_Zeugnis_Test.pdf",))
    (exact / "to_send/03_Zeugnis_Test.pdf").write_bytes(b"CANON")
    other = make_app(tmp_path, "2026-05-01", "B_BA",
                     files=("to_send/03_Zeugnis_Test.pdf",))
    (other / "to_send/03_Zeugnis_Test.pdf").write_bytes(b"DIFFERENT")

    result = dedupe_static_pdfs(tmp_path / "applications", canonical_dir)
    assert result["removed"] == 1
    assert not (exact / "to_send/03_Zeugnis_Test.pdf").exists()
    assert (other / "to_send/03_Zeugnis_Test.pdf").exists()
    assert len(result["mismatched"]) == 1


# ── rotate_dir ────────────────────────────────────────────────────────────────

def test_rotate_by_filename_date(tmp_path):
    d = tmp_path / "var" / "run"
    d.mkdir(parents=True)
    (d / "digest_2026-06-01.json").write_text("{}")
    (d / "digest_2026-07-10.json").write_text("{}")
    rotated = rotate_dir(d, ["*.json"], 14, TODAY)
    assert rotated == ["digest_2026-06-01.json"]
    assert (d / "digest_2026-07-10.json").exists()


# ── run_cleanup ───────────────────────────────────────────────────────────────

def _big_tracker_lines():
    lines = [row(i, "2026-06-20", f"Firma{i}xx", "BA", "sent", "2026-07-01")
             for i in range(MIN_TRACKER_ROWS)]
    lines.append(row(99, "2026-05-01", "Tot GmbH", "Junior BA", "rejected", "2026-06-01"))
    return lines


def _project(tmp_path):
    tracker = tmp_path / "tracker.md"
    tracker.write_text(HEADER + "".join(_big_tracker_lines()), encoding="utf-8")
    make_app(tmp_path, "2026-05-01", "TotGmbH_JuniorBA")
    return tmp_path


def test_run_cleanup_report_shape(tmp_path):
    project = _project(tmp_path)
    report = run_cleanup(project, today=TODAY)
    assert report["errors"] == []
    assert {"ghosted", "deleted", "unmatched", "capped", "dedupe", "rotated"} <= set(report)
    assert len(report["deleted"]) == 1
    assert (project / "var" / "run" / "cleanup_2026-07-14.json").exists()


def test_rerun_same_day_merges_ghosted_and_deleted(tmp_path):
    """Регресія 2026-07-16: агент перезапустив --cleanup, щоб догорнути довгий
    stdout, і другий (уже no-op) запуск перезаписав денний JSON нулями."""
    lines = [row(i, "2026-06-20", f"Firma{i}xx", "BA", "sent", "2026-07-01")
             for i in range(MIN_TRACKER_ROWS)]
    lines.append(row(97, "2026-05-01", "Stille AG", "Junior BA", "sent", "2026-05-20"))
    lines.append(row(98, "2026-05-01", "Tot GmbH", "Junior BA", "rejected", "2026-06-01"))
    tracker = tmp_path / "tracker.md"
    tracker.write_text(HEADER + "".join(lines), encoding="utf-8")
    make_app(tmp_path, "2026-05-01", "TotGmbH_JuniorBA")
    make_app(tmp_path, "2026-05-01", "StilleAG_JuniorBA")

    first = run_cleanup(tmp_path, today=TODAY)
    assert len(first["ghosted"]) == 1
    assert len(first["deleted"]) == 2

    second = run_cleanup(tmp_path, today=TODAY)
    assert len(second["ghosted"]) == 1
    assert len(second["deleted"]) == 2
    assert second["runs"] == 2

    saved = json.loads(
        (tmp_path / "var" / "run" / "cleanup_2026-07-14.json").read_text(encoding="utf-8"))
    assert len(saved["ghosted"]) == 1 and len(saved["deleted"]) == 2


def test_compact_report_collapses_unmatched():
    from bot.cleanup import compact_report
    report = {"date": "2026-07-14", "deleted": [], "unmatched": [
        {"company": "A", "reason": "not_found"},
        {"company": "B", "reason": "ambiguous"},
        {"company": "C", "reason": "not_found"},
    ]}
    compact = compact_report(report)
    assert compact["unmatched"] == {"count": 3,
                                    "by_reason": {"not_found": 2, "ambiguous": 1}}
    assert compact["deleted"] == []
    assert report["unmatched"][0]["company"] == "A"  # оригінал не мутується


def test_run_cleanup_sanity_abort_on_tiny_tracker(tmp_path):
    (tmp_path / "docs").mkdir()
    tracker = tmp_path / "docs" / "07_application_tracker.md"
    tracker.write_text(HEADER + row(1, "2026-05-01", "Einzig AG", "BA", "rejected", "2026-06-01"),
                       encoding="utf-8")
    app = make_app(tmp_path, "2026-05-01", "EinzigAG_BA")
    report = run_cleanup(tmp_path, today=TODAY)
    assert report["errors"] and app.exists()
    assert "deleted" not in report
