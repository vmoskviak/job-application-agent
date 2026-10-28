"""Транспорт Telegram Bot API через httpx: long polling, повідомлення з кнопками.

Лише чотири методи API: getUpdates, sendMessage, answerCallbackQuery,
editMessageReplyMarkup. Сервіс працює з Transport (протокол), тож у тестах
замість HTTP стоїть FakeTransport.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

import httpx

TEXT_LIMIT = 4096          # sendMessage: максимум знаків у тексті
CALLBACK_DATA_LIMIT = 64   # callback_data: максимум байтів


@dataclass
class Button:
    text: str
    data: str | None = None   # callback_data
    url: str | None = None


@dataclass
class Event:
    update_id: int
    kind: Literal["message", "callback"]
    chat_id: int
    user_id: int
    text: str                       # текст повідомлення або callback_data
    callback_id: str | None = None
    message_id: int | None = None


def parse_updates(raw: list[dict]) -> list[Event]:
    """Оновлення Bot API → події; інші типи (edited_message, стікери) пропускаються."""
    events: list[Event] = []
    for upd in raw or []:
        uid = int(upd.get("update_id", 0))
        msg = upd.get("message")
        if msg and isinstance(msg.get("text"), str):
            events.append(Event(uid, "message", int(msg["chat"]["id"]), int(msg.get("from", {}).get("id", 0)),
                                msg["text"], message_id=msg.get("message_id")))
            continue
        cb = upd.get("callback_query")
        if cb and cb.get("data") is not None:
            m = cb.get("message") or {}
            chat_id = int((m.get("chat") or {}).get("id", cb.get("from", {}).get("id", 0)))
            events.append(Event(uid, "callback", chat_id, int(cb.get("from", {}).get("id", 0)),
                                str(cb["data"]), callback_id=str(cb.get("id")), message_id=m.get("message_id")))
    return events


def keyboard(rows: list[list[Button]]) -> dict:
    """reply_markup для inline-кнопок; callback_data понад 64 байти Telegram відкидає."""
    out = []
    for row in rows:
        line = []
        for b in row:
            if b.url:
                line.append({"text": b.text, "url": b.url})
            else:
                data = b.data or ""
                if len(data.encode("utf-8")) > CALLBACK_DATA_LIMIT:
                    raise ValueError(f"callback_data понад {CALLBACK_DATA_LIMIT} байтів: {data!r}")
                line.append({"text": b.text, "callback_data": data})
        out.append(line)
    return {"inline_keyboard": out}


class Transport(Protocol):
    def get_updates(self, offset: int | None, timeout: int) -> list[Event]: ...
    def send(self, chat_id: int, text: str, buttons: list[list[Button]] | None = None) -> int: ...
    def answer(self, callback_id: str, text: str = "") -> None: ...
    def clear_buttons(self, chat_id: int, message_id: int) -> None: ...


class HttpTransport:
    def __init__(self, token: str, client: httpx.Client | None = None) -> None:
        self._base = f"https://api.telegram.org/bot{token}/"
        self._client = client or httpx.Client()

    def _call(self, method: str, payload: dict, timeout: float = 30) -> object:
        r = self._client.post(self._base + method, json=payload, timeout=timeout)
        body = r.json()
        if not body.get("ok"):
            raise RuntimeError(f"{method}: {body.get('description') or r.status_code}")
        return body.get("result")

    def get_updates(self, offset: int | None, timeout: int) -> list[Event]:
        payload: dict = {"timeout": timeout, "allowed_updates": ["message", "callback_query"]}
        if offset is not None:
            payload["offset"] = offset
        return parse_updates(self._call("getUpdates", payload, timeout=timeout + 10) or [])

    def send(self, chat_id: int, text: str, buttons: list[list[Button]] | None = None) -> int:
        payload: dict = {"chat_id": chat_id, "text": text[:TEXT_LIMIT], "parse_mode": "HTML",
                         "disable_web_page_preview": True}
        if buttons:
            payload["reply_markup"] = keyboard(buttons)
        result = self._call("sendMessage", payload)
        return int(result["message_id"])

    def answer(self, callback_id: str, text: str = "") -> None:
        self._call("answerCallbackQuery", {"callback_query_id": callback_id, "text": text})

    def clear_buttons(self, chat_id: int, message_id: int) -> None:
        self._call("editMessageReplyMarkup",
                   {"chat_id": chat_id, "message_id": message_id, "reply_markup": {"inline_keyboard": []}})
