"""Вхідна пошта контрактного клієнта: класи листів, відправники платформ,
мова й валюта чернетки, нотатки в трекер. Без конфігу клієнта на імпорті.

Класи (spec фази 2, §8): confirmed, rejected, interview, info_request — відповіді
на подачі (клас дає responses.classify_response); outreach — рекрутер чи агентура
пише першим; invitation — запрошення маркетплейсу; system — розсилки, статистика,
знижки; unknown — решта. Правило відправника важливіше за текст: агентура зі
списку клієнта завжди outreach і завжди німецькою, маркетплейс без запрошення
в тексті це розсилка.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Literal

from bot.generators.cover_note import _no_dashes
from bot.generators.employer_scan import registrable_domain
from bot.generators.engagement import detect_lang
from bot.generators.responses import _fold, extract_domain

MailClass = Literal[
    "confirmed", "rejected", "interview", "info_request",
    "outreach", "invitation", "system", "unknown",
]
_REPLY_CLASSES = ("confirmed", "rejected", "interview", "info_request")

# Домени платформ, з яких приходять листи про подачі й ліди (spec §8).
PLATFORM_SENDERS: dict[str, tuple[str, ...]] = {
    "ats": ("greenhouse-mail.io", "ashbyhq.com", "candidates.workablemail.com",
            "hire.lever.co", "teamtailor.com", "personio.de", "factorialhr.com"),
    "marketplace": ("upwork.com", "malt.com", "a.team", "arc.dev"),
    "agency": ("freelancermap.de", "gulp.de", "randstad.de", "etengo.de", "hays.de",
               "solcom.de", "westhouse.de", "computerfutures.com"),
    "social": ("linkedin.com", "xing.com"),
}
# InMail рекрутерів: LinkedIn шле їх з окремих адрес, не з jobalerts
INMAIL_SENDERS = ("inmail-hit-reply@linkedin.com", "messages-noreply@linkedin.com")

# Тексти вже прогнані через responses._fold (нижній регістр, ae/oe/ue)
SYSTEM_RE = re.compile(
    r"newsletter|tipps|tips|statistik|статистик|rabatt|discount|willkommen|welcome to"
    r"|weekly|digest|your week|credits"
    # алерти збережених пошуків (GULP, freelancermap, LinkedIn): 26.10 два GULP-алерти стали картками
    r"|stored search|new results|projektvorschl|neue projekte|neue auftr|job alert|new jobs"
    r"|jobs for you|empfehlungen"
    # автовідповіді скриньок: нічого не кажуть про заявку
    r"|automatic reply|automatische antwort|abwesenheit|out of office|autoreply|auto-reply")
OUTREACH_RE = re.compile(
    r"spannendes profil|ihr profil|dein profil|your profile|opportunity|position for you"
    r"|projektanfrage|projektangebot|freelance project|contract role")
INVITE_RE = re.compile(r"invit|mission|proposal|new message|neue nachricht")
_USD_RE = re.compile(r"\$|\busd\b", re.IGNORECASE)


def platform_domains() -> list[str]:
    return [d for group in PLATFORM_SENDERS.values() for d in group]


def sender_domain(from_header: str) -> str:
    """Реєстрований домен відправника: «inmail-hit-reply@linkedin.com» → linkedin.com."""
    host = extract_domain(from_header) or ""
    return registrable_domain(host.lower()) if host else ""


def _sender_host(from_header: str) -> str:
    return (extract_domain(from_header) or "").lower()


def is_agency(from_header: str, agencies_de: list[str]) -> bool:
    """Домен відправника дорівнює або закінчується на агентуру зі списку клієнта."""
    host = _sender_host(from_header)
    suffixes = [a.lower().lstrip(".") for a in agencies_de if a]
    return bool(host) and any(host == a or host.endswith("." + a) for a in suffixes)


def platform_group(from_header: str) -> str | None:
    """Група платформи за доменом відправника (ats, marketplace, agency, social) або None."""
    dom = sender_domain(from_header)
    for group, domains in PLATFORM_SENDERS.items():
        if dom in domains:
            return group
    return None


def classify_mail(item: dict, agencies_de: list[str]) -> MailClass:
    """Клас листа за відправником, темою, текстом і збігом з трекером.

    item: from, subject, body, company (збіг із трекером або None),
    classification (responses.classify_response)."""
    frm = item.get("from") or ""
    subject = _fold(item.get("subject") or "")
    text = f"{subject} {_fold(item.get('body') or '')}"
    cls = item.get("classification") or "unknown"
    if SYSTEM_RE.search(subject):
        return "system"
    if item.get("company") and cls in _REPLY_CLASSES:
        return cls  # type: ignore[return-value]
    if is_agency(frm, agencies_de) or any(s in frm.lower() for s in INMAIL_SENDERS):
        return "outreach"
    group = platform_group(frm)
    if group == "social":
        # LinkedIn і XING без InMail: «хто дивився ваш профіль», пошукові сповіщення
        return "system"
    if group == "marketplace":
        return "invitation" if INVITE_RE.search(text) else "system"
    if group == "agency":
        # агентурні платформи шлють і розсилки, і особисті листи: без ключових слів
        # вирішує модель (unknown → LLM у watcher-і), а не фільтр
        return "outreach" if OUTREACH_RE.search(text) else "unknown"
    if OUTREACH_RE.search(text):
        return "outreach"
    if cls in _REPLY_CLASSES:
        return cls  # type: ignore[return-value]
    return "unknown"


def draft_language(item: dict, agencies_de: list[str]) -> Literal["de", "en"]:
    """Агентура → de; інакше мова тексту листа."""
    if is_agency(item.get("from") or "", agencies_de):
        return "de"
    return detect_lang(f"{item.get('subject') or ''} {item.get('body') or ''}")


def currency_for_mail(item: dict, lang: str) -> Literal["EUR", "USD"]:
    """USD лише для англомовного листа, де згадано долари."""
    text = f"{item.get('subject') or ''} {item.get('body') or ''}"
    return "USD" if lang == "en" and _USD_RE.search(text) else "EUR"


def status_for_class(cls: str) -> str | None:
    """Статус трекера, який лист встановлює сам; None = лише картка."""
    return {"confirmed": "confirmed", "rejected": "rejected", "interview": "interview"}.get(cls)


def platform_queries(days: int, exclude_senders: list[str], chunk: int = 20) -> list[str]:
    """Gmail-запити по доменах платформ, партіями по chunk, без алерт-відправників."""
    negatives = " ".join(f"-from:{s}" for s in exclude_senders if s)
    domains = platform_domains()
    queries: list[str] = []
    for i in range(0, len(domains), chunk):
        senders = " OR ".join(f"from:{d}" for d in domains[i:i + chunk])
        q = f"newer_than:{days}d in:inbox ({senders})"
        queries.append(f"{q} {negatives}".strip())
    return queries


def append_note(tracker_path: Path, company: str, note: str) -> bool:
    """Дописати « · note» у Notes рядка головної таблиці за компанією."""
    from bot.filters.rules import normalize_company  # читає конфіг клієнта на імпорті

    target = normalize_company(company)
    if not target:
        return False
    lines = Path(tracker_path).read_text(encoding="utf-8").splitlines(keepends=True)
    for i, line in enumerate(lines):
        if not line.startswith("|"):
            continue
        cells = line.split("|")
        if len(cells) < 12 or not cells[1].strip().isdigit():
            continue
        if normalize_company(cells[3].strip()) != target:
            continue
        cells[10] = f" {cells[10].strip()} · {note} "
        lines[i] = "|".join(cells)
        Path(tracker_path).write_text("".join(lines), encoding="utf-8")
        return True
    return False


def _cell(text: str) -> str:
    return " ".join(str(text or "").replace("|", "/").split())


def mark_ready_row_submitted(tracker_path: Path, *, company: str, position: str, status: str, date: str,
                             notes: str) -> bool:
    """Ринок найму: --batch-render уже дописав рядок «🔄 Файли готові…» чи «🌐 Portal: …»;
    після подачі воркером цей рядок стає подачею, а не другий рядок поруч. Обидва формати
    таблиці; збіг за компанією й схожою роллю; False, якщо такого рядка нема."""
    from bot.ats import same_role
    from bot.filters.rules import normalize_company
    from bot.presend import READY_STATUS_RE

    path = Path(tracker_path)
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    want = normalize_company(company)
    date_re = re.compile(r"^\d{4}-\d{2}-\d{2}")
    for i, line in enumerate(lines):
        if not line.startswith("|"):
            continue
        cells = line.split("|")
        stripped = [c.strip() for c in cells]
        legacy = len(cells) >= 6 and date_re.match(stripped[1]) is not None
        main = not legacy and len(cells) >= 12 and date_re.match(stripped[2]) is not None
        if legacy:   # | date | title | company, location | status |
            hit = (READY_STATUS_RE.match(stripped[4]) and normalize_company(stripped[3].split(",")[0]) == want
                   and same_role(position, stripped[2]))
        elif main:   # | # | Date | Company | Position | Source | Salary | Status | Last update | Next | Notes |
            hit = (READY_STATUS_RE.match(stripped[7]) and normalize_company(stripped[3]) == want
                   and same_role(position, stripped[4]))
        else:
            hit = False
        if not hit:
            continue
        if legacy:
            urls = re.findall(r"https?://[^\s|)>\]]+", stripped[4])
            keep = f" · {urls[0]}" if urls and urls[0] not in notes else ""
            cells[4] = f" {status} ({date}){keep} · {notes} "
        else:
            cells[7], cells[8] = f" {status} ", f" {date} "
            cells[10] = f" {notes} · {stripped[10]} " if stripped[10] else f" {notes} "
        lines[i] = "|".join(cells)
        path.write_text("".join(lines), encoding="utf-8")
        return True
    return False


def append_row(tracker_path: Path, *, date: str, company: str, position: str, source: str, salary: str,
               status: str, next_action: str, notes: str) -> int:
    """Новий рядок головної таблиці одразу після останнього рядка з номером;
    # = максимум + 1 (1 для порожньої таблиці). Повертає новий номер."""
    lines = Path(tracker_path).read_text(encoding="utf-8").splitlines(keepends=True)
    last_idx, max_no = -1, 0
    for i, line in enumerate(lines):
        if not line.startswith("|"):
            continue
        cells = line.split("|")
        if len(cells) >= 12 and cells[1].strip().isdigit():
            last_idx, max_no = i, max(max_no, int(cells[1].strip()))
        elif last_idx < 0 and len(cells) >= 12 and cells[1].strip().startswith("---"):
            last_idx = i
    no = max_no + 1
    row = "| " + " | ".join([str(no), _cell(date), _cell(company), _cell(position), _cell(source), _cell(salary),
                             _cell(status), _cell(date), _cell(next_action), _cell(notes)]) + " |\n"
    if last_idx < 0:
        lines.append(row)
    else:
        if not lines[last_idx].endswith("\n"):
            lines[last_idx] += "\n"
        lines.insert(last_idx + 1, row)
    Path(tracker_path).write_text("".join(lines), encoding="utf-8")
    return no


# ── чернетка відповіді ───────────────────────────────────────────────────────

_LANG_NAMES = {"de": "German", "en": "English"}
_VALID_CLASSES = ("confirmed", "rejected", "interview", "info_request",
                  "outreach", "invitation", "unknown")


def draft_prompt(item: dict, cls: str, lang: str, brief: str, profile: dict, rate_line: str,
                 forbidden: list[str] | None = None) -> str:
    """Промпт для claude -p: один JSON-об'єкт {class, draft}. У промпт іде лише те,
    що може піти в лист: бриф, рядок профілю, доступність, готове речення про
    ставку. hourly_min і решта профілю сюди не потрапляють. forbidden: назви, які
    цьому адресатові не називаються (поточний роботодавець для агентур і платформ)."""
    extra = profile.get("cv_contact_extra") or []
    head = ("First decide the class from the mail, then write the draft for that class."
            if cls == "unknown" else f"Class guess: {cls}. Keep it unless the mail clearly says otherwise.")
    rate = rate_line.strip() or "(none: do not mention any rate)"
    banned = (f"Never name any of these (use a neutral description instead): {', '.join(forbidden)}.\n"
              if forbidden else "")
    return (
        "You draft a reply on behalf of a freelance software engineer. Answer with ONE JSON object "
        "and nothing else:\n"
        '{"class": "<confirmed|rejected|interview|info_request|outreach|invitation|unknown>", '
        '"draft": "<reply body>"}\n\n'
        f"Rules for \"draft\": written in {_LANG_NAMES.get(lang, lang)}; 80-160 words; no greeting line "
        "and no signature (both are added later); no dash connectors (no em or en dashes); "
        "no rate numbers other than the rate line below, repeated verbatim when a rate is asked; "
        "no start date other than the availability below; no facts that are not in the brief; "
        "never claim that an action has already been taken (a slot booked, a document sent, a call made): "
        "when the mail asks to book or confirm, say that you will do it or propose times.\n"
        f"{banned}{head}\n\n"
        f"Mail from: {item.get('from') or ''}\n"
        f"Subject: {item.get('subject') or ''}\n"
        "Mail body:\n<<<\n"
        f"{(item.get('body') or '')[:3000]}\n>>>\n\n"
        f"About the candidate (brief):\n{brief}\n\n"
        f"Profile line: {extra[0] if extra else ''}\n"
        f"Availability: {profile.get('availability') or ''}\n"
        f"Rate line: {rate}\n"
    )


def parse_draft(text: str) -> dict:
    """Перший JSON-об'єкт у відповіді моделі (проза й огорожі навколо дозволені)."""
    start = (text or "").find("{")
    if start < 0:
        raise ValueError("відповідь без JSON-об'єкта")
    try:
        data, _ = json.JSONDecoder().raw_decode(text[start:])
    except json.JSONDecodeError as e:
        raise ValueError(f"JSON не розібрано: {e}") from None
    if not isinstance(data, dict) or data.get("class") not in _VALID_CLASSES:
        raise ValueError(f"невідомий клас: {data.get('class') if isinstance(data, dict) else data!r}")
    if not str(data.get("draft") or "").strip():
        raise ValueError("порожня чернетка")
    return {"class": data["class"], "draft": str(data["draft"]).strip()}


def assemble_draft(draft: str, lang: str, profile: dict, rate_line: str, company: str | None) -> str:
    """Лист із тіла чернетки: привітання, тіло без тире, ставка, підпис (як cover_note.build_note)."""
    name = f"{profile.get('first_name', '')} {profile.get('last_name', '')}".strip()
    if lang == "de":
        greeting, closing = "Guten Tag,", "Mit freundlichen Grüßen"
    else:
        greeting = f"Hello {company.strip()} team," if company and company.strip() else "Hello,"
        closing = "Best regards"
    sign = [name, profile.get("city") or ""]
    extra = profile.get("cv_contact_extra") or []
    if extra:
        sign.append(str(extra[0]))
    signoff = "\n".join([closing, *[x for x in sign if x]])
    body = _no_dashes(draft).strip()
    # промпт просить повторити речення про ставку дослівно, якщо її питають: тоді не дублювати
    rate = "" if rate_line.strip() and rate_line.strip() in body else rate_line.strip()
    blocks = [greeting, body, rate, signoff]
    return "\n\n".join(b for b in blocks if b)


def append_note_by_row(tracker_path: Path, row_id: str, note: str) -> bool:
    """Дописати « · note» у Notes рядка головної таблиці за його номером (#)."""
    lines = Path(tracker_path).read_text(encoding="utf-8").splitlines(keepends=True)
    for i, line in enumerate(lines):
        if not line.startswith("|"):
            continue
        cells = line.split("|")
        if len(cells) < 12 or cells[1].strip() != str(row_id):
            continue
        cells[10] = f" {cells[10].strip()} · {note} "
        lines[i] = "|".join(cells)
        Path(tracker_path).write_text("".join(lines), encoding="utf-8")
        return True
    return False
