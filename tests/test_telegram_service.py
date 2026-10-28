"""Сервіс бота (bot.telegram.service): allow-list чату, команди, кнопки → черга, offset."""
from __future__ import annotations

from datetime import datetime, timezone

from bot.state import JsonlStore, is_paused, set_paused
from bot.telegram.api import Event
from bot.telegram.service import Action, BotService
from tests.test_telegram_api import FakeTransport


class StubDispatcher:
    def __init__(self, receipt: str = "OK sent") -> None:
        self.applied: list[tuple[str, str | None]] = []
        self.receipt = receipt

    def apply(self, action: Action) -> str:
        self.applied.append((action.action, action.card))
        return self.receipt


def _service(tmp_path, chat_id=42, dispatcher=None, lang="en"):
    t = FakeTransport()
    d = dispatcher or StubDispatcher()
    s = BotService(t, tmp_path, chat_id, d,
                   lambda: {"open_cards": 1, "sent_today": 0, "followups_today": 0,
                            "paused": is_paused(tmp_path), "last_watch": "10:00"},
                   lang=lang, sleep=lambda secs: None)
    return t, d, s


def test_foreign_chat_is_ignored(tmp_path):
    t, d, s = _service(tmp_path)
    t.queued = [[Event(1, "message", 7, 7, "/pause"), Event(2, "callback", 7, 7, "send:re-1", callback_id="c")]]
    assert s.poll_once() == 2
    assert t.sent == [] and t.answered == [] and d.applied == [] and is_paused(tmp_path) is False
    assert (tmp_path / "tg_offset").read_text() == "3"


def test_commands(tmp_path):
    t, _, s = _service(tmp_path)
    for text in ("/start", "/status", "/pause", "/resume", "/limits"):
        s.handle(Event(1, "message", 42, 42, text))
    assert len(t.sent) == 5
    assert "/status" in t.sent[0][1]                 # help
    assert "1" in t.sent[1][1]                       # status: open cards
    assert is_paused(tmp_path) is False              # pause then resume
    assert "2b" in t.sent[4][1] and "7" in t.sent[4][1]


def test_pause_command_sets_flag(tmp_path):
    _, _, s = _service(tmp_path)
    s.handle(Event(1, "message", 42, 42, "/pause"))
    assert is_paused(tmp_path) is True


def test_callback_queues_dispatches_and_clears(tmp_path):
    t, d, s = _service(tmp_path)
    s.handle(Event(3, "callback", 42, 42, "send:re-1", callback_id="cb", message_id=9))
    assert d.applied == [("send", "re-1")] and t.answered == ["cb"] and t.cleared == [(42, 9)]
    rec = JsonlStore(tmp_path / "queue.jsonl").read()[0]
    assert rec["action"] == "send" and rec["card"] == "re-1" and rec["by"] == 42 and rec["at"]


def test_err_receipt_keeps_buttons(tmp_path):
    t, _, s = _service(tmp_path, dispatcher=StubDispatcher("ERR paused"))
    s.handle(Event(3, "callback", 42, 42, "send:re-1", callback_id="cb", message_id=9))
    assert t.answered == ["cb"] and t.cleared == []


def test_malformed_callback_is_answered_not_dispatched(tmp_path):
    t, d, s = _service(tmp_path)
    s.handle(Event(3, "callback", 42, 42, "garbage", callback_id="cb", message_id=9))
    assert d.applied == [] and t.answered == ["cb"]


def test_unknown_chat_gets_its_id_once(tmp_path):
    t, _, s = _service(tmp_path, chat_id=None)
    s.handle(Event(1, "message", 55, 55, "/start"))
    s.handle(Event(2, "message", 55, 55, "/pause"))
    assert len(t.sent) == 1 and "55" in t.sent[0][1] and is_paused(tmp_path) is False


def test_transport_error_keeps_offset(tmp_path):
    t, _, s = _service(tmp_path)
    (tmp_path / "tg_offset").write_text("10")
    t.fail_next_get = True
    assert s.poll_once() == 0 and (tmp_path / "tg_offset").read_text() == "10"


def test_poll_passes_offset_and_advances(tmp_path):
    t, _, s = _service(tmp_path)
    seen: list[int | None] = []
    orig = t.get_updates

    def spy(offset, timeout):
        seen.append(offset)
        return orig(offset, timeout)

    t.get_updates = spy
    t.queued = [[Event(41, "message", 42, 42, "/status")], []]
    assert s.poll_once() == 1 and s.poll_once() == 0
    assert seen == [None, 42]


def test_run_stops_on_stop_file(tmp_path):
    t, _, s = _service(tmp_path)
    polls = []

    def stop_after_first(offset, timeout):
        polls.append(offset)
        (tmp_path / "stop").touch()
        return [Event(1, "message", 42, 42, "/status")]

    t.get_updates = stop_after_first
    s.run()
    assert polls == [None] and len(t.sent) == 1
    set_paused(tmp_path, False)


def test_long_receipt_is_truncated_for_callback_answer(tmp_path):
    """answerCallbackQuery приймає до 200 знаків; довший ERR інакше губиться разом із квитанцією."""
    t, _, s = _service(tmp_path, dispatcher=StubDispatcher("ERR " + "x" * 500))
    s.handle(Event(3, "callback", 42, 42, "send:re-1", callback_id="cb", message_id=9))
    assert len(t.answered_text[0]) <= 200 and t.answered_text[0].startswith("ERR ")
    assert t.sent and t.sent[0][1].startswith("ERR ")      # повний текст окремим повідомленням


# ── /platform (етап 2d) ──────────────────────────────────────────────────────

def _platform_service(tmp_path):
    from bot.sendqueue import QUEUE_FILE

    targeting = {"platforms": {"gulp": True, "freelancermap": "shadow", "malt": False},
                 "daily_limits": {"gulp": 3}}
    stamp = datetime.now(tz=timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M")
    JsonlStore(tmp_path / QUEUE_FILE).append({"id": "l1", "status": "sent", "platform": "gulp", "sent_at": stamp})
    t = FakeTransport()
    s = BotService(t, tmp_path, 42, StubDispatcher(), dict, lang="en", sleep=lambda secs: None,
                   targeting=targeting)
    return t, s


def _msg(text: str) -> Event:
    return Event(1, "message", 42, 42, text)


def test_platform_command_on_off_shadow(tmp_path):
    from bot.platforms import read_state

    t, s = _platform_service(tmp_path)
    path = tmp_path / "platforms.json"
    s.handle(_msg("/platform gulp off"))
    assert read_state(path)["gulp"]["reason"] == "operator" and read_state(path)["gulp"]["off_until"].endswith("23:59")
    assert t.sent[-1][1].startswith("gulp: on → off (operator) 1/3")
    s.handle(_msg("/platform gulp shadow"))
    assert "shadow_until" in read_state(path)["gulp"] and t.sent[-1][1].startswith("gulp: on → shadow")
    s.handle(_msg("/platform gulp on"))
    assert read_state(path) == {} and t.sent[-1][1] == "gulp: on 1/3"
    s.handle(_msg("/platform"))
    assert t.sent[-1][1].splitlines()[0] == "gulp: on 1/3" and "freelancermap: shadow" in t.sent[-1][1]


def test_platform_command_errors_and_precedence(tmp_path):
    t, s = _platform_service(tmp_path)
    s.handle(_msg("/platform nope on"))
    assert t.sent[-1][1] == "unknown platform: nope; known: gulp, freelancermap, malt, linkedin"
    s.handle(_msg("/platform gulp maybe"))
    assert t.sent[-1][1] == "/platform NAME on|off|shadow"
    s.handle(_msg("/platform malt on"))
    assert "malt: off" in t.sent[-1][1] and "declared off in targeting.yaml" in t.sent[-1][1]


def test_platform_command_without_targeting(tmp_path):
    t, _, s = _service(tmp_path)
    s.handle(_msg("/platform linkedin off"))
    assert "linkedin" in t.sent[-1][1]


def test_platform_lines_for_status(tmp_path):
    """Те, що telegram_bot.py кладе в /status як "platforms": лічильник сьогоднішніх подач і ліміт."""
    _, s = _platform_service(tmp_path)
    assert s.platform_lines() == ["gulp: on 1/3", "freelancermap: shadow 0/0", "malt: off 0/0"]


def test_platform_reply_escapes_user_text(tmp_path):
    t, s = _platform_service(tmp_path)
    s.handle(_msg("/platform <b> on"))
    assert t.sent[-1][1].startswith("unknown platform: &lt;b&gt;; known: ")
    s.handle(_msg("/platform gulp"))
    assert "<" not in t.sent[-1][1] and "/platform NAME on|off|shadow" == t.sent[-1][1]


def test_platform_off_keeps_longer_quota_block(tmp_path):
    from bot.platforms import first_of_next_month, read_state, set_off

    t, s = _platform_service(tmp_path)
    path = tmp_path / "platforms.json"
    now = datetime.now(tz=timezone.utc).astimezone()
    until = first_of_next_month(now.replace(tzinfo=None))
    set_off(path, "gulp", "platform_quota", until, now)
    for verb in ("off", "shadow"):
        s.handle(_msg(f"/platform gulp {verb}"))
        entry = read_state(path)["gulp"]
        assert entry["off_until"] == until.strftime("%Y-%m-%d %H:%M") and entry["reason"] == "platform_quota"
        assert t.sent[-1][1].startswith("gulp: on → off (platform_quota)")
        assert f"kept off until {entry['off_until']} (platform_quota)" in t.sent[-1][1]
        assert "declared" not in t.sent[-1][1]   # декларація on, тримає саме квота
    s.handle(_msg("/platform gulp on"))   # явне on знімає й квоту: рішення оператора
    assert read_state(path) == {}
