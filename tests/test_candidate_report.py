"""report_mode: candidate — події з трекера для повного звіту кандидатці."""
from bot import candidate_report as cr
from bot import operator_report as rep

TRACKER = """# Application Tracker — erika

| # | Date | Company | Position | Source | Salary range | Status | Last update | Next action | Notes |
|---|---|---|---|---|---|---|---|---|---|
| 2026-10-08 | Empfang (m/w/d) | Alpha GmbH, Köln | sent 2026-10-08 (Msg 1a11ae48cb6c8d84) |
| 2026-10-08 | Teamassistenz (m/w/d) | RP Stellenmarkt | Beta AG, Köln | confirmed 2026-10-09 — Eingangsbestätigung, Rückmeldung bis 03.11. (Bew. Msg 1a11ae517d7e0d73 · Best. Msg 1a11ae53357fd22c); portal_submitted (StepStone) 2026-10-08 (Screenshot applications/2026-10-08/Beta/working/apply_confirmation.png) |
| 2026-10-08 | HR Assistant | Gamma SE, Köln | manual 2026-10-08 — nur mit Konto: https://jobs.example.com/apply/1 |
| 2026-10-08 | Assistenz Finance | Kappa GmbH, Köln | rejected 2026-10-12 — Absage (Msg 1a12f9845b03676f); portal_submitted (Firmen-ATS) 2026-10-08 |
| 2026-10-08 | Empfangsmitarbeiter | Eta Group, Köln | interview_invited 2026-10-08 — Einladung (Msg 1a11bec1417698b7) |
| 2026-10-13 | Kaufm. Assistenz | Lambda KG, Köln | portal_submitted (Karriereseite) 2026-10-13 — manuell beworben |
"""


def _rows(tmp_path):
    f = tmp_path / "tracker.md"
    f.write_text(TRACKER, encoding="utf-8")
    return rep.parse_rows(f)


def test_events_one_per_status_and_date(tmp_path):
    ev = {(e.company, e.kind, e.event_date) for e in cr.events(_rows(tmp_path))}
    assert ev == {
        ("Alpha GmbH", "submitted", "2026-10-08"),
        ("Beta AG", "confirmed", "2026-10-09"), ("Beta AG", "submitted", "2026-10-08"),
        ("Kappa GmbH", "rejected", "2026-10-12"), ("Kappa GmbH", "submitted", "2026-10-08"),
        ("Eta Group", "interview", "2026-10-08"),
        ("Lambda KG", "submitted", "2026-10-13"),
    }  # manual — не подія для кандидатки


def test_note_has_no_msg_ids_or_paths_and_channel_is_named(tmp_path):
    beta = next(e for e in cr.events(_rows(tmp_path)) if e.company == "Beta AG")
    assert "Msg" not in beta.note and "applications/" not in beta.note and "Screenshot" not in beta.note
    assert "Rückmeldung bis 03.11." in beta.note
    assert beta.channel == "StepStone" and beta.applied_on == "2026-10-08"
    alpha = next(e for e in cr.events(_rows(tmp_path)) if e.company == "Alpha GmbH")
    assert alpha.channel == "E-Mail"


def test_pending_skips_reported_and_too_old(tmp_path):
    rows, var = _rows(tmp_path), tmp_path / "var"
    new = cr.pending(rows, var, "2026-10-13")
    assert len(new) == 7  # усі в межах 14 днів
    assert cr.mark_reported(rows, var, "2026-10-13") == 7
    assert cr.pending(rows, var, "2026-10-13") == []
    # через місяць жодних старих подій, навіть без стану
    assert cr.pending(rows, tmp_path / "fresh", "2026-11-20") == []


def test_new_status_after_report_is_pending(tmp_path):
    rows, var = _rows(tmp_path), tmp_path / "var"
    cr.mark_reported(rows, var, "2026-10-13")
    f = tmp_path / "tracker.md"
    f.write_text(TRACKER.replace("sent 2026-10-08 (Msg 1a11ae48cb6c8d84)",
                                 "rejected 2026-10-14 — Absage; sent 2026-10-08 (Msg 1a11ae48cb6c8d84)"),
                 encoding="utf-8")
    new = cr.pending(rep.parse_rows(f), var, "2026-10-14")
    assert [(e.company, e.kind) for e in new] == [("Alpha GmbH", "rejected")]


def test_digest_groups_and_totals(tmp_path):
    d = cr.digest(_rows(tmp_path), tmp_path / "var", "2026-10-13")
    assert d["new_count"] == 7 and d["since"] == ""
    assert {e["company"] for e in d["submitted"]} == {"Alpha GmbH", "Beta AG", "Kappa GmbH", "Lambda KG"}
    assert [e["company"] for e in d["rejected"]] == ["Kappa GmbH"]
    assert d["totals"] == {"beworben_insgesamt": 5, "warten_auf_antwort": 3, "gespraeche": 1, "absagen": 1}
