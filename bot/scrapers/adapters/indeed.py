"""Indeed: назва, компанія й місто з HTML, опис і решта — з тексту.

Indeed доходить до інтейку через firecrawl (Playwright упирається в стіну), а
його markdown починається з кнопки «Suchen». У HTML тієї ж відповіді назва
стоїть в окремому елементі (`data-testid="vj-job-title"`, у старій розмітці
`jobsearch-JobInfoHeader-title`), тож беремо її звідти, а не з першого рядка.
"""

from __future__ import annotations

import json
import re

from bs4 import BeautifulSoup

from bot.generators.intake import JobData, parse_text
from bot.scrapers.models import PageFetch
from bot.job_title import is_junk_company, is_junk_title

_TITLE_SELECTORS = (
    "[data-testid='vj-job-title']",
    "[data-testid='jobsearch-JobInfoHeader-title']",
    ".jobsearch-JobInfoHeader-title",
    "h1",
)
_COMPANY_SELECTORS = (
    "[data-testid='inlineHeader-companyName']",
    "[data-company-name]",
    "[data-testid='jobsearch-CompanyInfoContainer'] a[href*='/cmp/']",
    "a[href*='/cmp/']",
)
_LOCATION_SELECTORS = (
    "[data-testid='inlineHeader-companyLocation']",
    "[data-testid='jobsearch-JobInfoHeader-companyLocation']",
    "[data-testid='job-location']",
)
# Desktop-шапка дописує до назви прихований span «- job post»
_JOB_POST_SUFFIX_RE = re.compile(r"\s*-\s*job post\s*$", re.IGNORECASE)
_OG_SUFFIX_RE = re.compile(r"\s*[-|]\s*Indeed(?:\.com)?\s*$", re.IGNORECASE)


def _text(el) -> str:
    return re.sub(r"\s+", " ", el.get_text(" ", strip=True)).strip() if el else ""


def _json_ld_posting(soup: BeautifulSoup) -> dict:
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        for item in data if isinstance(data, list) else [data]:
            if isinstance(item, dict) and item.get("@type") == "JobPosting":
                return item
    return {}


def _title(soup: BeautifulSoup, posting: dict) -> str | None:
    candidates = [str(posting.get("title") or "")]
    candidates += [_text(soup.select_one(sel)) for sel in _TITLE_SELECTORS]
    og = soup.find("meta", property="og:title")
    if og and og.get("content"):
        # «<Назва> - <Місто> - Indeed.com»: місто відрізаємо разом із суфіксом
        stripped = _OG_SUFFIX_RE.sub("", og["content"]).strip()
        candidates.append(stripped.rsplit(" - ", 1)[0] if " - " in stripped else stripped)
    for raw in candidates:
        title = _JOB_POST_SUFFIX_RE.sub("", raw).strip()
        if title and not is_junk_title(title):
            return title
    return None


def _company(soup: BeautifulSoup, posting: dict) -> str | None:
    org = posting.get("hiringOrganization") or {}
    candidates = [str(org.get("name") or "") if isinstance(org, dict) else ""]
    for sel in _COMPANY_SELECTORS:
        el = soup.select_one(sel)
        if el is not None:
            candidates.append(el.get("data-company-name") or _text(el))
    for c in candidates:
        c = c.strip()
        # лінки «Gehaltslink für X» / «Salaries for X» теж ведуть на /cmp/
        if c and not is_junk_company(c) and not re.match(r"(?i)gehalt|salar", c):
            return c
    return None


def _location(soup: BeautifulSoup, posting: dict, company: str | None) -> str | None:
    loc = posting.get("jobLocation") or {}
    if isinstance(loc, list):
        loc = loc[0] if loc else {}
    address = (loc.get("address") or {}) if isinstance(loc, dict) else {}
    city = str(address.get("addressLocality") or "").strip() if isinstance(address, dict) else ""
    if city:
        return city
    for sel in _LOCATION_SELECTORS:
        txt = _text(soup.select_one(sel))
        if txt:
            return txt
    # Мобільна шапка: блок company-info-metadata = «<компанія> <місто>»,
    # місто — останній листовий div
    meta = soup.select_one("[data-testid='company-info-metadata']")
    if meta is not None:
        leaves = [_text(d) for d in meta.find_all("div") if not d.find("div") and _text(d)]
        leaves = [t for t in leaves if t != company]
        if leaves:
            return leaves[-1]
    return None


class IndeedAdapter:
    NAME = "indeed"

    def extract(self, page: PageFetch) -> JobData:
        job = parse_text(page.text, source_url=page.url)
        if not page.html:
            return job
        soup = BeautifulSoup(page.html, "html.parser")
        posting = _json_ld_posting(soup)
        title = _title(soup, posting)
        if title:
            job.title = title
        company = _company(soup, posting)
        if company:
            job.company = company
        location = _location(soup, posting, company)
        if location:
            job.location = location
        return job
