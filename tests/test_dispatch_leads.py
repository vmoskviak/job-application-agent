"""Прохід диспетчера: email-ліди йдуть без тапу, ліміт і пауза, квитанції, тіньовий режим."""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from bot.dispatch_leads import DispatchConfig, run_dispatch
from bot.packages import Package
from bot.sendqueue import DRY_FILE, QUEUE_FILE, enqueue
from bot.state import JsonlStore, set_paused

HEADER = ("# Application Tracker — Jordan Example\n\n"
          "| # | Date | Company | Position | Source | Salary range | Status | Last update | Next action | Notes |\n"
          "|---|---|---|---|---|---|---|---|---|---|\n")
LID = "lead-2026-10-26-contract-co-senior-android-engineer"


@dataclass
class SentCall:
    account: str
    to: str
    subject: str
    body: str
    attachments: list[Path]


class FakeSender:
    def __init__(self) -> None:
        self.calls: list[SentCall] = []
        self.raise_with: Exception | None = None

    def reply(self, account, msg_id, body, to=None):
        raise AssertionError("reply не має викликатись")

    def send(self, account, to, subject, body, attachments):
        if self.raise_with:
            raise self.raise_with
        self.calls.append(SentCall(account, to, subject, body, list(attachments)))
        return f"m{len(self.calls)}"


@dataclass
class SentMsg:
    chat_id: int
    text: str
    buttons: list | None


class FakeTransport:
    def __init__(self) -> None:
        self.sent: list[SentMsg] = []

    def get_updates(self, offset, timeout):
        return []

    def send(self, chat_id, text, buttons=None):
        self.sent.append(SentMsg(chat_id, text, buttons))
        return len(self.sent)

    def answer(self, callback_id, text=""):
        pass

    def clear_buttons(self, chat_id, message_id):
        pass


@dataclass
class Env:
    cfg: DispatchConfig
    store: JsonlStore
    tracker: Path
    state: Path
    sender: FakeSender
    transport: FakeTransport
    cv: Path
    packages: Path = field(default=None)


def _write_package(root: Path, slug: str, lead: dict, lang: str, subject: str, note: str, attachments: list[Path]) -> Package:
    d = root / "packages_2026-10-26" / slug
    d.mkdir(parents=True)
    (d / "lead.json").write_text(json.dumps(lead, ensure_ascii=False), encoding="utf-8")
    (d / "note.txt").write_text(note + "\n", encoding="utf-8")
    (d / "meta.json").write_text(json.dumps({"slug": slug, "lang": lang, "subject": subject,
                                             "attachments": [str(p) for p in attachments],
                                             "created": "2026-10-26 20:40"}), encoding="utf-8")
    return Package(slug, d, lead, lead["channel"], lead["apply_url"], lead.get("ats"), lang, subject, note, attachments)


def _env(tmp_path, channel="email") -> Env:
    state = tmp_path / "state"
    state.mkdir()
    cv = tmp_path / "CV_Android.pdf"
    cv.write_bytes(b"%PDF-1.4")
    tracker = tmp_path / "tracker.md"
    tracker.write_text(HEADER, encoding="utf-8")
    lead = {"title": "Senior Android Engineer", "company": "Contract Co", "category": "android",
            "source": "HN Who is hiring", "source_url": "https://news.ycombinator.com/item?id=1",
            "email": "jobs@contract.example", "channel": channel,
            "apply_url": "https://boards.greenhouse.io/contractco/jobs/123" if channel == "form" else "",
            "ats": "greenhouse" if channel == "form" else None, "stated_rate": "€90/h"}
    pkg = _write_package(tmp_path, "contract-co-senior-android-engineer", lead, "en",
                         "Contract: Senior Android Engineer", "Hello Contract Co team,\n\nBody.", [cv])
    store = JsonlStore(state / QUEUE_FILE)
    enqueue(store, pkg, [], "2026-10-26", "2026-10-26 20:40")
    cfg = DispatchConfig(account="jobs", limits={"email": 5, "form": 5}, lang="uk", chat_id=4242,
                         followup_days=7)
    return Env(cfg, store, tracker, state, FakeSender(), FakeTransport(), cv, tmp_path / "packages_2026-10-26")


@pytest.fixture
def env(tmp_path):
    return _env(tmp_path)


@pytest.fixture
def env_form(tmp_path):
    return _env(tmp_path, channel="form")


def _run(env: Env):
    return run_dispatch(env.cfg, env.store, env.tracker, env.state, env.sender, env.transport,
                        "2026-10-27", "2026-10-27 09:00")


def test_sends_email_lead_and_writes_row(env):
    res = _run(env)
    assert res.sent == [LID] and res.failed == [] and res.paused is False
    call = env.sender.calls[0]
    assert call.account == "jobs" and call.to == "jobs@contract.example"
    assert call.subject == "Contract: Senior Android Engineer" and call.body.startswith("Hello Contract Co team")
    assert call.attachments == [env.cv]
    text = env.tracker.read_text(encoding="utf-8")
    assert "| 1 | 2026-10-27 | Contract Co | Senior Android Engineer | contract: HN Who is hiring | €90/h | sent | 2026-10-27 | wait reply until 2026-11-03 |" in text
    assert "Msg-ID m1" in text and "jobs@contract.example" in text and "CV_Android.pdf" in text
    rec = env.store.get(LID)
    assert rec["status"] == "sent" and rec["sent_id"] == "m1" and rec["sent_at"] == "2026-10-27 09:00"
    assert env.transport.sent[0].chat_id == 4242 and env.transport.sent[0].text.startswith("Подано")
    assert env.transport.sent[0].buttons in (None, [])


def test_limit_and_pause(env):
    env.cfg.limits["email"] = 0
    assert _run(env).skipped_limit == 1 and env.sender.calls == []
    env.cfg.limits["email"] = 5
    set_paused(env.state, True)
    res = _run(env)
    assert res.paused is True and env.sender.calls == [] and env.store.get(LID)["status"] == "queued"


def test_limit_counts_sends_from_file(env, tmp_path):
    env.cfg.limits["email"] = 1
    _run(env)
    lead2 = {**json.loads((env.packages / "contract-co-senior-android-engineer" / "lead.json").read_text()),
             "company": "Second Co", "email": "jobs@second.example"}
    pkg2 = _write_package(tmp_path, "second-co-senior-android-engineer", lead2, "en", "S2", "Hi.", [env.cv])
    enqueue(env.store, pkg2, [], "2026-10-27", "t")
    res = _run(env)
    assert res.sent == [] and res.skipped_limit == 1 and len(env.sender.calls) == 1


def test_tracker_failure_after_send_does_not_resend(env, monkeypatch):
    def boom(*a, **k):
        raise OSError("locked")
    monkeypatch.setattr("bot.dispatch_leads.append_row", boom)
    _run(env)
    _run(env)
    assert len(env.sender.calls) == 1
    rec = env.store.get(LID)
    assert rec["status"] == "sent" and "locked" in rec["tracker_error"]
    assert any("locked" in m.text for m in env.transport.sent)


def test_send_failure_marks_failed_and_cards(env):
    env.sender.raise_with = RuntimeError("gmail down")
    res = _run(env)
    assert res.failed == [LID] and res.sent == []
    rec = env.store.get(LID)
    assert rec["status"] == "failed" and rec["attempts"] == 1 and "gmail down" in rec["error"]
    assert "gmail down" in env.transport.sent[0].text
    assert "| 1 |" not in env.tracker.read_text(encoding="utf-8")


def test_dry_run_touches_nothing(env):
    env.cfg.dry_run = True
    res = _run(env)
    assert res.sent == [] and env.sender.calls == [] and env.transport.sent == []
    assert env.store.get(LID)["status"] == "queued" and "| 1 |" not in env.tracker.read_text(encoding="utf-8")
    dry = JsonlStore(env.state / DRY_FILE).read()
    assert dry[0]["id"] == LID and dry[0]["would_send_to"] == "jobs@contract.example" and dry[0]["at"] == "2026-10-27 09:00"


def test_form_leads_are_left_alone(env_form):
    res = _run(env_form)
    assert res.sent == [] and res.failed == [] and env_form.sender.calls == []
    assert env_form.store.get(LID)["status"] == "queued"


def test_no_transport_still_sends(env):
    env.transport = None
    res = _run(env)
    assert res.sent == [LID] and len(env.sender.calls) == 1


def test_missing_package_marks_failed(env):
    import shutil
    shutil.rmtree(env.packages / "contract-co-senior-android-engineer")
    res = _run(env)
    assert res.failed == [LID] and env.sender.calls == []
    assert "package" in env.store.get(LID)["error"]


def test_attachment_vanished_before_send_fails_without_sending(env):
    """Рев'ю 2b, Critical 1: CV зник між enqueue і send → failed, картка, нічого не пішло."""
    env.cv.unlink()
    res = _run(env)
    assert res.failed == [LID] and env.sender.calls == []
    assert "CV_Android.pdf" in env.store.get(LID)["error"] and "CV_Android.pdf" in env.transport.sent[0].text


def test_empty_attachment_file_fails_without_sending(env):
    env.cv.write_bytes(b"")
    assert _run(env).failed == [LID] and env.sender.calls == []


def test_sending_state_is_set_before_the_send_call(env):
    """Рев'ю 2b, Critical 2: запис стає sending до виклику пошти, тож падіння між send і mark не шле двічі."""
    seen = {}

    class SpySender(FakeSender):
        def send(self, *a, **k):
            seen["status"] = env.store.get(LID)["status"]
            return super().send(*a, **k)

    env.sender = SpySender()
    _run(env)
    assert seen["status"] == "sending" and env.store.get(LID)["status"] == "sent"


def test_stuck_sending_record_is_not_resent_and_becomes_failed(env):
    from bot.sendqueue import mark
    mark(env.store, LID, status="sending", sending_at="2026-10-27 08:00", attempts=1)
    res = run_dispatch(env.cfg, env.store, env.tracker, env.state, env.sender, env.transport,
                       "2026-10-27", "2026-10-27 08:10")
    assert env.sender.calls == [] and env.store.get(LID)["status"] == "sending"   # 10 хв: ще може жити
    res = run_dispatch(env.cfg, env.store, env.tracker, env.state, env.sender, env.transport,
                       "2026-10-27", "2026-10-27 09:00")
    assert env.sender.calls == [] and res.failed == [LID]
    rec = env.store.get(LID)
    assert rec["status"] == "failed" and "unknown" in rec["error"]
    assert any("unknown" in m.text.lower() or "невідом" in m.text.lower() for m in env.transport.sent)


def test_stuck_form_sending_is_left_to_the_worker(env_form):
    """Рев'ю 5, Important 3a: form-записи в sending розбирає браузерний воркер, не диспетчер пошти."""
    from bot.sendqueue import mark
    mark(env_form.store, LID, status="sending", sending_at="2026-10-27 08:00", attempts=1)
    res = run_dispatch(env_form.cfg, env_form.store, env_form.tracker, env_form.state, env_form.sender,
                       env_form.transport, "2026-10-27", "2026-10-27 09:00")
    assert res.failed == [] and env_form.store.get(LID)["status"] == "sending" and env_form.transport.sent == []
