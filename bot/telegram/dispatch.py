"""Dispatcher of pressed buttons: deterministic actions without an LLM.

send → a reply in the thread through scripts/gmail_rest.py (the same path the
morning agent replies by), the card becomes sent, the tracker gets a note with the
Msg-ID. A second tap on the same card sends nothing: Telegram repeats callbacks and
the operator taps twice. Pause (/pause) stops sending only.
Upwork proposal cards (stage 2e) send nothing at all: the operator submits on Upwork
himself, "done" closes the card and writes the tracker row, "send" is refused.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path
from typing import Protocol

from bot.generators.inbox import append_note, append_row, is_no_reply
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


def upwork_row(tracker: Path, card: dict, today: str, followup_days: int, source_label: str, by: str) -> None:
    """Tracker row for an Upwork proposal (stage 2e), sent by the operator or by the server. The
    alert does not name the client, so the company cell carries the client's country; the notes
    identify the job without the click-tracking URL."""
    until = (date.fromisoformat(today[:10]) + timedelta(days=followup_days)).isoformat()
    country = card.get("country") or ""
    notes = "; ".join(x for x in (
        f"Upwork alert {card['alert_date']}" if card.get("alert_date") else "",
        f"saved search: {card['saved_search']}" if card.get("saved_search") else "",
        f"budget {card['budget']}" if card.get("budget") else "",
        f"job {card['job_id']}" if card.get("job_id") else "",
        f"proposal {card['proposal_id']}" if card.get("proposal_id") else "",
        by) if x)
    append_row(tracker, date=today, company=f"Upwork client ({country})" if country else "Upwork client",
               position=card.get("title") or "", source=f"{source_label}: upwork", salary="",
               status="portal_submitted (портал)", next_action=f"wait reply until {until}", notes=notes)


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

    def _upwork_row(self, card: dict) -> None:
        upwork_row(self.tracker, card, self.today(), self.followup_days, self.source_label,
                   "submitted by the operator")

    def apply(self, action: Action) -> str:
        """Receipt for the operator: "OK …" or "ERR …"."""
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
            if card.get("kind") == "upwork":
                try:
                    self._upwork_row(card)
                except Exception as e:  # noqa: BLE001 — the proposal is on Upwork already; a human fixes the tracker
                    return f"OK done, tracker error: {e}"
                return "OK done"
            platform = self._platform(card)   # before _close_lead: the lead record is still the one the worker saw
            if self._close_lead(card, status="sent", sent_id=f"{platform or 'form'}:operator",
                                sent_at=self.today()):
                try:
                    self._handoff_row(card, platform)
                except Exception as e:  # noqa: BLE001 — the application counts already; a human fixes the tracker
                    self.queue.update(card["lead_id"], tracker_error=str(e)[:500])
                    return f"OK done, tracker error: {e}"
            return "OK done"
        if action.action != "send":
            return f"ERR unknown action {action.action}"
        if card.get("kind") == "upwork":   # the card has no send button; an old or forged callback
            return "ERR an Upwork proposal is submitted on Upwork by hand, nothing goes out by email"
        if is_paused(self.state_dir):
            return "ERR paused"
        # cards posted before the watcher knew no-reply senders still carry a send button (29.10.2026)
        if is_no_reply(card.get("reply_to") or card.get("from") or ""):
            return "ERR no-reply sender, reply in the portal or Gmail"
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
