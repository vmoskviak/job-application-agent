"""GULP / Randstad Professional (gulp.de/gulp2/g/projekte/<direkt|agentur>/<id>).

Angular-SPA: текст береться з inner_text після settle (див. fetch.SETTLE_DOMAINS).
Два типи проєктів: «Direkt vom Auftraggeber» (замовник анонімний) і «Randstad
Agentur» (агентура Randstad Professional, у картці консультант із телефоном).
Контакти консультанта в опис не беремо: це не частина вакансії.
"""
from __future__ import annotations

import re

from bs4 import BeautifulSoup

from bot.generators.intake import JobData, detect_portal
from bot.scrapers.models import PageFetch

# Перша строка опису: проєктна біржа для фрилансерів, тому engagement = contract,
# якщо текст сам не каже інакше (R4 ловить «nur Festangestellte» тощо далі в тексті).
_LEAD = "Projektbörse GULP (freiberuflich)"

# Початок власне проєкту: після шапки сайту й картки з кнопкою «Jetzt bewerben»
_HEADER_END_RE = re.compile(r"^(Teilen|URL Kopieren|Seite drucken)$", re.M)
_BODY_START = ("Beschreibung", "Projektbeschreibung")
# Футер сайту / блоки Randstad після тексту проєкту
_BODY_END = ("Über Randstad Professional", "Auf Randstad können Sie vertrauen", "powered by")
# Рядки картки, які варто пронести в опис (ставка, auslastung, dauer — сигнали R4/ставки)
_CARD_KEYS = ("Startdatum", "Beginn", "Dauer", "Auslastung", "Einsatzort", "Projekt-Rolle",
              "Remote-Arbeit", "Stundensatz", "Laufzeit")


def _card_lines(head: str) -> list[str]:
    """Картка проєкту: «Beginn: 04.10.2026» (direkt) або «Startdatum\n03.11.2026»
    (agentur, label і значення в сусідніх рядках через іконку)."""
    lines = [ln.strip() for ln in head.splitlines() if ln.strip()]
    out: list[str] = []
    i = 0
    while i < len(lines):
        ln = lines[i]
        key = next((k for k in _CARD_KEYS if ln.startswith(k)), None)
        if key is None:
            i += 1
            continue
        if ":" in ln or ln.startswith("Remote-Arbeit"):
            out.append(ln)
            i += 1
        elif i + 1 < len(lines):
            out.append(f"{ln}: {lines[i + 1]}")
            i += 2
        else:
            i += 1
    return out


def _location(card: list[str]) -> str:
    loc = next((ln.split(":", 1)[1].strip() for ln in card if ln.startswith("Einsatzort")), "")
    remote = next((ln for ln in card if ln.startswith("Remote-Arbeit")), "")
    if loc and remote:
        return f"{loc} ({remote})"
    return loc or remote or "Deutschland"


class GulpAdapter:
    NAME = "gulp"

    def extract(self, page: PageFetch) -> JobData:
        text = page.text
        soup = BeautifulSoup(page.html or "", "html.parser")
        h1 = soup.find("h1")
        title = " ".join(h1.get_text(" ", strip=True).split()) if h1 else ""

        # шапка сайту закінчується на «Teilen / URL Kopieren / Seite drucken»
        m = _HEADER_END_RE.search(text)
        head_start = text.rfind("\n", 0, text.find(title)) if title and title in text else 0
        card_text = text[max(head_start, 0):m.start()] if m else ""
        after_card = text[m.end():] if m else text

        # Картка закінчується на «Jetzt bewerben» (+ рядок Job ID і, в агентурних,
        # блок «Ihr Ansprechpartner» з контактами, який відсікає порожній рядок).
        body_idx = -1
        apply_idx = after_card.find("Jetzt bewerben")
        if apply_idx != -1:
            rest = after_card[apply_idx:]
            contact = rest.find("Ihr Ansprechpartner")
            if contact != -1:
                gap = re.search(r"\n\s*\n", rest[contact:])
                body_idx = apply_idx + contact + (gap.end() if gap else 0)
            else:
                nl = rest.find("\n", rest.find("\n") + 1)  # після «Jetzt bewerben» і Veröffentlicht/Job ID
                body_idx = apply_idx + (nl + 1 if nl != -1 else len(rest))
        card_block = after_card[:body_idx] if body_idx != -1 else after_card
        body = after_card[body_idx:] if body_idx != -1 else after_card
        end_idx = min([body.find(k) for k in _BODY_END if body.find(k) != -1] or [len(body)])
        body = body[:end_idx].strip()

        card = _card_lines(card_block)
        company = "Randstad Professional (GULP)" if "Randstad Agentur" in card_text \
            else "Direkt vom Auftraggeber (GULP)"
        if not title:
            first = [ln for ln in card_text.splitlines() if ln.strip()]
            title = first[0].strip() if first else "GULP Projekt"

        description = "\n".join([f"{_LEAD}. {company}."] + card + ["", body])
        return JobData(
            title=title,
            company=company,
            location=_location(card),
            description=description[:3000],
            email=None,
            application_url=detect_portal(text),
            source_url=page.url.split("?")[0],
            is_portal=True,
        )
