"""Детекція прострочених follow-ups із стану трекера.

Дизайн: docs/superpowers/specs/2026-07-23-overdue-followups-design.md.
Чисті функції парсингу — тестуються на фікстурі; надсилання (reply у thread)
робить ранковий агент через scripts/gmail_rest.py за прапором --send.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, asdict
from datetime import date
from pathlib import Path
from typing import Iterable


MAX_FOLLOWUPS_PER_RUN = 12
ACTIVE_STATUSES = {"sent"}

# «follow-up якщо тиша до 2026-06-10» / «тиша до ~06.08» / «до 28.07»
_DEADLINE_RE = re.compile(
    r"(?:follow-?up|тиша)[^|]*?до\s*~?\s*"
    r"(\d{4}-\d{2}-\d{2}|\d{1,2}\.\d{1,2}(?:\.\d{2,4})?)",
    re.IGNORECASE,
)
# 16-hex Gmail message id: у бектиках `...` або після «Msg-ID »
_MSGID_RE = re.compile(r"`([0-9a-f]{16})`|Msg-?ID\s+([0-9a-f]{16})", re.IGNORECASE)
# останній зафіксований follow-up: «✅ 23.07 follow-up» / «follow-up (Msg ...)»
_DONE_RE = re.compile(r"follow-?up", re.IGNORECASE)
# email-канали: reply у thread можливий лише коли ми слали e-mail АБО
# роботодавець відповів e-mail. LinkedIn Easy Apply / Portal / StepStone / XING /
# Avature-confirmations мають Msg-ID (лист від платформи, no-reply) — НЕ тред
# роботодавця, reply туди безглуздий → не follow-up-канал.
_EMAIL_CHANNEL_RE = re.compile(r"\bemail\b|\bdirect\b|\be-mail\b", re.IGNORECASE)
_EMPLOYER_MAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


@dataclass
class FollowupRow:
    row_id: str
    company: str
    role: str
    status: str
    deadline: str | None       # ISO або None якщо не розпарсено
    msg_id: str | None
    account: str               # jobs | main
    already_done: bool
    email_channel: bool        # чи є реплайабельний email-тред роботодавця
    raw_next_action: str
    sent_on: str = ""         # ISO-дата подачі (cells[1]); контракт: follow-up за віком
    notes: str = ""           # Notes рядка: адреса роботодавця дає мову follow-up


@dataclass
class DueFollowup:
    row_id: str
    company: str
    role: str
    deadline: str
    msg_id: str
    account: str
    body: str


def _parse_deadline(text: str, default_year: int) -> str | None:
    m = _DEADLINE_RE.search(text)
    if not m:
        return None
    raw = m.group(1)
    if "-" in raw:
        return raw
    parts = raw.split(".")
    day, month = int(parts[0]), int(parts[1])
    if len(parts) == 3:
        year = int(parts[2])
        if year < 100:
            year += 2000
    else:
        year = default_year
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


def _account_for(notes: str, aliases: Iterable[str] | None = None) -> str:
    """Скринька для reply у тред. `aliases` — що реально є в accounts.yaml:
    у клієнта без `main` відповіді роботодавців лежать у `jobs`."""
    low = notes.lower()
    acc = "jobs"
    if "main" in low or "reply роботодавц" in low or "у thread" in low:
        acc = "main"
    if aliases is not None and acc not in set(aliases):
        acc = "jobs"
    return acc


def followup_body(role: str, lang: str, full_name: str, phone: str) -> str:
    """Текст follow-up за мовою заявки; без тире як сполучника."""
    if lang == "de":
        return (
            "Sehr geehrte Damen und Herren,\n\n"
            f"ich möchte höflich an meine Bewerbung als {role} anknüpfen und mein "
            "weiterhin großes Interesse an der Position bekräftigen. Gerne stehe ich "
            "für Rückfragen oder ein persönliches Gespräch zur Verfügung.\n\n"
            "Über eine kurze Rückmeldung zum aktuellen Stand des Auswahlverfahrens "
            "würde ich mich sehr freuen.\n\n"
            f"Mit freundlichen Grüßen\n{full_name}\n{phone}"
        )
    return (
        "Hello,\n\n"
        f"I wanted to follow up on my application for the {role} role. I remain very "
        "interested in the position and would be glad to answer any questions or walk "
        "you through how I could contribute to the team. If it helps, I can also share "
        "more detail on the projects that are closest to this role.\n\n"
        "Could you let me know where the selection process currently stands?\n\n"
        f"Best regards\n{full_name}\n{phone}"
    )


def _followup_body(role: str) -> str:
    # candidate читає профіль клієнта на імпорті (і вимагає street); контрактний
    # клієнт його не має, тому імпорт тут, а не на рівні модуля
    from bot.apply.candidate import FULL_NAME, PHONE
    return followup_body(role, "de", FULL_NAME, PHONE)


def parse_tracker_rows(text: str, default_year: int,
                       aliases: Iterable[str] | None = None) -> list[FollowupRow]:
    rows: list[FollowupRow] = []
    for line in text.splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        # головна таблиця: # | date | company | role | channel | salary |
        #                  status | updated | next_action | notes
        if len(cells) < 10 or not cells[0].isdigit():
            continue
        row_id, sent_on, company, role, channel = (
            cells[0], cells[1], cells[2], cells[3], cells[4])
        status = cells[6].split()[0].lower() if cells[6] else ""
        next_action, notes = cells[8], cells[9]
        msg_m = _MSGID_RE.search(notes)
        msg_id = (msg_m.group(1) or msg_m.group(2)) if msg_m else None
        # реплайабельний тред: канал email/direct, АБО в notes є пошта
        # роботодавця / прямий маркер reply роботодавцю
        email_channel = bool(
            _EMAIL_CHANNEL_RE.search(channel)
            or _EMPLOYER_MAIL_RE.search(notes)
            or "reply роботодавц" in notes.lower()
        )
        rows.append(FollowupRow(
            row_id=row_id, company=company, role=role, status=status,
            deadline=_parse_deadline(next_action, default_year),
            msg_id=msg_id, account=_account_for(notes, aliases),
            already_done=bool(_DONE_RE.search(notes)),
            email_channel=email_channel,
            raw_next_action=next_action,
            sent_on=sent_on,
            notes=notes,
        ))
    return rows


def find_due_followups(
    rows: list[FollowupRow], today: date
) -> tuple[list[DueFollowup], list[dict]]:
    """Повертає (due, skipped). skipped — рядки з причиною (no silent skip)."""
    due: list[DueFollowup] = []
    skipped: list[dict] = []
    for r in rows:
        if r.status not in ACTIVE_STATUSES:
            continue
        # має ознаки follow-up-циклу (дедлайн у next_action), інакше не наш кейс
        if "follow" not in r.raw_next_action.lower() and "тиша" not in r.raw_next_action.lower():
            continue
        if r.deadline is None:
            skipped.append({"row_id": r.row_id, "company": r.company,
                            "reason": "deadline unparsed", "next_action": r.raw_next_action})
            continue
        if date.fromisoformat(r.deadline) > today:
            continue
        if r.already_done:
            skipped.append({"row_id": r.row_id, "company": r.company,
                            "reason": "already followed up"})
            continue
        if not r.msg_id:
            skipped.append({"row_id": r.row_id, "company": r.company,
                            "reason": "no Msg-ID (no email thread) — auto-ghost handles it"})
            continue
        if not r.email_channel:
            # Msg-ID є, але це confirmation платформи (LinkedIn Easy Apply /
            # Portal / StepStone), не тред роботодавця — reply безглуздий
            skipped.append({"row_id": r.row_id, "company": r.company,
                            "reason": "non-email channel (platform confirmation, "
                                      "not employer thread) — auto-ghost handles it"})
            continue
        due.append(DueFollowup(
            row_id=r.row_id, company=r.company, role=r.role,
            deadline=r.deadline, msg_id=r.msg_id, account=r.account,
            body=_followup_body(r.role),
        ))
    return due, skipped


def find_due_by_age(
    rows: list[FollowupRow], today: date, days: int
) -> tuple[list[DueFollowup], list[dict]]:
    """Контрактний ринок: follow-up через `days` днів після подачі, без дедлайну в
    next_action. Умови ті самі, що й у find_due_followups: статус sent, Msg-ID,
    email-тред роботодавця, ще не нагадували. Повертає (due, skipped з причиною)."""
    from datetime import timedelta

    due: list[DueFollowup] = []
    skipped: list[dict] = []
    for r in rows:
        if r.status not in ACTIVE_STATUSES:
            continue
        try:
            sent = date.fromisoformat(r.sent_on)
        except ValueError:
            skipped.append({"row_id": r.row_id, "company": r.company,
                            "reason": "date unparsed", "sent_on": r.sent_on})
            continue
        deadline = sent + timedelta(days=days)
        if deadline > today:
            continue
        if r.already_done:
            skipped.append({"row_id": r.row_id, "company": r.company,
                            "reason": "already followed up"})
            continue
        if not r.msg_id:
            skipped.append({"row_id": r.row_id, "company": r.company,
                            "reason": "no Msg-ID (no email thread) — auto-ghost handles it"})
            continue
        if not r.email_channel:
            skipped.append({"row_id": r.row_id, "company": r.company,
                            "reason": "non-email channel (platform confirmation, "
                                      "not employer thread) — auto-ghost handles it"})
            continue
        due.append(DueFollowup(
            row_id=r.row_id, company=r.company, role=r.role,
            deadline=deadline.isoformat(), msg_id=r.msg_id, account=r.account,
            body=_followup_body(r.role),
        ))
    return due, skipped


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Прострочені follow-ups із трекера")
    parser.add_argument("--tracker", type=Path,
                        default=None)
    parser.add_argument("--today", help="ISO дата (для тестів); дефолт — сьогодні")
    parser.add_argument("--out", type=Path, help="куди писати JSON зі списком DUE")
    parser.add_argument("--send", action="store_true",
                        help="(резерв) сигнал агенту, що треба слати; сам скрипт "
                             "reply НЕ шле — це робить агент через gmail_rest")
    args = parser.parse_args(argv)

    today = date.fromisoformat(args.today) if args.today else _today()
    from bot import client
    if args.tracker is None:
        args.tracker = client.active().tracker
    text = args.tracker.read_text(encoding="utf-8")
    rows = parse_tracker_rows(text, default_year=today.year,
                              aliases=client.active().gmail_aliases)
    due, skipped = find_due_followups(rows, today)

    guard = len(due) > MAX_FOLLOWUPS_PER_RUN
    payload = {
        "date": today.isoformat(),
        "guard_triggered": guard,
        "due": [asdict(d) for d in due],
        "skipped": skipped,
        "send_requested": args.send,
    }
    if args.out:
        args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                            encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if guard:
        print(f"[followups] GUARD: {len(due)} > {MAX_FOLLOWUPS_PER_RUN} — "
              f"агенту НЕ слати автоматично, перевірити трекер", file=sys.stderr)
        return 3
    return 0


def _today() -> date:
    from datetime import datetime
    return datetime.now().date()


if __name__ == "__main__":
    sys.exit(main())
