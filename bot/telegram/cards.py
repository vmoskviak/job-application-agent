"""Картки для оператора: текст у HTML Telegram і кнопки під ним.

Кожне значення з листа проходить html.escape; текст понад ліміт Telegram
обрізається. Мова картки — report_lang клієнта (uk або en), мова чернетки
окрема, її задає лист.
"""
from __future__ import annotations

import html
from dataclasses import dataclass, field

from bot.generators.upwork_alert import UpworkJob, money, rating_text
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
           "why_no_reply": "Відправник не приймає відповідей, тому чернетки нема: відповідати в порталі чи на сайті.",
           "why_closed": "Рядок у трекері закрито ({status}), тому чернетки нема.",
           "why_trashed": "Лист у Кошику (його видалили вручну), тому чернетки нема.",
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
           "why_no_reply": "The sender does not accept replies, so there is no draft: answer in the portal or on the site.",
           "why_closed": "The tracker row is closed ({status}), so there is no draft.",
           "why_trashed": "The mail is in the Trash (deleted by hand), so there is no draft.",
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


def info_card(item: dict, cls: str, url: str, lang: str, reason: str, status: str = "") -> Card:
    """A mail the bot must not answer (reason: no_reply sender, closed tracker row, trashed by
    the owner): who, why there is no draft, an excerpt and the Gmail button; nothing to send."""
    ui = _ui(lang)
    text = f"{_header(item, cls, lang)}\n\n{ui['why_' + reason].format(status=html.escape(status))}"
    body = " ".join((item.get("body") or "").split())[:600]
    if body:
        text += f"\n\n{html.escape(body)}"
    return Card("info", _cut(text), [[Button(ui["open"], url=url)]])


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


# Upwork proposal card (stage 2e): the operator submits by hand (manual mode, or what auto-submit handed over)
_UPWORK = {
    "uk": {"fixed": "Фіксована ціна", "hourly": "Погодинно", "unknown": "Бюджет невідомий",
           "client": "Клієнт", True: "оплату підтверджено", False: "оплату не підтверджено",
           None: "оплата невідома", "rating": "рейтинг {rating}", "spent": "витрачено {spent}",
           "skills": "Навички", "saved": "Збережений пошук", "bid": "Ставка", "per_hour": "{amount}/год",
           "client_budget": "{amount} (бюджет клієнта)", "fit": "Відповідність",
           "high": "висока", "medium": "середня", "low": "низька",
           "open": "Відкрити вакансію на Upwork", "proposal": "Пропозиція (скопіювати):",
           "link_too_long": "Посилання задовге для Telegram, вакансія є в листі:", "gmail": "лист у Gmail",
           "done": "Подав", "claude": "Подати через Claude: напиши в сесії «подай Upwork»."},
    "en": {"fixed": "Fixed price", "hourly": "Hourly", "unknown": "Budget unknown",
           "client": "Client", True: "payment verified", False: "payment unverified",
           None: "payment unknown", "rating": "rating {rating}", "spent": "{spent} spent",
           "skills": "Skills", "saved": "Saved search", "bid": "Bid", "per_hour": "{amount}/h",
           "client_budget": "{amount} (client's budget)", "fit": "Fit",
           "high": "high", "medium": "medium", "low": "low",
           "open": "Open the job on Upwork", "proposal": "Proposal (copy):",
           "link_too_long": "The link is too long for Telegram, the job is in the alert mail:",
           "gmail": "open in Gmail", "done": "Submitted",
           "claude": "To have Claude submit it, say \"submit Upwork\" in a session."},
}
PROPOSAL_ROOM = 1500   # characters kept for the proposal before the job link gives way


def _upwork_budget(job: UpworkJob, ui: dict) -> str:
    lo, hi = job.budget_min, job.budget_max
    if job.budget_type == "fixed":
        return f"{ui['fixed']} {money(hi)}" if hi is not None else ui["fixed"]
    if job.budget_type == "hourly":
        if lo is not None and hi is not None and lo != hi:
            return f"{ui['hourly']} {money(lo)}-{money(hi)}"
        return f"{ui['hourly']} {money(hi)}" if hi is not None else ui["hourly"]
    return ui["unknown"]


def _upwork_bid(job: UpworkJob, bid_hourly_usd: float, ui: dict) -> str:
    """Hourly: the client's own bid rate; fixed: the client's budget; unknown: no suggestion."""
    if job.budget_type == "hourly":
        return ui["per_hour"].format(amount=money(bid_hourly_usd))
    if job.budget_type == "fixed" and job.budget_max is not None:
        return ui["client_budget"].format(amount=money(job.budget_max))
    return "—"


def _escaped_within(text: str, room: int) -> str:
    """html.escape(text) cut to at most `room` characters with "…", never inside an entity."""
    out = html.escape(text)
    cut = len(text)
    while len(out) > room and cut > 0:
        cut = max(0, cut - max(1, len(out) - room + 1))
        out = html.escape(text[:cut]) + "…"
    return out


def upwork_card(job: UpworkJob, proposal: str, fit: str, reason: str, bid_hourly_usd: float, cid: str,
                lang: str, mail_url: str = "") -> Card:
    """Proposal card: what the alert says about the job and the client, the suggested bid, the
    model's fit, the job link as an anchor in the text (a ~1 000-character tracking URL as a
    button risks a failed sendMessage) and the proposal in <pre>, which Telegram copies in one
    tap. The proposal is cut so the escaped text stays under CUT_AT; only a link that alone
    would break the limit is replaced by the alert mail in Gmail. Buttons: submitted, skip."""
    ui = _UPWORK.get(lang, _UPWORK["en"])
    e = html.escape
    head = [f"<b>Upwork · {e(job.title)}</b>",
            " · ".join(x for x in (_upwork_budget(job, ui), e(job.duration)) if x)]
    signals = [ui[job.payment_verified]]
    if job.rating is not None:
        signals.append(ui["rating"].format(rating=rating_text(job.rating)))
    if job.spent:
        signals.append(ui["spent"].format(spent=e(job.spent)))
    if job.country:
        signals.append(e(job.country))
    head.append(f"{ui['client']}: {' · '.join(signals)}")
    if job.skills:
        head.append(f"{ui['skills']}: {e(', '.join(job.skills))}")
    if job.saved_search:
        head.append(f"{ui['saved']}: {e(job.saved_search)}")
    head.append(f"{ui['bid']}: {_upwork_bid(job, bid_hourly_usd, ui)}")
    fit_line = f"{ui['fit']}: {ui.get(fit, e(fit))}"
    head.append(f"{fit_line} · {e(reason)}" if reason else fit_line)
    head.append(f"<i>{ui['claude']}</i>")

    def text_with(link: str, body: str) -> str:
        return "\n".join([*head, link]) + f"\n\n{ui['proposal']}\n<pre>{body}</pre>"

    link = f'<a href="{e(job.job_link)}">{ui["open"]}</a>' if job.job_link else ""
    if len(text_with(link, "")) > CUT_AT - PROPOSAL_ROOM:
        link = ui["link_too_long"] + (f' <a href="{e(mail_url)}">{ui["gmail"]}</a>' if mail_url else "")
    shown = _escaped_within(proposal, CUT_AT - len(text_with(link, "")))
    ui_main = _ui(lang)
    return Card("upwork", _cut(text_with(link, shown)),
                [[Button(ui["done"], data=f"done:{cid}"), Button(ui_main["skip"], data=f"skip:{cid}")]])
