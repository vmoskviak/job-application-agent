"""Сервіс бота: long polling, allow-list одного чату, команди, кнопки в чергу.

Бот сам нічого не вирішує: натиснута кнопка стає записом у var/state/queue.jsonl
і одразу йде в диспетчер (детермінований), відповідь диспетчера повертається
оператору як текст під кнопкою. Жодного LLM тут нема. Чужі чати ігноруються
мовчки; без chat_id у конфігу бот лише називає id на /start. /platform показує
й перемикає платформи подачі до кінця дня (var/state/platforms.json), декларацію
в targeting.yaml не чіпає: оголошена off чи shadow платформа такою й лишається.
"""
from __future__ import annotations

import html
import sys
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from bot.platforms import (
    STATE_FILE,
    TS_FORMAT,
    clear,
    declared,
    effective,
    end_of_day,
    set_off,
    set_shadow,
    status_lines,
)
from bot.platforms import read_state as read_platforms
from bot.sendqueue import QUEUE_FILE, limits_from, sent_today_platform
from bot.state import JsonlStore, is_paused, set_paused
from bot.telegram.api import TEXT_LIMIT, Event, Transport
from bot.telegram.cards import help_text, status_text

ACTIONS = ("send", "skip", "done", "pause", "resume")
ANSWER_LIMIT = 200   # answerCallbackQuery.text
ERROR_SLEEP = 30.0
PLATFORM_VERBS = ("on", "off", "shadow")
PLATFORM_HELP = "/platform NAME on|off|shadow"   # без <...>: parse_mode HTML відкидає невідомий тег

_TEXTS = {
    "uk": {"paused": "Пауза: відправка зупинена.", "resumed": "Відправка знову працює.",
           "limits": "Ліміти на день приходять з етапом 2b; follow-up через {days} днів після подачі.",
           "chat_id": "Твій chat id: {chat_id}. Впиши його в accounts.yaml → telegram.chat_id.",
           "bad_callback": "Невідома кнопка."},
    "en": {"paused": "Paused: nothing is sent.", "resumed": "Sending resumed.",
           "limits": "Daily limits arrive with stage 2b; follow-ups go out {days} days after an application.",
           "chat_id": "Your chat id is {chat_id}. Put it into accounts.yaml → telegram.chat_id.",
           "bad_callback": "Unknown button."},
}


@dataclass
class Action:
    at: str
    action: str
    card: str | None
    by: int


class BotService:
    def __init__(self, transport: Transport, state_dir: Path, chat_id: int | None, dispatcher,
                 status_fn: Callable[[], dict], lang: str = "en",
                 sleep: Callable[[float], None] = time.sleep, followup_days: int = 7,
                 targeting: dict | None = None) -> None:
        self.transport = transport
        self.state_dir = Path(state_dir)
        self.chat_id = chat_id
        self.dispatcher = dispatcher
        self.status_fn = status_fn
        self.lang = lang if lang in _TEXTS else "en"
        self.sleep = sleep
        self.followup_days = followup_days
        self.queue = JsonlStore(self.state_dir / QUEUE_FILE)
        self.targeting = targeting or {}
        self.platforms_path = self.state_dir / STATE_FILE
        self._offset_path = self.state_dir / "tg_offset"

    # ── події ────────────────────────────────────────────────────────────────
    def _t(self, key: str, **kw) -> str:
        return _TEXTS[self.lang][key].format(**kw)

    def handle(self, ev: Event) -> None:
        if self.chat_id is None:
            # Конфіг без chat_id: єдине, що бот робить, це називає id на /start
            if ev.kind == "message" and ev.text.strip().startswith("/start"):
                self.transport.send(ev.chat_id, self._t("chat_id", chat_id=ev.chat_id))
            return
        if ev.chat_id != self.chat_id:
            return
        if ev.kind == "message":
            self._command(ev)
        else:
            self._callback(ev)

    def _command(self, ev: Event) -> None:
        cmd = ev.text.strip().split()[0].lower() if ev.text.strip() else ""
        cmd = cmd.split("@", 1)[0]
        if cmd == "/start":
            self.transport.send(ev.chat_id, help_text(self.lang))
        elif cmd == "/status":
            self.transport.send(ev.chat_id, status_text(self.status_fn(), self.lang))
        elif cmd == "/pause":
            set_paused(self.state_dir, True)
            self.transport.send(ev.chat_id, self._t("paused"))
        elif cmd == "/resume":
            set_paused(self.state_dir, False)
            self.transport.send(ev.chat_id, self._t("resumed"))
        elif cmd == "/limits":
            self.transport.send(ev.chat_id, self._t("limits", days=self.followup_days))
        elif cmd == "/platform":
            self.transport.send(ev.chat_id, self._platform(ev.text.strip().split()[1:]))
        # інший текст: етап 2e зробить із нього джоб чернетки; поки що мовчимо

    # ── /platform ────────────────────────────────────────────────────────────
    def platform_lines(self, now: datetime | None = None) -> list[str]:
        """Рядок на оголошену платформу (bot.platforms.status_lines): стан і подано сьогодні / ліміт."""
        now = now or _local_now()
        today = now.date().isoformat()
        sent = {name: sent_today_platform(self.queue, today, name) for name in declared(self.targeting)}
        return status_lines(self.targeting, self.platforms_path, now, sent, limits_from(self.targeting))

    def _platform(self, args: list[str]) -> str:
        """Без аргументів: рядок на платформу; «NAME on|off|shadow»: тимчасовий запис до кінця дня
        і рядок цієї платформи після зміни. Довшого автоматичного off (квота до 1-го числа) off і shadow
        не вкорочують, його знімає лише on. Відповідь іде з parse_mode HTML: текст оператора екранується."""
        now = _local_now()
        if not args:
            return html.escape("\n".join(self.platform_lines(now))) or "no platforms in targeting.yaml"
        if len(args) != 2:
            return PLATFORM_HELP
        name, verb = args[0].lower(), args[1].lower()
        decl = declared(self.targeting)
        known = [*decl, *([] if "linkedin" in decl else ["linkedin"])]
        if name not in known:
            return f"unknown platform: {html.escape(name)}; known: {html.escape(', '.join(known))}"
        if verb not in PLATFORM_VERBS:
            return PLATFORM_HELP
        kept = self._longer_off(name, end_of_day(now)) if verb != "on" else None
        if verb == "on":
            clear(self.platforms_path, name)
        elif kept is None and verb == "off":
            set_off(self.platforms_path, name, "operator", end_of_day(now), now)
        elif kept is None:
            set_shadow(self.platforms_path, name, end_of_day(now), now)
        if name not in decl:   # linkedin без ключа в targeting: прохід її однаково не бере
            return f"{name}: off, not declared in targeting.yaml"
        line = next(ln for ln in self.platform_lines(now) if ln.startswith(f"{name}: "))
        if kept is None and effective(self.targeting, self.platforms_path, now)[name] != verb:
            # спека §5.2: тимчасовий запис не піднімає декларацію off чи shadow
            line += f" (declared {decl[name]} in targeting.yaml)"
        if kept is not None:
            line += f"; kept off until {kept['off_until']} ({kept.get('reason') or 'temporary'}), /platform {name} on lifts it"
        return html.escape(line)

    def _longer_off(self, name: str, until: datetime) -> dict | None:
        """Чинний запис off, що триває довше за until (квота платформи до 1-го числа), або None."""
        entry = read_platforms(self.platforms_path).get(name)
        if not isinstance(entry, dict):
            return None
        try:
            # позначки platforms.json: місцевий час без зони, як і until (bot.platforms)
            off_until = datetime.strptime(str(entry.get("off_until") or ""), TS_FORMAT)  # noqa: DTZ007
        except ValueError:
            return None
        return entry if off_until > until else None

    def _callback(self, ev: Event) -> None:
        name, _, card = ev.text.partition(":")
        if name not in ACTIONS:
            if ev.callback_id:
                self.transport.answer(ev.callback_id, self._t("bad_callback"))
            return
        action = Action(at=datetime.now(tz=timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M"), action=name,
                        card=card or None, by=ev.user_id)
        self.queue.append(asdict(action))
        receipt = self.dispatcher.apply(action)
        if ev.callback_id:
            # answerCallbackQuery приймає до 200 знаків; довгий ERR іде ще й повідомленням
            self.transport.answer(ev.callback_id, receipt[:ANSWER_LIMIT])
        if receipt.startswith("ERR"):
            self.transport.send(ev.chat_id, receipt[:TEXT_LIMIT])
        if receipt.startswith("OK") and ev.message_id is not None and name in ("send", "skip", "done"):
            self.transport.clear_buttons(ev.chat_id, ev.message_id)

    # ── цикл ─────────────────────────────────────────────────────────────────
    def _read_offset(self) -> int | None:
        try:
            return int(self._offset_path.read_text(encoding="utf-8").strip())
        except (FileNotFoundError, ValueError):
            return None

    def poll_once(self, timeout: int = 50) -> int:
        """Один getUpdates: обробити події, зсунути offset. Збій транспорту не
        рухає offset і дає паузу перед наступною спробою."""
        offset = self._read_offset()
        try:
            events = self.transport.get_updates(offset, timeout)
        except Exception as e:  # noqa: BLE001 — мережа, Telegram; сервіс має жити далі
            print(f"[telegram] getUpdates failed: {e}", file=sys.stderr)
            self.sleep(ERROR_SLEEP)
            return 0
        last = offset - 1 if offset is not None else None
        for ev in events:
            try:
                self.handle(ev)
            except Exception as e:  # noqa: BLE001 — одна зламана подія не має зупинити решту
                print(f"[telegram] event {ev.update_id} failed: {e}", file=sys.stderr)
            last = ev.update_id if last is None else max(last, ev.update_id)
        if events and last is not None:
            self.state_dir.mkdir(parents=True, exist_ok=True)
            self._offset_path.write_text(str(last + 1), encoding="utf-8")
        return len(events)

    def run(self) -> None:
        """До появи файлу state_dir/stop (тести, зупинка сервісу)."""
        stop = self.state_dir / "stop"
        while not stop.exists():
            self.poll_once()
        stop.unlink(missing_ok=True)


def _local_now() -> datetime:
    """Місцевий час без зони: bot.platforms порівнює його з наївними позначками platforms.json."""
    return datetime.now(tz=timezone.utc).astimezone().replace(tzinfo=None, second=0, microsecond=0)


__all__ = ["Action", "BotService", "is_paused"]
