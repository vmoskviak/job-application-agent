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


# --- Page titles: role and company apart -------------------------------------------------
# «<Role> bei <Firma GmbH> | hokify», «<Role> - <Firma> | BeBee», «<Role> | Köln | <Firma>»,
# «<Role> - Karriere bei <Firma>», «<Role> bei der <Firma GmbH> (m/w/d)». Before 03.11.2026 the
# company of such a job was the whole title or «Unbekannt», and the check against the tracker
# missed three repeat applications in one morning.

_LEGAL_FORM = (r"(?:gGmbH|GmbH(?:\s*&\s*Co\.?\s*KG)?|mbH|AG|SE|KGaA|KG|OHG|GbR|e\.\s?V\.|eG"
               r"|PartG(?:\s*mbB)?|UG(?:\s*\(haftungsbeschränkt\))?|Ltd\.?|Inc\.?|B\.V\.|S\.A\.)")
_BEI_COMPANY_RE = re.compile(
    r"\bbei\s+(?:der|dem|den)?\s*(?P<c>[^|()]{2,80}?\s" + _LEGAL_FORM + r")(?=\s*(?:\(|$|[,|–-]))")
_KARRIERE_BEI_RE = re.compile(r"\s*(?:[-–|]\s*)?\b(?:Karriere|Jobs?)\s+bei\s+(?P<c>[^|()]{2,60}?)\s*$",
                              re.IGNORECASE)
_DASH_COMPANY_RE = re.compile(r"^(?P<title>.+?)\s+[-–]\s+(?P<company>[^-–|]{3,}?)\s*$")
# Segments that name the site's section, not the employer («RP Stellenmarkt», «Jobs bei …»).
_GENERIC_SEGMENT_RE = re.compile(
    r"^(?:rp\s+)?(?:stellenmarkt|stellenangebot\w*|stellenanzeige\w*|stellenbörse|jobbörse|jobs?|karriere|career\w*)\b",
    re.IGNORECASE)
_PLACE_SEGMENT_RE = re.compile(
    r"(?:köln|düsseldorf|hamburg|berlin|münchen|frankfurt(?: am main)?|stuttgart|nrw|wuppertal|essen|bonn"
    r"|dortmund|duisburg|leverkusen|neuss|deutschland|germany|remote|home-?office|hybrid)"
    r"(?:\s*[,/]\s*[\wäöüß .-]+)?", re.IGNORECASE)
# Not a company: a job reference («JN-102026-7000001»), a place, the hours or the contract.
_NOT_COMPANY_RE = re.compile(
    r"^(?:[A-Z]{1,5}[-_ ]?\d[\d-]{3,}|standort\b.*|(?:voll|teil)zeit\b.*|minijob\b.*|(?:un)?befristet\b.*"
    r"|ab sofort\b.*|.*\((?:m/w/d|w/m/d|d/m/w|m/w/x|all genders?)\).*)$", re.IGNORECASE)
_EXTRA_BOARD_NAMES = {"bundesagenturfürarbeit", "jobbörse", "stellenwerk", "joinch", "ziprecruiter"}


def _board_names() -> set[str]:
    # Import here: job_title stays free of other bot modules at import time (see the module docstring).
    from bot.generators.employer_scan import NON_EMPLOYER_DOMAINS
    return {re.sub(r"[^a-z0-9]", "", d.rsplit(".", 1)[0]) for d in NON_EMPLOYER_DOMAINS} | _EXTRA_BOARD_NAMES


def _is_board(segment: str) -> bool:
    key = re.sub(r"\.(?:de|com|net|org|ch|at|co)$", "", segment.strip().lower())
    return re.sub(r"[^a-z0-9äöüß]", "", key) in _board_names()


def _company_ok(text: str) -> bool:
    t = text.strip()
    return len(t) >= 2 and not _NOT_COMPANY_RE.match(t) and not _PLACE_SEGMENT_RE.fullmatch(t) and not _is_board(t)


def split_page_title(raw: str) -> tuple[str, str | None]:
    """(role, company or None) from a page title. Board and section names after « | » go, a place
    segment goes, the employer named after «bei» (with a legal form), «Karriere bei» or a dash
    (the dash only when the title had a « | » suffix) is the company, else the last other segment.
    A title that names no company comes back unchanged with None."""
    text = " ".join((raw or "").split())
    parts = [p.strip() for p in re.split(r"\s+\|\s+", text)]
    head, tails = parts[0], parts[1:]
    employer_tails = [t for t in tails
                      if not (_is_board(t) or _GENERIC_SEGMENT_RE.match(t) or _PLACE_SEGMENT_RE.fullmatch(t))]
    company = None
    m = _KARRIERE_BEI_RE.search(head)
    if m and _company_ok(m.group("c")):
        company, head = m.group("c").strip(), head[:m.start()].rstrip(" -–|")
    if company is None:
        m = _BEI_COMPANY_RE.search(head)
        if m and _company_ok(m.group("c")):
            company = m.group("c").strip()
    if company is None and tails:
        m = _DASH_COMPANY_RE.match(head)
        if m and _company_ok(m.group("company")):
            head, company = m.group("title").strip(), m.group("company").strip()
    if company is None and employer_tails:
        last = employer_tails[-1]
        # «Neuss, Deutschland - Muster GmbH»: the last place of a location list glued to the employer.
        m = re.match(r"^(?P<place>[^-–]+?)\s+[-–]\s+(?P<rest>.+)$", last)
        if m and (_PLACE_SEGMENT_RE.fullmatch(m.group("place"))
                  or re.search(r",\s*(?:Deutschland|Germany)$", m.group("place"), re.IGNORECASE)):
            last = m.group("rest")
        if _company_ok(last):
            company = last
    if company is None and not tails:
        return raw.strip(), None
    return head, company
