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


# ── LinkedIn for the employment market: Easy Apply through the worker queue ──

LI = "https://www.linkedin.com/jobs/view/4312345678"
ONSITE = '<a data-tracking-control-name="public_jobs_apply-link-onsite">Easy Apply</a>'
OFFSITE = '<a data-tracking-control-name="public_jobs_apply-link-offsite">Apply</a>'


def _linkedin(tmp_path, platforms=None, limits=None, channel="linkedin"):
    """erika with the given LinkedIn switch and limits, one LinkedIn lead with a rendered package."""
    c = _erika(tmp_path)
    targeting = {**c.targeting, "platforms": platforms or {}}
    if limits is not None:
        targeting["daily_limits"] = limits
    c = dataclasses.replace(c, targeting=targeting)
    d = _package(c, name="LinkedCo_Teamassistenz", source=LI)
    filtered = c.run_dir / f"filtered_alerts_{DAY}.json"
    filtered.write_text(json.dumps({"selected": [
        {"title": "Teamassistenz", "company": "LinkedCo", "source_url": LI, "liveness": "live",
         "apply_channel": channel}]}), encoding="utf-8")
    batch = c.run_dir / f"batch_alerts_{DAY}.json"
    batch.write_text(json.dumps([{"job": {"company": "LinkedCo", "title": "Teamassistenz", "source_url": LI},
                                  "tailored": {"anschreiben": "Sehr geehrte Damen und Herren, ich möchte mich bewerben."}}]),
                     encoding="utf-8")
    return c, d, [filtered], [batch]


def test_linkedin_easy_apply_lead_is_queued_with_platform_linkedin(tmp_path):
    c, d, filtered, batches = _linkedin(tmp_path, platforms={"linkedin": "on"}, limits={"form": 8, "linkedin": 5})
    out = enqueue_leads.run_employment(c, DAY, filtered, batches, c.applications, c.attachments,
                                       lambda u: ONSITE, "2026-10-28 03:30")
    assert out["queued"] == {"linkedin_easy": 1} and out["held"] == [] and out["skipped"] == []
    rec = JsonlStore(c.var / "state" / QUEUE_FILE).read()[0]
    assert rec["channel"] == "linkedin_easy" and rec["platform"] == "linkedin" and rec["apply_url"] == LI
    assert rec["package_dir"] == str(d)
    meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
    # the Easy Apply driver uploads the package's CV (ctx.resume) and takes the German letter as the note
    assert [Path(p).name for p in meta["attachments"]][:2] == ["Erika_Mustermann_CV.pdf", "01_Anschreiben.pdf"]
    assert (d / "note.txt").read_text(encoding="utf-8").startswith("Sehr geehrte")


def test_linkedin_external_apply_lead_is_queued_as_offsite(tmp_path):
    c, d, filtered, batches = _linkedin(tmp_path, platforms={"linkedin": "on"}, limits={"linkedin": 5})
    out = enqueue_leads.run_employment(c, DAY, filtered, batches, c.applications, c.attachments,
                                       lambda u: OFFSITE, "t")
    assert out["queued"] == {"linkedin_offsite": 1}
    rec = JsonlStore(c.var / "state" / QUEUE_FILE).read()[0]
    assert rec["platform"] == "linkedin" and rec["apply_url"] == LI


def test_linkedin_shadow_queues_for_the_shadow_pass(tmp_path):
    c, _, filtered, batches = _linkedin(tmp_path, platforms={"linkedin": "shadow"}, limits={"linkedin": 3})
    out = enqueue_leads.run_employment(c, DAY, filtered, batches, c.applications, c.attachments,
                                       lambda u: ONSITE, "t")
    assert out["queued"] == {"linkedin_easy": 1}


def test_linkedin_on_without_daily_limit_is_held_not_silently_parked(tmp_path):
    """Without daily_limits.linkedin the worker's limit is 0: the lead would stay queued for ever."""
    c, _, filtered, batches = _linkedin(tmp_path, platforms={"linkedin": "on"}, limits={"form": 8})
    out = enqueue_leads.run_employment(c, DAY, filtered, batches, c.applications, c.attachments,
                                       lambda u: ONSITE, "t")
    assert out["queued"] == {} and out["held"][0]["reasons"] == ["platform_limit_zero:linkedin"]


def test_legacy_manual_link_linkedin_lead_still_resolves(tmp_path):
    """filtered files written before the linkedin channel carry manual_link for LinkedIn."""
    c, _, filtered, batches = _linkedin(tmp_path, platforms={"linkedin": "on"}, limits={"linkedin": 5},
                                        channel="manual_link")
    out = enqueue_leads.run_employment(c, DAY, filtered, batches, c.applications, c.attachments,
                                       lambda u: ONSITE, "t")
    assert out["queued"] == {"linkedin_easy": 1}


# ── Portal leads the runner met as external applications go to the worker as employer forms ──

SS = "https://www.stepstone.de/stellenangebote--Sachbearbeitung-Koeln-Example-GmbH--1234567-inline.html"
EMPLOYER_APPLY = "https://karriere.example.org/de/jobposting/3fa9c1e07b/apply"
SS_HTML = '<script>{"applyUrl":"' + EMPLOYER_APPLY.replace("/", "\\/") + '"}</script>'
EXTERNAL = "pre-flight classified as external_ats"


def _portal(tmp_path, status="form_unsupported", reason=EXTERNAL, employer_url="", channel="stepstone_auto",
            url=SS, result=True):
    """erika with one portal lead, its rendered package and the runner's result in it."""
    from bot.apply.models import ApplyResult

    c = _erika(tmp_path)
    d = _package(c, name="ExampleGmbH_Sachbearbeitung", source=url)
    if result:
        ApplyResult(status, channel.split("_")[0], url, reason=reason, employer_url=employer_url).save(d)
    filtered = c.run_dir / f"filtered_{DAY}.json"
    filtered.write_text(json.dumps({"selected": [
        {"title": "Sachbearbeitung", "company": "Example GmbH", "source_url": url, "liveness": "live",
         "apply_channel": channel}]}), encoding="utf-8")
    batch = c.run_dir / f"batch_{DAY}.json"
    batch.write_text(json.dumps([{"job": {"company": "Example GmbH", "title": "Sachbearbeitung", "source_url": url},
                                  "tailored": {"anschreiben": "Sehr geehrte Damen und Herren, ich bewerbe mich."}}]),
                     encoding="utf-8")
    return c, d, [filtered], [batch]


def _run(c, filtered, batches, fetch):
    return enqueue_leads.run_employment(c, DAY, filtered, batches, c.applications, c.attachments, fetch, "t")


def test_external_stepstone_lead_with_employer_link_in_posting_is_queued_as_form(tmp_path):
    c, d, filtered, batches = _portal(tmp_path)
    out = _run(c, filtered, batches, lambda u: SS_HTML if u == SS else None)
    assert out["queued"] == {"form": 1} and out["held"] == [] and out["skipped"] == []
    rec = JsonlStore(c.var / "state" / QUEUE_FILE).read()[0]
    assert rec["channel"] == "form" and rec["apply_url"] == EMPLOYER_APPLY and rec["package_dir"] == str(d)
    lead = json.loads((d / "lead.json").read_text(encoding="utf-8"))
    assert lead["apply_url"] == EMPLOYER_APPLY and lead["source_url"] == SS and "stepstone" in lead["resolver_note"]
    assert (d / "note.txt").read_text(encoding="utf-8").startswith("Sehr geehrte")


def test_employer_url_recorded_by_the_runner_wins_without_a_fetch(tmp_path):
    c, _, filtered, batches = _portal(tmp_path, employer_url=EMPLOYER_APPLY)

    def fetch(url):
        raise AssertionError("the runner already had the employer URL")
    out = _run(c, filtered, batches, fetch)
    assert out["queued"] == {"form": 1}
    assert JsonlStore(c.var / "state" / QUEUE_FILE).read()[0]["apply_url"] == EMPLOYER_APPLY


def test_handoff_url_in_the_runner_reason_is_used(tmp_path):
    reason = f"StepStone передає на сайт роботодавця, не подано: {EMPLOYER_APPLY}"
    c, _, filtered, batches = _portal(tmp_path, reason=reason)
    out = _run(c, filtered, batches, lambda u: None)
    assert out["queued"] == {"form": 1}
    assert JsonlStore(c.var / "state" / QUEUE_FILE).read()[0]["apply_url"] == EMPLOYER_APPLY


def test_board_url_from_the_runner_is_never_queued(tmp_path):
    c, _, filtered, batches = _portal(tmp_path, employer_url="https://de.indeed.com/viewjob?jk=1")
    out = _run(c, filtered, batches, lambda u: None)
    assert out["queued"] == {} and out["held"] == [{"slug": "example-gmbh-sachbearbeitung",
                                                   "reasons": ["no_employer_url"]}]


def test_external_stepstone_lead_without_employer_link_is_held(tmp_path):
    c, d, filtered, batches = _portal(tmp_path)
    out = _run(c, filtered, batches, lambda u: "<html>no link</html>")
    assert out["queued"] == {}
    assert out["held"] == [{"slug": "example-gmbh-sachbearbeitung", "reasons": ["no_employer_url"]}]
    rec = JsonlStore(c.var / "state" / QUEUE_FILE).read()[0]
    assert rec["status"] == "held" and rec["channel"] == "none"


def test_external_lead_without_employer_link_takes_the_lookup_page(tmp_path):
    """02.11.2026: three StepStone/XING leads were held no_employer_url; the employer lookup finds the
    job on the employer's site and the lead is queued as a form."""
    from bot.apply.employer_lookup import Posting

    c, d, filtered, batches = _portal(tmp_path)
    asked = []
    out = enqueue_leads.run_employment(
        c, DAY, filtered, batches, c.applications, c.attachments, lambda u: "<html>no link</html>", "t",
        lookup=lambda company, title, city: (asked.append(company), Posting("https://careers.example.com/job/9"))[1])
    assert out["queued"] == {"form": 1} and out["held"] == [] and asked
    rec = JsonlStore(c.var / "state" / QUEUE_FILE).read()[0]
    assert rec["channel"] == "form" and rec["apply_url"] == "https://careers.example.com/job/9"
    lead = json.loads((d / "lead.json").read_text(encoding="utf-8"))
    assert lead["resolver_note"] == "employer page by lookup (board posting without an employer link)"


def test_external_xing_lead_is_handled_the_same_way(tmp_path):
    xing = "https://www.xing.com/jobs/koeln-sachbearbeitung-123456789"
    reason = "під заголовком немає Easy Apply: зовнішня подача на сайті роботодавця"
    c, _, filtered, batches = _portal(tmp_path, channel="xing_auto", url=xing, reason=reason,
                                      employer_url=EMPLOYER_APPLY)
    out = _run(c, filtered, batches, lambda u: None)
    assert out["queued"] == {"form": 1}


def test_portal_lead_the_runner_submitted_is_not_enqueued(tmp_path):
    c, _, filtered, batches = _portal(tmp_path, status="submitted", reason="")
    out = _run(c, filtered, batches, lambda u: SS_HTML)
    assert out == {"date": DAY, "packages": 0, "queued": {}, "held": [], "skipped": []}
    assert JsonlStore(c.var / "state" / QUEUE_FILE).read() == []


def test_portal_lead_stuck_inside_the_portal_form_is_not_enqueued(tmp_path):
    """A quick-apply form that failed half way is the portal's own form: the worker cannot fill it."""
    c, _, filtered, batches = _portal(tmp_path, reason="submit disabled — required fields left: filled=[]")
    out = _run(c, filtered, batches, lambda u: SS_HTML)
    assert out["queued"] == {} and out["held"] == [] and out["skipped"] == []


def test_portal_lead_without_runner_result_is_not_enqueued(tmp_path):
    c, _, filtered, batches = _portal(tmp_path, result=False)
    out = _run(c, filtered, batches, lambda u: SS_HTML)
    assert out["queued"] == {} and out["held"] == [] and out["skipped"] == []


def test_external_portal_lead_rerun_is_idempotent(tmp_path):
    c, _, filtered, batches = _portal(tmp_path)
    first = _run(c, filtered, batches, lambda u: SS_HTML)
    second = _run(c, filtered, batches, lambda u: SS_HTML)
    assert first["queued"] == {"form": 1} and second["queued"] == {}
    assert len(JsonlStore(c.var / "state" / QUEUE_FILE).read()) == 1


def test_portal_lead_for_the_same_job_as_a_manual_lead_is_not_queued_twice(tmp_path):
    c, _, filtered, batches = _portal(tmp_path)
    data = json.loads(filtered[0].read_text(encoding="utf-8"))
    data["selected"].insert(0, {**data["selected"][0], "source_url": "https://karriere.example.org/jobs/1",
                                "apply_channel": "manual_link"})
    filtered[0].write_text(json.dumps(data), encoding="utf-8")
    out = _run(c, filtered, batches, lambda u: SS_HTML)
    assert out["queued"] == {"form": 1} and out["held"] == []
    records = JsonlStore(c.var / "state" / QUEUE_FILE).read()
    assert len(records) == 1 and records[0]["apply_url"] == "https://karriere.example.org/jobs/1"


def test_manual_lead_with_a_stepstone_link_is_held_not_queued_as_a_board_page(tmp_path):
    c = _erika(tmp_path)
    _package(c, name="ExampleGmbH_Sachbearbeitung", source=SS)
    filtered = c.run_dir / "f.json"
    filtered.write_text(json.dumps({"selected": [
        {"title": "Sachbearbeitung", "company": "Example GmbH", "source_url": SS, "liveness": "live",
         "apply_channel": "manual_link"}]}), encoding="utf-8")
    out = _run(c, [filtered], [], lambda u: "<html></html>")
    assert out["queued"] == {} and out["held"][0]["reasons"] == ["no_employer_url"]
