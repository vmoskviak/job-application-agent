"""Текстове сповіщення оператору в Telegram, поза картками з кнопками.

Один бот обслуговує кількох клієнтів: токен і chat_id беруться з
accounts.yaml → telegram клієнта, текст іде з іменем клієнта попереду, щоб у
спільному чаті було видно, про кого мова. Без токена або chat_id сповіщення
не йде, а той, хто кликав, іде далі своїм каналом (email).
"""
from __future__ import annotations

import html
import os
import re
from collections.abc import Mapping

from bot.telegram.api import HttpTransport, Transport

RUN_NAMES = {"uk": {"morning": "ранковий ран", "evening": "вечірній ран", "inbox": "inbox watch"},
             "en": {"morning": "morning run", "evening": "evening run", "inbox": "inbox watch"}}


def configured(c) -> bool:
    """Чи є у клієнта токен і chat_id: лише тоді сповіщення й алерти йдуть у Telegram."""
    tg = c.telegram
    return bool(os.environ.get(tg["token_env"])) and tg["chat_id"] is not None


def transport_for(c, env: Mapping[str, str] | None = None) -> Transport | None:
    tg = c.telegram
    token = (env if env is not None else os.environ).get(tg["token_env"])
    return HttpTransport(token) if token and tg["chat_id"] else None


def with_name(c, text: str) -> str:
    """«<b>Erika</b> · текст»: спільний чат кількох клієнтів."""
    return f"<b>{html.escape(str(c.profile.get('first_name') or c.slug))}</b> · {text}"


def notify(c, text: str, transport: Transport | None = None, buttons=None) -> bool:
    """Надсилає HTML-текст оператору; False, якщо Telegram у клієнта не налаштовано."""
    t = transport if transport is not None else transport_for(c)
    if t is None:
        return False
    t.send(c.telegram["chat_id"], with_name(c, text), buttons or [])
    return True


def run_name(run: str, lang: str) -> str:
    return RUN_NAMES.get(lang, RUN_NAMES["en"]).get(run, run)


def alert_text(run: str, message: str, log_tail: str, lang: str) -> str:
    """Алерт обгортки: причина й хвіст лога у <pre>, щоб назви фірм і Msg-ID не губились
    (у зведеному листі хвіст викидався, і оператор бачив лише загальну фразу)."""
    text = f"⚠️ {html.escape(run_name(run, lang))}: {html.escape(message)}"
    tail = (log_tail or "").strip()
    if tail:
        text += f"\n<pre>{html.escape(tail[-1200:])}</pre>"
    return text


# Статуси, про які оператор має дізнатись одразу після рану: співбесіда, оферта,
# запит роботодавця (документи, строки, згода). Решту (confirmed, rejected) він
# прочитає в трекері, коли захоче.
_ACTIONABLE = ("interview", "offer", "action_needed")
_TITLES = {"uk": "нові співбесіди й запити", "en": "new interviews and requests"}


def _status_word(status: str) -> str:
    """Перше слово статусу без емодзі й жирного: «🟢 **interview_1 28.10**» → interview_1."""
    return re.sub(r"^\W+", "", status or "").split()[0].lower() if (status or "").strip() else ""


def actionable(rows) -> dict[tuple[str, str], object]:
    """Рядки трекера (operator_report.Row) зі статусом співбесіди/оферти/запиту за ключем
    (компанія, посада)."""
    return {(r.company, r.title): r for r in rows if _status_word(r.status).startswith(_ACTIONABLE)}


def run_changes_text(before_rows, after_rows, run: str, lang: str) -> str | None:
    """Текст сповіщення про рядки, що стали actionable за цей ран; None, якщо таких нема.
    Нотатки рядка йдуть уривком: там дата, час і контакт із листа роботодавця."""
    was = actionable(before_rows)
    new = [r for key, r in actionable(after_rows).items()
           if key not in was or _status_word(was[key].status) != _status_word(r.status)]
    if not new:
        return None
    lines = [f"🎯 {html.escape(run_name(run, lang))}: {_TITLES.get(lang, _TITLES['en'])}"]
    for r in new:
        note = " ".join(r.status.split())[:300]
        lines.append(f"• <b>{html.escape(r.company)}</b> · {html.escape(r.title)}\n{html.escape(note)}")
    return "\n".join(lines)
