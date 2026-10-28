"""Картки для оператора: текст у HTML Telegram і кнопки під ним.

Кожне значення з листа проходить html.escape; текст понад ліміт Telegram
обрізається. Мова картки — report_lang клієнта (uk або en), мова чернетки
окрема, її задає лист.
"""
from __future__ import annotations

import html
from dataclasses import dataclass, field

from bot.telegram.api import TEXT_LIMIT, Button

CUT_AT = 4000
DRAFT_LIMIT = 3000   # чернетка ріжеться до <pre>, щоб тег лишився закритим

_LABELS = {
    "uk": {"confirmed": "Підтвердження", "rejected": "Відмова", "interview": "Співбесіда",
           "info_request": "Запит", "outreach": "Звернення", "invitation": "Запрошення",
           "unknown": "Невідомо", "followup": "Follow-up"},
    "en": {"confirmed": "Confirmation", "rejected": "Rejection", "interview": "Interview",
           "info_request": "Request", "outreach": "Outreach", "invitation": "Invitation",
           "unknown": "Unknown", "followup": "Follow-up"},
}
_UI = {
    "uk": {"send": "Надіслати", "open": "Відкрити в Gmail", "skip": "Пропустити",
           "no_draft": "Чернетки нема (модель не відповіла), відповісти вручну.",
           "draft": "Чернетка відповіді:", "followup_sent": "Follow-up надіслано",
           "status": "Відкритих карток: {open_cards}\nНадіслано сьогодні: {sent_today}\n"
                     "Follow-ups сьогодні: {followups_today}\nПауза: {paused}\nОстанній прохід пошти: {last_watch}",
           "paused_yes": "так", "paused_no": "ні",
           "queue": "Черга: {queued}, held {held}, hand-off {handoff}\nПодано сьогодні: {sent}\nЗбоїв: {failed}",
           "lead_sent": "Подано: {company}, {role}\nКанал: {channel}, Msg {sent_id}",
           "lead_failed": "Збій подачі: {company}, {role}\nКанал: {channel}\n<pre>{error}</pre>",
           "handoff": "Твоя черга: {company}, {role}\nПричина: {reason}\nВкладка відкрита на {vnc_hint}\n{url}",
           "handoff_done": "Подано",
           "help": "Бот пошуку контракту. Картки приходять після кожного проходу пошти.\n"
                   "/status стан, /pause і /resume зупиняють і повертають відправку, /limits ліміти, "
                   "/platform платформи (/platform NAME on|off|shadow)."},
    "en": {"send": "Send draft", "open": "Open in Gmail", "skip": "Skip",
           "no_draft": "Draft unavailable (the model gave no usable answer), reply by hand.",
           "draft": "Reply draft:", "followup_sent": "Follow-up sent",
           "status": "Open cards: {open_cards}\nSent today: {sent_today}\n"
                     "Follow-ups today: {followups_today}\nPaused: {paused}\nLast inbox pass: {last_watch}",
           "paused_yes": "yes", "paused_no": "no",
           "queue": "Queue: {queued}, held {held}, handoff {handoff}\nApplied today: {sent}\nFailed: {failed}",
           "lead_sent": "Applied: {company}, {role}\nChannel: {channel}, Msg {sent_id}",
           "lead_failed": "Failed application: {company}, {role}\nChannel: {channel}\n<pre>{error}</pre>",
           "handoff": "Your turn: {company}, {role}\nReason: {reason}\nTab open on {vnc_hint}\n{url}",
           "handoff_done": "Submitted",
           "help": "Contract search bot. Cards arrive after every inbox pass.\n"
                   "/status state, /pause and /resume stop and restart sending, /limits limits, "
                   "/platform platforms (/platform NAME on|off|shadow)."},
}

# Картка про вимкнену платформу (етап 2d), ключ: статус проходу воркера
_PLATFORM = {
    # &lt;…&gt; уже екрановані: текст іде з parse_mode HTML, а relogin.py бере URL входу й --wait-for домен
    "uk": {"login_required": "Сесія на {platform} злетіла. Зайди по VNC ({hint}): DISPLAY=:99 "
                             "JOBSEARCH_CLIENT=&lt;slug&gt; bin/js scripts/relogin.py &lt;login-url&gt; "
                             "--wait-for &lt;domain&gt;, або в браузері на сервері; воркер тримає профіль "
                             "лише під час проходу (кілька хвилин о :40). "
                             "Після входу: /platform {platform} on",
           "restricted": "{platform} обмежив акаунт: платформу вимкнено до кінця дня, перевір акаунт. "
                         "Увімкнути: /platform {platform} on",
           "platform_quota": "Місячна квота {platform} вичерпана: вимкнено до 1-го числа."},
    "en": {"login_required": "Session on {platform} expired. Log in over VNC ({hint}): DISPLAY=:99 "
                             "JOBSEARCH_CLIENT=&lt;slug&gt; bin/js scripts/relogin.py &lt;login-url&gt; "
                             "--wait-for &lt;domain&gt;, or in the browser on the server; the worker holds the "
                             "profile only during a pass (a few minutes at :40). "
                             "After login: /platform {platform} on",
           "restricted": "{platform} restricted the account: platform switched off until the end of the day, "
                         "check the account. Switch on: /platform {platform} on",
           "platform_quota": "Monthly quota on {platform} used up: off until the 1st."},
}


@dataclass
class Card:
    kind: str
    text: str
    buttons: list[list[Button]] = field(default_factory=list)


def _ui(lang: str) -> dict:
    return _UI.get(lang, _UI["en"])


def _label(cls: str, lang: str) -> str:
    return _LABELS.get(lang, _LABELS["en"]).get(cls, cls)


def card_id(kind: str, message_id: str) -> str:
    """Короткий id картки: «send:<id>» лишається далеко від 64 байтів callback_data."""
    return f"{kind[:2]}-{message_id[:12]}"


def gmail_url(address: str, message_id: str) -> str:
    return f"https://mail.google.com/mail/u/0/?authuser={address}#all/{message_id}"


def _cut(text: str) -> str:
    return text if len(text) <= TEXT_LIMIT else text[:CUT_AT] + "…"


def _who(item: dict) -> str:
    company = (item.get("company") or "").strip()
    if company:
        return company
    # імпорт тут: модуль важкий, а картці домен потрібен лише без компанії
    from bot.generators.inbox import sender_domain
    return sender_domain(item.get("from") or "") or (item.get("from") or "")


def _header(item: dict, cls: str, lang: str) -> str:
    lines = [f"<b>{html.escape(_who(item))}</b> · {_label(cls, lang)}"]
    meta = " · ".join(html.escape(str(item.get(k) or "")) for k in ("date", "subject") if item.get(k))
    if meta:
        lines.append(meta)
    frm = item.get("from") or ""
    if frm:
        lines.append(html.escape(frm))
    return "\n".join(lines)


def reply_card(item: dict, cls: str, draft: str | None, cid: str, url: str, lang: str) -> Card:
    """Лист, який чекає людину: чернетка в <pre>, кнопки надіслати / відкрити / пропустити."""
    ui = _ui(lang)
    parts = [_header(item, cls, lang)]
    if draft:
        shown = draft if len(draft) <= DRAFT_LIMIT else draft[:DRAFT_LIMIT] + "…"
        parts.append(f"{ui['draft']}\n<pre>{html.escape(shown)}</pre>")
    else:
        parts.append(ui["no_draft"])
    row1 = []
    if draft:
        row1.append(Button(ui["send"], data=f"send:{cid}"))
    row1.append(Button(ui["open"], url=url))
    return Card("reply", _cut("\n\n".join(parts)), [row1, [Button(ui["skip"], data=f"skip:{cid}")]])


def notify_card(item: dict, cls: str, url: str, lang: str) -> Card:
    """Сповіщення без чернетки й без дій у боті (режим notify): хто, що, уривок
    листа і кнопка в Gmail. Трекер веде ранковий агент, оператор лише дізнається."""
    body = " ".join((item.get("body") or "").split())[:600]
    text = _header(item, cls, lang)
    if body:
        text += f"\n\n{html.escape(body)}"
    return Card("notify", _cut(text), [[Button(_ui(lang)["open"], url=url)]])


def rejection_card(item: dict, cid: str, url: str, lang: str) -> Card:
    """Відмова: трекер уже оновлено, кнопок нема, лінк у тексті."""
    text = _header(item, "rejected", lang) + f"\n{html.escape(url)}"
    return Card("rejected", _cut(text), [])


def followup_card(company: str, role: str, sent_id: str, lang: str) -> Card:
    ui = _ui(lang)
    text = (f"<b>{html.escape(company)}</b> · {_label('followup', lang)}\n"
            f"{ui['followup_sent']}: {html.escape(role)}\nMsg {html.escape(sent_id)}")
    return Card("followup", _cut(text), [])


def _counts(d: dict) -> str:
    return ", ".join(f"{k} {v}" for k, v in sorted(d.items())) or "0"


def status_text(summary: dict, lang: str) -> str:
    ui = _ui(lang)
    paused = ui["paused_yes"] if summary.get("paused") else ui["paused_no"]
    text = ui["status"].format(open_cards=summary.get("open_cards", 0),
                               sent_today=summary.get("sent_today", 0),
                               followups_today=summary.get("followups_today", 0),
                               paused=paused, last_watch=summary.get("last_watch") or "—")
    q = summary.get("queue")
    if q:
        text += "\n" + ui["queue"].format(queued=_counts(q.get("queued") or {}), held=q.get("held", 0),
                                          sent=_counts(q.get("sent_today") or {}), failed=q.get("failed", 0),
                                          handoff=q.get("handoff", 0))
    platforms = summary.get("platforms")
    if platforms:   # рядки bot.platforms.status_lines
        text += "\n" + "\n".join(html.escape(str(line)) for line in platforms)
    return text


ERROR_CUT = 300


def lead_sent_card(company: str, role: str, channel: str, sent_id: str, lang: str) -> Card:
    """Квитанція про подачу, без кнопок (етап 2b)."""
    e = html.escape
    return Card("lead_sent", _ui(lang)["lead_sent"].format(
        company=e(company), role=e(role), channel=e(channel), sent_id=e(sent_id)))


def lead_failed_card(company: str, role: str, channel: str, error: str, lang: str) -> Card:
    """Збій подачі, без кнопок: оператору варто знати, рішення не треба."""
    e = html.escape
    return Card("lead_failed", _ui(lang)["lead_failed"].format(
        company=e(company), role=e(role), channel=e(channel), error=e((error or "")[:ERROR_CUT])))


def handoff_card(company: str, role: str, reason: str, url: str, vnc_hint: str, cid: str, lang: str) -> Card:
    """Форму не довели до кінця: вкладка відкрита на сервері, оператор подає сам або пропускає."""
    ui = _ui(lang)
    e = html.escape
    text = ui["handoff"].format(company=e(company), role=e(role), reason=e((reason or "")[:ERROR_CUT]),
                                vnc_hint=e(vnc_hint), url=e(url))
    return Card("handoff", _cut(text), [[Button(ui["handoff_done"], data=f"done:{cid}"),
                                         Button(ui["skip"], data=f"skip:{cid}")]])


def platform_card(platform: str, status: str, hint: str, lang: str) -> Card:
    """Платформу вимкнено (злетів вхід, обмеження акаунта, квота): що зробити оператору, без кнопок."""
    e = html.escape
    tpl = _PLATFORM.get(lang, _PLATFORM["en"]).get(status)
    if tpl is None:
        return Card("platform", f"{e(platform)}: {e(status)}")
    return Card("platform", _cut(tpl.format(platform=e(platform), hint=e(hint))))


def help_text(lang: str) -> str:
    return _ui(lang)["help"]
