"""scripts/enqueue_leads.py: selected + нотатки → пакети → перевірки → черга, без мережі."""
from __future__ import annotations

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
ONSITE_HTML = '<button data-tracking-control-name="public_jobs_apply-link-onsite">Easy Apply</button>'
SLUG_EMAIL = "contract-co-senior-android-engineer"
SLUG_LI = "mobile-co-android-developer"
OFFSITE_HTML = '<a data-tracking-control-name="public_jobs_apply-link-offsite">Apply</a>'
SLUG_ASHBY = "ashby-co-android-developer"


class NoBoard:
    def get_json(self, url):
        return None


class AshbyBoard:
    """Дошка Ashby компанії ashbyco з фікстури: гібридний постинг у San Francisco."""

    def get_json(self, url):
        from bot.ats import BOARD_URLS
        if url != BOARD_URLS["ashby"].format(slug="ashbyco"):
            return None
        return json.loads((Path(__file__).parent / "fixtures" / "ats" / "ashby_board.json").read_text(encoding="utf-8"))


def _jordan(tmp_path, with_cv=True, platforms=None):
    import dataclasses
    c = dataclasses.replace(_client.load("jordan", base=EX), root=tmp_path / "jordan")   # трекер і var у tmp
    if platforms is not None:
        c = dataclasses.replace(c, targeting={**c.targeting, "platforms": platforms})
    (c.root / "var" / "run").mkdir(parents=True)
    c.tracker.write_text((EX / "jordan" / "tracker.md").read_text(encoding="utf-8"), encoding="utf-8")
    att = tmp_path / "attachments"
    att.mkdir()
    if with_cv:
        (att / "CV_Android.pdf").write_bytes(b"%PDF-1.4")
    return c, att


def _inputs(tmp_path):
    filtered = tmp_path / "filtered_2026-10-26.json"
    filtered.write_text(json.dumps({"selected": [
        {"title": "Senior Android Engineer", "company": "Contract Co", "category": "android", "score": 40,
         "email": "jobs@contract.example", "source_url": "https://contract.example/jobs/1", "liveness": "live"},
        {"title": "Android Developer", "company": "Mobile Co", "category": "android", "score": 35,
         "source_url": "https://www.linkedin.com/jobs/view/2/", "liveness": "live"},
    ]}), encoding="utf-8")
    notes = tmp_path / "notes_2026-10-26"
    notes.mkdir()
    (notes / f"{SLUG_EMAIL}.txt").write_text(
        "Contract: Senior Android Engineer · Jordan Example\n\nHello Contract Co team,\n\nBody one.\n\nBest regards\nJordan Example\nLisbon\n",
        encoding="utf-8")
    (notes / f"{SLUG_LI}.txt").write_text("Hello Mobile Co team,\n\nBody two.\n\nBest regards\nJordan Example\n",
                                          encoding="utf-8")
    return filtered, notes


def test_run_builds_queue_from_fixtures(tmp_path):
    c, att = _jordan(tmp_path, platforms={"linkedin": "on"})
    filtered, notes = _inputs(tmp_path)
    out = enqueue_leads.run(c, "2026-10-26", filtered, notes, c.run_dir, att,
                            lambda u: ONSITE_HTML, NoBoard(), "2026-10-26 20:40")
    assert out["packages"] == 2 and out["held"] == [] and out["skipped"] == []
    assert out["queued"] == {"email": 1, "linkedin_easy": 1}
    pkg_dir = c.run_dir / "packages_2026-10-26" / SLUG_EMAIL
    lead = json.loads((pkg_dir / "lead.json").read_text(encoding="utf-8"))
    assert lead["channel"] == "email" and lead["email"] == "jobs@contract.example"
    meta = json.loads((pkg_dir / "meta.json").read_text(encoding="utf-8"))
    assert meta["lang"] == "en" and meta["subject"] == "Contract: Senior Android Engineer · Jordan Example"
    assert meta["attachments"] == [str(att / "CV_Android.pdf")]
    note = (pkg_dir / "note.txt").read_text(encoding="utf-8")
    assert note.startswith("Hello Contract Co team") and "Contract:" not in note
    recs = JsonlStore(c.var / "state" / QUEUE_FILE).read()
    assert {r["id"] for r in recs} == {f"lead-2026-10-26-{SLUG_EMAIL}", f"lead-2026-10-26-{SLUG_LI}"}


def test_run_holds_missing_cv(tmp_path):
    c, att = _jordan(tmp_path, with_cv=False)
    filtered, notes = _inputs(tmp_path)
    out = enqueue_leads.run(c, "2026-10-26", filtered, notes, c.run_dir, att,
                            lambda u: ONSITE_HTML, NoBoard(), "2026-10-26 20:40")
    assert out["queued"] == {} and len(out["held"]) == 2
    assert out["held"][0] == {"slug": SLUG_EMAIL, "reasons": ["attachment_missing:CV_Android.pdf"]}


def test_run_is_idempotent_and_reports_no_note(tmp_path):
    c, att = _jordan(tmp_path)
    filtered, notes = _inputs(tmp_path)
    (notes / f"{SLUG_LI}.txt").unlink()
    args = (c, "2026-10-26", filtered, notes, c.run_dir, att, lambda u: ONSITE_HTML, NoBoard(), "t")
    first = enqueue_leads.run(*args)
    second = enqueue_leads.run(*args)
    assert first["skipped"] == [{"slug": SLUG_LI, "reason": "no_note"}]
    assert first["queued"] == {"email": 1} and second["queued"] == {}
    assert len(JsonlStore(c.var / "state" / QUEUE_FILE).read()) == 1


def test_main_exits_2_without_filtered(tmp_path, monkeypatch):
    c, _ = _jordan(tmp_path)
    monkeypatch.setattr(_client, "active", lambda: c)
    assert enqueue_leads.main(["--date", "2026-10-26"]) == 2


def test_run_holds_office_outside_eu_from_board(tmp_path):
    """Тіньовий прохід 27.10: LinkedIn дав remote_scope global, дошка каже Hybrid у San Francisco.
    Лід лягає в held з office_outside_eu, а не в чергу форм, звідки став би карткою hand-off."""
    c, att = _jordan(tmp_path)
    filtered = tmp_path / "filtered_2026-10-26.json"
    filtered.write_text(json.dumps({"selected": [
        {"title": "Android Developer", "company": "Ashby Co", "category": "android", "score": 35,
         "source_url": "https://www.linkedin.com/jobs/view/3/", "liveness": "live", "remote_scope": "global"},
    ]}), encoding="utf-8")
    notes = tmp_path / "notes_2026-10-26"
    notes.mkdir()
    (notes / f"{SLUG_ASHBY}.txt").write_text("Hello Ashby Co team,\n\nBody three.\n\nBest regards\nJordan Example\n",
                                            encoding="utf-8")
    out = enqueue_leads.run(c, "2026-10-26", filtered, notes, c.run_dir, att,
                            lambda u: OFFSITE_HTML, AshbyBoard(), "2026-10-26 20:40")
    assert out["queued"] == {} and out["held"] == [{"slug": SLUG_ASHBY, "reasons": ["office_outside_eu"]}]
    lead = json.loads((c.run_dir / "packages_2026-10-26" / SLUG_ASHBY / "lead.json").read_text(encoding="utf-8"))
    assert lead["channel"] == "form" and lead["ats"] == "ashby"
    assert lead["board_workplace"] == "hybrid" and lead["board_location"] == "San Francisco, United States"


def test_run_holds_linkedin_lead_when_platform_not_declared(tmp_path):
    """targeting без platforms: LinkedIn вимкнено, Easy Apply лягає в held, email іде як і раніше."""
    c, att = _jordan(tmp_path)
    filtered, notes = _inputs(tmp_path)
    out = enqueue_leads.run(c, "2026-10-26", filtered, notes, c.run_dir, att,
                            lambda u: ONSITE_HTML, NoBoard(), "2026-10-26 20:40")
    assert out["queued"] == {"email": 1}
    assert out["held"] == [{"slug": SLUG_LI, "reasons": ["platform_off:linkedin"]}]
    rec = JsonlStore(c.var / "state" / QUEUE_FILE).get(f"lead-2026-10-26-{SLUG_LI}")
    assert rec["platform"] == "linkedin" and rec["channel"] == "linkedin_easy"


SLUG_OFF = "contract-co-android-engineer"


def test_rechannel_reresolves_held_channel_none_lead(tmp_path, monkeypatch):
    """Пакет старого резолвера з каналом none і запис held channel_none: --rechannel
    перераховує канал, переписує lead.json і переоцінює запис; queued-запис не чіпає."""
    from bot.ats import Resolution

    c, att = _jordan(tmp_path, platforms={"linkedin": "on"})
    filtered = tmp_path / "filtered_2026-10-26.json"
    filtered.write_text(json.dumps({"selected": [
        {"title": "Android Engineer", "company": "Contract Co", "category": "android", "score": 35,
         "source_url": "https://www.linkedin.com/jobs/view/9/", "liveness": "live"},
        {"title": "Android Developer", "company": "Mobile Co", "category": "android", "score": 40,
         "source_url": "https://www.linkedin.com/jobs/view/2/", "liveness": "live"},
    ]}), encoding="utf-8")
    notes = tmp_path / "notes_2026-10-26"
    notes.mkdir()
    (notes / f"{SLUG_OFF}.txt").write_text("Hello Contract Co team,\n\nBody.\n\nBest regards\nJordan Example\n",
                                         encoding="utf-8")
    (notes / f"{SLUG_LI}.txt").write_text("Hello Mobile Co team,\n\nBody two.\n\nBest regards\nJordan Example\n",
                                        encoding="utf-8")
    real = enqueue_leads.resolve_channel
    pages = {"https://www.linkedin.com/jobs/view/9/": OFFSITE_HTML, "https://www.linkedin.com/jobs/view/2/": ONSITE_HTML}
    old = (lambda entry, fetch, board: Resolution("none", entry["source_url"], None, "linkedin offsite, board not found")
           if entry["company"] == "Contract Co" else real(entry, fetch, board))
    monkeypatch.setattr(enqueue_leads, "resolve_channel", old)
    enqueue_leads.run(c, "2026-10-26", filtered, notes, c.run_dir, att, pages.get, NoBoard(), "t1")
    store = JsonlStore(c.var / "state" / QUEUE_FILE)
    assert store.get(f"lead-2026-10-26-{SLUG_OFF}")["reasons"] == ["channel_none"]
    easy_before = store.get(f"lead-2026-10-26-{SLUG_LI}")
    assert easy_before["status"] == "queued" and easy_before["channel"] == "linkedin_easy"
    monkeypatch.setattr(enqueue_leads, "resolve_channel", real)
    fetched = []
    out = enqueue_leads.rechannel(c, "2026-10-26", c.run_dir, lambda u: fetched.append(u) or pages[u],
                                  NoBoard(), "t2")
    assert fetched == ["https://www.linkedin.com/jobs/view/9/"]      # queued-пакет не перераховується
    stored = store.get(f"lead-2026-10-26-{SLUG_OFF}")
    assert stored["status"] == "queued" and stored["channel"] == "linkedin_offsite"
    assert stored["platform"] == "linkedin" and stored["reasons"] == []
    lead = json.loads((c.run_dir / "packages_2026-10-26" / SLUG_OFF / "lead.json").read_text(encoding="utf-8"))
    assert lead["channel"] == "linkedin_offsite" and lead["resolver_note"] == "linkedin offsite, board not found"
    assert lead["apply_url"] == "https://www.linkedin.com/jobs/view/9/" and lead["title"] == "Android Engineer"
    assert store.get(f"lead-2026-10-26-{SLUG_LI}") == easy_before
    assert out == {"date": "2026-10-26", "packages": 2, "queued": {"linkedin_offsite": 1}, "held": [],
                   "skipped": [], "skipped_failed": []}


def test_main_rechannel_runs_without_filtered(tmp_path, monkeypatch, capsys):
    c, _ = _jordan(tmp_path)
    monkeypatch.setattr(_client, "active", lambda: c)
    calls = []
    monkeypatch.setattr(enqueue_leads, "rechannel", lambda *a: calls.append(a) or {"date": a[1]})
    assert enqueue_leads.main(["--date", "2026-10-26", "--rechannel"]) == 0
    assert calls[0][1] == "2026-10-26" and json.loads(capsys.readouterr().out) == {"date": "2026-10-26"}


def test_rechannel_skips_failed_records(tmp_path):
    """failed (процес упав посеред форми, hand-off протух) міг уже подати заявку: --rechannel
    його не повертає в queued і перелічує в skipped_failed; held переоцінюється як раніше."""
    from bot.sendqueue import mark

    c, att = _jordan(tmp_path, platforms={"linkedin": "on"})
    filtered, notes = _inputs(tmp_path)
    pages = {"https://www.linkedin.com/jobs/view/2/": ONSITE_HTML}
    enqueue_leads.run(c, "2026-10-26", filtered, notes, c.run_dir, att, pages.get, NoBoard(), "t1")
    store = JsonlStore(c.var / "state" / QUEUE_FILE)
    li, em = f"lead-2026-10-26-{SLUG_LI}", f"lead-2026-10-26-{SLUG_EMAIL}"
    mark(store, li, status="failed", error="apply status unknown: process died during the form fill at x")
    mark(store, em, status="held", reasons=["platform_off"])
    before = store.get(li)
    fetched = []
    out = enqueue_leads.rechannel(c, "2026-10-26", c.run_dir, lambda u: fetched.append(u) or pages.get(u),
                                  NoBoard(), "t2")
    assert store.get(li) == before and "https://www.linkedin.com/jobs/view/2/" not in fetched
    assert out["skipped_failed"] == [li]
    assert store.get(em)["status"] == "queued" and store.get(em)["reasons"] == []   # held переоцінено
