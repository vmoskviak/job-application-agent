"""Upwork intake (bot.upwork_intake): config, fit filter, proposal validation, the intake pass."""
from __future__ import annotations

import json
from dataclasses import replace
from datetime import date

import pytest

from bot.generators.upwork_alert import UpworkJob
from bot.inbox_watch import leaked_name
from bot.llm import LockBusy
from bot.state import JsonlStore
from bot.upwork_intake import (
    IntakeConfig,
    fit_reasons,
    intake_config,
    parse_proposal,
    processed_ids,
    proposal_prompt,
    run_intake,
    signed,
    track_hits,
)
from tests.test_llm import FakeLLM
from tests.test_telegram_api import FakeTransport
from tests.upwork_fixtures import (
    META,
    PLUS_JOB_ID,
    PLUS_SUBJECT,
    TITLE,
    alert_body,
    job_block,
    plus_alert_body,
)

# A contract client's tracks: two real ones, two that only filter out look-alikes
TRACKS = {
    "android": {"keywords": ["Android", "Kotlin", "Jetpack Compose", "Kotlin Multiplatform"], "variant": "android"},
    "ai": {"keywords": ["LLM", "AI Agent", "MCP"], "variant": "ai"},
    "ios_only": {"keywords": ["iOS", "Swift", "SwiftUI"], "variant": "android", "excluded": True},
    "management": {"keywords": ["CTO", "Engineering Manager"], "variant": "android", "excluded": True},
}
CFG = IntakeConfig()


def _job(**over) -> UpworkJob:
    base = UpworkJob(message_id="19a0000000000001", date="Thu, 29 Oct 2026 09:12:00 +0000",
                     title="Kotlin Multiplatform app for a fitness startup",
                     snippet="We need help with an existing app ...",
                     budget_type="fixed", budget_min=2000.0, budget_max=2000.0, duration="1 to 3 months",
                     payment_verified=True, rating=4.8, spent="$5k", country="PRT", category="Mobile Development",
                     skills=["Android", "Kotlin"], saved_search="android kotlin",
                     job_link="https://link.example.com/ls/click?upn=u001.TOKEN13")
    return replace(base, **over)


def test_intake_is_off_unless_switched_on():
    assert intake_config({}) is None
    assert intake_config({"upwork": {}}) is None
    assert intake_config({"upwork": {"intake": False}}) is None
    assert intake_config({"upwork": "yes"}) is None


def test_intake_shadow_runs_dry_while_the_watcher_stays_live():
    assert intake_config({"upwork": {"intake": "shadow"}}, dry_run=False).dry_run is True
    assert intake_config({"upwork": {"intake": True}}, dry_run=False).dry_run is False
    assert intake_config({"upwork": {"intake": True}}, dry_run=True).dry_run is True
    assert intake_config({"upwork": {"intake": "on"}}) is None   # only true or shadow switch it on


def test_intake_config_reads_keys_and_falls_back_to_defaults():
    cfg = intake_config({"upwork": {"intake": True, "min_fixed_usd": 800, "max_cards_per_day": None},
                         "forbidden_names": ["Example Corp", ""]}, lang="uk", dry_run=True)
    assert cfg.min_fixed_usd == 800 and cfg.max_cards_per_day == 6 and cfg.min_hourly_usd == 50
    assert cfg.require_payment_verified is True and cfg.bid_hourly_usd == 120 and cfg.max_cards_per_pass == 3
    assert cfg.max_llm_per_pass == 4 and cfg.forbidden_names == ["Example Corp"]
    assert cfg.lang == "uk" and cfg.dry_run is True


def test_android_job_with_verified_client_fits():
    assert fit_reasons(_job(), TRACKS, CFG) == []


def test_payment_must_be_verified():
    assert fit_reasons(_job(payment_verified=False), TRACKS, CFG) == ["payment not verified"]
    assert fit_reasons(_job(payment_verified=None), TRACKS, CFG) == ["payment verification unknown"]
    assert fit_reasons(_job(payment_verified=None), TRACKS, replace(CFG, require_payment_verified=False)) == []


def test_fixed_budget_minimum():
    assert fit_reasons(_job(budget_min=300.0, budget_max=300.0), TRACKS, CFG) == ["fixed budget $300 below $500"]
    assert fit_reasons(_job(budget_min=500.0, budget_max=500.0), TRACKS, CFG) == []


def test_hourly_top_must_reach_the_minimum_and_unknown_passes():
    hourly = {"budget_type": "hourly"}
    assert fit_reasons(_job(budget_min=20.0, budget_max=45.0, **hourly), TRACKS, CFG) == ["hourly top $45 below $50"]
    assert fit_reasons(_job(budget_min=30.0, budget_max=60.0, **hourly), TRACKS, CFG) == []
    assert fit_reasons(_job(budget_min=None, budget_max=None, **hourly), TRACKS, CFG) == []
    assert fit_reasons(_job(budget_type="unknown", budget_min=None, budget_max=None), TRACKS, CFG) == []


def test_keyword_in_skills_or_snippet_is_enough():
    assert fit_reasons(_job(title="Mobile app rework", skills=["Kotlin"], snippet=""), TRACKS, CFG) == []
    assert fit_reasons(_job(title="App rework", skills=[], snippet="Our LLMs need an MCP server"), TRACKS, CFG) == []


def test_excluded_track_blocks_only_without_a_real_keyword():
    ios = _job(title="Senior iOS Engineer", skills=["Swift", "SwiftUI"], snippet="Native app")
    assert fit_reasons(ios, TRACKS, CFG) == ["excluded track: ios_only"]
    both = _job(title="iOS and Android app", skills=["Swift"], snippet="")
    assert fit_reasons(both, TRACKS, CFG) == []
    none = _job(title="Shopify theme tweaks", skills=["Liquid"], snippet="Store redesign")
    assert fit_reasons(none, TRACKS, CFG) == ["no track keyword"]


def test_keywords_match_stems_but_not_inside_words():
    assert track_hits("We build llms and agents", TRACKS) == (["ai"], [])
    assert track_hits("Director of a small studio", TRACKS) == ([], [])
    assert track_hits("Fractional CTO wanted", TRACKS) == ([], ["management"])
    assert track_hits("JETPACK   COMPOSE screens", TRACKS) == (["android"], [])


def test_reasons_accumulate():
    job = _job(payment_verified=False, budget_min=100.0, budget_max=100.0, title="Logo", skills=[], snippet="")
    assert fit_reasons(job, TRACKS, CFG) == ["payment not verified", "fixed budget $100 below $500",
                                             "no track keyword"]


# ── proposal prompt and answer ───────────────────────────────────────────────

PROFILE = {"first_name": "Jordan", "availability": "from 1 December 2026",
           "cv_contact_extra": ["linkedin.com/in/jordan-example"], "rate": {"hourly_target": 95}}
GOOD = {"fit": "High", "reason": "Kotlin Multiplatform migration, his core work.",
        "proposal": "Your Android app has to share its sync layer with iOS without a rewrite.\n"
                    "I would move the data layer to Kotlin Multiplatform module by module.\n\n"
                    "I did the same migration for a banking app.\n\nWhich screens hurt the most today?"}


def test_leaked_name_is_case_insensitive_and_ignores_blank_names():
    assert leaked_name("I worked at EXAMPLE corp for years", ["", "Example Corp"]) == "Example Corp"
    assert leaked_name("nothing to see", ["Example Corp", ""]) is None


def test_prompt_carries_job_brief_and_banned_names_but_no_rate():
    prompt = proposal_prompt(_job(), "BRIEF: twelve years of Android.", PROFILE, ["Example Corp"])
    assert "Kotlin Multiplatform app for a fitness startup" in prompt and "BRIEF: twelve years of Android." in prompt
    assert "fixed $2,000" in prompt and "payment verified, rating 4.8, $5k spent, PRT" in prompt
    assert "Android, Kotlin" in prompt and "android kotlin" in prompt and "We need help with an existing app" in prompt
    assert "Never name any of these" in prompt and "Example Corp" in prompt
    assert "at most 170 words" in prompt and "from 1 December 2026" in prompt and "95" not in prompt
    assert "Never name" not in proposal_prompt(_job(), "B", PROFILE, [])


def test_answer_json_inside_prose_and_fences():
    got = parse_proposal("Sure:\n```json\n" + json.dumps(GOOD) + "\n```", [])
    assert got == {"fit": "high", "reason": "Kotlin Multiplatform migration, his core work.",
                   "proposal": GOOD["proposal"]}


def test_answer_dashes_rewritten_and_greeting_dropped():
    answer = {"fit": "medium", "reason": "ok", "proposal": "Hi there,\n\nYour sync drops events — I would add a queue."}
    assert parse_proposal(json.dumps(answer), [])["proposal"] == "Your sync drops events, I would add a queue."


def test_low_fit_needs_no_proposal():
    assert parse_proposal('{"fit": "low", "reason": "Flutter only", "proposal": ""}', []) == {
        "fit": "low", "reason": "Flutter only", "proposal": ""}


@pytest.mark.parametrize(("answer", "error"), [
    ("no json at all", "no JSON object"),
    ('{"fit": "great", "reason": "x", "proposal": "Text."}', "unknown fit"),
    ('{"fit": "high", "reason": "x", "proposal": "  "}', "empty proposal"),
    ('{"fit": "high", "reason": "x", "proposal": "My rate is $120/h."}', "money figure"),
    ('{"fit": "medium", "reason": "x", "proposal": "Happy to take it on for 2,000 USD."}', "money figure"),
    ('{"fit": "high", "reason": "x", "proposal": "At example corp I led the app."}', "forbidden name: Example Corp"),
    (json.dumps({"fit": "high", "reason": "x", "proposal": "word " * 231}), "231 words"),
])
def test_invalid_answers_are_rejected(answer, error):
    with pytest.raises(ValueError, match=error):
        parse_proposal(answer, ["Example Corp"])


def test_signed_adds_the_first_name():
    assert signed("Text.", PROFILE) == "Text.\n\nBest regards,\nJordan"
    assert signed("Text.", {}) == "Text."


# ── the intake pass ──────────────────────────────────────────────────────────

TODAY = date(2026, 10, 29)
ANSWER = json.dumps(GOOD)
LOW = '{"fit": "low", "reason": "Flutter only", "proposal": ""}'
RATE = '{"fit": "high", "reason": "x", "proposal": "Happy to help at $120/h."}'


def _mid(n: int) -> str:
    return f"19a{n:013x}"


def _mail(n: int, *, hour: int = 9, title: str = TITLE, saved_search: str = "android kotlin",
          subject: str | None = None, **block) -> dict:
    return {"message_id": _mid(n), "account": "main", "date": f"Thu, 29 Oct 2026 {hour:02d}:00:00 +0000",
            "subject": subject or f"New job alert: {title}", "trashed": False,
            "body": alert_body(job_block(title=title, **block), saved_search=saved_search),
            "mail_url": f"https://mail.google.com/mail/u/0/#all/{_mid(n)}"}


class RaisingLLM:
    def __init__(self, exc: Exception) -> None:
        self.exc = exc
        self.prompts: list[str] = []

    def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        raise self.exc


class BrokenTransport(FakeTransport):
    def send(self, chat_id, text, buttons=None):
        raise RuntimeError("Bad Request: can't parse entities")


class IntakeEnv:
    def __init__(self, tmp_path, answers=None, llm=None, **cfg):
        self.state = tmp_path / "state"
        self.t = FakeTransport()
        self.llm = llm or FakeLLM(answers if answers is not None else (lambda p: ANSWER))
        self.cfg = IntakeConfig(lang="en", **cfg)

    def run(self, items, transport="default", today=TODAY):
        t = self.t if transport == "default" else transport
        return run_intake(self.cfg, items, TRACKS, self.state, t, 42, self.llm, "BRIEF TEXT", PROFILE, today)

    def records(self, dry: bool = False) -> list[dict]:
        return JsonlStore(self.state / ("upwork_jobs_dry.jsonl" if dry else "upwork_jobs.jsonl")).read()

    def record(self, n: int) -> dict:
        return next(r for r in self.records() if r["id"] == _mid(n))


def test_fit_job_becomes_a_card_with_its_record(tmp_path):
    env = IntakeEnv(tmp_path)
    r = env.run([_mail(1)])
    cid = f"up-{_mid(1)}"
    assert r.carded == [cid] and r.llm_calls == 1 and len(env.t.sent) == 1
    chat, text, buttons = env.t.sent[0]
    assert chat == 42 and "Upwork · Example Fitness Companion App" in text and "Best regards,\nJordan</pre>" in text
    assert [b.data for row in buttons for b in row] == [f"done:{cid}", f"skip:{cid}"]
    card = JsonlStore(env.state / "cards.jsonl").get(cid)
    assert card["kind"] == "upwork" and card["status"] == "open" and card["tg_message_id"] == 100
    assert (card["title"], card["country"], card["alert_date"], card["saved_search"], card["budget"]) == (
        "Example Fitness Companion App", "PRT", "2026-10-29", "android kotlin", "fixed $2,000")
    assert card["draft"].endswith("Best regards,\nJordan") and card["job_link"].startswith("https://link.example.com/")
    assert card["reason"]   # the model's one-line fit reason travels with the card
    rec = env.record(1)
    assert rec["status"] == "carded" and rec["card"] == cid and rec["fit"] == "high" and rec["day"] == "2026-10-29"
    assert rec["fp"].startswith("example fitness companion app|fixed:2000-2000|PRT|we are looking for an")
    assert processed_ids(env.state, dry_run=False) == {_mid(1)}


def test_freelancer_plus_alert_becomes_a_card_with_a_direct_job_link(tmp_path):
    """04.11.2026: the Freelancer Plus alerts (donotreply@upwork.com) were never read."""
    env = IntakeEnv(tmp_path)
    mail = {"message_id": _mid(7), "account": "main", "date": "Thu, 29 Oct 2026 10:00:00 +0000",
            "subject": PLUS_SUBJECT, "trashed": False, "body": plus_alert_body(),
            "mail_url": f"https://mail.google.com/mail/u/0/#all/{_mid(7)}"}
    r = env.run([mail])
    assert r.carded == [f"up-{_mid(7)}"] and r.unknown == []
    card = JsonlStore(env.state / "cards.jsonl").get(f"up-{_mid(7)}")
    assert (card["country"], card["budget"], card["saved_search"]) == ("Portugal", "fixed $3,500", "")
    assert PLUS_JOB_ID in card["job_link"]


def test_filtered_job_is_recorded_without_the_model(tmp_path):
    env = IntakeEnv(tmp_path)
    no_payment = tuple(m for m in META if "Payment" not in m)
    r = env.run([_mail(1, meta=no_payment)])
    assert r.carded == [] and env.llm.prompts == [] and env.t.sent == []
    assert env.record(1)["status"] == "skipped" and env.record(1)["reasons"] == ["payment verification unknown"]


def test_low_fit_is_recorded_without_a_card(tmp_path):
    env = IntakeEnv(tmp_path, answers=[LOW])
    r = env.run([_mail(1)])
    assert r.carded == [] and env.t.sent == [] and env.record(1)["reasons"] == ["low fit: Flutter only"]


def test_same_job_from_two_saved_searches_gives_one_card(tmp_path):
    env = IntakeEnv(tmp_path)
    r = env.run([_mail(1, hour=9, saved_search="android kotlin"), _mail(2, hour=9, saved_search="kotlin multiplatform")])
    assert len(r.carded) == 1 and r.duplicates == 1 and len(env.llm.prompts) == 1 and len(env.t.sent) == 1
    dup = next(rec for rec in env.records() if rec["status"] == "duplicate")
    first = next(rec for rec in env.records() if rec["status"] == "carded")
    assert dup["reasons"] == [f"same job as {first['id']}"] and dup["fp"] == first["fp"]


def test_two_jobs_with_the_same_title_budget_and_country_both_get_a_card(tmp_path):
    env = IntakeEnv(tmp_path)
    r = env.run([_mail(1, hour=9), _mail(2, hour=10, snippet="Build a parking app with offline maps\n...")])
    assert len(r.carded) == 2 and r.duplicates == 0


def test_a_processed_mail_is_silent_on_the_next_pass(tmp_path):
    env = IntakeEnv(tmp_path)
    env.run([_mail(1)])
    r = env.run([_mail(1)])
    assert r.carded == [] and r.skipped == [] and len(env.t.sent) == 1 and len(env.llm.prompts) == 1


def test_pass_card_limit_leaves_the_rest_for_the_next_pass(tmp_path):
    env = IntakeEnv(tmp_path, max_cards_per_pass=1)
    mails = [_mail(1, hour=9, title="Kotlin app one"), _mail(2, hour=11, title="Kotlin app two")]
    r = env.run(mails)
    assert r.carded == [f"up-{_mid(2)}"]   # newest first: a fresh Upwork job is worth the most
    assert {"message_id": _mid(1), "reason": "pass card limit"} in r.skipped
    assert processed_ids(env.state, dry_run=False) == {_mid(2)}
    assert env.run(mails).carded == [f"up-{_mid(1)}"]


def test_daily_card_limit_records_the_rest(tmp_path):
    env = IntakeEnv(tmp_path, max_cards_per_day=1)
    r = env.run([_mail(1, hour=9, title="Kotlin app one"), _mail(2, hour=11, title="Kotlin app two")])
    assert r.carded == [f"up-{_mid(2)}"] and env.record(1)["reasons"] == ["daily card limit"]
    tomorrow = env.run([_mail(3, title="Kotlin app three")], today=date(2026, 10, 30))
    assert tomorrow.carded == [f"up-{_mid(3)}"]


def test_llm_budget_leaves_the_mail_unrecorded(tmp_path):
    env = IntakeEnv(tmp_path, max_llm_per_pass=1)
    r = env.run([_mail(1, hour=9, title="Kotlin app one"), _mail(2, hour=11, title="Kotlin app two")])
    assert r.carded == [f"up-{_mid(2)}"] and r.llm_calls == 1
    assert {"message_id": _mid(1), "reason": "llm budget"} in r.skipped
    assert processed_ids(env.state, dry_run=False) == {_mid(2)}


@pytest.mark.parametrize(("exc", "reason"), [(LockBusy("busy"), "lock busy"),
                                             (RuntimeError("claude -p exit 1: auth"), "llm: claude -p exit 1: auth")])
def test_lock_busy_or_claude_failure_stops_drafting_for_the_pass(tmp_path, exc, reason):
    env = IntakeEnv(tmp_path, llm=RaisingLLM(exc))
    r = env.run([_mail(1, hour=9, title="Kotlin app one"), _mail(2, hour=11, title="Kotlin app two")])
    assert r.carded == [] and len(env.llm.prompts) == 1 and env.records() == []
    assert [s["reason"] for s in r.skipped] == [reason, reason]


def test_invalid_answer_twice_records_skip(tmp_path):
    env = IntakeEnv(tmp_path, answers=[RATE, RATE])
    r = env.run([_mail(1)])
    assert r.carded == [] and r.llm_calls == 2 and env.t.sent == []
    assert "Your previous answer was rejected: the proposal names a money figure" in env.llm.prompts[1]
    assert env.record(1)["status"] == "skipped" and env.record(1)["reasons"][0].startswith("llm: the proposal names")


def test_corrective_retry_gives_the_card(tmp_path):
    env = IntakeEnv(tmp_path, answers=["Sorry, here is the text without JSON", ANSWER])
    assert env.run([_mail(1)]).carded == [f"up-{_mid(1)}"] and len(env.llm.prompts) == 2


def test_forbidden_name_is_never_carded(tmp_path):
    leak = json.dumps({**GOOD, "proposal": "At Example Corp I shipped the app."})
    env = IntakeEnv(tmp_path, answers=[leak, leak], forbidden_names=["Example Corp"])
    r = env.run([_mail(1)])
    assert r.carded == [] and "Never name any of these" in env.llm.prompts[0]
    assert env.record(1)["reasons"] == ["llm: forbidden name: Example Corp"]


def test_telegram_failure_leaves_the_mail_for_the_next_pass(tmp_path):
    env = IntakeEnv(tmp_path)
    r = env.run([_mail(1)], transport=BrokenTransport())
    assert r.carded == [] and env.records() == [] and r.skipped[0]["reason"].startswith("send: Bad Request")
    assert env.run([_mail(1)]).carded == [f"up-{_mid(1)}"]


def test_dry_run_touches_only_the_dry_files(tmp_path):
    env = IntakeEnv(tmp_path, dry_run=True)
    r = env.run([_mail(1)])
    assert r.carded == [f"up-{_mid(1)}"] and env.t.sent == []
    dry = JsonlStore(env.state / "cards_dry.jsonl").read()
    assert len(dry) == 1 and "Upwork · Example Fitness Companion App" in dry[0]["text"]
    assert not (env.state / "cards.jsonl").exists() and not (env.state / "upwork_jobs.jsonl").exists()
    assert processed_ids(env.state, dry_run=True) == {_mid(1)} and processed_ids(env.state, dry_run=False) == set()


def test_without_a_transport_cards_go_to_the_dry_file_and_the_state_is_live(tmp_path):
    env = IntakeEnv(tmp_path)
    assert env.run([_mail(1)], transport=None).carded == [f"up-{_mid(1)}"]
    assert len(JsonlStore(env.state / "cards_dry.jsonl").read()) == 1 and env.record(1)["status"] == "carded"


def test_trashed_alert_is_recorded_without_the_model(tmp_path):
    env = IntakeEnv(tmp_path)
    r = env.run([{**_mail(1), "trashed": True}])
    assert r.carded == [] and env.llm.prompts == [] and env.record(1)["reasons"] == ["trashed by the owner"]


def test_unknown_format_is_recorded_and_reported(tmp_path):
    env = IntakeEnv(tmp_path)
    r = env.run([_mail(1, subject="Jobs picked for you")])
    assert r.carded == [] and env.llm.prompts == [] and env.record(1)["status"] == "unknown_format"
    assert r.unknown == [{"message_id": _mid(1), "subject": "Jobs picked for you",
                          "reason": env.record(1)["reasons"][0]}]


def test_client_template_declares_every_key_with_intake_off():
    from pathlib import Path

    import yaml
    path = Path(__file__).resolve().parent.parent / "templates" / "client" / "targeting.yaml"
    template = yaml.safe_load(path.read_text(encoding="utf-8"))
    block = template["upwork"]
    assert block["intake"] is False and intake_config(template) is None
    assert set(block) == {"intake", "require_payment_verified", "min_fixed_usd", "min_hourly_usd",
                          "max_cards_per_day", "max_cards_per_pass", "bid_hourly_usd", "max_llm_per_pass"}
    on = intake_config({**template, "upwork": {**block, "intake": True}})
    assert on == replace(IntakeConfig(), forbidden_names=[])   # the template states the code defaults
