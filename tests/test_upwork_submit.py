"""Upwork auto-submit (bot.upwork_submit): config, job id, page text, model answers, the pass."""
from __future__ import annotations

import json
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone

import pytest

from bot.generators.upwork_alert import UpworkJob
from bot.llm import LockBusy
from bot.state import JsonlStore
from bot.upwork_submit import (
    ApplyPage,
    NeedsOwner,
    StopPass,
    SubmitConfig,
    job_text,
    parse_check,
    parse_connects,
    parse_final,
    queue_record,
    resolve_job_id,
    run_submit,
    submit_config,
)
from tests.test_llm import FakeLLM
from tests.test_telegram_api import FakeTransport

NOW = datetime(2026, 11, 2, 21, 30, tzinfo=timezone(timedelta(hours=1)))
PROFILE = {"first_name": "Jordan", "cv_contact_extra": ["Android engineer"], "availability": "now"}
JOB = UpworkJob(message_id="1a0000000000000a", title="Kotlin BLE app", budget_type="hourly",
                budget_min=20.0, budget_max=40.0, payment_verified=True, country="NOR",
                skills=["Kotlin"], saved_search="android kotlin",
                job_link="https://link.t.upwork.com/ls/click?upn=u001.TOKEN")
JOB_ID = "~022117212269323992637"
PAGE = ApplyPage(kind="hourly", title="Kotlin BLE app", description="We need BLE sync.", required=13,
                 left_after=78)
FINAL = {"go": True, "reason": "core work", "proposal": "BLE sync that survives bad signal.\nI would start with the GATT layer.\n\nDo you have test devices?",
         "answers": [], "duration": "1 to 3 months"}
CLEAN = json.dumps({"unsupported": []})


def answers_for(final=FINAL, check=CLEAN):
    """The model: the final text for the final prompt, the check verdict for the check prompt."""
    def answer(prompt: str) -> str:
        return check if prompt.startswith("Below is a text") else json.dumps(final)
    return answer


class FakeDriver:
    def __init__(self, page=PAGE, open_error=None, submit_error=None, pid="2117325900268978177"):
        self.page, self.open_error, self.submit_error, self.pid = page, open_error, submit_error, pid
        self.opened: list[str] = []
        self.plans: list[tuple] = []
        self.closed = False

    def open(self, job_id):
        self.opened.append(job_id)
        if self.open_error:
            raise self.open_error
        return self.page

    def submit(self, plan, required, send):
        self.plans.append((plan, required, send))
        if self.submit_error:
            raise self.submit_error
        return self.pid if send else "dry"

    def shot(self, name):
        return ""

    def close(self):
        self.closed = True


class Env:
    def __init__(self, tmp_path, llm=None, driver=None, **cfg):
        self.state = tmp_path / "state"
        self.cards = JsonlStore(self.state / "cards.jsonl")
        self.t = FakeTransport()
        self.llm = llm or FakeLLM(answers_for())
        self.driver = driver or FakeDriver()
        self.rows: list[dict] = []
        self.heads: list[str] = []
        self.cfg = SubmitConfig(lang="en", bid_hourly_usd=75, **cfg)

    def queue(self, n=1, job=JOB, at=NOW, **extra):
        cid = f"up-{n}"
        rec = {"id": cid, "kind": "upwork", "status": "open", "title": job.title, "country": job.country,
               "alert_date": "2026-11-02", "saved_search": job.saved_search, "budget": "hourly $20-$40",
               "fit": "high", "draft": "draft text\n\nBest regards,\nJordan", **extra}
        self.cards.append(queue_record(rec, job, "https://mail.example/1", "core work", at))
        return cid

    def head(self, url):
        self.heads.append(url)
        return f"https://www.upwork.com/jobs/{JOB_ID}"

    def run(self, now=NOW):
        return run_submit(self.cfg, self.state, self.t, 42, self.llm, "BRIEF", PROFILE, self.rows.append,
                          lambda: self.driver, self.head, now)

    def card(self, cid="up-1"):
        return self.cards.get(cid)


# ── config, link, page text ──────────────────────────────────────────────────

def test_submit_is_off_unless_switched_on_with_the_intake():
    assert submit_config({}) is None
    assert submit_config({"upwork": {"intake": True}}) is None
    assert submit_config({"upwork": {"intake": False, "submit": True}}) is None
    assert submit_config({"upwork": {"intake": True, "submit": "yes"}}) is None
    cfg = submit_config({"upwork": {"intake": True, "submit": True, "bid_hourly_usd": 75,
                                    "max_connects_per_proposal": 24}, "followup_days": 5})
    assert (cfg.dry_run, cfg.bid_hourly_usd, cfg.max_connects_per_proposal, cfg.followup_days) == (False, 75, 24, 5)
    assert cfg.max_submits_per_day == 4 and cfg.min_connects_left == 10


def test_dry_submit_and_shadow_intake_never_send():
    assert submit_config({"upwork": {"intake": True, "submit": "dry"}}).dry_run is True
    assert submit_config({"upwork": {"intake": "shadow", "submit": True}}).dry_run is True
    assert submit_config({"upwork": {"intake": True, "submit": True}}, dry_run=True).dry_run is True


def test_job_id_comes_from_one_redirect_of_the_tracking_link():
    seen = []
    assert resolve_job_id(JOB.job_link, lambda u: seen.append(u) or f"https://www.upwork.com/jobs/{JOB_ID}") == JOB_ID
    assert seen == [JOB.job_link]
    assert resolve_job_id(f"https://www.upwork.com/jobs/{JOB_ID}", lambda u: pytest.fail("no request")) == JOB_ID


def test_an_upwork_page_without_a_job_id_is_never_requested():
    with pytest.raises(ValueError, match="job id"):
        resolve_job_id(JOB.job_link, lambda u: "https://www.upwork.com/nx/find-work/" if "link.t" in u
                       else pytest.fail(f"requested {u}"))


def test_a_refused_tracker_request_names_its_status(monkeypatch):
    import httpx

    from bot.upwork_submit import http_head

    monkeypatch.setattr(httpx, "get", lambda url, **kw: httpx.Response(403, request=httpx.Request("GET", url)))
    with pytest.raises(ValueError, match="HTTP 403"):
        resolve_job_id(JOB.job_link, http_head)


def test_connects_and_job_text_from_the_apply_page():
    main = ("Submit a proposal\nProposal settings\nThis proposal requires 11 Connects\n"
            "When you submit this proposal, you’ll have 80 Connects remaining.\nJob details\n"
            "Dating App MVP\nMobile App DevelopmentPosted Nov 2, 2026\nWe need an MVP.\n\nAdditional details\nCover Letter")
    assert parse_connects(main) == (11, 80)
    assert parse_connects("nothing here") == (None, None)
    assert job_text(main) == ("Dating App MVP", "Mobile App DevelopmentPosted Nov 2, 2026\nWe need an MVP.")


# ── model answers ────────────────────────────────────────────────────────────

def test_final_answer_is_cleaned_and_validated():
    out = parse_final(json.dumps({**FINAL, "proposal": "Hi there,\nBLE sync — reliable.", "answers": ["Yes."],
                                  "duration": "3 TO 6 MONTHS"}), 1, [])
    assert out["proposal"] == "BLE sync, reliable." and out["answers"] == ["Yes."] and out["duration"] == "3 to 6 months"
    assert parse_final(json.dumps({"go": False, "reason": "needs iOS"}), 2, []) == {"go": False, "reason": "needs iOS"}
    assert parse_final(json.dumps({**FINAL, "duration": "a week"}), 0, [])["duration"] == "Less than 1 month"


@pytest.mark.parametrize("final, n, error", [
    ({**FINAL, "proposal": "My rate is $75 per hour."}, 0, "money figure"),
    ({**FINAL, "answers": ["one"]}, 2, "2 answers expected"),
    ({**FINAL, "answers": ["It costs 500 USD."]}, 1, "money figure"),
    ({**FINAL, "go": "yes"}, 0, "'go' must be"),
    ({**FINAL, "proposal": "Worked at Example Corp."}, 0, "forbidden name"),
])
def test_final_answer_rejections(final, n, error):
    with pytest.raises(ValueError, match=error):
        parse_final(json.dumps(final), n, ["Example Corp"])


def test_check_lists_claims():
    assert parse_check('{"unsupported": ["shipped audio apps", " "]}') == ["shipped audio apps"]
    with pytest.raises(ValueError):
        parse_check('{"unsupported": "none"}')


# ── the pass ─────────────────────────────────────────────────────────────────

def test_queued_hourly_job_is_submitted_with_tracker_row_and_a_note_without_buttons(tmp_path):
    env = Env(tmp_path)
    env.queue()
    r = env.run()
    assert r.submitted == ["up-1"] and r.llm_calls == 2 and env.driver.closed
    plan, required, send = env.driver.plans[0]
    assert (plan.kind, plan.hourly_rate, required, send) == ("hourly", 75, 13, True)
    assert plan.cover_letter.endswith("Best regards,\nJordan") and plan.cover_letter.startswith("BLE sync")
    card = env.card()
    assert card["status"] == "done" and card["submitted_by"] == "server" and card["proposal_id"] == "2117325900268978177"
    assert card["job_id"] == JOB_ID and card["connects"] == 13 and card["connects_left"] == 78
    assert len(env.rows) == 1 and env.rows[0]["proposal_id"] == "2117325900268978177"
    (chat, text, buttons), = env.t.sent
    assert chat == 42 and buttons is None and "Upwork · submitted" in text and "13 Connects, 78 left" in text
    assert "/nx/proposals/2117325900268978177" in text and "Bid $75/h" in text


def test_fixed_job_keeps_the_clients_amount_and_the_models_duration(tmp_path):
    env = Env(tmp_path, driver=FakeDriver(page=replace(PAGE, kind="fixed", fixed_bid=250.0)))
    env.queue()
    env.run()
    plan, _, _ = env.driver.plans[0]
    assert (plan.kind, plan.hourly_rate, plan.duration) == ("fixed", None, "1 to 3 months")
    assert "Fixed $250" in env.t.sent[0][1]


def test_unsupported_claim_gets_one_rewrite(tmp_path):
    verdicts = [json.dumps({"unsupported": ["shipped audio apps"]}), CLEAN]
    llm = FakeLLM(lambda p: verdicts.pop(0) if p.startswith("Below is a text") else json.dumps(FINAL))
    env = Env(tmp_path, llm=llm)
    env.queue()
    assert env.run().submitted == ["up-1"]
    assert "claims the brief does not support: shipped audio apps" in llm.prompts[2]


def test_text_that_keeps_inventing_goes_to_the_owner_as_a_card(tmp_path):
    env = Env(tmp_path, llm=FakeLLM(answers_for(check=json.dumps({"unsupported": ["10 BLE apps"]}))))
    env.queue()
    r = env.run()
    assert r.submitted == [] and env.driver.plans == [] and r.to_owner[0]["reason"].startswith("the text did not pass")
    (_, text, buttons), = env.t.sent
    assert text.startswith("Not submitted: the text did not pass the check") and "draft text" in text
    assert [b.data for row in buttons for b in row] == ["done:up-1", "skip:up-1"]
    assert env.card()["status"] == "open" and env.card()["tg_message_id"] == 100


def test_model_says_no_after_the_full_text(tmp_path):
    env = Env(tmp_path, llm=FakeLLM(answers_for(final={"go": False, "reason": "on-site in Oslo"})))
    env.queue()
    r = env.run()
    assert r.declined == [{"id": "up-1", "reason": "on-site in Oslo"}] and env.driver.plans == [] and env.t.sent == []
    assert env.card()["status"] == "declined" and env.rows == []


def test_expensive_job_goes_to_the_owner(tmp_path):
    env = Env(tmp_path, driver=FakeDriver(page=replace(PAGE, required=26)))
    env.queue()
    r = env.run()
    assert r.to_owner == [{"id": "up-1", "reason": "costs 26 Connects, the limit is 20"}] and env.llm.prompts == []


def test_low_connects_alert_the_owner(tmp_path):
    env = Env(tmp_path, driver=FakeDriver(page=replace(PAGE, left_after=4)))
    env.queue()
    r = env.run()
    assert r.to_owner[0]["reason"] == "only 4 Connects would be left" and "running out" in r.alerts[0]


def test_logged_out_stops_the_pass_and_keeps_the_queue(tmp_path):
    env = Env(tmp_path, driver=FakeDriver(open_error=StopPass("Upwork login expired")))
    env.queue(1)
    env.queue(2)
    r = env.run()
    assert r.alerts == ["Upwork login expired"] and env.driver.opened == [JOB_ID] and env.t.sent == []
    assert env.card("up-1")["status"] == env.card("up-2")["status"] == "queued"


def test_page_problem_hands_one_job_over_and_goes_on(tmp_path):
    driver = FakeDriver(submit_error=NeedsOwner("no confirmation after Send (Are you sure?)"))
    env = Env(tmp_path, driver=driver)
    env.queue()
    r = env.run()
    assert r.to_owner[0]["reason"] == "no confirmation after Send (Are you sure?)" and env.rows == []


def test_daily_limit_and_stale_jobs_go_to_the_owner(tmp_path):
    env = Env(tmp_path, max_submits_per_day=1)
    env.queue(1, at=NOW - timedelta(hours=1))
    env.queue(2, at=NOW - timedelta(minutes=30))
    env.queue(3, at=NOW - timedelta(hours=30))
    r = env.run()
    assert r.submitted == ["up-1"]
    assert {d["id"]: d["reason"] for d in r.to_owner} == {
        "up-2": "daily limit of 1 proposals reached", "up-3": "waited more than 20 h in the queue"}


def test_busy_model_keeps_the_job_queued(tmp_path):
    class Busy:
        def complete(self, prompt):
            raise LockBusy("night run")
    env = Env(tmp_path, llm=Busy())
    env.queue()
    r = env.run()
    assert r.deferred[0]["reason"].startswith("llm:") and env.card()["status"] == "queued" and env.t.sent == []


def test_dry_mode_fills_but_never_sends(tmp_path):
    env = Env(tmp_path, dry_run=True)
    env.queue()
    r = env.run()
    assert env.driver.plans[0][2] is False and env.rows == [] and r.submitted == ["up-1"]
    assert env.card()["status"] == "dry_filled" and "submitted_by" not in env.card()
    assert "filled, not sent (dry)" in env.t.sent[0][1] and "/nx/proposals/" not in env.t.sent[0][1]


def test_queue_record_keeps_the_job_for_a_later_card():
    rec = queue_record({"id": "up-1", "status": "open"}, JOB, "https://mail", "why", NOW)
    assert rec["status"] == "queued" and UpworkJob(**rec["job"]) == JOB and rec["queued_at"] == "2026-11-02T21:30:00+01:00"
    assert asdict(JOB) == rec["job"]
