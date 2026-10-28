"""Прохід диспетчера по черзі лідів: email-канал без тапу (спека §5.3, §6, §15).

Детерміновано, без LLM. Для кожного queued email-ліда в межах денного ліміту:
лист через Sender.send, запис стає sent ДО запису в трекер (повторна відправка
гірша за відсутній рядок), рядок у трекері, квитанція оператору. Збій
відправки = failed і картка, без повторів. Пауза (/pause) зупиняє все.
Тіньовий режим пише, що пішло б, у queue_dry.jsonl і більше нічого не чіпає.
Ліди інших каналів (form, linkedin_easy, linkedin_offsite, platform_login) лишаються queued для
браузерного воркера наступного етапу. Жодного конфігу клієнта на імпорті.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

from bot.generators.inbox import append_row
from bot.packages import Package, load_packages
from bot.sendqueue import DRY_FILE, due, mark, sent_today
from bot.state import JsonlStore, is_paused
from bot.telegram.api import Transport
from bot.telegram.cards import lead_failed_card, lead_sent_card
from bot.telegram.dispatch import Sender

ERROR_CUT = 500
# Запис у стані sending довше за це означає, що процес упав між POST і mark:
# лист міг піти, повторно не шлемо, оператор перевіряє «Надіслані».
STALE_SENDING_MINUTES = 30


@dataclass
class DispatchConfig:
    account: str
    limits: dict[str, int]
    lang: str
    chat_id: int | None
    dry_run: bool = False
    source_label: str = "contract"
    followup_days: int = 7


@dataclass
class DispatchResult:
    sent: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    skipped_limit: int = 0
    paused: bool = False


def _package_for(rec: dict) -> Package | None:
    pdir = Path(rec.get("package_dir") or "")
    if not pdir.is_dir():
        return None
    for pkg in load_packages(pdir.parent):
        if pkg.dir == pdir:
            return pkg
    return None


def _source(cfg: DispatchConfig, lead: dict) -> str:
    src = lead.get("source") or urlparse(lead.get("source_url") or "").netloc or "?"
    return f"{cfg.source_label}: {src}"


def _post(transport: Transport | None, chat_id: int | None, text: str) -> None:
    if transport is not None and chat_id:
        transport.send(chat_id, text, [])


def _missing_attachment(pkg: Package) -> str | None:
    """Назва першого вкладення, якого нема або яке порожнє; None, якщо все на місці."""
    if not pkg.attachments:
        return "no attachments"
    for p in pkg.attachments:
        if not Path(p).is_file() or Path(p).stat().st_size == 0:
            return Path(p).name
    return None


def _minutes(a: str, b: str) -> float:
    fmt = "%Y-%m-%d %H:%M"
    try:
        return (datetime.strptime(b, fmt) - datetime.strptime(a, fmt)).total_seconds() / 60
    except ValueError:
        return float("inf")


def _fail(store: JsonlStore, rec: dict, error: str, res: DispatchResult, transport, cfg: DispatchConfig,
          channel: str = "email") -> None:
    mark(store, rec["id"], status="failed", attempts=rec.get("attempts", 0) + 1, error=error[:ERROR_CUT])
    res.failed.append(rec["id"])
    _post(transport, cfg.chat_id, lead_failed_card(rec.get("company") or "", rec.get("title") or "",
                                                   channel, error, cfg.lang).text)


def _expire_stuck(store: JsonlStore, now: str, res: DispatchResult, transport, cfg: DispatchConfig) -> None:
    for rec in store.read():
        # form-записи в sending розбирає браузерний воркер (apply_leads): там інші таймінги й поради
        if rec.get("status") != "sending" or rec.get("channel") != "email":
            continue
        if _minutes(str(rec.get("sending_at") or ""), now) < STALE_SENDING_MINUTES:
            continue
        _fail(store, rec, f"send status unknown: process died after the send call at {rec.get('sending_at')}; "
                          "check the Sent folder before re-queueing", res, transport, cfg)


def run_dispatch(cfg: DispatchConfig, store: JsonlStore, tracker: Path, state_dir: Path, sender: Sender,
                 transport: Transport | None, today: str, now: str) -> DispatchResult:
    res = DispatchResult()
    if is_paused(state_dir):
        res.paused = True
        return res
    _expire_stuck(store, now, res, transport, cfg)
    limit = int(cfg.limits.get("email", 0))
    already = sent_today(store, today, "email")
    for rec in due(store, "email"):
        if already + len(res.sent) >= limit:
            res.skipped_limit += 1
            continue
        pkg = _package_for(rec)
        if pkg is None:
            _fail(store, rec, f"package missing: {rec.get('package_dir')}", res, transport, cfg)
            continue
        missing = _missing_attachment(pkg)
        if missing:   # CV зник або порожній між enqueue і send: без CV лист не йде
            _fail(store, rec, f"attachment missing or empty: {missing}", res, transport, cfg)
            continue
        to = pkg.lead.get("email") or ""
        if cfg.dry_run:
            JsonlStore(Path(state_dir) / DRY_FILE).append({**rec, "would_send_to": to, "at": now})
            continue
        # sending ДО виклику пошти: падіння між POST і mark не дасть другої подачі
        mark(store, rec["id"], status="sending", sending_at=now, attempts=rec.get("attempts", 0) + 1)
        try:
            sent_id = sender.send(cfg.account, to, pkg.subject, pkg.note, pkg.attachments)
        except Exception as e:  # noqa: BLE001 — збій пошти: failed і картка, без повторів
            mark(store, rec["id"], status="failed", error=str(e)[:ERROR_CUT])
            res.failed.append(rec["id"])
            _post(transport, cfg.chat_id, lead_failed_card(pkg.lead.get("company") or "", pkg.lead.get("title") or "",
                                                           "email", str(e), cfg.lang).text)
            continue
        mark(store, rec["id"], status="sent", sent_id=sent_id, sent_at=now)
        res.sent.append(rec["id"])
        tracker_error = ""
        try:
            until = (date.fromisoformat(today) + timedelta(days=cfg.followup_days)).isoformat()
            append_row(tracker, date=today, company=pkg.lead.get("company") or "", position=pkg.lead.get("title") or "",
                       source=_source(cfg, pkg.lead), salary=pkg.lead.get("stated_rate") or "", status="sent",
                       next_action=f"wait reply until {until}",
                       notes=f"E-Mail to {to}, Msg-ID {sent_id}, {', '.join(p.name for p in pkg.attachments) or 'no attachments'}, "
                             f"{pkg.apply_url or pkg.lead.get('source_url') or ''}".rstrip(", "))
        except Exception as e:  # noqa: BLE001 — лист уже пішов; трекер поправить людина
            tracker_error = str(e)[:ERROR_CUT]
            mark(store, rec["id"], tracker_error=tracker_error)
        text = lead_sent_card(pkg.lead.get("company") or "", pkg.lead.get("title") or "", "email", sent_id, cfg.lang).text
        if tracker_error:
            text += "\n" + lead_failed_card(pkg.lead.get("company") or "", pkg.lead.get("title") or "", "tracker",
                                            tracker_error, cfg.lang).text
        _post(transport, cfg.chat_id, text)
    return res
