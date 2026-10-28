"""Диспетчер натиснутих кнопок: детерміновані дії без LLM.

send → відповідь у треді через scripts/gmail_rest.py (той самий шлях, яким
відповідає ранковий агент), картка стає sent, у трекер іде нотатка з Msg-ID.
Другий тап на ту саму картку нічого не шле: Telegram повторює callback-и, а
оператор тисне двічі. Пауза (/pause) зупиняє лише відправку.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path
from typing import Protocol

from bot.generators.inbox import append_note, append_row
from bot.state import JsonlStore, is_paused, set_paused
from bot.telegram.service import Action


class Sender(Protocol):
    def reply(self, account: str, msg_id: str, body: str, to: str | None = None) -> str: ...
    def send(self, account: str, to: str, subject: str, body: str, attachments: list[Path]) -> str: ...


class GmailRestSender:
    """Відповідь у треді через gmail_rest.py reply; повертає id надісланого листа."""

    def __init__(self, engine: Path, python: Path, client_slug: str) -> None:
        self.engine = Path(engine)
        self.python = Path(python)
        self.client_slug = client_slug

    def reply(self, account: str, msg_id: str, body: str, to: str | None = None) -> str:
        """to: явний адресат. Без нього gmail_rest відповідає відправнику листа msg_id,
        а для follow-up це наш власний надісланий лист, тобто ми самі."""
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
            f.write(body)
            body_file = f.name
        try:
            proc = subprocess.run(
                [str(self.python), str(self.engine / "scripts" / "gmail_rest.py"),
                 "reply", account, msg_id, body_file, *([to] if to else [])],
                capture_output=True, text=True, timeout=120, check=False,
                env={**os.environ, "JOBSEARCH_CLIENT": self.client_slug})
        finally:
            Path(body_file).unlink(missing_ok=True)
        return self._sent_id(proc, "reply")

    def send(self, account: str, to: str, subject: str, body: str, attachments: list[Path]) -> str:
        """Новий лист із вкладеннями через gmail_rest send (лише з send_from-акаунта)."""
        import json

        draft = {"to": to, "subject": subject, "body": body, "attachments": [str(p) for p in attachments]}
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump(draft, f, ensure_ascii=False)
            draft_file = f.name
        try:
            proc = subprocess.run(
                [str(self.python), str(self.engine / "scripts" / "gmail_rest.py"), "send", account, draft_file],
                capture_output=True, text=True, timeout=180, check=False,
                env={**os.environ, "JOBSEARCH_CLIENT": self.client_slug})
        finally:
            Path(draft_file).unlink(missing_ok=True)
        return self._sent_id(proc, "send")

    @staticmethod
    def _sent_id(proc: subprocess.CompletedProcess, cmd: str) -> str:
        for line in proc.stdout.splitlines():
            if line.startswith("SENT id="):
                return line.split()[1].split("=", 1)[1]
        raise RuntimeError(
            f"gmail_rest {cmd} без SENT (exit {proc.returncode}): "
            f"{(proc.stdout + proc.stderr).strip()[-500:]}")


class Dispatcher:
    def __init__(self, sender: Sender, cards: JsonlStore, tracker: Path, state_dir: Path,
                 today: Callable[[], str], queue: JsonlStore | None = None, source_label: str = "contract",
                 followup_days: int = 7) -> None:
        self.sender = sender
        self.queue = queue   # черга лідів: тап на hand-off картці закриває і запис ліда
        self.source_label = source_label
        self.followup_days = followup_days
        self.cards = cards
        self.tracker = Path(tracker)
        self.state_dir = Path(state_dir)
        self.today = today

    def _close_lead(self, card: dict, **fields) -> bool:
        """Hand-off картка: той самий тап переводить лід у черзі (воркер міг уже не чекати).
        True, якщо запис ліда змінено; поданий лід (сторінка вже підтвердила) не чіпаємо."""
        if self.queue is None or card.get("kind") != "handoff" or not card.get("lead_id"):
            return False
        rec = self.queue.get(card["lead_id"])
        if rec is None or rec.get("status") == "sent":
            return False
        return self.queue.update(card["lead_id"], **fields)

    def _platform(self, card: dict) -> str | None:
        """Платформа hand-off: з картки (воркер 2d пише її туди), інакше із запису ліда в черзі."""
        if card.get("platform"):
            return card["platform"]
        rec = self.queue.get(card["lead_id"]) if self.queue is not None and card.get("lead_id") else None
        return (rec or {}).get("platform") or None

    def _handoff_row(self, card: dict, platform: str | None = None) -> None:
        """Рядок у трекері за подачу, яку підтвердив оператор: воркер, що чекав, його вже не пише.
        Джерело: платформа ліда, інакше ATS картки, інакше form."""
        today = self.today()
        until = (date.fromisoformat(today[:10]) + timedelta(days=self.followup_days)).isoformat()
        url = card.get("url") or ""
        source = f"{self.source_label}: {platform or card.get('ats') or 'form'}"
        append_row(self.tracker, date=today, company=card.get("company") or "", position=card.get("title") or "",
                   source=source, salary=card.get("salary") or "",
                   status="portal_submitted (портал)", next_action=f"wait reply until {until}",
                   notes=f"{url}, confirmed by operator")

    def apply(self, action: Action) -> str:
        """Квитанція для оператора: «OK …» або «ERR …»."""
        if action.action == "pause":
            set_paused(self.state_dir, True)
            return "OK paused"
        if action.action == "resume":
            set_paused(self.state_dir, False)
            return "OK resumed"
        card = self.cards.get(action.card) if action.card else None
        if card is None:
            return "ERR unknown card"
        if card.get("status") != "open":
            return f"OK already {card.get('status')}"
        if action.action == "skip":
            self.cards.update(card["id"], status="skipped", closed_at=self.today())
            self._close_lead(card, status="held", reasons=["operator_skipped"])
            return "OK skipped"
        if action.action == "done":
            self.cards.update(card["id"], status="done", closed_at=self.today())
            platform = self._platform(card)   # до _close_lead: запис ліда ще той, що бачив воркер
            if self._close_lead(card, status="sent", sent_id=f"{platform or 'form'}:operator",
                                sent_at=self.today()):
                try:
                    self._handoff_row(card, platform)
                except Exception as e:  # noqa: BLE001 — подача вже зарахована; трекер поправить людина
                    self.queue.update(card["lead_id"], tracker_error=str(e)[:500])
                    return f"OK done, tracker error: {e}"
            return "OK done"
        if action.action != "send":
            return f"ERR unknown action {action.action}"
        if is_paused(self.state_dir):
            return "ERR paused"
        if not (card.get("draft") or "").strip():
            return "ERR no draft"
        try:
            sent_id = self.sender.reply(card["account"], card["message_id"], card["draft"])
        except Exception as e:  # noqa: BLE001 — збій пошти показуємо оператору, картка лишається
            return f"ERR {e}"
        self.cards.update(card["id"], status="sent", sent_id=sent_id, sent_at=self.today())
        if card.get("company"):
            append_note(self.tracker, card["company"], f"{self.today()} reply sent Msg {sent_id}")
        return f"OK sent {sent_id}"
