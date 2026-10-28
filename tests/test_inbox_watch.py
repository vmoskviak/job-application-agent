"""Прохід inbox watcher-а (bot.inbox_watch): класи → трекер → картки з чернетками → follow-ups."""
from __future__ import annotations

from dataclasses import replace
from datetime import date

from bot.inbox_watch import WatchConfig, run_notify, run_watch, watch_summary
from bot.state import JsonlStore, SeenSet, set_paused
from tests.inbox_fixtures import AGENCIES, CONTRACT_TRACKER, MAILS, jordan_profile
from tests.test_llm import FakeLLM
from tests.test_telegram_api import FakeTransport
from tests.test_telegram_dispatch import FakeSender

TODAY = date(2026, 10, 26)
_IDS = {name: f"{i + 1:016x}" for i, name in enumerate(MAILS)}
CFG = WatchConfig(agencies_de=AGENCIES, followup_days=7, reply_account="main",
                  gmail_address="jordan@example.com", lang="en")
INTERVIEW_JSON = '{"class": "interview", "draft": "Thanks, Thursday at 10 works for me."}'


def ITEM_ID(name: str) -> str:
    return _IDS[name]


def ITEM(name: str, **over) -> dict:
    base = MAILS[name]
    item = {"message_id": _IDS[name], "thread_id": "t-" + _IDS[name], "account": "main",
            "date": "Mon, 26 Oct 2026 10:00:00 +0000", "from": base["from"], "subject": base["subject"],
            "body": base["body"], "classification": base["classification"], "company": base["company"],
            "thread_answered": False}
    return {**item, **over}


class Env:
    def __init__(self, tmp_path, llm=None, tracker_text=CONTRACT_TRACKER):
        self.tracker = tmp_path / "tracker.md"
        self.tracker.write_text(tracker_text, encoding="utf-8")
        self.state = tmp_path / "state"
        self.t = FakeTransport()
        self.llm = llm or FakeLLM(lambda p: INTERVIEW_JSON)
        self.sender = FakeSender()
        self.profile = jordan_profile()

    def run(self, items, cfg=CFG, today=TODAY, transport="default"):
        t = self.t if transport == "default" else transport
        return run_watch(cfg, items, self.tracker, self.state, t, 42, self.llm, self.sender,
                         "BRIEF TEXT", self.profile, today)

    def text(self) -> str:
        return self.tracker.read_text(encoding="utf-8")


def test_confirmed_updates_tracker_without_card(tmp_path):
    env = Env(tmp_path)
    r = env.run([ITEM("greenhouse_confirmed_en")])
    assert r.tracker_updates == [("Example Co", "confirmed")] and env.t.sent == [] and r.carded == []
    assert "| confirmed | 2026-10-26 |" in env.text() and env.llm.prompts == []
    assert ITEM_ID("greenhouse_confirmed_en") in SeenSet(env.state / "inbox_seen.json")


def test_interview_posts_card_with_draft_and_sets_status(tmp_path):
    env = Env(tmp_path)
    r = env.run([ITEM("company_interview_de")])
    assert r.tracker_updates == [("Example Co", "interview")] and len(env.t.sent) == 1
    chat, text, buttons = env.t.sent[0]
    assert chat == 42 and "<pre>" in text and "Thursday at 10" in text and "Guten Tag," in text
    cid = "re-" + ITEM_ID("company_interview_de")[:12]
    rec = JsonlStore(env.state / "cards.jsonl").get(cid)
    assert rec["status"] == "open" and rec["tg_message_id"] == 100 and rec["account"] == "main"
    assert rec["message_id"] == ITEM_ID("company_interview_de") and rec["company"] == "Example Co"
    assert r.carded == [cid] and r.llm_calls == 1
    assert [b.data for row in buttons for b in row if b.data] == [f"send:{cid}", f"skip:{cid}"]


def test_seen_mail_posts_no_second_card(tmp_path):
    env = Env(tmp_path)
    env.run([ITEM("company_interview_de")])
    env.run([ITEM("company_interview_de")])
    assert len(env.t.sent) == 1 and env.text().count("| interview |") == 1 and len(env.llm.prompts) == 1


def test_rejection_updates_tracker_and_posts_info_card(tmp_path):
    env = Env(tmp_path)
    r = env.run([ITEM("ats_rejected_en")])
    assert r.tracker_updates == [("Example Co", "rejected")] and len(env.t.sent) == 1
    assert env.t.sent[0][2] == [] and env.llm.prompts == []
    assert JsonlStore(env.state / "cards.jsonl").read()[0]["status"] == "info"


def test_agency_outreach_prompt_is_german(tmp_path):
    env = Env(tmp_path, llm=FakeLLM(['{"class": "outreach", "draft": "Gern, ich melde mich."}']))
    item = ITEM("agency_outreach_de", subject="Opportunity", body="We have a contract role for you.")
    r = env.run([item])
    assert "Mein Stundensatz liegt bei 95" in env.llm.prompts[0] and "written in German" in env.llm.prompts[0]
    assert "Mein Stundensatz liegt bei 95" in env.t.sent[0][1] and r.tracker_updates == []


def test_non_agency_outreach_has_no_rate(tmp_path):
    env = Env(tmp_path, llm=FakeLLM(['{"class": "outreach", "draft": "Thanks for reaching out."}']))
    env.run([ITEM("linkedin_inmail_de")])
    assert "Rate line: (none" in env.llm.prompts[0] and "Stundensatz" not in env.t.sent[0][1]


def test_llm_garbage_still_posts_card(tmp_path):
    env = Env(tmp_path, llm=FakeLLM(["no json here"]))
    r = env.run([ITEM("linkedin_inmail_de")])
    assert len(env.t.sent) == 1 and "draft unavailable" in env.t.sent[0][1].lower()
    assert r.skipped[0]["reason"].startswith("llm")
    assert ITEM_ID("linkedin_inmail_de") in SeenSet(env.state / "inbox_seen.json")
    assert JsonlStore(env.state / "cards.jsonl").read()[0]["draft"] is None


def test_terminal_status_not_rolled_back(tmp_path):
    rejected = CONTRACT_TRACKER.replace("| Example Co | Android Engineer | HN Who is hiring (email) | — | sent |",
                                        "| Example Co | Android Engineer | HN Who is hiring (email) | — | rejected |")
    env = Env(tmp_path, tracker_text=rejected)
    r = env.run([ITEM("company_interview_de")])
    assert r.tracker_updates == [] and "| rejected |" in env.text() and len(env.t.sent) == 1


def test_system_and_stranger_mails_make_no_card(tmp_path):
    env = Env(tmp_path)
    r = env.run([ITEM("freelance_newsletter_de"), ITEM("stranger_en")])
    assert env.t.sent == [] and r.system == 1 and env.llm.prompts == []
    assert r.skipped == [{"message_id": ITEM_ID("stranger_en"), "reason": "unknown sender"}]
    seen = SeenSet(env.state / "inbox_seen.json")
    assert ITEM_ID("freelance_newsletter_de") in seen and ITEM_ID("stranger_en") in seen


def test_unknown_mail_from_platform_goes_to_llm_and_takes_its_class(tmp_path):
    env = Env(tmp_path, llm=FakeLLM(['{"class": "outreach", "draft": "Thanks."}']))
    item = ITEM("stranger_en", **{"from": "Recruiter <r@mail.hays.de>", "company": None})
    env.run([item])
    assert len(env.llm.prompts) == 1 and "decide the class" in env.llm.prompts[0].lower()
    assert "Outreach" in env.t.sent[0][1]


def test_already_answered_thread_gets_no_card(tmp_path):
    env = Env(tmp_path)
    r = env.run([ITEM("company_info_request_de", thread_answered=True)])
    assert env.t.sent == [] and env.llm.prompts == [] and r.skipped[0]["reason"] == "already answered"


def test_llm_budget_caps_drafts(tmp_path):
    env = Env(tmp_path)
    r = env.run([ITEM("company_interview_de"), ITEM("linkedin_inmail_de")], cfg=replace(CFG, max_llm_per_run=1))
    assert r.llm_calls == 1 and len(env.t.sent) == 2
    assert sum("draft unavailable" in s[1].lower() for s in env.t.sent) == 1


def test_card_budget_leaves_rest_unseen(tmp_path):
    env = Env(tmp_path)
    r = env.run([ITEM("company_interview_de"), ITEM("linkedin_inmail_de")], cfg=replace(CFG, max_cards_per_run=1))
    assert len(env.t.sent) == 1 and len(r.carded) == 1
    assert ITEM_ID("linkedin_inmail_de") not in SeenSet(env.state / "inbox_seen.json")


def test_followup_due_is_sent_and_noted(tmp_path):
    env = Env(tmp_path)
    r = env.run([], today=date(2026, 10, 29))
    assert r.followups_sent == ["Example Co"]
    assert env.sender.calls[0][:2] == ("jobs", "1a2b3c4d5e6f7a8b") and "Hello," in env.sender.calls[0][2]
    assert "Android Engineer" in env.sender.calls[0][2]
    assert "· 2026-10-29 follow-up Msg fake-id-1 |" in env.text()
    assert len(env.t.sent) == 1 and "Follow-up" in env.t.sent[0][1]
    assert {s["company"] for s in r.skipped} == {"Portal Co"}


def test_followup_not_sent_twice(tmp_path):
    env = Env(tmp_path)
    env.run([], today=date(2026, 10, 29))
    r = env.run([], today=date(2026, 10, 30))
    assert r.followups_sent == [] and len(env.sender.calls) == 1


def test_followup_language_follows_agency_domain(tmp_path):
    agency_tracker = CONTRACT_TRACKER.replace("jobs@example-co.com", "recruiter@example-agentur.de")
    env = Env(tmp_path, tracker_text=agency_tracker)
    env.run([], today=date(2026, 10, 29))
    assert env.sender.calls[0][2].startswith("Sehr geehrte Damen und Herren")


def test_paused_skips_followups_but_still_posts_cards(tmp_path):
    env = Env(tmp_path)
    set_paused(env.state, True)
    r = env.run([ITEM("company_interview_de")], today=date(2026, 10, 29))
    assert r.followups_sent == [] and env.sender.calls == [] and len(env.t.sent) == 1


def test_dry_run_writes_cards_to_file_only(tmp_path):
    env = Env(tmp_path)
    r = env.run([ITEM("company_interview_de")], cfg=replace(CFG, dry_run=True), today=date(2026, 10, 29))
    assert env.t.sent == [] and env.sender.calls == [] and r.tracker_updates == [] and r.followups_sent == []
    assert "| sent |" in env.text() and (env.state / "cards_dry.jsonl").exists()
    assert ITEM_ID("company_interview_de") not in SeenSet(env.state / "inbox_seen.json")


def test_no_transport_writes_dry_cards_but_updates_tracker(tmp_path):
    env = Env(tmp_path)
    r = env.run([ITEM("company_interview_de")], transport=None)
    assert r.tracker_updates == [("Example Co", "interview")] and (env.state / "cards_dry.jsonl").exists()


def test_watch_summary(tmp_path):
    env = Env(tmp_path)
    env.run([ITEM("company_interview_de")])
    s = watch_summary(env.state, TODAY)
    assert s["open_cards"] == 1 and s["sent_today"] == 0 and s["followups_today"] == 0
    assert s["paused"] is False and s["last_watch"]


# --- виправлення після рев'ю гілки (26.10.2026) ---

HEADER = CONTRACT_TRACKER[:CONTRACT_TRACKER.index("| 1 |")]


def test_followup_goes_to_employer_address(tmp_path):
    """Msg-ID у трекері це наш власний лист: без явного адресата reply пішов би нам самим."""
    env = Env(tmp_path)
    r = env.run([], today=date(2026, 10, 29))
    assert r.followups_sent == ["Example Co"] and env.sender.calls[0][3] == "jobs@example-co.com"


def test_followup_without_employer_address_is_skipped(tmp_path):
    no_mail = CONTRACT_TRACKER.replace("E-Mail на jobs@example-co.com, Msg-ID 1a2b3c4d5e6f7a8b",
                                       "E-Mail, Msg-ID 1a2b3c4d5e6f7a8b")
    env = Env(tmp_path, tracker_text=no_mail)
    r = env.run([], today=date(2026, 10, 29))
    assert r.followups_sent == [] and env.sender.calls == []
    assert any(s.get("company") == "Example Co" and s["reason"] == "no employer address" for s in r.skipped)


def test_lock_busy_leaves_mail_unseen_and_stops_llm(tmp_path):
    from bot.llm import LockBusy

    def busy(prompt):
        raise LockBusy("busy")

    env = Env(tmp_path, llm=FakeLLM(busy))
    r = env.run([ITEM("company_interview_de"), ITEM("linkedin_inmail_de")])
    assert env.t.sent == [] and r.llm_calls == 1 and r.carded == []
    seen = SeenSet(env.state / "inbox_seen.json")
    assert ITEM_ID("company_interview_de") not in seen and ITEM_ID("linkedin_inmail_de") not in seen
    assert [s["reason"] for s in r.skipped] == ["lock busy", "lock busy"]


def test_dry_run_has_its_own_seen_set(tmp_path):
    env = Env(tmp_path)
    cfg = replace(CFG, dry_run=True)
    env.run([ITEM("company_interview_de")], cfg=cfg)
    env.run([ITEM("company_interview_de")], cfg=cfg)
    assert len(env.llm.prompts) == 1
    assert ITEM_ID("company_interview_de") in SeenSet(env.state / "inbox_seen_dry.json")
    assert ITEM_ID("company_interview_de") not in SeenSet(env.state / "inbox_seen.json")


def test_followup_guard_caps_per_pass(tmp_path):
    rows = [f"| {i} | 2026-10-20 | Firma {i} GmbH | Android Engineer | HN Who is hiring (email) | — | sent "
            f"| 2026-10-20 | чекати відповідь | E-Mail на jobs@firma{i}.example, Msg-ID {i:016x} |" for i in range(1, 14)]
    env = Env(tmp_path, tracker_text=HEADER + "\n".join(rows) + "\n")
    r = env.run([], today=date(2026, 10, 29))
    assert len(r.followups_sent) == 12 and len(env.sender.calls) == 12
    assert [s["reason"] for s in r.skipped] == ["follow-up guard"]


def test_followup_note_keyed_by_row(tmp_path):
    two = CONTRACT_TRACKER.replace(
        "| 3 | 2026-10-25 | Fresh Co | Kotlin Multiplatform Engineer | HN Who is hiring (email) | — | sent "
        "| 2026-10-25 | чекати відповідь | E-Mail на hiring@fresh-co.example, Msg-ID 1c2b3c4d5e6f7a8d |",
        "| 3 | 2026-10-22 | Example Co | Kotlin Multiplatform Engineer | HN Who is hiring (email) | — | sent "
        "| 2026-10-22 | чекати відповідь | E-Mail на jobs@example-co.com, Msg-ID 1c2b3c4d5e6f7a8d |")
    env = Env(tmp_path, tracker_text=two)
    r = env.run([], today=date(2026, 10, 29))
    assert r.followups_sent == ["Example Co", "Example Co"]
    marked = [ln for ln in env.text().splitlines() if ln.startswith(("| 1 |", "| 3 |"))]
    assert len(marked) == 2 and all("follow-up Msg" in ln for ln in marked)
    assert env.run([], today=date(2026, 10, 30)).followups_sent == []


def test_older_confirmation_does_not_roll_back_interview(tmp_path):
    env = Env(tmp_path)
    newer = ITEM("company_interview_de", date="Tue, 27 Oct 2026 10:00:00 +0000")
    older = ITEM("greenhouse_confirmed_en", date="Mon, 26 Oct 2026 09:00:00 +0000")
    r = env.run([newer, older])
    assert "| interview |" in env.text() and "| confirmed |" not in env.text()
    assert r.tracker_updates == [("Example Co", "confirmed"), ("Example Co", "interview")]


def test_info_request_from_known_company_gets_rate(tmp_path):
    env = Env(tmp_path, llm=FakeLLM(['{"class": "info_request", "draft": "Gern, mein Stundensatz steht unten."}']))
    env.run([ITEM("company_info_request_de")])
    assert "Mein Stundensatz liegt bei 95" in env.llm.prompts[0]
    assert "Mein Stundensatz liegt bei 95" in env.t.sent[0][1]


def test_llm_class_cannot_set_terminal_status(tmp_path):
    env = Env(tmp_path, llm=FakeLLM(['{"class": "rejected", "draft": "Schade."}']))
    item = ITEM("stranger_en", **{"from": "Recruiter <r@mail.hays.de>", "company": "Example Co"})
    r = env.run([item])
    assert r.tracker_updates == [] and "| sent |" in env.text()


def test_followup_language_de_for_german_domain(tmp_path):
    env = Env(tmp_path, tracker_text=CONTRACT_TRACKER.replace("jobs@example-co.com", "jobs@example-co.de"))
    env.run([], today=date(2026, 10, 29))
    assert env.sender.calls[0][2].startswith("Sehr geehrte Damen und Herren")


def test_send_failure_leaves_mail_unseen(tmp_path):
    env = Env(tmp_path)

    def boom(chat_id, text, buttons=None):
        raise RuntimeError("telegram 400")

    env.t.send = boom
    r = env.run([ITEM("company_interview_de")])
    assert ITEM_ID("company_interview_de") not in SeenSet(env.state / "inbox_seen.json")
    assert r.carded == [] and r.skipped[-1]["reason"].startswith("send")


def test_forbidden_name_in_draft_holds_card_for_non_tracker_mail(tmp_path):
    """Чужому адресату (агентура, платформа) поточний роботодавець не називається."""
    env = Env(tmp_path, llm=FakeLLM(['{"class": "outreach", "draft": "Bei Example Corp baue ich die SuperApp."}']))
    r = env.run([ITEM("agency_outreach_de")], cfg=replace(CFG, forbidden_names=["Example Corp"]))
    assert len(env.t.sent) == 1 and "draft unavailable" in env.t.sent[0][1].lower()
    assert r.skipped[0]["reason"] == "forbidden name: Example Corp"
    assert "Example Corp" in env.llm.prompts[0]


def test_forbidden_name_allowed_for_tracker_company(tmp_path):
    env = Env(tmp_path, llm=FakeLLM(['{"class": "interview", "draft": "Bei Example Corp baue ich die SuperApp."}']))
    env.run([ITEM("company_interview_de")], cfg=replace(CFG, forbidden_names=["Example Corp"]))
    assert "<pre>" in env.t.sent[0][1] and "Example Corp" in env.t.sent[0][1]


# --- режим notify (клієнти з ранковим агентом: лише сповіщення, трекер не чіпати) ---

def _notify(env: Env, items, transport="default", cfg=CFG):
    t = env.t if transport == "default" else transport
    return run_notify(cfg, items, env.state, t, 42, "<b>Erika</b> · ", TODAY)


def test_notify_mode_posts_interview_and_request_without_touching_tracker(tmp_path):
    env = Env(tmp_path)
    before = env.text()
    res = _notify(env, [ITEM("company_interview_de"), ITEM("company_info_request_de"),
                        ITEM("greenhouse_confirmed_en"), ITEM("ats_rejected_en"), ITEM("stranger_en")])
    assert len(env.t.sent) == 2 and res.carded and not res.tracker_updates
    assert env.text() == before
    assert env.sender.calls == []
    _, text, buttons = env.t.sent[0]
    assert text.startswith("<b>Erika</b> · <b>Example Co</b>")
    assert buttons[0][0].url.startswith("https://mail.google.com/")
    assert JsonlStore(env.state / "cards.jsonl").read()[0]["kind"] == "notify"


def test_notify_mode_marks_everything_seen_and_never_repeats(tmp_path):
    env = Env(tmp_path)
    items = [ITEM("company_interview_de"), ITEM("ats_rejected_en"), ITEM("freelance_newsletter_de")]
    _notify(env, items)
    res = _notify(env, items)
    assert len(env.t.sent) == 1 and res.carded == []
    seen = SeenSet(env.state / "inbox_seen.json")
    assert all(ITEM_ID(n) in seen for n in ("company_interview_de", "ats_rejected_en", "freelance_newsletter_de"))


def test_notify_mode_unknown_mail_from_tracker_company_is_posted(tmp_path):
    env = Env(tmp_path)
    _notify(env, [ITEM("stranger_en", company="Example Co", classification="unknown")])
    assert len(env.t.sent) == 1


def test_notify_mode_without_transport_writes_dry_file(tmp_path):
    env = Env(tmp_path)
    res = _notify(env, [ITEM("company_interview_de")], transport=None)
    assert res.carded and JsonlStore(env.state / "cards_dry.jsonl").read()
    assert env.t.sent == []


def test_notify_mode_send_failure_leaves_mail_unseen(tmp_path):
    env = Env(tmp_path)

    class Broken(type(env.t)):
        def send(self, chat_id, text, buttons=None):
            raise RuntimeError("telegram down")

    res = run_notify(CFG, [ITEM("company_interview_de")], env.state, Broken(), 42, "", TODAY)
    assert res.carded == [] and res.skipped[0]["reason"].startswith("send:")
    assert ITEM_ID("company_interview_de") not in SeenSet(env.state / "inbox_seen.json")


def test_notify_mode_dry_run_has_own_seen_set_and_skips_autoreplies(tmp_path):
    env = Env(tmp_path)
    auto = ITEM("company_interview_de", message_id="aa" * 8, subject="Automatic reply: Bewerbung", company="Example Co")
    _notify(env, [ITEM("company_interview_de"), auto], cfg=replace(CFG, dry_run=True))
    assert env.t.sent == [] and len(JsonlStore(env.state / "cards_dry.jsonl").read()) == 1
    assert ITEM_ID("company_interview_de") not in SeenSet(env.state / "inbox_seen.json")
    res = _notify(env, [ITEM("company_interview_de"), auto])
    assert len(env.t.sent) == 1 and res.system == 1
