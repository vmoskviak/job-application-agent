"""Прохід браузерного воркера по черзі лідів: усі канали подачі (етапи 2c і 2d).

Детерміновано, без LLM на рівні проходу (модель лише всередині драйвера форми
для вільного тексту). Канали: form (ATS роботодавця), linkedin_offsite,
linkedin_easy і platform_login. Порядок у проході: спершу linkedin_offsite
(дешева резолюція), потім form, linkedin_easy, platform_login, щоб конверсії
відбулися до того, як витрачено квоту платформ; усередині каналу порядок файла.

linkedin_offsite: under the machine lock the LinkedIn page gives the external link;
the record becomes form with the ATS from the host (the package's lead.json too), and
the same pass submits it as form if the form limit allows. A page with the Easy Apply
button instead of an external link (the enqueue resolver could not read the guest page
and guessed offsite) turns the record into linkedin_easy, and the same pass applies with
Easy Apply within the linkedin switch and limits. The resolution counts against no limit
and submits nothing, so a shadow rehearsal (global dry_run or linkedin shadow) resolves
too: it rewrites the queue record and the package's lead.json. A closed posting or a page
without a link: held. A browser failure during the resolution: failed in live mode, only
a line in queue_dry.jsonl in shadow mode.

Платформа запису (linkedin, gulp, freelancermap...) має вимикач з
bot.platforms.effective: off пропускає лід не чіпаючи (skipped_platform),
shadow заповнює без Submit. Платформа без ключа в targeting вимкнена. Ліміти:
form за каналом, платформа за полем platform (денний ліміт, не більше
platform_per_pass подач за прохід, пауза platform_gap_seconds після будь-якої
живої спроби на платформі, що дійшла до сторінки, але не до запасу перед
дедлайном; /pause під час паузи зупиняє прохід). Запис без ключа platform
(поставлений до 2d) отримує його з platform_of перед подачею, щоб подача
рахувалась у ліміт платформи.
login_required і restricted не валять лід: він лишається queued, платформа
вимикається до кінця дня в platforms.json, одна картка на платформу за прохід,
прохід іде далі з іншими платформами. quota: лід held, платформа вимкнена до
першого числа наступного місяця.

Для кожної подачі: запис стає sending, під локом машини драйвер заповнює й
подає, далі sent (рядок у трекері, квитанція), held (вакансія закрита), failed
(картка) або handoff.
Hand-off does not block the pass: the card (buttons "Подано" / "Пропустити") goes
out right away, the tab stays open on the server and the pass moves on to the
remaining leads. Between leads every open hand-off is polled once (card answer,
closed tab, confirmation on its page); after the queue the pass waits for all of
them together, without the machine lock, until handoff_until, and the ones still
open then expire. A /pause mid-pass starts no new lead but still waits for the
hand-offs already open, so their tabs and records end in a defined state. Після
handoff_until живих подач нема: hand-off уже ніхто не дочекається. Hand-off,
що пережив свій день або дедлайн, на старті наступного проходу закривається за
відповіддю на картці (подано, пропущено) або стає failed. Завислий запис у
sending будь-якого каналу теж розбирає воркер, не диспетчер пошти. Пауза
(/pause) зупиняє все. Тіньовий режим (глобальний або shadow платформи)
заповнює без Submit і пише результат у queue_dry.jsonl, не більше ліміту за
прохід і не двічі за день той самий лід. Жодного конфігу клієнта на імпорті.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import logging
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Protocol

from bot.apply.ats_answers import AnswerContext
from bot.apply.models import ApplyResult
from bot.ats import ats_from_url
from bot.dispatch_leads import _minutes, _package_for
from bot.generators.inbox import append_row, mark_ready_row_submitted
from bot.llm import LockBusy, machine_lock
from bot.packages import Package
from bot.platforms import (
    Switch,
    declared,
    effective,
    end_of_day,
    first_of_next_month,
    limit_for,
    platform_of,
    set_off,
)
from bot.sendqueue import DRY_FILE, due_any, mark, sent_today, sent_today_platform
from bot.state import JsonlStore, is_paused
from bot.telegram.api import CALLBACK_DATA_LIMIT, Transport
from bot.telegram.cards import (
    handoff_card,
    lead_failed_card,
    lead_sent_card,
    platform_card,
)

log = logging.getLogger(__name__)

ERROR_CUT = 500
FORM = "form"
OFFSITE = "linkedin_offsite"
EASY = "linkedin_easy"
CHANNELS = (FORM, OFFSITE, EASY, "platform_login")
# Порядок каналів у проході: дешеві конверсії (резолюція offsite, форми) до квоти платформ.
PASS_ORDER = (OFFSITE, FORM, EASY, "platform_login")
# Результати, після яких платформа вимикається до кінця дня, а лід лишається queued.
ACCOUNT_STOP = ("login_required", "restricted")
OFFSITE_NOTE = "linkedin offsite, external link"
LOOKUP_NOTE = "linkedin offsite without apply link, employer page by lookup"
EASY_NOTE = "linkedin easy apply, logged-in page"
# Форма в sending довше за це: процес упав посеред заповнення чи Submit. Має
# перевищувати lock_wait (300 с) плюс найдовшу подачу, тому 45 хв.
STALE_SENDING_MINUTES = 45
_FMT = "%Y-%m-%d %H:%M"
# callback_data «done:<cid>» не довша за 64 байти, тож id картки обмежений.
_CID_MAX = CALLBACK_DATA_LIMIT - len("done:")


class Driver(Protocol):
    """Браузер воркера: сторінка на лід, подача за каналом, резолюція offsite, перевірка підтвердження."""

    def open(self, url: str) -> object: ...
    def apply(self, page, url: str, ctx: AnswerContext, dry_run: bool, channel: str) -> ApplyResult: ...
    # state restricted | login_required | closed | open | easy_apply and the external link (None if there is none)
    def resolve_offsite(self, page, url: str) -> tuple[str, str | None]: ...
    def probe_submitted(self, page) -> str: ...   # фраза підтвердження або ""
    def is_closed(self, page) -> bool: ...        # оператор закрив вкладку hand-off
    def close(self, page) -> None: ...


@dataclass
class ApplyConfig:
    limits: dict[str, int]
    lang: str
    chat_id: int | None
    dry_run: bool
    vnc_hint: str
    handoff_until: str = "19:55"
    poll_seconds: int = 20
    lock_path: Path | None = None
    lock_wait: int = 300
    source_label: str = "contract"
    followup_days: int = 7
    # Нова подача (жива чи тіньова) не стартує ближче за стільки хвилин до handoff_until:
    # заповнення з LLM триває хвилини, hand-off о 19:54 протух би після першого ж опитування,
    # а тіньовий прохід заважав би вечірньому ранку, що відкриває той самий профіль.
    deadline_margin_minutes: int = 10
    targeting: dict = field(default_factory=dict)   # секція platforms для bot.platforms.effective
    platform_per_pass: dict[str, int] = field(default_factory=lambda: {"linkedin": 2})
    platform_gap_seconds: int = 180
    platforms_path: Path | None = None   # var/state/platforms.json; None: лише декларація з targeting
    market: str = "contract"             # de_employment: рядок «готово» в трекері стає подачею, apply_result.json
    # Спільний бот оператора (telegram.mode: notify) не приймає кнопок цього клієнта:
    # картка hand-off без кнопок, завершення за сторінкою (підтвердження чи закрита вкладка).
    handoff_buttons: bool = True
    # The employer's own page for a job a board hides (bot.apply.employer_lookup.find_posting):
    # (company, title, city) -> Posting | None. None: no lookup, the lead is held as before.
    lookup: Callable[[str, str, str], object] | None = None


@dataclass
class ApplyPassResult:
    sent: list[str] = field(default_factory=list)       # подано (драйвером або через hand-off)
    handoff: list[str] = field(default_factory=list)    # ліди, для яких відкривали hand-off
    failed: list[str] = field(default_factory=list)
    skipped_limit: int = 0
    paused: bool = False
    lock_busy: bool = False
    after_deadline: bool = False   # прохід після handoff_until (або дійшов до запасу перед ним): решта queued
    error: str = ""                # збій інфраструктури (лок машини) для алерту оператору
    resolved: list[str] = field(default_factory=list)   # linkedin_offsite, що стали form
    skipped_platform: int = 0      # платформа off (декларація, тимчасовий запис або вимкнена цим проходом)
    platform_off: dict[str, str] = field(default_factory=dict)   # платформа → причина, яку поставив цей прохід


def handoff_deadline(now: datetime, until: str) -> datetime:
    """Сьогоднішній момент HH:MM, до якого чекаємо оператора."""
    hh, mm = (int(x) for x in until.split(":", 1))
    return now.replace(hour=hh, minute=mm, second=0, microsecond=0)


def handoff_cid(lead_id: str) -> str:
    """Id hand-off картки; задовгий lead id ріжеться і отримує хеш, щоб кнопка влізла в callback_data."""
    cid = f"handoff-{lead_id}"
    if len(cid.encode("utf-8")) <= _CID_MAX:
        return cid
    digest = hashlib.sha1(lead_id.encode("utf-8")).hexdigest()[:10]
    head = cid.encode("utf-8")[:_CID_MAX - len(digest) - 1].decode("utf-8", "ignore")
    return f"{head}-{digest}"


def _post(transport: Transport | None, chat_id: int | None, text: str, buttons=None) -> None:
    if transport is not None and chat_id:
        transport.send(chat_id, text, buttons or [])


def _label(rec: dict) -> str:
    """Платформа запису для sent_id і карток; form, коли платформи нема."""
    return rec.get("platform") or FORM


def _platform_card_text(platform: str, status: str, cfg: ApplyConfig) -> str:
    """Текст картки про вимкнену платформу."""
    return platform_card(platform, status, cfg.vnc_hint, cfg.lang).text


def _switch_off(cfg: ApplyConfig, switch: dict[str, Switch], platform: str, reason: str, until: datetime,
                at: datetime, res: ApplyPassResult, transport) -> None:
    """Платформа off у platforms.json і в пам'яті проходу; картка одна на платформу за прохід."""
    if cfg.platforms_path is not None:
        set_off(cfg.platforms_path, platform, reason, until, at)
    switch[platform] = "off"
    if platform in res.platform_off:
        return
    res.platform_off[platform] = reason
    try:
        _post(transport, cfg.chat_id, _platform_card_text(platform, reason, cfg))
    except Exception:  # платформа вже вимкнена; картка не має валити прохід
        log.warning("картку про платформу %s не вдалося надіслати", platform, exc_info=True)


def _enter_lock(held: contextlib.ExitStack, lock, res: ApplyPassResult) -> bool:
    """Лок машини в held; False (і поля res для CLI), коли його не дали або він зламаний."""
    try:
        held.enter_context(lock())
    except LockBusy:
        res.lock_busy = True   # запис не чіпали: sending ставиться вже під локом
        return False
    except Exception as e:  # noqa: BLE001 (лок машини зламаний: це збій машини, не ліда)
        # запис лишається queued (sending ставиться лише під локом), алерт робить CLI з res.error
        res.lock_busy = True
        res.error = f"lock: {e}"
        return False
    return True


class _Gate:
    """Вимикачі й ліміти одного проходу: switch з effective (прохід сам вимикає платформи в ньому),
    подачі цього проходу за ключем (платформа або form) і час останньої живої подачі на платформі."""

    def __init__(self, cfg: ApplyConfig, store: JsonlStore, today: str, switch: dict[str, Switch]) -> None:
        self.cfg, self.store, self.today, self.switch = cfg, store, today, switch
        self._before: dict[str, int] = {}
        self.done: dict[str, int] = {}
        self.last: dict[str, datetime] = {}

    def _sent_before(self, key: str) -> int:
        if key not in self._before:
            self._before[key] = (sent_today(self.store, self.today, FORM) if key == FORM
                                 else sent_today_platform(self.store, self.today, key))
        return self._before[key]

    def _limit(self, key: str) -> int:
        return int(self.cfg.limits.get(FORM, 0)) if key == FORM else limit_for(self.cfg.limits, key)

    def allow(self, channel: str, platform: str | None) -> str | None:
        """Причина пропуску (off, limit, per_pass) або None. Резолюцію offsite ліміти не обмежують."""
        if channel != FORM and (platform is None or self.switch.get(platform, "off") == "off"):
            return "off"
        if channel == OFFSITE:
            return None
        key = platform or FORM
        if self._sent_before(key) + self.done.get(key, 0) >= self._limit(key):
            return "limit"
        cap = self.cfg.platform_per_pass.get(platform) if platform else None
        if cap is not None and self.done.get(key, 0) >= cap:
            return "per_pass"
        return None

    def dry(self, channel: str, platform: str | None) -> bool:
        """Тіньова подача: глобальний dry_run або shadow платформи; form лише глобальний."""
        return self.cfg.dry_run or (channel != FORM and self.switch.get(platform or "") == "shadow")

    def gap_left(self, platform: str | None, at: datetime) -> float:
        """Секунди, яких бракує до platform_gap_seconds від попередньої живої спроби на платформі."""
        last = self.last.get(platform or "")
        if platform is None or last is None:
            return 0.0
        return max(0.0, self.cfg.platform_gap_seconds - (at - last).total_seconds())

    def note(self, channel: str, platform: str | None) -> None:
        """Подача (жива чи тіньова) рахується в ліміт дня й проходу; невдалі спроби ні."""
        if channel == OFFSITE:
            return
        key = platform or FORM
        self.done[key] = self.done.get(key, 0) + 1

    def attempted(self, platform: str | None, at: datetime) -> None:
        """Жива спроба на платформі дійшла до сторінки: від цього моменту йде пауза платформи."""
        if platform:
            self.last[platform] = at


def _fail(store: JsonlStore, rec: dict, error: str, res: ApplyPassResult, transport, cfg: ApplyConfig,
          **fields) -> None:
    """failed і картка; attempts рахує sending, тут їх не чіпаємо (крім явно переданих)."""
    mark(store, rec["id"], status="failed", error=error[:ERROR_CUT], **fields)
    res.failed.append(rec["id"])
    _post(transport, cfg.chat_id, lead_failed_card(rec.get("company") or "", rec.get("title") or "",
                                                   _label(rec), error, cfg.lang).text)


def _lead_of(rec: dict, pkg: Package | None) -> tuple[dict, str | None]:
    """Лід і ATS з пакета; без пакета (видалили після hand-off) лише те, що є в записі черги."""
    if pkg is not None:
        return pkg.lead, pkg.ats
    return {"company": rec.get("company"), "title": rec.get("title")}, None


def _submitted(store: JsonlStore, cards: JsonlStore, rec: dict, lead: dict, ats: str | None, url: str, sent_id: str,
               screenshots: list[str], now: str, tracker: Path, res: ApplyPassResult, transport,
               cfg: ApplyConfig) -> None:
    """sent ДО трекера (повторна подача гірша за відсутній рядок), рядок у трекері, квитанція.
    Джерело в трекері: платформа запису, інакше ATS; URL у нотатках (presend шукає за ним дублі платформ)."""
    company, title = lead.get("company") or rec.get("company") or "", lead.get("title") or rec.get("title") or ""
    label = _label(rec)
    today = now[:10]
    mark(store, rec["id"], status="sent", sent_id=sent_id, sent_at=now, screenshots=list(screenshots))
    res.sent.append(rec["id"])
    _close_lead_cards(cards, rec["id"], "done", now)
    tracker_error = ""
    try:
        until = (date.fromisoformat(today) + timedelta(days=cfg.followup_days)).isoformat()
        notes = f"{url}, {screenshots[-1] if screenshots else 'no screenshot'}"
        done = cfg.market == "de_employment" and mark_ready_row_submitted(
            tracker, company=company, position=title, status="portal_submitted (портал)", date=today, notes=notes)
        if not done:
            append_row(tracker, date=today, company=company, position=title,
                       source=f"{cfg.source_label}: {rec.get('platform') or ats or 'form'}",
                       salary=lead.get("stated_rate") or "",
                       status="portal_submitted (портал)", next_action=f"wait reply until {until}", notes=notes)
    except Exception as e:  # noqa: BLE001 — форма вже подана; трекер поправить людина
        tracker_error = str(e)[:ERROR_CUT]
        mark(store, rec["id"], tracker_error=tracker_error)
    if cfg.market == "de_employment" and rec.get("package_dir"):
        # як у bot.apply.runner: ранковий агент і --cleanup читають apply_result.json пакета
        try:
            ApplyResult("submitted", rec.get("platform") or ats or "ats", url, list(screenshots),
                        confirmation_text=sent_id).save(Path(rec["package_dir"]))
        except OSError:
            log.warning("apply_result.json для %s не записано", rec.get("id"), exc_info=True)
    text = lead_sent_card(company, title, label, sent_id.removeprefix(f"{label}:"), cfg.lang).text
    if tracker_error:
        text += "\n" + lead_failed_card(company, title, "tracker", tracker_error, cfg.lang).text
    try:
        _post(transport, cfg.chat_id, text)
    except Exception:  # заявка вже sent і в трекері: квитанція не має валити прохід
        log.warning("квитанцію для %s не вдалося надіслати", rec.get("id"), exc_info=True)


def _open_handoff_card(cards: JsonlStore, record: dict) -> None:
    """One record per card id. The id comes from the lead id, so a lead that reaches hand-off again
    reuses its card; a second record would sit behind the first, and cards.get() would read the old
    answer (02.11.2026: an earlier «skipped» resolved the new card at once, the new one stayed open)."""
    if cards.get(record["id"]) is None:
        cards.append(record)
    else:
        cards.update(record["id"], **record, closed_at=None)


def _close_lead_cards(cards: JsonlStore, lead_id: str, status: str, at: str) -> None:
    """Closes every open hand-off card of a lead that is decided (sent: done, held: skipped)."""
    for card in cards.read():
        if card.get("kind") == "handoff" and card.get("lead_id") == lead_id and card.get("status") == "open":
            cards.update(card["id"], status=status, closed_at=at)


def _close_decided_cards(store: JsonlStore, cards: JsonlStore, now: str) -> None:
    """Open hand-off cards of leads already sent or held, e.g. marked by hand after a manual
    submit: they would hang in /status and the hand-off list for days."""
    closing = {"sent": "done", "held": "skipped"}
    status_of = {rec.get("id"): rec.get("status") for rec in store.read()}
    for card in cards.read():
        if card.get("kind") != "handoff" or card.get("status") != "open":
            continue
        new = closing.get(status_of.get(card.get("lead_id")) or "")
        if new:
            cards.update(card["id"], status=new, closed_at=now)


def _expire_handoffs(store: JsonlStore, cards: JsonlStore, now: datetime, tracker: Path, res: ApplyPassResult,
                     transport, cfg: ApplyConfig) -> None:
    """Hand-off з минулого дня або після сьогоднішнього дедлайну, на який ніхто вже не чекає:
    відповідь на картці (воркер упав, а диспетчер без черги) виграє, інакше failed."""
    deadline = handoff_deadline(now, cfg.handoff_until)
    today = now.date().isoformat()
    for rec in store.read():
        if rec.get("status") != "handoff":
            continue
        at = str(rec.get("handoff_at") or "")
        if at[:10] == today and now < deadline:
            continue
        cid = rec.get("handoff_card") or ""
        card = cards.get(cid) if cid else None
        answer = (card or {}).get("status")
        if answer == "done":
            lead, ats = _lead_of(rec, _package_for(rec))
            _submitted(store, cards, rec, lead, ats, rec.get("apply_url") or card.get("url") or "", f"{_label(rec)}:operator",
                       rec.get("screenshots") or [], str(card.get("closed_at") or now.strftime(_FMT)),
                       tracker, res, transport, cfg)
            continue
        if answer == "skipped":
            mark(store, rec["id"], status="held", reasons=["operator_skipped"])
            continue
        if card is not None:
            cards.update(cid, status="expired")
        _fail(store, rec, f"handoff_expired: no answer by {at[:10] or '?'} {cfg.handoff_until}", res, transport, cfg)


def _expire_sending(store: JsonlStore, now: str, res: ApplyPassResult, transport, cfg: ApplyConfig) -> None:
    """Запис будь-якого каналу воркера в sending довше за STALE_SENDING_MINUTES: процес упав,
    повторно не подаємо."""
    for rec in store.read():
        if rec.get("status") != "sending" or rec.get("channel") not in CHANNELS:
            continue
        if _minutes(str(rec.get("sending_at") or ""), now) < STALE_SENDING_MINUTES:
            continue
        _fail(store, rec, f"apply status unknown: process died during the form fill at {rec.get('sending_at')}; "
                          "check the portal or the confirmation mail before re-queueing", res, transport, cfg)


@dataclass
class _Handoff:
    """A live lead waiting for the operator: its card is posted and its page stays open."""
    rec: dict
    lead: dict
    ats: str | None
    url: str
    cid: str
    shots: list[str]
    page: object


def _post_handoff(store: JsonlStore, cards: JsonlStore, h: _Handoff, reason: str, driver: Driver,
                  res: ApplyPassResult, transport, cfg: ApplyConfig) -> bool:
    """Sends the hand-off card. Without a card nobody knows about the tab: the lead fails
    and its page is closed (False)."""
    card = handoff_card(h.lead.get("company") or "", h.lead.get("title") or "", reason, h.url, cfg.vnc_hint,
                        h.cid, cfg.lang)
    try:
        _post(transport, cfg.chat_id, card.text, card.buttons if cfg.handoff_buttons else [])
    except Exception as e:  # noqa: BLE001 — no card means nobody to wait for
        cards.update(h.cid, status="not_posted")
        mark(store, h.rec["id"], status="failed", error=f"handoff card not posted: {e}"[:ERROR_CUT])
        res.failed.append(h.rec["id"])
        driver.close(h.page)
        return False
    return True


def _check_handoff(store: JsonlStore, cards: JsonlStore, h: _Handoff, driver: Driver, tracker: Path,
                   res: ApplyPassResult, transport, cfg: ApplyConfig, now: Callable[[], datetime]) -> bool:
    """One look at an open hand-off: True once it is resolved (card answer, closed tab or a
    confirmation on its page). Does not close the page and does not sleep."""
    rid = h.rec["id"]
    status = (cards.get(h.cid) or {}).get("status")
    if status == "done":
        if (store.get(rid) or {}).get("status") == "sent":
            res.sent.append(rid)   # the dispatcher already closed the lead and wrote the tracker row
        else:
            _submitted(store, cards, h.rec, h.lead, h.ats, h.url, f"{_label(h.rec)}:operator", h.shots,
                       now().strftime(_FMT), tracker, res, transport, cfg)
        return True
    if status == "skipped":
        mark(store, rid, status="held", reasons=["operator_skipped"])
        return True
    if driver.is_closed(h.page):
        # without buttons a closed tab is the operator's "skip"
        cards.update(h.cid, status="skipped", closed_at=now().strftime(_FMT))
        mark(store, rid, status="held", reasons=["operator_skipped"])
        return True
    phrase = driver.probe_submitted(h.page)
    if phrase:
        cards.update(h.cid, status="done", closed_at=now().strftime(_FMT))
        _submitted(store, cards, h.rec, h.lead, h.ats, h.url, f"{_label(h.rec)}:{phrase}", h.shots,
                   now().strftime(_FMT), tracker, res, transport, cfg)
        return True
    return False


def _poll_handoffs(pending: list[_Handoff], store: JsonlStore, cards: JsonlStore, driver: Driver, tracker: Path,
                   res: ApplyPassResult, transport, cfg: ApplyConfig, now: Callable[[], datetime]) -> None:
    """Checks every open hand-off once; resolved ones leave `pending` with their page closed."""
    for h in list(pending):
        if _check_handoff(store, cards, h, driver, tracker, res, transport, cfg, now):
            pending.remove(h)
            driver.close(h.page)


def _wait_handoffs(pending: list[_Handoff], store: JsonlStore, cards: JsonlStore, driver: Driver, tracker: Path,
                   res: ApplyPassResult, transport, cfg: ApplyConfig, now: Callable[[], datetime],
                   sleep: Callable[[float], None]) -> None:
    """Waits for all open hand-offs together until handoff_until, without the machine lock;
    the ones still open then expire (failed, card expired)."""
    if not pending:
        return
    deadline = handoff_deadline(now(), cfg.handoff_until)
    while pending:
        sleep(cfg.poll_seconds)
        _poll_handoffs(pending, store, cards, driver, tracker, res, transport, cfg, now)
        if pending and now() >= deadline:
            for h in list(pending):
                pending.remove(h)
                try:
                    cards.update(h.cid, status="expired")
                    _fail(store, h.rec, f"handoff_expired: no answer by {cfg.handoff_until}", res, transport, cfg)
                finally:
                    driver.close(h.page)


def _dry_seen(dry: JsonlStore, today: str) -> set[str]:
    """Ліди, які тіньовий режим уже заповнював сьогодні: повторний тік не витрачає форму й LLM."""
    return {str(r.get("id")) for r in dry.read() if str(r.get("at") or "").startswith(today)}


def _resolve_offsite(store: JsonlStore, rec: dict, pkg: Package, driver: Driver, lock, gate: _Gate,
                     res: ApplyPassResult, transport, cfg: ApplyConfig, now: Callable[[], datetime],
                     dry: JsonlStore | None) -> dict | str | None:
    """The external link of a linkedin_offsite lead under the machine lock; the page closes on every exit.
    Returns the updated record (now form, or linkedin_easy when the page under the login has Easy
    Apply) to submit in the same pass, "stop" without the lock, or None when the lead is settled
    (held, failed, or the platform switched off while the lead stays queued). No sending here.
    dry (shadow pass): a browser failure becomes a line in queue_dry, the queue record is untouched."""
    url = rec.get("apply_url") or pkg.apply_url
    page = None
    error = ""
    state, ext = "", None
    with contextlib.ExitStack() as held:
        if not _enter_lock(held, lock, res):
            return "stop"
        try:
            page = driver.open(url)
            state, ext = driver.resolve_offsite(page, url)
        except Exception as e:  # noqa: BLE001 (браузер упав: failed і картка, як збій драйвера форми)
            error = f"resolve: {type(e).__name__}: {e}"
        finally:
            if page is not None:
                driver.close(page)
    if error:
        if dry is not None:
            dry.append({**rec, "error": error[:ERROR_CUT], "at": now().strftime(_FMT)})
        else:
            _fail(store, rec, error, res, transport, cfg)
        return None
    if state in ACCOUNT_STOP:
        _switch_off(cfg, gate.switch, "linkedin", state, end_of_day(now()), now(), res, transport)
        return None
    if state == "closed":
        mark(store, rec["id"], status="held", reasons=["not_live"])
        return None
    if state == "easy_apply":
        mark(store, rec["id"], channel=EASY, platform="linkedin", resolved_from=url)
        _update_lead(pkg, channel=EASY, resolver_note=EASY_NOTE)
        res.resolved.append(rec["id"])
        return store.get(rec["id"])
    note = OFFSITE_NOTE
    if ext is None and cfg.lookup is not None:
        lead = pkg.lead or {}
        posting = cfg.lookup(str(lead.get("company") or rec.get("company") or ""),
                             str(lead.get("title") or rec.get("title") or ""), str(lead.get("location") or ""))
        if posting is not None:
            ext, note = posting.url, LOOKUP_NOTE
    if ext is None:
        mark(store, rec["id"], status="held", reasons=["no_apply_link"])
        return None
    ats = ats_from_url(ext)
    mark(store, rec["id"], channel=FORM, apply_url=ext, ats=ats, platform=None, resolved_from=url)
    _update_lead(pkg, channel=FORM, apply_url=ext, ats=ats, resolver_note=note)
    res.resolved.append(rec["id"])
    return store.get(rec["id"])


def _update_lead(pkg: Package, **fields) -> None:
    """The package's lead.json follows the queue record after a resolution."""
    path = Path(pkg.dir) / "lead.json"
    lead = json.loads(path.read_text(encoding="utf-8"))
    lead.update(fields)
    path.write_text(json.dumps(lead, ensure_ascii=False, indent=1), encoding="utf-8")


def run_apply(cfg: ApplyConfig, store: JsonlStore, cards: JsonlStore, tracker: Path, state_dir: Path,
              driver: Driver, ctx_for: Callable[[dict, Package], AnswerContext], transport: Transport | None,
              now: Callable[[], datetime], sleep: Callable[[float], None]) -> ApplyPassResult:
    """One pass over the queue; hand-offs opened by it are awaited at the end, whatever way
    the queue loop stopped (pause, deadline margin, lock). Their pages close on every exit."""
    res = ApplyPassResult()
    pending: list[_Handoff] = []
    try:
        _run_queue(cfg, store, cards, tracker, state_dir, driver, ctx_for, transport, now, sleep, res, pending)
        _wait_handoffs(pending, store, cards, driver, tracker, res, transport, cfg, now, sleep)
    finally:
        for h in pending:   # only on an exception: the records stay handoff, the next pass expires them
            driver.close(h.page)
    return res


def _run_queue(cfg: ApplyConfig, store: JsonlStore, cards: JsonlStore, tracker: Path, state_dir: Path,
               driver: Driver, ctx_for: Callable[[dict, Package], AnswerContext], transport: Transport | None,
               now: Callable[[], datetime], sleep: Callable[[float], None], res: ApplyPassResult,
               pending: list[_Handoff]) -> ApplyPassResult:
    if is_paused(state_dir):
        res.paused = True
        return res
    started = now()
    today = started.date().isoformat()
    if started >= handoff_deadline(started, cfg.handoff_until):
        # hand-off ніхто вже не дочекається, а картка о 20:00 = гарантований failed;
        # тіньовий прохід теж стоїть: вечірній ранок о 20:30 відкриває той самий профіль
        res.after_deadline = True
        return res
    _close_decided_cards(store, cards, started.strftime(_FMT))
    _expire_handoffs(store, cards, started, tracker, res, transport, cfg)
    _expire_sending(store, started.strftime(_FMT), res, transport, cfg)
    switch = (effective(cfg.targeting, cfg.platforms_path, started) if cfg.platforms_path is not None
              else declared(cfg.targeting))
    gate = _Gate(cfg, store, today, switch)
    dry = JsonlStore(Path(state_dir) / DRY_FILE)
    dry_seen = _dry_seen(dry, today)
    lock = (lambda: machine_lock(cfg.lock_path, cfg.lock_wait)) if cfg.lock_path else contextlib.nullcontext
    last_start = handoff_deadline(started, cfg.handoff_until) - timedelta(minutes=cfg.deadline_margin_minutes)
    order = {ch: i for i, ch in enumerate(PASS_ORDER)}
    for rec in sorted(due_any(store, CHANNELS), key=lambda r: order[r["channel"]]):
        # an operator's quick answer on an earlier hand-off is recorded before the next lead
        _poll_handoffs(pending, store, cards, driver, tracker, res, transport, cfg, now)
        if is_paused(state_dir):   # /pause mid-pass: no new lead; open hand-offs are still awaited
            res.paused = True
            return res
        channel = rec["channel"]
        platform = None if channel == FORM else (rec.get("platform") or platform_of(channel, rec.get("apply_url") or ""))
        is_dry = gate.dry(channel, platform)
        if is_dry and rec["id"] in dry_seen:
            continue
        why = gate.allow(channel, platform)
        if why == "off":
            res.skipped_platform += 1   # запис не чіпаємо: платформу ввімкнуть або завтра спробуємо знову
            continue
        if why:
            res.skipped_limit += 1
            continue
        if now() >= last_start:
            # hand-off цієї подачі вже не встиг би до дедлайну, а тіньове заповнення з LLM
            # уперлося б у вечірній ранок, якому потрібен профіль: решта лишається на завтра
            res.after_deadline = True
            return res
        pkg = _package_for(rec)
        if pkg is None:
            if is_dry:
                dry.append({**rec, "error": f"package missing: {rec.get('package_dir')}", "at": now().strftime(_FMT)})
                gate.note(channel, platform)
                continue
            _fail(store, rec, f"package missing: {rec.get('package_dir')}", res, transport, cfg,
                  attempts=rec.get("attempts", 0) + 1)
            continue
        if channel == OFFSITE:
            step = _resolve_offsite(store, rec, pkg, driver, lock, gate, res, transport, cfg, now,
                                    dry if is_dry else None)
            if step == "stop":
                return res
            if step is None:
                continue
            # the same lead goes on as form (form limit, global dry_run, no platform) or as
            # linkedin_easy (the linkedin switch and limits)
            rec, channel = step, step["channel"]
            platform = None if channel == FORM else "linkedin"
            is_dry = gate.dry(channel, platform)
            why = gate.allow(channel, platform)
            if why == "off":
                res.skipped_platform += 1
                continue
            if why:
                res.skipped_limit += 1   # stays queued, already with the new channel
                continue
            if now() >= last_start:
                res.after_deadline = True
                return res
            pkg = _package_for(rec) or pkg
        if platform and not is_dry and rec.get("platform") != platform:
            # запис до 2d без platform: без ключа подача не рахувалась би в ліміт платформи
            mark(store, rec["id"], platform=platform)
            rec = {**rec, "platform": platform}
        url = rec.get("apply_url") or pkg.apply_url
        lead, ats = _lead_of(rec, pkg)
        try:
            ctx = ctx_for(pkg.lead, pkg)
        except ValueError as e:   # пакет без CV: подача без резюме не йде ніколи (як presend)
            if is_dry:
                dry.append({**rec, "error": str(e), "at": now().strftime(_FMT)})
                gate.note(channel, platform)
            else:
                mark(store, rec["id"], status="held", reasons=["attachment_none"])
            continue
        if not is_dry:
            left = gate.gap_left(platform, now())
            if left > 0:
                if now() + timedelta(seconds=left) >= last_start:
                    res.after_deadline = True   # пауза платформи закінчилась би вже в запасі перед дедлайном
                    return res
                sleep(left)
                if is_paused(state_dir):   # /pause, поки прохід чекав паузу платформи
                    res.paused = True
                    return res
        at = now().strftime(_FMT)
        page = None
        with contextlib.ExitStack() as held:
            if not _enter_lock(held, lock, res):
                return res
            if not is_dry:
                # sending під локом і ДО драйвера: падіння між Submit і mark не дасть другої подачі,
                # а вбивство під час очікування лока не робить лід failed
                mark(store, rec["id"], status="sending", sending_at=at, attempts=rec.get("attempts", 0) + 1)
            try:
                page = driver.open(url)
                result = driver.apply(page, url, ctx, is_dry, channel)
            except Exception as e:  # noqa: BLE001 — браузер упав: failed і картка, без повторів
                result = None
                error = f"driver: {type(e).__name__}: {e}"
        if not is_dry and (result is not None or page is not None):
            # будь-яка жива спроба на сторінці, не лише подача; виняток драйвера після open
            # теж: сторінку платформи вже відкрили, пауза потрібна так само
            gate.attempted(platform, now())
        if result is None:
            if page is not None:
                driver.close(page)
            if is_dry:
                dry.append({**rec, "would_submit": url, "error": error[:ERROR_CUT], "at": at})
                gate.note(channel, platform)
                continue
            _fail(store, rec, error, res, transport, cfg)
            continue
        if platform and result.status in ACCOUNT_STOP:
            # не збій ліда: сесія чи акаунт платформи; лід чекає, платформа off до кінця дня
            driver.close(page)
            if not is_dry:
                mark(store, rec["id"], status="queued", attempts=rec.get("attempts", 0), sending_at=None)
            _switch_off(cfg, switch, platform, result.status, end_of_day(now()), now(), res, transport)
            continue
        if platform and result.status == "quota":
            driver.close(page)
            if not is_dry:
                mark(store, rec["id"], status="held", reasons=["platform_quota"])
            _switch_off(cfg, switch, platform, "platform_quota", first_of_next_month(now()), now(), res, transport)
            continue
        if is_dry:
            driver.close(page)
            dry.append({**rec, "would_submit": url, "result": asdict(result), "at": at})
            gate.note(channel, platform)
            continue
        if result.status == "submitted":
            driver.close(page)
            gate.note(channel, platform)
            _submitted(store, cards, rec, lead, ats, url, f"{_label(rec)}:{result.confirmation_text}", result.screenshots,
                       now().strftime(_FMT), tracker, res, transport, cfg)
            continue
        if result.status == "closed":
            driver.close(page)
            mark(store, rec["id"], status="held", reasons=["not_live"])
            continue
        if result.status != "handoff":
            driver.close(page)
            error = result.reason if result.status == "failed" else f"{result.status}: {result.reason}"
            _fail(store, rec, error or result.status, res, transport, cfg)
            continue
        cid = handoff_cid(rec["id"])
        hat = now().strftime(_FMT)
        _open_handoff_card(cards, {"id": cid, "kind": "handoff", "status": "open", "lead_id": rec["id"],
                      "company": lead.get("company") or "", "title": lead.get("title") or "", "url": url,
                      "ats": ats, "platform": platform, "salary": lead.get("stated_rate") or "",
                      "reason": result.reason, "at": hat})
        mark(store, rec["id"], status="handoff", handoff_at=hat, handoff_card=cid, reason=result.reason,
             screenshots=list(result.screenshots))
        res.handoff.append(rec["id"])
        h = _Handoff(rec, lead, ats, url, cid, list(result.screenshots), page)
        if _post_handoff(store, cards, h, result.reason, driver, res, transport, cfg):
            pending.append(h)   # the tab stays open; the pass goes on and waits for it at the end
    return res
