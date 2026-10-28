"""Назва вакансії: розпізнати сміття і відновити справжню з тексту сторінки.

Модуль без конфігу клієнта, тож його імпортують і скрапери (під час інтейку),
і фільтр (для вже зібраних записів дайджесту). Лежить поза bot.scrapers і не
імпортує його на рівні модуля: bot.scrapers → models → intake → сюди дає цикл.

Типовий збій: firecrawl-markdown Indeed починається з кнопки «Suchen», а назва
стоїть нижче заголовком п'ятого рівня («##### Office Manager (m/w/d)»). Перший
рядок ставав title, і фільтр відкидав живі вакансії як scrape_failed_title
(22.10.2026: 28 з 33 записів одного клієнта).
"""

from __future__ import annotations

import re

# Назви, які дає зламаний скрап замість вакансії: shell порталу за
# login-wall («XING Job»), заголовок сторінки пошуку Indeed («Suchen»/«Search»),
# порожнеча. Такий запис не вакансія, скор у нього по сміттю.
JUNK_TITLES = {
    "xing job", "linkedin job", "suchen", "search", "jobs", "job", "jobsuche",
    "stellenangebote", "stellenangebot", "indeed", "stepstone", "xing", "linkedin",
    "karriere", "careers", "career", "stellen", "unbekannt", "unknown", "none", "",
    "unbekannte stelle",
}

# Заголовки секцій і навігації, які стоять у markdown вище або замість назви.
_NOT_A_TITLE = {
    "stellenbeschreibung", "vollständige stellenbeschreibung", "job details",
    "jobdetails", "job description", "full job description", "beschreibung",
    "profilerkenntnisse", "leistungen", "standort", "über uns", "about us",
    "go to", "navigation", "menü", "menu", "inhalt", "content", "skip to content",
    "zum inhalt springen", "cookie-einstellungen", "datenschutz", "impressum",
}

# Нижче цих рядків починаються чужі вакансії («Ähnliche Jobs»): назву з них не брати.
_FOREIGN_JOBS_RE = re.compile(
    r"^(?:ähnliche|weitere|andere|passende) (?:jobs|stellen|stellenangebote)\b"
    r"|^similar jobs\b|^das könnte (?:dich|sie) auch interessieren",
    re.IGNORECASE,
)

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_MD_LINK_RE = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
# Гендерна позначка німецьких вакансій: (m/w/d), (w/m/d), m/f/d, (gn), (all genders)
_GENDER_RE = re.compile(
    r"\b[mwdfx]\s*/\s*[mwdfx](?:\s*/\s*[mwdfx])?\b"
    r"|\((?:gn|d|all genders|alle geschlechter)\)",
    re.IGNORECASE,
)
_HEAD_LINES = 15
_H1_LINES = 10


def is_junk_title(title: str) -> bool:
    t = (title or "").strip().strip("#*·-–— ").lower()
    return t in JUNK_TITLES or len(t) < 4


def _clean(line: str) -> str:
    line = _MD_LINK_RE.sub(r"\1", line)
    line = re.sub(r"\*\*|__", "", line)
    # markdown-екранування: «Sekretär\*in» → «Sekretär*in»
    line = re.sub(r"\\([\\`*_{}\[\]()#+\-.!|])", r"\1", line)
    return line.strip(" *_·|")


def _usable(text: str) -> bool:
    from bot.scrapers.liveness import CLOSED_PATTERNS
    low = text.lower().rstrip(":")
    # «1/2/2026» і подібне: у назві ролі мають бути слова; банер «Diese
    # Stellenanzeige ist auf Indeed abgelaufen» стоїть заголовком над назвою
    return (not is_junk_title(text) and low not in _NOT_A_TITLE
            and len(re.findall(r"[^\W\d_]", text)) >= 4 and len(text) <= 200
            and not any(re.search(p, low) for p in CLOSED_PATTERNS))


def _head(text: str) -> list[str]:
    lines = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        if _FOREIGN_JOBS_RE.search(_clean(line.lstrip("# "))):
            break
        lines.append(line)
        if len(lines) >= _HEAD_LINES:
            break
    return lines


def _headings(text: str) -> list[tuple[int, int, str]]:
    out = []
    for i, line in enumerate(_head(text)):
        m = _HEADING_RE.match(line)
        if m:
            out.append((i, len(m.group(1)), _clean(m.group(2))))
    return out


def first_h1(text: str) -> str | None:
    """H1 у перших рядках — назва сторінки, навіть сміттєва («# 404»)."""
    for i, level, txt in _headings(text):
        if level == 1 and i < _H1_LINES:
            return txt
    return None


def heading_title(text: str) -> str | None:
    """Назва з markdown-заголовка. Сміттєвий H1 інші заголовки не підміняють:
    на сторінці з «# 404» «## Go to» — навігація, не вакансія. Без H1 береться
    перший змістовний заголовок будь-якого рівня (Indeed з 10.2026 дає «#####»)."""
    h1 = first_h1(text)
    if h1 is not None:
        return h1 if _usable(h1) else None
    for _, _, txt in _headings(text):
        if _usable(txt):
            return txt
    return None


def marker_title(text: str) -> str | None:
    """Перший рядок із гендерною позначкою (m/w/d), що починається з великої
    літери: «sucht eine Schulbegleitung (m/w/d)» — це речення, не назва."""
    for line in _head(text):
        if line.startswith("!["):
            continue
        txt = _clean(line.lstrip("#").strip())
        if _GENDER_RE.search(txt) and txt[:1].isupper() and _usable(txt):
            return txt
    return None


def recover_title(text: str) -> str | None:
    """Справжня назва вакансії з тексту сторінки, коли зібрана назва сміттєва;
    None, якщо певності нема (тоді запис чесно відпадає як scrape_failed_title)."""
    if first_h1(text) is not None:
        return heading_title(text)
    return heading_title(text) or marker_title(text)


# URL буває з дужками: /cmp/Beispiel-Praxis-(Mitte)
_CMP_LINK_RE = re.compile(r"^\[([^\]]+)\]\(\S*?/cmp/\S*\)$")


def company_from_text(text: str) -> str | None:
    """Компанія з Indeed-markdown: лінк на її /cmp/-сторінку в шапці."""
    for line in _head(text):
        m = _CMP_LINK_RE.match(line)
        if m:
            return m.group(1).strip()
    return None


def is_junk_company(company: str) -> bool:
    """«##### Назва ролі …», «!Firma logo», порожнє: парсер взяв не той рядок."""
    c = (company or "").strip()
    return (not c or c.lower() in ("unbekannt", "unknown") or c.startswith(("#", "!", "["))
            or c.lower().endswith(" logo"))
