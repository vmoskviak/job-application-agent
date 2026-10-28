"""Черга лідів у var/state/queue.jsonl: денні ліміти з файла, захист від дублів."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from bot.packages import Package
from bot.sendqueue import (
    DEFAULT_LIMITS,
    DRY_FILE,
    QUEUE_FILE,
    due,
    due_any,
    enqueue,
    lead_id,
    limits_from,
    mark,
    sent_today,
    sent_today_platform,
    summary,
)
from bot.state import JsonlStore

LID = "lead-2026-10-26-contract-co-senior-android-engineer"


def _pkg(tmp_path, slug="contract-co-senior-android-engineer", company="Contract Co",
         title="Senior Android Engineer", channel="form",
         apply_url="https://boards.greenhouse.io/contractco/jobs/123"):
    d = tmp_path / "packages" / slug
    d.mkdir(parents=True, exist_ok=True)
    return Package(slug, d, {"company": company, "title": title}, channel,
                   apply_url, "greenhouse", "en", "S", "Body", [])


@pytest.fixture
def store(tmp_path):
    return JsonlStore(tmp_path / QUEUE_FILE)


def test_constants():
    assert QUEUE_FILE == "queue.jsonl" and DRY_FILE == "queue_dry.jsonl"
    assert DEFAULT_LIMITS == {"email": 5, "form": 5}
    assert lead_id("2026-10-26", "x") == "lead-2026-10-26-x"


def test_enqueue_queued_and_held(store, tmp_path):
    rec = enqueue(store, _pkg(tmp_path), [], "2026-10-26", "2026-10-26 20:40")
    assert rec["status"] == "queued" and rec["id"] == LID and rec["channel"] == "form"
    assert rec["package_dir"] == str(tmp_path / "packages" / "contract-co-senior-android-engineer")
    assert rec["attempts"] == 0 and rec["sent_id"] is None and rec["created"] == "2026-10-26 20:40"
    held = enqueue(store, _pkg(tmp_path, "other-co-ai", "Other Co", "AI Engineer", "email"),
                   ["attachment_missing:CV_AI.pdf"], "2026-10-26", "2026-10-26 20:40")
    assert held["status"] == "held" and held["reasons"] == ["attachment_missing:CV_AI.pdf"]


def test_enqueue_same_id_is_noop(store, tmp_path):
    first = enqueue(store, _pkg(tmp_path), [], "2026-10-26", "t")
    mark(store, LID, status="sent")
    again = enqueue(store, _pkg(tmp_path), [], "2026-10-26", "t2")
    assert len(store.read()) == 1 and again["status"] == "sent" and first["id"] == again["id"]


def test_enqueue_same_company_similar_title_is_held(store, tmp_path):
    enqueue(store, _pkg(tmp_path), [], "2026-10-26", "t")
    rec = enqueue(store, _pkg(tmp_path, "contract-co-senior-android-developer-kotlin", "Contract Co GmbH",
                              "Senior Android Developer (Kotlin)"), [], "2026-10-27", "t")
    assert rec["status"] == "held" and rec["reasons"] == ["duplicate_in_queue"]
    other = enqueue(store, _pkg(tmp_path, "contract-co-data-engineer", "Contract Co", "Data Engineer"),
                    [], "2026-10-27", "t")
    assert other["status"] == "queued"


def test_due_and_sent_today_counts_from_file(tmp_path):
    store = JsonlStore(tmp_path / QUEUE_FILE)
    enqueue(store, _pkg(tmp_path), [], "2026-10-26", "t")
    enqueue(store, _pkg(tmp_path, "b-co-android", "B Co", "Android Lead", "email"), [], "2026-10-26", "t")
    assert [r["id"] for r in due(store, "form")] == [LID]
    assert mark(store, LID, status="sent", sent_at="2026-10-26 09:00", sent_id="abc") is True
    assert mark(store, "lead-nope", status="sent") is False
    fresh = JsonlStore(tmp_path / QUEUE_FILE)
    assert sent_today(fresh, "2026-10-26", "form") == 1
    assert sent_today(fresh, "2026-10-27", "form") == 0
    assert sent_today(fresh, "2026-10-26", "email") == 0
    assert due(fresh, "form") == [] and [r["channel"] for r in due(fresh, "email")] == ["email"]


def test_limits_from():
    assert limits_from({}) == {"email": 5, "form": 5}
    assert limits_from({"daily_limits": {"email": "2"}}) == {"email": 2, "form": 5}


def test_summary(store, tmp_path):
    enqueue(store, _pkg(tmp_path), [], "2026-10-26", "t")
    enqueue(store, _pkg(tmp_path, "h-co-x", "H Co", "X Role", "email"), ["channel_none"], "2026-10-26", "t")
    assert summary(store, "2026-10-26") == {"queued": {"form": 1}, "held": 1, "sent_today": {}, "failed": 0,
                                           "handoff": 0}
    mark(store, LID, status="sent", sent_at="2026-10-26 09:00")
    assert summary(store, "2026-10-26")["sent_today"] == {"form": 1}


def test_held_and_failed_records_do_not_block_and_are_reevaluated(store, tmp_path):
    """Рев'ю 2b, Important 5: held/failed не є подачами; той самий лід після виправлення конфігу
    переоцінюється, а не ховається за duplicate_in_queue."""
    held = enqueue(store, _pkg(tmp_path), ["attachment_missing:CV_Android.pdf"], "2026-10-26", "t1")
    assert held["status"] == "held"
    again = enqueue(store, _pkg(tmp_path), [], "2026-10-26", "t2")      # той самий id, причин уже нема
    assert again["status"] == "queued" and again["reasons"] == [] and len(store.read()) == 1
    next_day = enqueue(store, _pkg(tmp_path, "contract-co-senior-android-engineer-2", "Contract Co",
                                   "Senior Android Engineer"), [], "2026-10-27", "t3")
    assert next_day["status"] == "held" and next_day["reasons"] == ["duplicate_in_queue"]   # queued блокує
    mark(store, LID, status="failed", error="gmail down")
    retry = enqueue(store, _pkg(tmp_path, "contract-co-senior-android-engineer-3", "Contract Co",
                                "Senior Android Engineer"), [], "2026-10-28", "t4")
    assert retry["status"] == "queued"        # failed не блокує
    mark(store, retry["id"], status="sent", sent_at="2026-10-28 09:00")
    later = enqueue(store, _pkg(tmp_path, "contract-co-senior-android-engineer-4", "Contract Co",
                                "Senior Android Engineer"), [], "2026-10-29", "t5")
    assert later["status"] == "held"          # sent блокує


def test_platform_leads_deduplicate_by_url_not_company(tmp_path):
    store = JsonlStore(tmp_path / "queue.jsonl")
    a = _pkg(tmp_path, "a", channel="platform_login", company="Agency One (GULP)", title="Android Dev",
             apply_url="https://www.gulp.de/gulp2/g/projekte/agentur/C1")
    b = _pkg(tmp_path, "b", channel="platform_login", company="Agency One (GULP)", title="Android Developer",
             apply_url="https://www.gulp.de/gulp2/g/projekte/agentur/C2")
    c = _pkg(tmp_path, "c", channel="platform_login", company="Other", title="X",
             apply_url="https://www.gulp.de/gulp2/g/projekte/agentur/C1")
    assert enqueue(store, a, [], "2026-10-28", "t")["status"] == "queued"
    assert enqueue(store, b, [], "2026-10-28", "t")["status"] == "queued"
    assert enqueue(store, c, [], "2026-10-28", "t")["reasons"] == ["duplicate_in_queue"]
    assert store.get("lead-2026-10-28-a")["platform"] == "gulp"


def test_enqueue_writes_platform_on_new_and_held_update_paths(store, tmp_path):
    form = enqueue(store, _pkg(tmp_path), [], "2026-10-26", "t")
    assert form["platform"] is None
    li = _pkg(tmp_path, "li-co-android", "Li Co", "Android Engineer", "linkedin_easy",
              "https://www.linkedin.com/jobs/view/7/")
    held = enqueue(store, li, ["attachment_none"], "2026-10-26", "t")
    assert held["status"] == "held" and held["platform"] == "linkedin"
    store.update(held["id"], platform=None)           # запис старого формату без ключа платформи
    again = enqueue(store, li, [], "2026-10-26", "t2")
    assert again["status"] == "queued" and again["platform"] == "linkedin"


def test_due_any_and_sent_today_platform(tmp_path):
    store = JsonlStore(tmp_path / "queue.jsonl")
    store.append({"id": "1", "status": "queued", "channel": "linkedin_easy", "platform": "linkedin"})
    store.append({"id": "2", "status": "queued", "channel": "form", "platform": None})
    store.append({"id": "3", "status": "sent", "channel": "linkedin_easy", "platform": "linkedin",
                  "sent_at": "2026-10-28 09:00"})
    store.append({"id": "4", "status": "queued", "channel": "email", "platform": None})
    assert [r["id"] for r in due_any(store, ("form", "linkedin_easy"))] == ["1", "2"]
    assert sent_today_platform(store, "2026-10-28", "linkedin") == 1
    assert sent_today_platform(store, "2026-10-27", "linkedin") == 0
    assert sent_today_platform(store, "2026-10-28", "gulp") == 0
