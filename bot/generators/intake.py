"""Job intake — URL scraper + text parser → JobData."""

from __future__ import annotations

import re
import sys
import httpx
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional
from bs4 import BeautifulSoup

from bot.textclean import strip_invisible

if TYPE_CHECKING:
    from bot.scrapers.models import IntakeResult

PORTAL_PATTERNS = [
    r"myworkdayjobs\.com",
    r"successfactors\.(eu|com)",
    r"softgarden\.de",
    r"rexx-systems\.de",
    r"recruitee\.com",
    r"greenhouse\.io",
    r"lever\.co",
]

EMAIL_PREFIXES = ["jobs", "bewerbung", "karriere", "hr", "personal",
                  "recruiting", "recruitment", "apply", "career"]


@dataclass
class JobData:
    title: str
    company: str
    location: str
    description: str
    email: Optional[str]
    application_url: Optional[str]
    source_url: str
    is_portal: bool


def extract_email(html_or_text: str, domain: str = "") -> Optional[str]:
    """Finds job application email in HTML or plain text."""
    mailto = re.search(
        r'mailto:([a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})',
        html_or_text
    )
    if mailto:
        return mailto.group(1)

    pattern = r'\b(' + '|'.join(EMAIL_PREFIXES) + r')[a-zA-Z0-9._\-]*@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}'
    match = re.search(pattern, html_or_text, re.IGNORECASE)
    if match:
        return match.group(0)

    if domain:
        parts = domain.split(".")
        if len(parts) >= 2:
            dom = re.escape(parts[-2] + "." + parts[-1])
            match = re.search(r'[a-zA-Z0-9._%+\-]+@' + dom, html_or_text, re.IGNORECASE)
            if match:
                return match.group(0)

    return None


def detect_portal(text: str) -> Optional[str]:
    """Returns portal URL if a known ATS portal link is found."""
    for pattern in PORTAL_PATTERNS:
        match = re.search(r'https?://[^\s"\']*' + pattern + r'[^\s"\']*', text, re.IGNORECASE)
        if match:
            return match.group(0)
    return None


_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]*\)")


# Заголовки сторінок регіональних Stellenmärkte: «<Роль> - <Firma> | RP Stellenmarkt»
# (jobs.rp-online.de, 08.10.2026: роль і фірма злипались в одне поле, company
# ставав копією title). Суфікс порталу відрізаємо, фірму з тире виймаємо.
_PORTAL_SUFFIX_RE = re.compile(
    r"\s*\|\s*(?:RP\s+Stellenmarkt|Stellenmarkt[^|]*|Jobs?\s*(?:bei|auf)?\s*[^|]*|Karriere[^|]*)\s*$",
    re.IGNORECASE,
)
_TITLE_COMPANY_RE = re.compile(r"^(?P<title>.+?)\s+[-–]\s+(?P<company>[^-–|]{3,}?)\s*$")


def split_portal_title(raw: str) -> tuple[str, str | None]:
    """(«Teamassistenz Personalwesen (m/w/d) Vollzeit / Teilzeit», «Beispiel Kultur GmbH»)
    з «Teamassistenz Personalwesen (m/w/d) Vollzeit / Teilzeit - Beispiel Kultur GmbH | RP Stellenmarkt».
    Без суфікса порталу нічого не чіпає: тире всередині назв ролей звичне."""
    stripped = _PORTAL_SUFFIX_RE.sub("", raw).strip()
    if stripped == raw.strip():
        return raw.strip(), None
    m = _TITLE_COMPANY_RE.match(stripped)
    if not m:
        return stripped, None
    return m.group("title").strip(), m.group("company").strip()


def parse_text(text: str, source_url: str = "") -> JobData:
    """Parses raw job description text into JobData."""
    text = strip_invisible(text)  # м'які переноси ламають і назву, і фільтри
    lines = [l.strip() for l in text.strip().splitlines() if l.strip()]
    title = lines[0] if lines else "Unbekannte Stelle"
    # Firecrawl-markdown (Indeed тощо) починається з шапки сайту («WasWoJobs
    # finden»), а справжня назва — в першому H1 нижче.
    for line in lines[:10]:
        if line.startswith("# "):
            title = line[2:].strip()
            break

    company = ""
    location = ""
    for line in lines[:15]:
        stripped = _MD_LINK_RE.sub(r"\1", line)
        # Indeed-markdown: компанія — лінк на власну /cmp/-сторінку,
        # часто без правової форми в тексті ("Beispiel Sprachen").
        md_link = _MD_LINK_RE.fullmatch(line)
        if md_link and "/cmp/" in line and not company:
            company = md_link.group(1).strip()
        if re.search(r'\b(GmbH|AG|SE|KG|mbH|e\.V\.|gGmbH)\b', stripped) and not company:
            company = stripped
        if re.search(
            r'\b(Düsseldorf|Köln|Hamburg|Berlin|München|Frankfurt|NRW|Wuppertal|Essen|Bonn|Dortmund)\b',
            stripped, re.IGNORECASE
        ) and not location:
            location = stripped

    title, company_from_title = split_portal_title(title)
    if company_from_title and (not company or company == title or len(company) > 80):
        company = company_from_title
    if location and (location == title or len(location) > 80):
        location = ""  # у RP-Stellenmarkt перший рядок із «Köln» був самим заголовком

    domain = re.search(r'https?://([^/]+)', source_url)
    domain_str = domain.group(1) if domain else ""

    email = extract_email(text, domain_str)
    portal_url = detect_portal(text)

    return JobData(
        title=title,
        company=company or "Unbekannt",
        location=location or "Deutschland",
        description=text[:3000],
        email=email,
        application_url=portal_url,
        source_url=source_url,
        is_portal=portal_url is not None,
    )


def scrape_url(url: str) -> Optional[str]:
    """Fetches job description text via httpx only (backward compat)."""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
    }
    try:
        resp = httpx.get(url, headers=headers, timeout=15, follow_redirects=True)
        if resp.status_code != 200:
            return None
        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style", "nav", "header", "footer"]):
            tag.decompose()
        return soup.get_text(separator="\n", strip=True)
    except Exception:
        return None


def intake(
    url: Optional[str] = None,
    text: Optional[str] = None,
    use_playwright: bool = True,
) -> Optional[JobData]:
    """Main entry point. Returns JobData or None if no input available."""
    if url:
        from bot.scrapers.intake_pipeline import intake_url as _intake_url

        result = _intake_url(url, use_playwright=use_playwright)
        if result.job is None:
            print(
                f"[intake] {result.liveness}: {url[:80]} — {result.reason}",
                file=sys.stderr,
            )
            return None
        return result.job
    if text:
        return parse_text(text, source_url=url or "")
    return None


def intake_with_meta(url: str, use_playwright: bool = True) -> IntakeResult:
    from bot.scrapers.intake_pipeline import intake_url as _intake_url

    return _intake_url(url, use_playwright=use_playwright)
