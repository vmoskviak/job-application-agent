"""Прохід браузерного воркера: подача form-лідів, hand-off картки, ліміт, пауза, лок машини."""
from __future__ import annotations

import fcntl
import json
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from bot.apply.ats_answers import AnswerContext
from bot.apply.models import ApplyResult
from bot.apply_leads import (
    ApplyConfig,
    _platform_card_text,
    handoff_cid,
    handoff_deadline,
    run_apply,
)
from bot.platforms import PLATFORM_HOSTS, STATE_FILE, read_state
from bot.sendqueue import (
    DRY_FILE,
    QUEUE_FILE,
    enqueue,
    sent_today,
    sent_today_platform,
    summary,
)
from bot.state import JsonlStore, set_paused
from bot.telegram.cards import handoff_card, status_text
from bot.telegram.dispatch import Dispatcher
from bot.telegram.service import Action
from tests.test_dispatch_leads import HEADER, FakeSender, FakeTransport, _write_package

LID = "lead-2026-10-26-contract-co-senior-android-engineer"
URL = "https://jobs.ashbyhq.com/contractco/123"
TODAY = "2026-10-27"
URL1 = "https://www.linkedin.com/jobs/view/111/"
URL2 = "https://www.linkedin.com/jobs/view/222/"
GULP1 = "https://www.gulp.de/gulp2/g/projekte/agentur/C00000001"
FM1 = "https://www.freelancermap.de/projekt/test-projekt-1"
FM2 = "https://www.freelancermap.de/projekt/test-projekt-2"
ASHBY1 = "https://jobs.ashbyhq.com/otherco/5"


class FakeDriver:
    """Сценарні ApplyResult на url; probe_submitted повертає фразу на заданому опитуванні;
    offsite: url сторінки LinkedIn → (стан, зовнішнє посилання) для resolve_offsite."""

    def __init__(self) -> None:
        self.script: dict[str, ApplyResult] = {}
        self.offsite: dict[str, tuple[str, str | None]] = {}
        self.channels: list[tuple[str, str]] = []   # (url, канал) кожного виклику apply
        self.resolves = 0
        self.probe_after_polls: dict[int, str] = {}
        self.calls = 0
        self.dry_calls = 0
        self.opened = 0
        self.closed = 0
        self.probes = 0
        self.closed_after_probes: int | None = None

    def open(self, url):
        self.opened += 1
        return {"url": url}

    def apply(self, page, url, ctx, dry_run, channel):
        assert ctx.lead["company"] == "Contract Co"
        self.channels.append((url, channel))
        if dry_run:
            self.dry_calls += 1
            return ApplyResult("dry_run", "ashby", url, ["d.png"], reason="filled 5, uploaded 1, llm 0")
        self.calls += 1
        return self.script.get(url) or ApplyResult("submitted", "ashby", url, ["s.png"],
                                                   confirmation_text="thank you for applying")

    def resolve_offsite(self, page, url):
        self.resolves += 1
        return self.offsite.get(url, ("open", None))

    def probe_submitted(self, page):
        i = self.probes
        self.probes += 1
        return self.probe_after_polls.get(i, "")

    def is_closed(self, page):
        return self.closed_after_probes is not None and self.probes >= self.closed_after_probes

    def close(self, page):
        self.closed += 1


@dataclass
class Env:
    cfg: ApplyConfig
    store: JsonlStore
    cards: JsonlStore
    tracker: Path
    state: Path
    driver: FakeDriver
    transport: FakeTransport
    held_lock: Path
    cv: Path
    polls: int = 0
    cards_after_polls: dict[int, str] = field(default_factory=dict)
    clock_jump_after_polls: int | None = None
    kw: dict = field(default_factory=dict)
    day_offset: int = 0
    slept_total: float = 0.0     # паузи між подачами на одній платформі (не опитування hand-off)
    root: Path | None = None

    def now(self) -> datetime:
        day = datetime.fromisoformat("2026-10-27T10:00") + timedelta(days=self.day_offset)
        if self.clock_jump_after_polls is not None and self.polls > self.clock_jump_after_polls:
            return day.replace(hour=20, minute=0)
        return day + timedelta(seconds=self.slept_total)

    def sleep(self, seconds: float) -> None:
        if seconds != self.cfg.poll_seconds:   # пауза між подачами платформи: годинник іде далі
            self.slept_total += seconds
            return
        self.polls += 1
        if self.polls > 1000:
            raise AssertionError("очікування hand-off не закінчилось")
        status = self.cards_after_polls.get(self.polls - 1)
        if status:
            card = next(c for c in self.cards.read() if c.get("kind") == "handoff" and c.get("lead_id") == LID)
            self.cards.update(card["id"], status=status)

    # ── помічники багатоканальних тестів ──
    @property
    def today(self) -> str:
        return (date.fromisoformat(TODAY) + timedelta(days=self.day_offset)).isoformat()

    @property
    def targeting(self) -> dict:
        return self.cfg.targeting

    @property
    def limits(self) -> dict:
        return self.cfg.limits

    @property
    def platforms_path(self) -> Path:
        return self.cfg.platforms_path

    @property
    def dry(self) -> JsonlStore:
        return JsonlStore(self.state / DRY_FILE)

    def pkg(self, rec_id: str) -> Path:
        return Path(self.store.get(rec_id)["package_dir"])

    def _queue(self, rec_id: str, channel: str, url: str, platform: str | None, ats: str | None = None) -> None:
        d = self.root / "packages_2026-10-27" / rec_id
        d.mkdir(parents=True)
        lead = {"title": f"Android {rec_id}", "company": "Contract Co", "channel": channel, "apply_url": url,
                "ats": ats, "stated_rate": "€90/h"}
        (d / "lead.json").write_text(json.dumps(lead), encoding="utf-8")
        (d / "note.txt").write_text("Hello.\n", encoding="utf-8")
        (d / "meta.json").write_text(json.dumps({"slug": rec_id, "lang": "en", "subject": "S",
                                                 "attachments": [str(self.cv)]}), encoding="utf-8")
        self.store.append({"id": rec_id, "kind": "lead", "slug": rec_id, "company": "Contract Co",
                           "title": f"Android {rec_id}", "channel": channel, "apply_url": url, "platform": platform,
                           "package_dir": str(d), "status": "queued", "reasons": [], "attempts": 0,
                           "sent_id": None, "sent_at": None})

    def queue_easy(self, rec_id: str, url: str) -> None:
        self._queue(rec_id, "linkedin_easy", url, "linkedin")

    def queue_offsite(self, rec_id: str, url: str) -> None:
        self._queue(rec_id, "linkedin_offsite", url, "linkedin")

    def queue_platform(self, rec_id: str, url: str, platform: str) -> None:
        self._queue(rec_id, "platform_login", url, platform)

    def queue_form(self, rec_id: str, url: str) -> None:
        self._queue(rec_id, "form", url, None, ats="ashby")

    def cards_sent_of_kind(self, kind: str) -> int:
        """Скільки карток цього виду пішло в Telegram (поки лише «platform»)."""
        assert kind == "platform"
        texts = {_platform_card_text(p, s, self.cfg) for p in ("linkedin", *PLATFORM_HOSTS)
                 for s in ("login_required", "restricted", "platform_quota")}
        return sum(m.text in texts for m in self.transport.sent)

    def run(self, day_offset: int = 0):
        self.day_offset = day_offset
        return run_apply(**self.kw)


@pytest.fixture
def env(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    cv = tmp_path / "CV_Android.pdf"
    cv.write_bytes(b"%PDF-1.4")
    tracker = tmp_path / "tracker.md"
    tracker.write_text(HEADER, encoding="utf-8")
    lead = {"title": "Senior Android Engineer", "company": "Contract Co", "category": "android",
            "source": "HN Who is hiring", "source_url": "https://news.ycombinator.com/item?id=1",
            "channel": "form", "apply_url": URL, "ats": "ashby", "stated_rate": "€90/h"}
    pkg = _write_package(tmp_path, "contract-co-senior-android-engineer", lead, "en",
                         "Contract: Senior Android Engineer", "Hello Contract Co team,\n\nBody.", [cv])
    store = JsonlStore(state / QUEUE_FILE)
    enqueue(store, pkg, [], "2026-10-26", "2026-10-26 20:40")
    held = tmp_path / "held.lock"
    cfg = ApplyConfig(limits={"email": 5, "form": 5, "linkedin": 5, "freelancermap": 2, "gulp": 3}, lang="uk",
                      chat_id=4242, dry_run=False, vnc_hint="vnc://192.0.2.10:5900", poll_seconds=20,
                      targeting={"platforms": {"linkedin": "on", "freelancermap": "on", "gulp": "on"}},
                      platforms_path=state / STATE_FILE)
    e = Env(cfg, store, JsonlStore(state / "cards.jsonl"), tracker, state, FakeDriver(), FakeTransport(), held, cv,
            root=tmp_path)
    e.kw = {"cfg": cfg, "store": store, "cards": e.cards, "tracker": tracker, "state_dir": state,
            "driver": e.driver, "transport": e.transport, "now": e.now, "sleep": e.sleep,
            "ctx_for": lambda lead_, pkg_: AnswerContext({}, lead_, pkg_.note, cv, date(2026, 10, 27), "en")}
    with open(held, "a") as fd:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)   # «інший процес» тримає лок машини
        yield e
        fcntl.flock(fd, fcntl.LOCK_UN)


def test_submitted_lead_gets_tracker_row_and_receipt(env):
    env.driver.script[URL] = ApplyResult("submitted", "ashby", URL, ["s.png"], confirmation_text="thank you for applying")
    res = run_apply(**env.kw)
    assert res.sent == [LID] and env.store.get(LID)["status"] == "sent"
    assert "portal_submitted" in env.tracker.read_text() and any(
        k in env.transport.sent[-1].text for k in ("Applied", "Подано"))


def test_submitted_record_fields_tracker_row_and_page_closed(env):
    run_apply(**env.kw)
    rec = env.store.get(LID)
    assert rec["sent_id"] == "form:thank you for applying" and rec["sent_at"] == f"{TODAY} 10:00"
    assert rec["screenshots"] == ["s.png"] and rec["attempts"] == 1
    assert ("| 1 | 2026-10-27 | Contract Co | Senior Android Engineer | contract: ashby | €90/h | "
            "portal_submitted (портал) | 2026-10-27 | wait reply until 2026-11-03 | "
            f"{URL}, s.png |") in env.tracker.read_text(encoding="utf-8")
    assert env.driver.opened == 1 and env.driver.closed == 1
    assert env.transport.sent[-1].text.startswith("Подано") and "form" in env.transport.sent[-1].text


def test_limit_counts_from_file(env):
    for i in range(5):
        env.store.append({"id": f"old{i}", "status": "sent", "channel": "form", "sent_at": f"{TODAY} 09:00"})
    assert run_apply(**env.kw).skipped_limit == 1 and env.driver.calls == 0


def test_paused_touches_nothing(env):
    set_paused(env.state, True)
    res = run_apply(**env.kw)
    assert res.paused and env.driver.opened == 0 and env.store.get(LID)["status"] == "queued"


def test_dry_run_writes_queue_dry_and_submits_nothing(env):
    env.kw["cfg"].dry_run = True
    run_apply(**env.kw)
    assert env.driver.dry_calls == 1 and env.store.get(LID)["status"] == "queued"
    dry = JsonlStore(env.state / DRY_FILE).read()
    assert dry[0]["would_submit"] == URL and dry[0]["result"]["status"] == "dry_run"
    assert dry[0]["at"] == f"{TODAY} 10:00" and env.transport.sent == [] and env.driver.closed == 1


def test_closed_posting_becomes_held(env):
    env.driver.script[URL] = ApplyResult("closed", "ashby", URL, ["c.png"], reason="posting closed")
    res = run_apply(**env.kw)
    rec = env.store.get(LID)
    assert rec["status"] == "held" and rec["reasons"] == ["not_live"] and res.sent == []
    assert env.driver.closed == 1 and "| 1 |" not in env.tracker.read_text(encoding="utf-8")


def test_failed_apply_marks_failed_and_cards(env):
    env.driver.script[URL] = ApplyResult("failed", "ashby", URL, [], reason="goto: TimeoutError: boom")
    res = run_apply(**env.kw)
    rec = env.store.get(LID)
    assert res.failed == [LID] and rec["status"] == "failed" and "boom" in rec["error"] and rec["attempts"] == 1
    assert "boom" in env.transport.sent[0].text and env.driver.closed == 1


def test_driver_exception_marks_failed_and_closes(env):
    def boom(page, url, ctx, dry_run, channel):
        raise RuntimeError("browser gone")
    env.driver.apply = boom
    res = run_apply(**env.kw)
    assert res.failed == [LID] and "browser gone" in env.store.get(LID)["error"] and env.driver.closed == 1


def test_missing_package_marks_failed(env, tmp_path):
    import shutil
    shutil.rmtree(tmp_path / "packages_2026-10-26" / "contract-co-senior-android-engineer")
    res = run_apply(**env.kw)
    assert res.failed == [LID] and env.driver.opened == 0 and "package" in env.store.get(LID)["error"]


def test_handoff_posts_card_and_waits_for_done(env):
    env.driver.script[URL] = ApplyResult("handoff", "ashby", URL, ["f.png"], reason="captcha")
    env.cards_after_polls[2] = "done"            # оператор тисне «Подано» на третьому опитуванні
    res = run_apply(**env.kw)
    assert res.handoff == [LID] and env.store.get(LID)["status"] == "sent" and env.store.get(LID)["sent_id"] == "form:operator"
    assert "captcha" in env.transport.sent[0].text and env.transport.sent[0].buttons
    assert env.polls == 3 and env.driver.closed == 1
    rec = env.store.get(LID)
    assert rec["handoff_card"] == handoff_cid(LID) and rec["handoff_at"] == f"{TODAY} 10:00"
    assert "portal_submitted (портал)" in env.tracker.read_text(encoding="utf-8") and "f.png" in env.tracker.read_text()
    assert env.transport.sent[-1].text.startswith("Подано")


def test_handoff_card_record_is_written(env):
    env.driver.script[URL] = ApplyResult("handoff", "ashby", URL, [], reason="captcha")
    env.cards_after_polls[0] = "skipped"
    run_apply(**env.kw)
    card = env.cards.get(handoff_cid(LID))
    assert card["kind"] == "handoff" and card["lead_id"] == LID and card["company"] == "Contract Co"
    assert card["title"] == "Senior Android Engineer" and card["url"] == URL and card["reason"] == "captcha"
    assert card["at"] == f"{TODAY} 10:00"


def test_handoff_confirmed_by_page(env):
    env.driver.script[URL] = ApplyResult("handoff", "ashby", URL, [], reason="no_confirmation")
    env.driver.probe_after_polls[1] = "application submitted"
    run_apply(**env.kw)
    assert env.store.get(LID)["status"] == "sent" and env.store.get(LID)["sent_id"] == "form:application submitted"
    assert env.cards.get(handoff_cid(LID))["status"] == "done"
    assert "no screenshot" in env.tracker.read_text(encoding="utf-8")


def test_handoff_skipped_by_operator(env):
    env.driver.script[URL] = ApplyResult("handoff", "ashby", URL, [], reason="captcha")
    env.cards_after_polls[0] = "skipped"
    res = run_apply(**env.kw)
    rec = env.store.get(LID)
    assert rec["status"] == "held" and rec["reasons"] == ["operator_skipped"] and res.sent == []
    assert env.driver.closed == 1 and "| 1 |" not in env.tracker.read_text(encoding="utf-8")


def test_handoff_blocks_resubmit(env):
    env.store.update(LID, status="handoff", handoff_at=f"{TODAY} 10:00")
    assert run_apply(**env.kw).sent == [] and env.driver.calls == 0


def test_stale_handoff_expires_once(env):
    env.store.update(LID, status="handoff", handoff_at="2026-10-26 10:00")
    run_apply(**env.kw); run_apply(**env.kw)
    assert env.store.get(LID)["status"] == "failed" and env.store.get(LID)["error"].startswith("handoff_expired")
    assert sum("handoff_expired" in m.text for m in env.transport.sent) == 1


def test_after_deadline_live_pass_touches_nothing(env):
    """Рев'ю 5, Important 1: після handoff_until живих подач нема, hand-off картка була б гарантованим failed."""
    env.store.append({"id": "ho-today", "status": "handoff", "channel": "form", "handoff_at": f"{TODAY} 10:00"})
    env.clock_jump_after_polls = -1              # now() уже 20:00
    res = run_apply(**env.kw)
    assert res.after_deadline and env.driver.opened == 0 and env.transport.sent == []
    assert env.store.get(LID)["status"] == "queued" and env.store.get("ho-today")["status"] == "handoff"


def test_after_deadline_dry_run_stops_too(env):
    """Фінальне рев'ю, M3: тіньовий прохід теж стоїть після дедлайну, бо вечірній ранок
    о 20:30 відкриває той самий профіль браузера."""
    env.kw["cfg"].dry_run = True
    env.clock_jump_after_polls = -1
    res = run_apply(**env.kw)
    assert res.after_deadline and env.driver.dry_calls == 0 and env.driver.opened == 0


def _stale_handoff(env, card_status: str | None) -> str:
    cid = handoff_cid(LID)
    if card_status:
        env.cards.append({"id": cid, "kind": "handoff", "status": card_status, "lead_id": LID, "url": URL,
                          "closed_at": "2026-10-26"})
    env.store.update(LID, status="handoff", handoff_at="2026-10-26 10:00", handoff_card=cid, screenshots=["f.png"])
    return cid


def test_stale_handoff_open_card_expires(env):
    cid = _stale_handoff(env, "open")
    run_apply(**env.kw)
    assert env.store.get(LID)["status"] == "failed" and env.cards.get(cid)["status"] == "expired"


def test_stale_handoff_done_card_counts_as_submitted(env):
    """Рев'ю 5, Important 2: воркер упав, диспетчер без черги; «Подано» на картці не губиться."""
    cid = _stale_handoff(env, "done")
    res = run_apply(**env.kw)
    rec = env.store.get(LID)
    assert rec["status"] == "sent" and rec["sent_id"] == "form:operator" and rec["sent_at"] == "2026-10-26"
    assert LID in res.sent and env.cards.get(cid)["status"] == "done" and env.driver.calls == 0
    text = env.tracker.read_text(encoding="utf-8")
    assert "| 1 | 2026-10-26 | Contract Co | Senior Android Engineer | contract: ashby |" in text and f"{URL}, f.png" in text
    assert env.transport.sent[-1].text.startswith("Подано") and not any("handoff_expired" in m.text for m in env.transport.sent)


def test_stale_handoff_skipped_card_becomes_held(env):
    cid = _stale_handoff(env, "skipped")
    run_apply(**env.kw)
    rec = env.store.get(LID)
    assert rec["status"] == "held" and rec["reasons"] == ["operator_skipped"] and env.cards.get(cid)["status"] == "skipped"
    assert env.transport.sent == [] and env.driver.calls == 0


def test_stale_form_sending_fails_after_45_minutes(env):
    """Рев'ю 5, Important 3b: завислий form-запис у sending розбирає воркер, повторно не подає."""
    env.store.update(LID, status="sending", sending_at=f"{TODAY} 09:30", attempts=1)
    run_apply(**env.kw)                          # 30 хв: ще може жити
    assert env.store.get(LID)["status"] == "sending" and env.transport.sent == []
    env.store.update(LID, sending_at=f"{TODAY} 09:00")
    res = run_apply(**env.kw); run_apply(**env.kw)
    rec = env.store.get(LID)
    assert res.failed == [LID] and rec["status"] == "failed" and env.driver.calls == 0
    assert rec["error"].startswith("apply status unknown: process died during the form fill at 2026-10-27 09:00")
    assert "check the portal or the confirmation mail" in rec["error"]
    assert len(env.transport.sent) == 1 and "form" in env.transport.sent[0].text


def test_handoff_deadline_ends_wait(env):
    env.driver.script[URL] = ApplyResult("handoff", "ashby", URL, [], reason="captcha")
    env.clock_jump_after_polls = 1               # now() повертає 20:00 з другого опитування
    run_apply(**env.kw)
    assert env.store.get(LID)["status"] == "failed" and env.driver.closed == 1
    assert env.store.get(LID)["error"].startswith("handoff_expired")
    assert env.cards.get(handoff_cid(LID))["status"] == "expired"
    assert "handoff_expired" in env.transport.sent[-1].text


def test_one_handoff_per_pass(env, tmp_path):
    lead2 = {"title": "Android Lead", "company": "Second Co", "channel": "form",
             "apply_url": "https://jobs.ashbyhq.com/secondco/9", "ats": "ashby"}
    pkg2 = _write_package(tmp_path, "second-co-android-lead", lead2, "en", "S", "Hi.", [env.cv])
    enqueue(env.store, pkg2, [], TODAY, "t")
    env.driver.script[URL] = ApplyResult("handoff", "ashby", URL, [], reason="captcha")
    env.cards_after_polls[0] = "skipped"
    run_apply(**env.kw)
    assert env.driver.calls == 1 and env.store.get(f"lead-{TODAY}-second-co-android-lead")["status"] == "queued"


def _second_lead(env, tmp_path) -> str:
    lead2 = {"title": "Android Lead", "company": "Contract Co", "channel": "form",
             "apply_url": "https://jobs.ashbyhq.com/contractco/9", "ats": "ashby"}
    pkg2 = _write_package(tmp_path, "contract-co-android-lead", lead2, "en", "S", "Hi.", [env.cv])
    enqueue(env.store, pkg2, [], TODAY, "t")
    lid = f"lead-{TODAY}-contract-co-android-lead"
    env.store.update(lid, status="queued", reasons=[])   # та сама компанія: enqueue тримає дубль, тут він потрібен
    return lid


def test_no_new_live_application_within_margin_of_deadline(env, tmp_path):
    """Рев'ю 6, Important 2: прохід, що почався до дедлайну, не починає нову живу подачу за
    deadline_margin_minutes до нього; решта лишається queued."""
    lid2 = _second_lead(env, tmp_path)
    assert env.cfg.deadline_margin_minutes == 10
    env.kw["now"] = lambda: datetime.fromisoformat(
        "2026-10-27T19:40" if env.driver.calls == 0 else "2026-10-27T19:46")
    res = run_apply(**env.kw)
    assert res.sent == [LID] and res.after_deadline
    assert env.driver.calls == 1 and env.store.get(lid2)["status"] == "queued"
    assert "attempts" not in env.store.get(lid2) or not env.store.get(lid2)["attempts"]


def test_margin_stops_dry_run_too(env, tmp_path):
    """Фінальне рев'ю, M3: запас перед дедлайном діє і на тіньовий прохід; те, що встиг
    до запасу, заповнюється."""
    lid2 = _second_lead(env, tmp_path)
    env.kw["cfg"].dry_run = True
    env.kw["now"] = lambda: datetime.fromisoformat(
        "2026-10-27T19:40" if env.driver.dry_calls == 0 else "2026-10-27T19:46")
    res = run_apply(**env.kw)
    assert res.after_deadline and env.driver.dry_calls == 1
    assert env.store.get(lid2)["status"] == "queued"


def test_lead_without_attachments_is_held_without_card(env):
    """Рев'ю 6, minor: ctx_for без вкладень кидає ValueError; лід held з attachment_none, без картки."""
    def no_cv(lead, pkg):
        raise ValueError("package has no attachments")

    env.kw["ctx_for"] = no_cv
    res = run_apply(**env.kw)
    rec = env.store.get(LID)
    assert rec["status"] == "held" and rec["reasons"] == ["attachment_none"]
    assert env.driver.opened == 0 and env.transport.sent == [] and res.failed == []


def test_lock_not_held_while_waiting(env):
    env.kw["cfg"].lock_path = env.state / "machine.lock"
    env.driver.script[URL] = ApplyResult("handoff", "ashby", URL, [], reason="captcha")
    seen = []

    def probe(page):
        with open(env.kw["cfg"].lock_path, "a") as f:
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(f, fcntl.LOCK_UN)
                seen.append("free")
            except BlockingIOError:
                seen.append("held")
        return "application submitted"
    env.driver.probe_submitted = probe
    run_apply(**env.kw)
    assert seen == ["free"] and env.store.get(LID)["status"] == "sent"


def test_lock_busy_requeues(env):
    env.kw["cfg"].lock_path = env.held_lock      # файл, який інший дескриптор тримає під flock
    env.kw["cfg"].lock_wait = 0
    res = run_apply(**env.kw)
    assert res.lock_busy and env.store.get(LID)["status"] == "queued"
    assert env.store.get(LID)["attempts"] == 0 and env.driver.calls == 0
    assert "sending_at" not in env.store.get(LID)   # sending ставиться лише під локом


def test_sending_is_marked_inside_the_lock(env):
    env.kw["cfg"].lock_path = env.state / "machine.lock"
    seen = {}
    real_open = env.driver.open

    def spy_open(url):
        seen["status"] = env.store.get(LID)["status"]
        with open(env.kw["cfg"].lock_path, "a") as f:
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(f, fcntl.LOCK_UN)
                seen["lock"] = "free"
            except BlockingIOError:
                seen["lock"] = "held"
        return real_open(url)
    env.driver.open = spy_open
    run_apply(**env.kw)
    assert seen == {"status": "sending", "lock": "held"} and env.store.get(LID)["status"] == "sent"


def test_lock_error_keeps_lead_queued_and_reports_error(env, tmp_path):
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x")
    env.kw["cfg"].lock_path = blocker / "machine.lock"   # mkdir батьківської теки впаде
    res = run_apply(**env.kw)
    rec = env.store.get(LID)
    assert rec["status"] == "queued" and rec["attempts"] == 0 and "sending_at" not in rec and "error" not in rec
    assert res.lock_busy and res.error.startswith("lock: ") and res.failed == [] and env.driver.opened == 0
    assert env.transport.sent == []


def test_handoff_card_post_failure_fails_lead_and_closes_page(env):
    env.driver.script[URL] = ApplyResult("handoff", "ashby", URL, [], reason="captcha")

    def boom(chat_id, text, buttons=None):
        raise RuntimeError("telegram down")
    env.transport.send = boom
    res = run_apply(**env.kw)
    rec = env.store.get(LID)
    assert rec["status"] == "failed" and rec["error"].startswith("handoff card not posted: telegram down")
    assert res.failed == [LID] and env.driver.closed == 1 and env.polls == 0


def test_dry_run_respects_limit_and_skips_leads_filled_today(env, tmp_path):
    lead2 = {"title": "Android Lead", "company": "Contract Co", "channel": "form",
             "apply_url": "https://jobs.ashbyhq.com/contractco/9", "ats": "ashby"}
    pkg2 = _write_package(tmp_path, "contract-co-android-lead", lead2, "en", "S", "Hi.", [env.cv])
    pkg2.lead["company"] = "Second Co"           # інша компанія: не дубль першого ліда в черзі
    enqueue(env.store, pkg2, [], TODAY, "t")
    env.kw["cfg"].dry_run = True
    env.kw["cfg"].limits["form"] = 1
    res = run_apply(**env.kw)
    assert env.driver.dry_calls == 1 and res.skipped_limit == 1
    run_apply(**env.kw)                          # другий тік: перший лід уже заповнений сьогодні
    run_apply(**env.kw)
    dry = JsonlStore(env.state / DRY_FILE).read()
    assert env.driver.dry_calls == 2 and [r["id"] for r in dry] == [LID, f"lead-{TODAY}-contract-co-android-lead"]


def test_dispatcher_done_and_skip_update_queue(env):
    d = Dispatcher(FakeSender(), env.cards, env.tracker, env.state, lambda: TODAY, queue=env.store)
    env.cards.append({"id": "handoff-" + LID, "kind": "handoff", "status": "open", "lead_id": LID})
    assert d.apply(Action(at="", action="skip", card="handoff-" + LID, by=1)) == "OK skipped" and env.store.get(LID)["status"] == "held"
    assert env.store.get(LID)["reasons"] == ["operator_skipped"]


def test_dispatcher_done_marks_lead_sent(env):
    d = Dispatcher(FakeSender(), env.cards, env.tracker, env.state, lambda: TODAY, queue=env.store)
    env.cards.append({"id": "handoff-" + LID, "kind": "handoff", "status": "open", "lead_id": LID})
    assert d.apply(Action(at="", action="done", card="handoff-" + LID, by=1)) == "OK done"
    rec = env.store.get(LID)
    assert rec["status"] == "sent" and rec["sent_id"] == "form:operator" and rec["sent_at"] == TODAY
    assert env.cards.get("handoff-" + LID)["status"] == "done"


def test_dispatcher_done_writes_tracker_row(env):
    """Рев'ю 5, Important 2: підтверджена оператором подача завжди має рядок у трекері."""
    d = Dispatcher(FakeSender(), env.cards, env.tracker, env.state, lambda: TODAY, queue=env.store)
    env.cards.append({"id": "handoff-" + LID, "kind": "handoff", "status": "open", "lead_id": LID,
                      "company": "Contract Co", "title": "Senior Android Engineer", "url": URL, "ats": "ashby",
                      "salary": "€90/h"})
    env.store.update(LID, status="handoff")
    assert d.apply(Action(at="", action="done", card="handoff-" + LID, by=1)) == "OK done"
    assert ("| 1 | 2026-10-27 | Contract Co | Senior Android Engineer | contract: ashby | €90/h | "
            "portal_submitted (портал) | 2026-10-27 | wait reply until 2026-11-03 | "
            f"{URL}, confirmed by operator |") in env.tracker.read_text(encoding="utf-8")


def test_dispatcher_done_on_already_sent_lead_writes_no_row(env):
    d = Dispatcher(FakeSender(), env.cards, env.tracker, env.state, lambda: TODAY, queue=env.store)
    env.cards.append({"id": "handoff-" + LID, "kind": "handoff", "status": "open", "lead_id": LID, "url": URL})
    env.store.update(LID, status="sent", sent_id="form:application submitted")
    assert d.apply(Action(at="", action="done", card="handoff-" + LID, by=1)) == "OK done"
    assert env.store.get(LID)["sent_id"] == "form:application submitted" and "| 1 |" not in env.tracker.read_text()


def test_waiting_worker_does_not_duplicate_dispatcher_row(env):
    """Тап «Подано» з підключеною чергою: рядок пише диспетчер, воркер, що чекав, другого не додає."""
    env.driver.script[URL] = ApplyResult("handoff", "ashby", URL, [], reason="captcha")
    d = Dispatcher(FakeSender(), env.cards, env.tracker, env.state, lambda: TODAY, queue=env.store)
    real_sleep = env.sleep

    def sleep(seconds):
        real_sleep(seconds)
        if env.polls == 2:
            d.apply(Action(at="", action="done", card=handoff_cid(LID), by=1))
    env.kw["sleep"] = sleep
    res = run_apply(**env.kw)
    assert res.sent == [LID] and env.store.get(LID)["status"] == "sent"
    assert env.tracker.read_text(encoding="utf-8").count("portal_submitted") == 1


def test_dispatcher_without_queue_leaves_lead(env):
    d = Dispatcher(FakeSender(), env.cards, env.tracker, env.state, lambda: TODAY)
    env.cards.append({"id": "handoff-" + LID, "kind": "handoff", "status": "open", "lead_id": LID})
    assert d.apply(Action(at="", action="done", card="handoff-" + LID, by=1)) == "OK done"
    assert env.store.get(LID)["status"] == "queued"


def test_handoff_deadline():
    assert handoff_deadline(datetime.fromisoformat("2026-10-27T10:05"), "19:55") == datetime.fromisoformat("2026-10-27T19:55")


def test_handoff_cid_fits_callback_data():
    assert handoff_cid(LID) == "handoff-" + LID
    long_id = "lead-2026-10-27-" + "very-long-company-name-" * 4 + "senior-android-engineer"
    cid = handoff_cid(long_id)
    assert len(f"done:{cid}".encode()) <= 64 and cid.startswith("handoff-") and cid != handoff_cid(long_id + "x")


def test_handoff_card_copy_and_buttons():
    c = handoff_card("Contract <Co>", "Android", "captcha", URL, "vnc://192.0.2.10:5900", "handoff-x", "uk")
    assert c.kind == "handoff" and c.text.startswith("Твоя черга: Contract &lt;Co&gt;, Android")
    assert "Причина: captcha" in c.text and "Вкладка відкрита на vnc://192.0.2.10:5900" in c.text and URL in c.text
    assert [(b.text, b.data) for row in c.buttons for b in row] == [("Подано", "done:handoff-x"),
                                                                     ("Пропустити", "skip:handoff-x")]
    e = handoff_card("Contract Co", "Android", "captcha", URL, "vnc", "handoff-x", "en")
    assert e.text.startswith("Your turn: Contract Co, Android\nReason: captcha\nTab open on vnc")
    assert [b.text for row in e.buttons for b in row] == ["Submitted", "Skip"]


def test_summary_counts_handoff_and_status_text_shows_it(env):
    env.store.update(LID, status="handoff")
    s = summary(env.store, TODAY)
    assert s["handoff"] == 1 and s["queued"] == {}
    st = {"open_cards": 0, "sent_today": 0, "followups_today": 0, "paused": False, "last_watch": "-", "queue": s}
    assert "hand-off 1" in status_text(st, "uk") and "handoff 1" in status_text(st, "en")


def test_reenqueue_keeps_handoff_record(env, tmp_path):
    from bot.packages import load_packages
    env.store.update(LID, status="handoff")
    pkg = next(p for p in load_packages(tmp_path / "packages_2026-10-26"))
    assert enqueue(env.store, pkg, [], "2026-10-26", "t")["status"] == "handoff"


def test_receipt_post_failure_does_not_crash_sent_lead(env, caplog):
    """Фінальне рев'ю, M1: Telegram упав після sent; запис sent, рядок у трекері, прохід триває."""
    env.driver.script[URL] = ApplyResult("submitted", "ashby", URL, ["s.png"], confirmation_text="thank you for applying")

    def boom(chat_id, text, buttons=None):
        raise RuntimeError("telegram down")
    env.transport.send = boom
    res = run_apply(**env.kw)
    rec = env.store.get(LID)
    assert res.sent == [LID] and rec["status"] == "sent" and "portal_submitted" in env.tracker.read_text(encoding="utf-8")
    assert "квитанцію" in caplog.text and "telegram down" in caplog.text


# ── етап 2d: прохід по всіх каналах ──

@pytest.fixture
def penv(env):
    """env без form-ліда фікстури: у черзі лише ліди, які ставить сам тест."""
    env.store.path.write_text("", encoding="utf-8")
    return env


def test_offsite_resolution_continues_as_form_in_same_pass(penv):
    env = penv
    env.queue_offsite("li1", "https://www.linkedin.com/jobs/view/1/")
    env.driver.offsite["https://www.linkedin.com/jobs/view/1/"] = ("open", "https://job-boards.greenhouse.io/contractco/jobs/1")
    res = env.run()
    rec = env.store.get("li1")
    assert res.resolved == ["li1"] and res.sent == ["li1"]
    assert rec["channel"] == "form" and rec["ats"] == "greenhouse" and rec["platform"] is None
    assert rec["resolved_from"] == "https://www.linkedin.com/jobs/view/1/" and rec["sent_id"].startswith("form:")
    lead = json.loads((env.pkg("li1") / "lead.json").read_text())
    assert lead["apply_url"].startswith("https://job-boards.greenhouse.io") and lead["channel"] == "form"
    assert lead["ats"] == "greenhouse" and lead["resolver_note"] == "linkedin offsite, external link"
    assert sent_today_platform(env.store, env.today, "linkedin") == 0 and sent_today(env.store, env.today, "form") == 1
    assert env.driver.channels == [("https://job-boards.greenhouse.io/contractco/jobs/1", "form")]
    assert env.driver.opened == 2 and env.driver.closed == 2   # сторінка LinkedIn і сторінка форми
    assert "contract: greenhouse" in env.tracker.read_text(encoding="utf-8")


def test_offsite_without_link_is_held(penv):
    env = penv
    env.queue_offsite("li1", URL1)
    env.driver.offsite[URL1] = ("open", None)
    env.run()
    assert env.store.get("li1")["status"] == "held" and env.store.get("li1")["reasons"] == ["no_apply_link"]
    assert env.driver.closed == 1 and env.driver.calls == 0


def test_offsite_closed_is_held_not_live(penv):
    env = penv
    env.queue_offsite("li1", URL1)
    env.driver.offsite[URL1] = ("closed", None)
    env.run()
    assert env.store.get("li1")["status"] == "held" and env.store.get("li1")["reasons"] == ["not_live"]
    assert env.driver.closed == 1 and env.transport.sent == []


def test_offsite_restricted_switches_linkedin_off_and_stays_queued(penv):
    env = penv
    env.queue_offsite("li1", URL1)
    env.queue_easy("a", URL2)
    env.driver.offsite[URL1] = ("restricted", None)
    res = env.run()
    rec = env.store.get("li1")
    assert rec["status"] == "queued" and rec["channel"] == "linkedin_offsite" and "sending_at" not in rec
    assert res.platform_off == {"linkedin": "restricted"} and res.skipped_platform == 1 and res.sent == []
    assert env.cards_sent_of_kind("platform") == 1 and env.driver.closed == 1 and env.driver.calls == 0


def test_offsite_resolved_over_form_limit_stays_queued_as_form(penv):
    env = penv
    env.limits["form"] = 0
    env.queue_offsite("li1", URL1)
    env.driver.offsite[URL1] = ("open", "https://jobs.lever.co/contractco/abc")
    res = env.run()
    rec = env.store.get("li1")
    assert res.resolved == ["li1"] and res.sent == [] and res.skipped_limit == 1
    assert rec["status"] == "queued" and rec["channel"] == "form" and rec["ats"] == "lever"
    assert env.driver.calls == 0


def test_offsite_unknown_host_becomes_form_without_ats(penv):
    env = penv
    env.queue_offsite("li1", URL1)
    env.driver.offsite[URL1] = ("open", "https://careers.example.org/jobs/7")
    res = env.run()
    rec = env.store.get("li1")
    assert res.sent == ["li1"] and rec["channel"] == "form" and rec["ats"] is None
    assert env.driver.channels == [("https://careers.example.org/jobs/7", "form")]


def test_pass_order_offsite_form_easy_platform(penv):
    env = penv
    env.queue_platform("g", GULP1, "gulp")
    env.queue_easy("a", URL1)
    env.queue_form("f", ASHBY1)
    env.queue_offsite("li1", URL2)
    env.driver.offsite[URL2] = ("open", "https://jobs.ashbyhq.com/thirdco/1")
    res = env.run()
    assert res.sent == ["li1", "f", "a", "g"]
    assert [ch for _, ch in env.driver.channels] == ["form", "form", "linkedin_easy", "platform_login"]


def test_restricted_switches_platform_off_for_the_day_and_continues(penv):
    env = penv
    env.queue_easy("a", URL1)
    env.queue_easy("b", URL2)
    env.queue_platform("g", GULP1, "gulp")
    env.driver.script[URL1] = ApplyResult("restricted", "linkedin", URL1, reason="checkpoint")
    res = env.run()
    assert [env.store.get(i)["status"] for i in ("a", "b")] == ["queued", "queued"]
    assert env.store.get("a")["attempts"] == 0 and "sending_at" not in env.store.get("b")
    assert env.store.get("a")["sending_at"] is None
    assert res.sent == ["g"] and res.platform_off == {"linkedin": "restricted"}
    assert env.cards_sent_of_kind("platform") == 1
    assert read_state(env.platforms_path)["linkedin"]["reason"] == "restricted"
    assert read_state(env.platforms_path)["linkedin"]["off_until"] == f"{TODAY} 23:59"
    env.driver.script.pop(URL1)   # наступного дня обмеження вже нема
    assert env.run(day_offset=1).sent == ["a", "b"]


def test_login_required_keeps_lead_queued_and_posts_one_card(penv):
    env = penv
    env.queue_platform("f1", FM1, "freelancermap")
    env.queue_platform("f2", FM2, "freelancermap")
    env.driver.script[FM1] = ApplyResult("login_required", "freelancermap", FM1)
    res = env.run()
    assert env.store.get("f1")["status"] == "queued" and env.store.get("f2")["status"] == "queued"
    assert env.cards_sent_of_kind("platform") == 1 and res.skipped_platform == 1
    assert res.platform_off == {"freelancermap": "login_required"} and res.failed == []


def test_quota_result_switches_platform_off_until_next_month(penv):
    env = penv
    env.queue_platform("f1", FM1, "freelancermap")
    env.driver.script[FM1] = ApplyResult("quota", "freelancermap", FM1, reason="platform_quota")
    res = env.run()
    assert env.store.get("f1")["status"] == "held" and env.store.get("f1")["reasons"] == ["platform_quota"]
    assert read_state(env.platforms_path)["freelancermap"]["off_until"].startswith("2026-11-01")
    assert read_state(env.platforms_path)["freelancermap"]["reason"] == "platform_quota"
    assert res.platform_off == {"freelancermap": "platform_quota"} and env.cards_sent_of_kind("platform") == 1


def test_platform_off_in_targeting_skips_without_touching(penv):
    env = penv
    env.targeting["platforms"]["gulp"] = "off"
    env.queue_platform("g", GULP1, "gulp")
    res = env.run()
    assert res.skipped_platform == 1 and env.driver.opened == 0 and env.store.get("g")["status"] == "queued"


def test_platform_missing_from_targeting_is_off(penv):
    env = penv
    env.queue_platform("h", "https://www.hays.de/jobsuche/stellenangebote-jobs-detail-x", "hays")
    res = env.run()
    assert res.skipped_platform == 1 and env.driver.opened == 0


def test_platform_shadow_fills_without_submit_while_others_submit(penv):
    env = penv
    env.targeting["platforms"]["gulp"] = "shadow"
    env.queue_platform("g", GULP1, "gulp")
    env.queue_easy("a", URL1)
    res = env.run()
    assert res.sent == ["a"] and env.driver.dry_calls == 1
    assert [r["id"] for r in env.dry.read()] == ["g"] and env.store.get("g")["status"] == "queued"
    env.run()   # тіньове заповнення того самого ліда вдруге за день не повторюється
    assert env.driver.dry_calls == 1


def test_linkedin_limits_per_day_per_pass_and_gap(penv):
    env = penv
    for i in range(4):
        env.queue_easy(f"a{i}", f"{URL1}{i}")
    res = env.run()
    assert res.sent == ["a0", "a1"] and res.skipped_limit == 2   # per-pass cap 2, rest stay queued
    assert env.slept_total == 180
    assert env.store.get("a2")["status"] == "queued" and env.store.get("a3")["status"] == "queued"
    env.limits["linkedin"] = 3
    res = env.run()
    assert res.sent == ["a2"] and res.skipped_limit == 1   # daily limit 3 reached (2 earlier today)


def test_gap_measured_from_previous_submission(penv):
    env = penv
    env.queue_easy("a", URL1)
    env.queue_easy("b", URL2)
    real_apply = env.driver.apply

    def slow(page, url, ctx, dry_run, channel):
        if url == URL1:
            env.slept_total += 100   # заповнення «a» тривало 100 с, подача о 10:01:40
        return real_apply(page, url, ctx, dry_run, channel)
    env.driver.apply = slow
    assert env.run().sent == ["a", "b"]
    assert env.slept_total == 100 + 180   # повна пауза від моменту подачі, не від старту заповнення


def test_gap_past_deadline_margin_stops_pass(penv):
    env = penv
    env.queue_easy("a", URL1)
    env.queue_easy("b", URL2)
    env.kw["now"] = lambda: datetime.fromisoformat("2026-10-27T19:43")   # last_start 19:45, пауза до 19:46
    res = env.run()
    assert res.sent == ["a"] and res.after_deadline and env.store.get("b")["status"] == "queued"
    assert env.slept_total == 0


def test_no_gap_for_shadow_fills(penv):
    env = penv
    env.targeting["platforms"]["linkedin"] = "shadow"
    env.queue_easy("a", URL1)
    env.queue_easy("b", URL2)
    env.run()
    assert env.driver.dry_calls == 2 and env.slept_total == 0


def test_platform_submission_sent_id_tracker_source_and_receipt(penv):
    env = penv
    env.queue_platform("g", GULP1, "gulp")
    env.driver.script[GULP1] = ApplyResult("submitted", "gulp", GULP1, ["g.png"], confirmation_text="Bewerbung gesendet")
    env.run()
    rec = env.store.get("g")
    assert rec["sent_id"] == "gulp:Bewerbung gesendet" and rec["platform"] == "gulp"
    text = env.tracker.read_text(encoding="utf-8")
    assert "| contract: gulp |" in text and f"{GULP1}, g.png" in text
    assert "gulp" in env.transport.sent[-1].text and "Bewerbung gesendet" in env.transport.sent[-1].text


def test_platform_handoff_card_carries_platform(penv):
    env = penv
    env.queue_easy("a", URL1)
    env.driver.script[URL1] = ApplyResult("handoff", "linkedin", URL1, [], reason="open_required: How many years")
    env.driver.probe_after_polls[0] = "application was sent"
    env.run()
    card = env.cards.get(handoff_cid("a"))
    assert card["platform"] == "linkedin" and env.store.get("a")["sent_id"] == "linkedin:application was sent"


def test_handoff_record_not_reopened(penv):
    env = penv
    env.queue_easy("a", URL1)
    env.driver.script[URL1] = ApplyResult("handoff", "linkedin", URL1, reason="open_required: How many years")
    env.clock_jump_after_polls = 1   # оператор не відповів до дедлайну
    env.run()
    env.driver.opened = 0
    env.clock_jump_after_polls = None
    env.run()
    assert env.driver.opened == 0 and env.store.get("a")["status"] == "failed"
    assert env.store.get("a")["error"].startswith("handoff_expired")


def test_handoff_ends_pass_after_platform_submissions(penv):
    env = penv
    env.queue_easy("a", URL1)
    env.queue_platform("g", GULP1, "gulp")
    env.queue_platform("g2", GULP1 + "2", "gulp")
    env.driver.script[GULP1] = ApplyResult("handoff", "gulp", GULP1, [], reason="captcha")
    env.driver.probe_after_polls[0] = "danke"
    res = env.run()
    assert res.sent == ["a", "g"] and res.handoff == ["g"] and env.store.get("g2")["status"] == "queued"


def test_form_restricted_is_failed(penv):
    env = penv
    env.queue_form("f", ASHBY1)
    env.driver.script[ASHBY1] = ApplyResult("restricted", "ashby", ASHBY1, reason="blocked")
    res = env.run()
    assert res.failed == ["f"] and env.store.get("f")["status"] == "failed" and res.platform_off == {}
    assert not env.platforms_path.exists()


def test_form_records_still_use_global_dry_run_only(penv):
    env = penv
    env.targeting["platforms"] = {}
    env.queue_form("f", ASHBY1)
    assert env.run().sent == ["f"]


def test_global_dry_run_covers_platforms(penv):
    env = penv
    env.cfg.dry_run = True
    env.queue_easy("a", URL1)
    env.queue_form("f", ASHBY1)
    res = env.run()
    assert res.sent == [] and env.driver.dry_calls == 2 and {r["id"] for r in env.dry.read()} == {"a", "f"}


def test_without_platforms_path_targeting_alone_decides(penv):
    env = penv
    env.cfg.platforms_path = None
    env.queue_easy("a", URL1)
    env.queue_platform("f1", FM1, "freelancermap")
    env.driver.script[FM1] = ApplyResult("login_required", "freelancermap", FM1)
    res = env.run()
    assert res.sent == ["a"] and res.platform_off == {"freelancermap": "login_required"}


def test_stale_platform_sending_fails_too(penv):
    env = penv
    env.queue_easy("a", URL1)
    env.store.update("a", status="sending", sending_at=f"{TODAY} 09:00", attempts=1)
    res = env.run()
    assert res.failed == ["a"] and env.store.get("a")["error"].startswith("apply status unknown")
    assert env.driver.calls == 0


# ── fix round 1 ──

def _drop_platform_key(env, rec_id: str) -> None:
    """Запис, поставлений до етапу 2d: без ключа platform."""
    recs = env.store.read()
    for r in recs:
        if r["id"] == rec_id:
            r.pop("platform")
    env.store.path.write_text("".join(json.dumps(r) + "\n" for r in recs), encoding="utf-8")


def test_record_without_platform_key_counts_against_platform_limit(penv):
    env = penv
    env.limits["linkedin"] = 1
    env.queue_easy("a", URL1)
    _drop_platform_key(env, "a")
    assert "platform" not in env.store.get("a")
    assert env.run().sent == ["a"]
    rec = env.store.get("a")
    assert rec["platform"] == "linkedin" and rec["sent_id"] == "linkedin:thank you for applying"
    assert "| contract: linkedin |" in env.tracker.read_text(encoding="utf-8")
    assert sent_today_platform(env.store, env.today, "linkedin") == 1
    env.queue_easy("b", URL2)
    _drop_platform_key(env, "b")
    res = env.run()
    assert res.sent == [] and res.skipped_limit == 1 and env.store.get("b")["status"] == "queued"


@pytest.mark.parametrize("mode", ["global", "linkedin_shadow"])
def test_offsite_resolution_crash_in_dry_mode_goes_to_queue_dry(penv, mode):
    env = penv
    if mode == "global":
        env.cfg.dry_run = True
    else:
        env.targeting["platforms"]["linkedin"] = "shadow"
    env.queue_offsite("li1", URL1)

    def boom(page, url):
        raise RuntimeError("page crashed")
    env.driver.resolve_offsite = boom
    res = env.run()
    rec = env.store.get("li1")
    assert rec["status"] == "queued" and rec["channel"] == "linkedin_offsite" and res.failed == []
    dry = env.dry.read()
    assert [r["id"] for r in dry] == ["li1"] and dry[0]["error"].startswith("resolve: RuntimeError: page crashed")
    assert env.transport.sent == [] and env.driver.closed == 1
    env.run()   # той самий день: тіньова резолюція не повторюється
    assert len(env.dry.read()) == 1


def test_offsite_resolution_crash_live_fails_lead(penv):
    env = penv
    env.queue_offsite("li1", URL1)

    def boom(page, url):
        raise RuntimeError("page crashed")
    env.driver.resolve_offsite = boom
    res = env.run()
    assert res.failed == ["li1"] and env.store.get("li1")["error"].startswith("resolve: RuntimeError")
    assert env.dry.read() == []


def test_gap_applies_after_failed_attempt_and_cap_counts_submissions_only(penv):
    env = penv
    env.queue_easy("a", URL1)
    env.queue_easy("b", URL2)
    env.queue_easy("c", URL1 + "3")
    env.driver.script[URL1] = ApplyResult("failed", "linkedin", URL1, [], reason="modal: no Next")
    res = env.run()
    assert res.failed == ["a"] and res.sent == ["b", "c"]   # невдала спроба не з'їдає ліміт за прохід
    assert env.slept_total == 2 * 180                       # але пауза йде й після неї


def test_gap_applies_after_closed_attempt(penv):
    env = penv
    env.queue_easy("a", URL1)
    env.queue_easy("b", URL2)
    env.driver.script[URL1] = ApplyResult("closed", "linkedin", URL1, [], reason="No longer accepting applications")
    assert env.run().sent == ["b"] and env.slept_total == 180


def test_gap_ending_exactly_at_margin_stops_pass(penv):
    env = penv
    env.queue_easy("a", URL1)
    env.queue_easy("b", URL2)
    env.kw["now"] = lambda: datetime.fromisoformat("2026-10-27T19:42")   # пауза до 19:45 = last_start
    res = env.run()
    assert res.sent == ["a"] and res.after_deadline and env.store.get("b")["status"] == "queued"


def test_pause_during_gap_stops_pass(penv):
    env = penv
    env.queue_easy("a", URL1)
    env.queue_easy("b", URL2)
    real_sleep = env.sleep

    def sleep(seconds):
        real_sleep(seconds)
        set_paused(env.state, True)   # оператор надіслав /pause, поки прохід чекав паузу платформи
    env.kw["sleep"] = sleep
    res = env.run()
    assert res.paused and res.sent == ["a"] and env.driver.calls == 1
    assert env.store.get("b")["status"] == "queued" and "sending_at" not in env.store.get("b")


def test_pause_between_leads_stops_pass(penv):
    """/pause, що прийшов під час подачі першого ліда: наступний лід уже не стартує."""
    env = penv
    env.queue_form("a", URL)
    env.queue_form("b", ASHBY1)
    real_apply = env.driver.apply

    def apply(page, url, ctx, dry_run, channel):
        out = real_apply(page, url, ctx, dry_run, channel)
        set_paused(env.state, True)
        return out
    env.driver.apply = apply
    res = env.run()
    assert res.paused and res.sent == ["a"] and env.driver.calls == 1 and env.driver.opened == 1
    assert env.store.get("b")["status"] == "queued" and "sending_at" not in env.store.get("b")


def test_gap_applies_after_driver_exception_once_page_was_opened(penv):
    env = penv
    env.queue_easy("a", URL1)
    env.queue_easy("b", URL2)
    real_apply = env.driver.apply

    def apply(page, url, ctx, dry_run, channel):
        if url == URL1:
            raise RuntimeError("Target closed")   # сторінку LinkedIn уже відкрито
        return real_apply(page, url, ctx, dry_run, channel)
    env.driver.apply = apply
    res = env.run()
    assert res.failed == ["a"] and res.sent == ["b"] and env.slept_total == 180
    assert env.driver.closed == 2


def test_no_gap_when_driver_failed_before_page_opened(penv):
    env = penv
    env.queue_easy("a", URL1)
    env.queue_easy("b", URL2)
    real_open = env.driver.open

    def open_(url):
        if url == URL1:
            raise RuntimeError("browser gone")
        return real_open(url)
    env.driver.open = open_
    res = env.run()
    assert res.failed == ["a"] and res.sent == ["b"] and env.slept_total == 0


# --- ринок найму: рядок «готово» стає подачею, hand-off без кнопок ---

READY_TRACKER = """# Tracker

| # | Date | Company | Position | Source | Salary range | Status | Last update | Next action | Notes |
|---|---|---|---|---|---|---|---|---|---|
| 2026-10-27 | Senior Android Engineer | Contract Co, Köln | 🔄 Файли готові — подати вручну · https://x.example/j/1 |
"""


def test_employment_submit_updates_ready_row_and_writes_result(env):
    env.cfg.market = "de_employment"
    env.tracker.write_text(READY_TRACKER, encoding="utf-8")
    env.driver.script[URL] = ApplyResult("submitted", "ats", URL, ["s.png"], confirmation_text="Vielen Dank")
    run_apply(**env.kw)
    text = env.tracker.read_text(encoding="utf-8")
    assert "🔄" not in text and "portal_submitted (портал) (2026-10-27)" in text and "https://x.example/j/1" in text
    assert text.count("Contract Co") == 1   # без другого рядка
    assert json.loads((env.pkg(LID) / "apply_result.json").read_text(encoding="utf-8"))["status"] == "submitted"


def test_employment_submit_without_ready_row_appends(env):
    env.cfg.market = "de_employment"
    env.driver.script[URL] = ApplyResult("submitted", "ats", URL, [], confirmation_text="Danke")
    run_apply(**env.kw)
    assert "| 1 |" in env.tracker.read_text(encoding="utf-8")


def test_handoff_without_buttons_ends_when_tab_closed(env):
    env.cfg.handoff_buttons = False
    env.driver.script[URL] = ApplyResult("handoff", "ats", URL, [], reason="captcha")
    env.driver.closed_after_probes = 2
    res = run_apply(**env.kw)
    assert "captcha" in env.transport.sent[0].text and not env.transport.sent[0].buttons
    assert res.handoff == [LID]
    rec = env.store.get(LID)
    assert rec["status"] == "held" and rec["reasons"] == ["operator_skipped"]
    assert next(c for c in env.cards.read() if c["kind"] == "handoff")["status"] == "skipped"


def test_handoff_without_buttons_confirmed_by_page(env):
    env.cfg.handoff_buttons = False
    env.cfg.market = "de_employment"
    env.tracker.write_text(READY_TRACKER, encoding="utf-8")
    env.driver.script[URL] = ApplyResult("handoff", "ats", URL, [], reason="open_required: Anrede")
    env.driver.probe_after_polls[1] = "Vielen Dank für Ihre Bewerbung"
    res = run_apply(**env.kw)
    assert res.sent == [LID] and "portal_submitted (портал)" in env.tracker.read_text(encoding="utf-8")
