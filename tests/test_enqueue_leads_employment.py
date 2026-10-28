"""scripts/enqueue_leads.py на ринку найму: manual_link-ліди з готовими пакетами → черга воркера."""
from __future__ import annotations

import dataclasses
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import enqueue_leads

from bot import client as _client
from bot.sendqueue import QUEUE_FILE
from bot.state import JsonlStore

EX = Path(__file__).parent.parent / "examples" / "clients"
DAY = "2026-10-28"
SRC = "https://karriere.beispielwerk.de/jobs/4711?utm=alert"
TRACKER = """# Tracker

| # | Date | Company | Position | Source | Salary range | Status | Last update | Next action | Notes |
|---|---|---|---|---|---|---|---|---|---|
| 2026-10-28 | Teamassistenz | Beispielwerk GmbH, Düsseldorf | 🔄 Файли готові — подати вручну · https://karriere.beispielwerk.de/jobs/4711 |
"""


def _erika(tmp_path):
    c = dataclasses.replace(_client.load("erika", base=EX), root=tmp_path / "erika")
    (c.root / "var" / "run").mkdir(parents=True)
    c.tracker.write_text(TRACKER, encoding="utf-8")
    att = c.root / "attachments"
    att.mkdir()
    for a in c.cv["attachments"]:
        (att / a["file"]).write_bytes(b"%PDF-1.4")
    return c


def _package(c, name="BeispielwerkGmbH_Teamassistenz", source=SRC):
    d = c.applications / DAY / name
    (d / "to_send").mkdir(parents=True)
    (d / "working").mkdir()
    (d / "to_send" / "Erika_Mustermann_CV.pdf").write_bytes(b"%PDF-1.4")
    (d / "to_send" / "01_Anschreiben.pdf").write_bytes(b"%PDF-1.4")
    (d / "working" / "job_description.txt").write_text(f"Source: {source}\n\nText", encoding="utf-8")
    return d


def _inputs(c):
    filtered = c.run_dir / f"filtered_{DAY}.json"
    filtered.write_text(json.dumps({"selected": [
        {"title": "Teamassistenz", "company": "Beispielwerk GmbH", "category": "B1_hausverwaltung", "score": 40,
         "source_url": SRC, "liveness": "live", "apply_channel": "manual_link"},
        {"title": "Sachbearbeitung", "company": "Portal AG", "source_url": "https://www.stepstone.de/j/1",
         "liveness": "live", "apply_channel": "stepstone_auto"},
        {"title": "Empfang", "company": "Ohne Paket KG", "source_url": "https://ohne.example/j/2",
         "liveness": "live", "apply_channel": "manual_link"},
    ]}), encoding="utf-8")
    batch = c.run_dir / f"batch_{DAY}.json"
    batch.write_text(json.dumps([{"job": {"company": "Beispielwerk GmbH", "title": "Teamassistenz", "source_url": SRC},
                                  "tailored": {"anschreiben": "Sehr geehrte Damen und Herren, ü."}}]), encoding="utf-8")
    return [filtered], [batch]


def test_manual_link_lead_with_package_is_queued_as_generic_form(tmp_path):
    c = _erika(tmp_path)
    d = _package(c)
    filtered, batches = _inputs(c)
    out = enqueue_leads.run_employment(c, DAY, filtered, batches, c.applications, c.attachments,
                                       lambda u: None, "2026-10-28 03:30")
    assert out["queued"] == {"form": 1} and out["held"] == []
    assert out["skipped"] == [{"slug": "ohne-paket-kg-empfang", "reason": "no_package"}]
    rec = JsonlStore(c.var / "state" / QUEUE_FILE).read()[0]
    assert rec["package_dir"] == str(d) and rec["channel"] == "form" and rec["apply_url"] == SRC
    lead = json.loads((d / "lead.json").read_text(encoding="utf-8"))
    assert lead["ats"] is None and "generic form" in lead["resolver_note"]
    meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
    names = [Path(p).name for p in meta["attachments"]]
    assert names[:2] == ["Erika_Mustermann_CV.pdf", "01_Anschreiben.pdf"]
    assert "01_Zeugnis_BeispielLogistik_2025.pdf" in names and "03_Bachelorzeugnis_2017.pdf" not in names
    assert (d / "note.txt").read_text(encoding="utf-8").startswith("Sehr geehrte")


def test_ready_tracker_row_is_not_a_duplicate_but_sent_row_is(tmp_path):
    c = _erika(tmp_path)
    _package(c)
    filtered, batches = _inputs(c)
    c.tracker.write_text(TRACKER.replace("🔄 Файли готові — подати вручну", "portal_submitted (портал) 2026-10-20"),
                         encoding="utf-8")
    out = enqueue_leads.run_employment(c, DAY, filtered, batches, c.applications, c.attachments,
                                       lambda u: None, "t")
    assert out["queued"] == {} and out["held"][0]["reasons"] == ["duplicate_in_tracker"]


def test_package_found_by_folder_name_and_rerun_is_idempotent(tmp_path):
    c = _erika(tmp_path)
    _package(c, source="https://other.example/x")   # Source не збігається → за назвою папки
    filtered, batches = _inputs(c)
    args = (c, DAY, filtered, batches, c.applications, c.attachments, lambda u: None, "t")
    first = enqueue_leads.run_employment(*args)
    second = enqueue_leads.run_employment(*args)
    assert first["queued"] == {"form": 1} and second["queued"] == {}
    assert len(JsonlStore(c.var / "state" / QUEUE_FILE).read()) == 1


def test_linkedin_lead_is_held_until_platform_is_on(tmp_path):
    c = _erika(tmp_path)
    _package(c, name="LinkedCo_Assistenz", source="https://www.linkedin.com/jobs/view/5/")
    filtered = c.run_dir / "f.json"
    filtered.write_text(json.dumps({"selected": [
        {"title": "Assistenz", "company": "LinkedCo", "source_url": "https://www.linkedin.com/jobs/view/5/",
         "liveness": "live", "apply_channel": "manual_link"}]}), encoding="utf-8")
    onsite = '<button data-tracking-control-name="public_jobs_apply-link-onsite">Easy Apply</button>'
    out = enqueue_leads.run_employment(c, DAY, [filtered], [], c.applications, c.attachments, lambda u: onsite, "t")
    assert out["queued"] == {} and out["held"][0]["reasons"] == ["platform_off:linkedin"]
