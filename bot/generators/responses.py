"""Response monitoring: parse tracker, classify email replies, generate report."""

from __future__ import annotations
import re
from pathlib import Path
from datetime import date
from typing import Optional

_REJECTED_KW  = ["leider", "absage", "nicht berücksichtigen", "anderweitig besetzt",
                  "nicht in die engere auswahl", "müssen wir ihnen absagen",
                  "konnten wir uns nicht", "bedauern wir"]
_INTERVIEW_KW = ["einladen", "gespräch", "interview", "kennenlernen", "termin vereinbaren",
                  "telefonat", "videocall", "zoom", "teams-call", "vorstellungsgespräch"]
_CONFIRMED_KW = ["eingangsbestätigung", "bewerbung erhalten", "bewerbung ist eingegangen",
                  "bewerbung ist bei uns eingegangen", "thank you for your application",
                  "haben deine bewerbung erhalten", "erfolgreich eingegangen"]
_INFO_KW      = ["bitte senden sie", "könnten sie uns", "noch folgende unterlagen",
                  "gehaltsvorstellung", "weitere informationen", "bitte teilen sie mit"]

_DE_TRANSLIT = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})


def _fold(text: str) -> str:
    """lower + ä→ae/ö→oe/ü→ue/ß→ss: листи й домени без умлаутів
    («beruecksichtigen», домен із ss замість ß) мають матчитись тими самими ключами."""
    return (text or "").lower().translate(_DE_TRANSLIT)


_REJECTED_KW_F  = [_fold(k) for k in _REJECTED_KW]
_INTERVIEW_KW_F = [_fold(k) for k in _INTERVIEW_KW]
_CONFIRMED_KW_F = [_fold(k) for k in _CONFIRMED_KW]
_INFO_KW_F      = [_fold(k) for k in _INFO_KW]


def classify_response(body: str) -> str:
    """Classify an email body.
    Returns: rejected | interview | confirmed | info_request | unknown."""
    folded = _fold(body)
    if any(kw in folded for kw in _REJECTED_KW_F):
        return "rejected"
    if any(kw in folded for kw in _INTERVIEW_KW_F):
        return "interview"
    if any(kw in folded for kw in _CONFIRMED_KW_F):
        return "confirmed"
    if any(kw in folded for kw in _INFO_KW_F):
        return "info_request"
    return "unknown"


def extract_domain(email: Optional[str]) -> Optional[str]:
    """Extract domain from email address. Returns None if email is None."""
    if not email:
        return None
    match = re.search(r"@([\w.-]+\.\w{2,})", email)
    return match.group(1) if match else None


def parse_tracker_sent(tracker_path: Path) -> list[dict]:
    """Parse tracker.md and return entries with 'Email sent' status."""
    entries = []
    for line in tracker_path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("| 20") or "Email sent" not in line:
            continue
        parts = [p.strip() for p in line.split("|") if p.strip()]
        if len(parts) < 3:
            continue
        date_str  = parts[0]
        position  = parts[1]
        employer  = parts[2]
        company   = employer.split(",")[0].strip()
        entries.append({
            "date":     date_str,
            "position": position,
            "employer": employer,
            "company":  company,
            "status":   "awaiting",
        })
    return entries


def generate_report(entries: list[dict], week_number: Optional[int] = None) -> str:
    """Generate a weekly status report from classified entries."""
    kw = week_number if week_number else date.today().isocalendar()[1]
    by_status: dict[str, list[str]] = {}
    for e in entries:
        by_status.setdefault(e["status"], []).append(e["company"])

    lines = [
        f"=== Bewerbungsstatus KW {kw} ===",
        f"Total: {len(entries)} | "
        f"Interview: {len(by_status.get('interview', []))} | "
        f"Absage: {len(by_status.get('rejected', []))} | "
        f"Info: {len(by_status.get('info_request', []))} | "
        f"Awaiting: {len(by_status.get('awaiting', []))}",
        "",
    ]
    for status, label in [
        ("interview",    "🎉 Interview eingeladen"),
        ("info_request", "📋 Rückfrage erhalten"),
        ("rejected",     "❌ Absagen"),
        ("awaiting",     "⏳ Noch keine Antwort"),
    ]:
        companies = by_status.get(status, [])
        if companies:
            lines.append(f"{label} ({len(companies)}):")
            for c in companies:
                lines.append(f"  • {c}")
            lines.append("")

    follow_up = [e["company"] for e in entries if e["status"] == "awaiting"]
    if follow_up:
        lines.append(f"📌 Follow-up empfohlen ({len(follow_up)} Bewerbungen — >10 Werktage):")
        for c in follow_up[:8]:
            lines.append(f"  • {c}")

    return "\n".join(lines)


_STATUS_BY_CLASS = {
    "rejected":     "rejected",
    "interview":    "interview_1",
    "confirmed":    "confirmed",
    "info_request": "action_needed",
}

# Статуси, які відповідь не може "відкотити" назад
_TERMINAL_STATUSES = {"rejected", "ghosted", "withdrew", "offer"}


def update_tracker_status(tracker_path: Path, company: str, new_status: str,
                          update_date: Optional[str] = None) -> bool:
    """In-place оновлення статусу рядка трекера за компанією.
    Підтримує обидва формати таблиць. Повертає True якщо рядок знайдено."""
    from bot.filters.rules import normalize_company

    update_date = update_date or date.today().strftime("%Y-%m-%d")
    target = normalize_company(company)
    if not target:
        return False

    date_re = re.compile(r"^\d{4}-\d{2}-\d{2}")
    lines = tracker_path.read_text(encoding="utf-8").splitlines(keepends=True)
    changed = False

    for i, line in enumerate(lines):
        if changed or not line.startswith("|"):
            continue
        cells = line.split("|")
        if len(cells) < 5:
            continue
        stripped = [c.strip() for c in cells]
        if date_re.match(stripped[1]) and len(cells) >= 5:
            # legacy: | date | title | company, location | status |
            if normalize_company(stripped[3].split(",")[0]) == target:
                # URL вакансії лишаємо: за ним працює dedup проти трекера.
                urls = re.findall(r"https?://[^\s|)>\]]+", cells[4])
                tail = f" · {' '.join(urls)}" if urls else ""
                cells[4] = f" {new_status} ({update_date}){tail} "
                lines[i] = "|".join(cells)
                changed = True
        elif len(cells) >= 11 and (date_re.match(stripped[2]) or stripped[2] in ("—", "2026-05-??")):
            # основна таблиця: | # | Date | Company | Position | Source | Salary | Status | Last update | ...
            if normalize_company(stripped[3]) == target:
                current = stripped[7].strip("*").lower()
                if current in _TERMINAL_STATUSES:
                    return False  # не відкочуємо фінальні статуси
                cells[7] = f" {new_status} "
                cells[8] = f" {update_date} "
                lines[i] = "|".join(cells)
                changed = True

    if changed:
        tracker_path.write_text("".join(lines), encoding="utf-8")
    return changed


# Токени, які самі по собі не ідентифікують компанію: міста NRW стоять у підписі
# майже кожного листа. Інцидент 25.07.2026: лист Falk & Falk приліпився до
# «Bildungswerk Düsseldorf», бо `duesseldorf` (10 літер) перебивав `falk` (4).
_WEAK_TOKENS = frozenset({
    "duesseldorf", "koeln", "cologne", "bonn", "essen", "dortmund", "duisburg",
    "wuppertal", "bochum", "aachen", "muenster", "krefeld", "moenchengladbach",
    "neuss", "leverkusen", "hilden", "ratingen", "velbert", "mettmann", "willich",
    "frechen", "pulheim", "wesseling", "monheim", "viersen", "borken", "oberhausen",
    "deutschland", "germany", "gmbh", "group", "holding", "service", "services",
    "international", "deutsche", "consulting", "partner", "partners", "gruppe",
    "nordrhein", "westfalen", "hamburg", "muenchen", "berlin", "frankfurt",
})


def match_response_to_company(response: dict, companies: list[str]) -> Optional[str]:
    """Матчить лист до компанії з трекера за from-доменом і subject/body.

    Домен важить більше за текст: у тексті трапляються міста й правові форми,
    спільні для десятків компаній. Слабкі токени (місто, «GmbH», «Group») не
    можуть бути єдиною підставою для матчу, інакше статус їде в чужий рядок.
    Серед кандидатів перемагає той, у кого більше збіглих сильних токенів.
    """
    from bot.filters.rules import normalize_company

    domain = _fold(extract_domain(response.get("from", "")) or "")
    haystack = _fold((response.get("subject", "") + " " + response.get("body", ""))[:3000])

    best: Optional[str] = None
    best_score: tuple[int, int, int] = (0, 0, 0)
    for company in companies:
        norm = _fold(normalize_company(company))
        tokens = [t for t in norm.split() if len(t) >= 4]
        if not tokens:
            continue
        strong = [t for t in tokens if t not in _WEAK_TOKENS]
        main_token = tokens[0]

        by_domain = bool(domain) and any(t in domain for t in (strong or tokens))
        # у тексті: усі токени назви присутні, і серед них є хоч один сильний
        by_text = bool(strong) and all(t in haystack for t in tokens[:2])

        if not (by_domain or by_text):
            continue
        matched_strong = sum(1 for t in strong if t in haystack or t in domain)
        score = (1 if by_domain else 0, matched_strong, len(main_token))
        if score > best_score:
            best, best_score = company, score
    return best


def process_responses(responses: list[dict], tracker_path: Path) -> dict:
    """Класифікує відповіді, оновлює трекер, повертає
    {updated, interviews, unknown}. Кожен елемент responses:
    {account: jobs|main, message_id, from, subject, body}."""
    from bot.filters.rules import parse_tracker_applications

    companies = list({app["company"] for app in parse_tracker_applications(tracker_path)})
    updated, interviews, unknown = [], [], []

    for resp in responses:
        classification = classify_response(resp.get("body", "") + " " + resp.get("subject", ""))
        company = match_response_to_company(resp, companies)
        account = resp.get("account", "jobs")

        item = {
            "message_id": resp.get("message_id"),
            "account": account,
            "from": resp.get("from"),
            "subject": resp.get("subject"),
            "classification": classification,
            "company": company,
            # на main нічого не видаляється НІКОЛИ (політика проєкту)
            "safe_to_trash": account == "jobs" and classification == "confirmed",
        }

        if classification == "unknown" or company is None:
            unknown.append(item)
            continue

        status = _STATUS_BY_CLASS[classification]
        item["tracker_updated"] = update_tracker_status(tracker_path, company, status)
        updated.append(item)
        if classification == "interview":
            interviews.append(item)

    return {"updated": updated, "interviews": interviews, "unknown": unknown}


def build_gmail_search_query(entries: list[dict]) -> str:
    """Build Gmail search query to find responses from known employers."""
    domains: list[str] = []
    for e in entries:
        if e.get("email"):
            d = extract_domain(e["email"])
            if d:
                domains.append(f"from:{d}")
    if not domains:
        return "label:Employer"
    domain_query = " OR ".join(domains[:20])
    return f"({domain_query}) label:Employer"
