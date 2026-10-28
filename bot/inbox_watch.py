"""Один прохід по свіжій пошті контрактного клієнта.

Клас листа → статус у трекері → картка оператору з чернеткою → follow-ups.
Детерміновано; модель лише пише чернетки за інтерфейсом LLM і обмежена
бюджетом на прохід. Без конфігу клієнта на імпорті: усе приходить параметрами,
їх збирає scripts/inbox_watch.py. Стан у var/state: inbox_seen.json (оброблені
листи), cards.jsonl (картки), cards_dry.jsonl (тіньовий режим), last_watch.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

from bot.followups import (
    _EMPLOYER_MAIL_RE,
    MAX_FOLLOWUPS_PER_RUN,
    find_due_by_age,
    followup_body,
    parse_tracker_rows,
)
from bot.generators.cover_note import rate_sentence
from bot.generators.employer_scan import tracker_status_by_company
from bot.generators.inbox import (
    append_note_by_row,
    assemble_draft,
    classify_mail,
    currency_for_mail,
    draft_language,
    draft_prompt,
    is_agency,
    parse_draft,
    platform_group,
    status_for_class,
)
from bot.generators.responses import update_tracker_status
from bot.llm import LLM, LockBusy
from bot.state import JsonlStore, SeenSet, is_paused
from bot.telegram.api import Transport
from bot.telegram.cards import (
    Card,
    card_id,
    followup_card,
    gmail_url,
    notify_card,
    rejection_card,
    reply_card,
)
from bot.telegram.dispatch import Sender


@dataclass
class WatchConfig:
    agencies_de: list[str]
    followup_days: int
    reply_account: str
    gmail_address: str
    lang: str
    max_llm_per_run: int = 5
    max_cards_per_run: int = 15
    dry_run: bool = False
    # назви, які не називаються адресатам поза трекером (агентури, платформи): поточний роботодавець
    forbidden_names: list[str] = field(default_factory=list)


@dataclass
class WatchResult:
    carded: list[str] = field(default_factory=list)
    tracker_updates: list[tuple[str, str]] = field(default_factory=list)
    followups_sent: list[str] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)
    llm_calls: int = 0
    system: int = 0


def _record(cid: str, kind: str, status: str, item: dict, cls: str, draft: str | None, created: str) -> dict:
    return {"id": cid, "kind": kind, "status": status, "account": item.get("account"),
            "message_id": item.get("message_id"), "thread_id": item.get("thread_id"),
            "company": item.get("company"), "class": cls, "draft": draft, "created": created}


def _now_text() -> str:
    return datetime.now(tz=timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M")


# Порядок статусів відповіді: старіше підтвердження не відкочує співбесіду
_STATUS_RANK = {"confirmed": 1, "interview": 2}
_TERMINAL = ("rejected",)
_DE_DOMAINS = (".de", ".at", ".ch")


def _mail_date(item: dict) -> datetime:
    try:
        return parsedate_to_datetime(item.get("date") or "").astimezone(timezone.utc)
    except (TypeError, ValueError, IndexError):
        return datetime.min.replace(tzinfo=timezone.utc)


def _followup_lang(address: str, agencies_de: list[str]) -> str:
    """Мова follow-up за адресою роботодавця: агентура зі списку або домен .de/.at/.ch → de."""
    if is_agency(address, agencies_de):
        return "de"
    domain = address.rsplit("@", 1)[-1].lower()
    return "de" if domain.endswith(_DE_DOMAINS) else "en"


def run_watch(cfg: WatchConfig, items: list[dict], tracker: Path, state_dir: Path,
              transport: Transport | None, chat_id: int | None, llm: LLM, sender: Sender,
              brief: str, profile: dict, today: date) -> WatchResult:
    tracker = Path(tracker)
    state_dir = Path(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    # Тіньовий тиждень має власну множину бачених: інакше ті самі листи йшли б у
    # модель щогодини, а перший живий прохід однаково бачить останні --days днів
    seen = SeenSet(state_dir / ("inbox_seen_dry.json" if cfg.dry_run else "inbox_seen.json"))
    cards = JsonlStore(state_dir / "cards.jsonl")
    dry_cards = JsonlStore(state_dir / "cards_dry.jsonl")
    res = WatchResult()
    posting = transport is not None and chat_id is not None and not cfg.dry_run
    today_s = today.isoformat()
    llm_blocked = False

    def mark_seen(mid: str) -> None:
        seen.add(mid)

    def post(card: Card, record: dict) -> bool:
        """Картка оператору; збій відправки лишає лист небаченим до наступного проходу."""
        if posting:
            try:
                tg_id = transport.send(chat_id, card.text, card.buttons)
            except Exception as e:  # noqa: BLE001 — Telegram 4xx/мережа не має зупиняти прохід
                res.skipped.append({"message_id": record.get("message_id"), "reason": f"send: {e}"})
                return False
            cards.append({**record, "tg_message_id": tg_id})
        else:
            dry_cards.append({**record, "text": card.text, "at": _now_text()})
        res.carded.append(record["id"])
        return True

    def set_status(company: str | None, status: str | None, from_llm: bool = False) -> None:
        if cfg.dry_run or not company or not status:
            return
        if from_llm and status in _TERMINAL:
            return  # клас від моделі не ставить фінальних статусів (текст листа чужий)
        current = tracker_status_by_company(tracker).get(company, "")
        if _STATUS_RANK.get(current.split()[0] if current else "", 0) > _STATUS_RANK.get(status, 0):
            return  # старіший лист не відкочує новіший статус
        if update_tracker_status(tracker, company, status, today_s):
            res.tracker_updates.append((company, status))

    for item in sorted(items, key=_mail_date):
        mid = str(item.get("message_id") or "")
        if not mid or mid in seen:
            continue
        if len(res.carded) >= cfg.max_cards_per_run:
            break  # решта лишається небаченою до наступного проходу
        frm = item.get("from") or ""
        company = item.get("company")
        cls = classify_mail(item, cfg.agencies_de)
        if cls == "system":
            res.system += 1
            mark_seen(mid)
            continue
        if cls == "unknown" and not (company or platform_group(frm) or is_agency(frm, cfg.agencies_de)):
            res.skipped.append({"message_id": mid, "reason": "unknown sender"})
            mark_seen(mid)
            continue
        set_status(company, status_for_class(cls))
        url = gmail_url(cfg.gmail_address, mid)
        if cls == "confirmed":
            mark_seen(mid)
            continue
        if cls == "rejected":
            cid = card_id("rejected", mid)
            if post(rejection_card(item, cid, url, cfg.lang), _record(cid, "rejected", "info", item, cls, None, today_s)):
                mark_seen(mid)
            continue
        if item.get("thread_answered"):
            res.skipped.append({"message_id": mid, "reason": "already answered"})
            mark_seen(mid)
            continue
        if llm_blocked:
            res.skipped.append({"message_id": mid, "reason": "lock busy"})
            continue  # небачений: наступна година дасть справжню чернетку
        draft: str | None = None
        if res.llm_calls < cfg.max_llm_per_run:
            lang = draft_language(item, cfg.agencies_de)
            to_agency = is_agency(frm, cfg.agencies_de)
            # Компанія, куди вже подавались, і співбесіда чи запит: це не перший контакт,
            # тож ставка йде навіть під omit_first_contact
            first_contact = not company and cls not in ("interview", "info_request")
            rate_line = rate_sentence(profile.get("rate") or {}, lang, currency_for_mail(item, lang),
                                      first_contact=first_contact, to_agency=to_agency)
            forbidden = [] if company else list(cfg.forbidden_names)
            res.llm_calls += 1
            try:
                parsed = parse_draft(llm.complete(
                    draft_prompt(item, cls, lang, brief, profile, rate_line, forbidden=forbidden)))
                draft = assemble_draft(parsed["draft"], lang, profile, rate_line, company)
                if cls == "unknown" and parsed["class"] != "unknown":
                    cls = parsed["class"]
                    set_status(company, status_for_class(cls), from_llm=True)
                leaked = next((n for n in forbidden if n.lower() in draft.lower()), None)
                if leaked:
                    draft = None
                    res.skipped.append({"message_id": mid, "reason": f"forbidden name: {leaked}"})
            except LockBusy:
                llm_blocked = True
                res.skipped.append({"message_id": mid, "reason": "lock busy"})
                continue  # небачений, без картки
            except (ValueError, RuntimeError, TimeoutError) as e:
                res.skipped.append({"message_id": mid, "reason": f"llm: {e}"})
        else:
            res.skipped.append({"message_id": mid, "reason": "llm budget"})
        cid = card_id("reply", mid)
        if post(reply_card(item, cls, draft, cid, url, cfg.lang), _record(cid, "reply", "open", item, cls, draft, today_s)):
            mark_seen(mid)

    _followups(cfg, res, tracker, state_dir, sender, profile, today, post)
    (state_dir / "last_watch").write_text(_now_text(), encoding="utf-8")
    return res


# Режим notify: класи, про які оператор має дізнатись одразу. Підтвердження й
# відмови веде ранковий агент у трекері, їх тут не шлють.
NOTIFY_CLASSES = ("interview", "info_request", "invitation")


def run_notify(cfg: WatchConfig, items: list[dict], state_dir: Path, transport: Transport | None,
               chat_id: int | None, prefix: str, today: date) -> WatchResult:
    """Прохід лише зі сповіщеннями (accounts.yaml → telegram.mode: notify): без правок
    трекера, без чернеток і follow-up. Клієнти з ранковим агентом (ринок de_employment)
    тримають трекер там, а тут лише дізнаються про співбесіду чи запит у ту ж годину,
    а не у вечірньому зведенні (28.10.2026: запрошення на 17:30 дійшло б о 20:40).

    Невідомий лист від компанії з трекера теж іде оператору: ключові слова
    пропускають частину запрошень."""
    state_dir = Path(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    seen = SeenSet(state_dir / ("inbox_seen_dry.json" if cfg.dry_run else "inbox_seen.json"))
    cards = JsonlStore(state_dir / "cards.jsonl")
    dry_cards = JsonlStore(state_dir / "cards_dry.jsonl")
    res = WatchResult()
    posting = transport is not None and chat_id is not None and not cfg.dry_run
    today_s = today.isoformat()
    for item in sorted(items, key=_mail_date):
        mid = str(item.get("message_id") or "")
        if not mid or mid in seen:
            continue
        if len(res.carded) >= cfg.max_cards_per_run:
            break
        company = item.get("company")
        cls = classify_mail(item, cfg.agencies_de)
        if cls not in NOTIFY_CLASSES and not (cls == "unknown" and company):
            res.system += cls == "system"
            seen.add(mid)
            continue
        card = notify_card(item, cls, gmail_url(cfg.gmail_address, mid), cfg.lang)
        record = _record(card_id("notify", mid), "notify", "info", item, cls, None, today_s)
        if posting:
            try:
                tg_id = transport.send(chat_id, f"{prefix}{card.text}", card.buttons)
            except Exception as e:  # noqa: BLE001 — збій Telegram лишає лист до наступної години
                res.skipped.append({"message_id": mid, "reason": f"send: {e}"})
                continue
            cards.append({**record, "tg_message_id": tg_id})
        else:
            dry_cards.append({**record, "text": card.text, "at": _now_text()})
        res.carded.append(record["id"])
        seen.add(mid)
    (state_dir / "last_watch").write_text(_now_text(), encoding="utf-8")
    return res


def _followups(cfg: WatchConfig, res: WatchResult, tracker: Path, state_dir: Path, sender: Sender,
               profile: dict, today: date, post) -> None:
    rows = parse_tracker_rows(tracker.read_text(encoding="utf-8"), today.year)
    due, skipped = find_due_by_age(rows, today, cfg.followup_days)
    res.skipped.extend(skipped)
    if not due:
        return
    if cfg.dry_run or is_paused(state_dir):
        reason = "dry-run" if cfg.dry_run else "paused"
        res.skipped.extend({"row_id": d.row_id, "company": d.company, "reason": f"follow-up {reason}"} for d in due)
        return
    notes_by_row = {r.row_id: r.notes for r in rows}
    full_name = f"{profile.get('first_name', '')} {profile.get('last_name', '')}".strip()
    phone = str(profile.get("phone") or "")
    today_s = today.isoformat()
    if len(due) > MAX_FOLLOWUPS_PER_RUN:
        # той самий запобіжник, що й у ранковому рані: решта піде наступного проходу
        res.skipped.extend({"row_id": d.row_id, "company": d.company, "reason": "follow-up guard"}
                           for d in due[MAX_FOLLOWUPS_PER_RUN:])
        due = due[:MAX_FOLLOWUPS_PER_RUN]
    for d in due:
        # Msg-ID у рядку це наш власний лист: адресата треба назвати явно, інакше
        # gmail_rest відповідає відправнику того листа, тобто нам самим
        m = _EMPLOYER_MAIL_RE.search(notes_by_row.get(d.row_id, ""))
        if not m:
            res.skipped.append({"row_id": d.row_id, "company": d.company, "reason": "no employer address"})
            continue
        to = m.group(0)
        lang = _followup_lang(to, cfg.agencies_de)
        try:
            sent_id = sender.reply(d.account, d.msg_id, followup_body(d.role, lang, full_name, phone), to=to)
        except Exception as e:  # noqa: BLE001 — збій пошти не має зупинити решту follow-ups
            res.skipped.append({"row_id": d.row_id, "company": d.company, "reason": f"follow-up send: {e}"})
            continue
        append_note_by_row(tracker, d.row_id, f"{today_s} follow-up Msg {sent_id}")
        cid = card_id("followup", sent_id)
        post(followup_card(d.company, d.role, sent_id, cfg.lang),
             {"id": cid, "kind": "followup", "status": "info", "account": d.account, "message_id": sent_id,
              "thread_id": None, "company": d.company, "class": "followup", "draft": None, "created": today_s})
        res.followups_sent.append(d.company)


def watch_summary(state_dir: Path, today: date) -> dict:
    """Числа для /status: відкриті картки, надіслано сьогодні, follow-ups, пауза, останній прохід."""
    state_dir = Path(state_dir)
    recs = JsonlStore(state_dir / "cards.jsonl").read()
    day = today.isoformat()
    last = state_dir / "last_watch"
    return {
        "open_cards": sum(1 for r in recs if r.get("status") == "open"),
        "sent_today": sum(1 for r in recs if r.get("sent_at") == day),
        "followups_today": sum(1 for r in recs if r.get("kind") == "followup" and r.get("created") == day),
        "paused": is_paused(state_dir),
        "last_watch": last.read_text(encoding="utf-8").strip() if last.exists() else "",
    }
