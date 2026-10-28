"""Транспорт Telegram Bot API (bot.telegram.api) і FakeTransport для решти тестів етапу 2a."""
from __future__ import annotations

import json

import httpx
import pytest

from bot.telegram.api import Button, Event, HttpTransport, keyboard, parse_updates


class FakeTransport:
    """Черга подій на get_updates, журнал надісланого; send дає id від 100."""

    def __init__(self) -> None:
        self.queued: list[list[Event]] = []
        self.sent: list[tuple[int, str, list | None]] = []
        self.answered: list[str] = []
        self.answered_text: list[str] = []
        self.cleared: list[tuple[int, int]] = []
        self.fail_next_get = False
        self._next_id = 100

    def get_updates(self, offset: int | None, timeout: int) -> list[Event]:
        if self.fail_next_get:
            self.fail_next_get = False
            raise RuntimeError("network down")
        return self.queued.pop(0) if self.queued else []

    def send(self, chat_id: int, text: str, buttons: list[list[Button]] | None = None) -> int:
        self.sent.append((chat_id, text, buttons))
        self._next_id += 1
        return self._next_id - 1

    def answer(self, callback_id: str, text: str = "") -> None:
        self.answered.append(callback_id)
        self.answered_text.append(text)

    def clear_buttons(self, chat_id: int, message_id: int) -> None:
        self.cleared.append((chat_id, message_id))


def test_parse_updates_message_and_callback():
    raw = [
        {"update_id": 5, "message": {"message_id": 1, "text": "/status", "chat": {"id": 42}, "from": {"id": 42}}},
        {"update_id": 6, "callback_query": {"id": "cb1", "data": "send:re-1", "from": {"id": 42},
                                            "message": {"message_id": 9, "chat": {"id": 42}}}},
        {"update_id": 7, "edited_message": {"message_id": 2, "text": "x", "chat": {"id": 42}, "from": {"id": 42}}},
        {"update_id": 8, "message": {"message_id": 4, "chat": {"id": 42}, "from": {"id": 42}, "sticker": {}}},
    ]
    evs = parse_updates(raw)
    assert [e.kind for e in evs] == ["message", "callback"]
    assert evs[0] == Event(5, "message", 42, 42, "/status", message_id=1)
    assert evs[1] == Event(6, "callback", 42, 42, "send:re-1", callback_id="cb1", message_id=9)


def test_keyboard_json_and_limit():
    kb = keyboard([[Button("Send", data="send:re-1"), Button("Open", url="https://mail.google.com/")]])
    assert kb == {"inline_keyboard": [[{"text": "Send", "callback_data": "send:re-1"},
                                       {"text": "Open", "url": "https://mail.google.com/"}]]}
    with pytest.raises(ValueError):
        keyboard([[Button("x", data="d" * 65)]])
    with pytest.raises(ValueError):
        keyboard([[Button("x", data="й" * 33)]])   # 66 байтів в UTF-8


def test_http_transport_send_and_get():
    calls = []

    def handler(request):
        calls.append((request.url.path, json.loads(request.content)))
        if request.url.path.endswith("/sendMessage"):
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 77}})
        return httpx.Response(200, json={"ok": True, "result": [
            {"update_id": 1, "message": {"message_id": 3, "text": "hi", "chat": {"id": 1}, "from": {"id": 1}}}]})

    t = HttpTransport("TOKEN", client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert t.send(1, "<b>x</b> & y", [[Button("A", data="a:1")]]) == 77
    assert calls[0][0] == "/botTOKEN/sendMessage" and calls[0][1]["parse_mode"] == "HTML"
    assert calls[0][1]["reply_markup"] == {"inline_keyboard": [[{"text": "A", "callback_data": "a:1"}]]}
    assert calls[0][1]["disable_web_page_preview"] is True
    assert [e.text for e in t.get_updates(None, 1)] == ["hi"]
    assert calls[1][1]["allowed_updates"] == ["message", "callback_query"] and calls[1][1]["timeout"] == 1
    assert "offset" not in calls[1][1]
    t.get_updates(10, 1)
    assert calls[2][1]["offset"] == 10


def test_http_transport_cuts_long_text_and_clears_buttons():
    calls = []

    def handler(request):
        calls.append((request.url.path, json.loads(request.content)))
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    t = HttpTransport("T", client=httpx.Client(transport=httpx.MockTransport(handler)))
    t.send(1, "x" * 5000)
    assert len(calls[0][1]["text"]) == 4096
    t.answer("cb", "done")
    t.clear_buttons(1, 9)
    assert calls[1][0].endswith("/answerCallbackQuery") and calls[1][1] == {"callback_query_id": "cb", "text": "done"}
    assert calls[2][0].endswith("/editMessageReplyMarkup")
    assert calls[2][1] == {"chat_id": 1, "message_id": 9, "reply_markup": {"inline_keyboard": []}}


def test_http_transport_raises_on_not_ok():
    t = HttpTransport("T", client=httpx.Client(transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json={"ok": False, "description": "Unauthorized"}))))
    with pytest.raises(RuntimeError, match="Unauthorized"):
        t.get_updates(None, 1)
