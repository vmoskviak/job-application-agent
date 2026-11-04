"""The employer's own page for a job a board hides: Indeed behind its captcha, a LinkedIn posting
without an apply link, a StepStone or XING posting without an employer link.

Perplexity names candidates (its answer and its citations). A candidate counts only when its page is
this job of this employer: the title's words in the page title, og:title or a heading, and the
company's name on the page or in the domain. A career overview leads one hop on, to the link that
names the job; without a candidate page the employer's usual career paths are tried. An email is kept
only when the page shows it. No API key, no answer or no match: None, and the caller keeps the lead as
it was (manual list or hand-off).
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from bot.apply.ats_detect import closed_in
from bot.apply.job_boards import employer_url_ok

log = logging.getLogger(__name__)

API_URL = "https://api.perplexity.ai/chat/completions"
KEY_ENV = "PERPLEXITY_API_KEY"
MODEL_ENV = "PERPLEXITY_MODEL"
DEFAULT_MODEL = "sonar"
USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")
CAREER_PATHS = ("karriere", "jobs", "stellenangebote", "career", "careers", "de/karriere", "unternehmen/karriere")
MAX_CANDIDATES = 8
SHARE_OF_TITLE = 0.7

SYSTEM = (
    "You find the employer's own job posting for a German job ad. Search the employer's career site or its "
    "applicant tracking system (softgarden, personio, onlyfy, concludis, d.vinci, SuccessFactors, Workday, "
    "join.com, recruitee, Abacus …). Never return job boards or aggregators (Indeed, StepStone, LinkedIn, XING, "
    "Glassdoor, Monster, Kimeta, Jobware, meinestadt, Arbeitsagentur, HeyJobs). Return the page that shows THIS "
    "exact job (title, city), not a career overview and not another job. Answer with JSON only: "
    '{"url": "<the posting or application page on the employer side, or empty>", '
    '"email": "<the application email named in the posting, or empty>"}.')

_GENDER = re.compile(r"\((?:[mwdfxgn]\s*/\s*)+[mwdfxgn]\)|\(gn\*?\)|\(all genders?\)|\(alle geschlechter\)", re.IGNORECASE)
_STOP = {"der", "die", "das", "und", "fur", "mit", "zur", "zum", "von", "bei", "den", "dem", "des", "ein", "eine",
         "the", "and", "for", "with", "in", "im", "an", "am", "auf", "als", "oder", "teilzeit", "vollzeit",
         "remote", "befristet", "unbefristet", "w/m/d", "m/w/d"}
_LEGAL = {"gmbh", "mbh", "ag", "kg", "co", "ug", "ohg", "gbr", "ev", "e.v", "se", "partg", "mbb", "aor",
          "ggmbh", "kgaa", "inc", "ltd", "llc", "partner", "partners", "group", "gruppe", "holding"}
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
# Words that make another job of the same name (02.11.2026: «Trainee Assistenz der Geschäftsführung»
# passed for «Assistenz der Geschäftsführung»): one of them on the page but not in the title refuses it.
_QUALIFIERS = {"trainee", "werkstudent", "werkstudentin", "werkstudierende", "praktikum", "praktikant",
               "praktikantin", "ausbildung", "auszubildende", "auszubildender", "azubi", "duales", "dualer",
               "minijob", "aushilfe", "junior", "senior", "lead", "leitung", "leiter", "leiterin", "head",
               "teamleitung", "teamleiter", "volontariat", "intern", "internship", "student", "studentin"}
# Applicant tracking systems: one site per employer, so the company may stand only in the page body.
_ATS_DOMAINS = ("softgarden.io", "softgarden.de", "personio.de", "personio.com", "onlyfy.jobs", "concludis.de",
                "dvinci-hr.com", "successfactors.com", "successfactors.eu", "myworkdayjobs.com", "join.com",
                "recruitee.com", "abaservices.ch", "b-ite.com", "b-ite.net", "rexx-systems.com", "umantis.com",
                "jobbase.io", "smartrecruiters.com", "greenhouse.io", "lever.co", "ashbyhq.com", "workable.com",
                "prescreen.io", "talentsconnect.com", "pi-asp.de", "persis.de")


@dataclass(frozen=True)
class Posting:
    url: str
    email: str = ""
    source: str = ""   # answer | citation | overview | career_path


def _fold(text: str) -> str:
    """Lower case without accents, ß as ss: «Geschäftsführung» and «Geschaeftsfuehrung» meet."""
    t = (text or "").replace("ß", "ss").replace("ä", "ae").replace("ö", "oe").replace("ü", "ue")
    t = t.replace("Ä", "ae").replace("Ö", "oe").replace("Ü", "ue")
    t = unicodedata.normalize("NFKD", t)
    return "".join(c for c in t if not unicodedata.combining(c)).lower()


def title_words(title: str) -> list[str]:
    """The words of a job title that name the job: no gender marker, no filler, no hours."""
    words = re.findall(r"[a-z0-9]+", _fold(_GENDER.sub(" ", title or "")))
    return [w for w in dict.fromkeys(words) if len(w) >= 3 and w not in _STOP]


def company_words(company: str) -> list[str]:
    words = re.findall(r"[a-z0-9]+", _fold(company or ""))
    return [w for w in dict.fromkeys(words) if len(w) >= 3 and w not in _LEGAL and w not in _STOP]


def _covers(words: list[str], text: str) -> bool:
    if not words:
        return False
    hay = _fold(text)
    found = sum(1 for w in words if re.search(rf"(?<![a-z0-9]){re.escape(w)}", hay))
    need = len(words) if len(words) <= 2 else math.ceil(SHARE_OF_TITLE * len(words))
    return found >= need


def _heads(soup: BeautifulSoup) -> str:
    """What names the page: <title>, og:title, h1–h2 and a JSON-LD JobPosting title."""
    parts = [soup.title.get_text(" ", strip=True) if soup.title else ""]
    og = soup.find("meta", attrs={"property": "og:title"})
    if og and og.get("content"):
        parts.append(og["content"])
    parts += [h.get_text(" ", strip=True) for h in soup.find_all(["h1", "h2"])[:12]]
    for s in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            data = json.loads(s.string or "")
        except (ValueError, TypeError):
            continue
        for item in data if isinstance(data, list) else [data]:
            if isinstance(item, dict) and item.get("title"):
                parts.append(str(item["title"]))
    return " | ".join(p for p in parts if p)


def _other_role(text: str, title: str) -> bool:
    """The text names a role qualifier the title has not (a trainee or senior posting of the same name)."""
    mine = set(re.findall(r"[a-z]+", _fold(title)))
    return any(q in set(re.findall(r"[a-z]+", _fold(text))) - mine for q in _QUALIFIERS)


def _ats_host(host: str) -> bool:
    return any(host == d or host.endswith("." + d) for d in _ATS_DOMAINS)


def _names_company(words: list[str], text: str) -> bool:
    hay = _fold(text)
    return any(re.search(rf"(?<![a-z0-9]){re.escape(w)}", hay) for w in words)


def page_is_job(html: str, url: str, title: str, company: str) -> bool:
    """This job of this employer: the title in what names the page and no other role qualifier there,
    the company in the domain or in what names the page (on an ATS also in the body), no closed marker."""
    soup = BeautifulSoup(html or "", "html.parser")
    heads = _heads(soup)
    if not _covers(title_words(title), heads) or _other_role(_first_head(soup) or heads, title):
        return False
    text = soup.get_text(" ", strip=True)
    if closed_in(text):
        return False
    words = company_words(company)
    host = (urlparse(url).hostname or "").lower()
    if not words or any(w in _fold(host) for w in words) or _names_company(words, heads):
        return True
    return _ats_host(host) and _names_company(words, text)


def _first_head(soup: BeautifulSoup) -> str:
    """The page's own name for the job: the first h1, else og:title, else <title>."""
    h1 = soup.find("h1")
    if h1 and h1.get_text(strip=True):
        return h1.get_text(" ", strip=True)
    og = soup.find("meta", attrs={"property": "og:title"})
    if og and og.get("content"):
        return og["content"]
    return soup.title.get_text(" ", strip=True) if soup.title else ""


def overview_link(html: str, base: str, title: str) -> str | None:
    """On a career overview, the link whose text names the job (an employer URL)."""
    soup = BeautifulSoup(html or "", "html.parser")
    words = title_words(title)
    for a in soup.find_all("a", href=True):
        url = urljoin(base, a["href"])
        if url.split("#")[0] == base.split("#")[0] or not employer_url_ok(url):
            continue
        text = a.get_text(" ", strip=True) or a.get("title") or a.get("aria-label") or ""
        if _covers(words, text) and not _other_role(text, title):
            return url
    return None


def fetch_html(url: str) -> str | None:
    import httpx

    try:
        r = httpx.get(url, headers={"User-Agent": USER_AGENT}, follow_redirects=True, timeout=20)
        return r.text if r.status_code == 200 else None
    except Exception:  # noqa: BLE001 - the page is simply not there
        return None


def ask_perplexity(company: str, title: str, city: str, api_key: str) -> tuple[dict, list[str]]:
    """Perplexity's answer as {url, email} and its citations; ({}, []) on any failure."""
    import httpx

    try:
        r = httpx.post(API_URL, timeout=90, headers={"Authorization": f"Bearer {api_key}"},
                       json={"model": os.environ.get(MODEL_ENV, DEFAULT_MODEL), "temperature": 0,
                             "web_search_options": {"search_context_size": "high"},
                             "messages": [{"role": "system", "content": SYSTEM},
                                          {"role": "user", "content": f"Firma: {company}\nStelle: {title}\nOrt: {city}"}]})
        r.raise_for_status()
        data = r.json()
    except Exception:
        log.warning("perplexity lookup failed for %s / %s", company, title, exc_info=True)
        return {}, []
    content = ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    m = re.search(r"\{.*\}", content, re.DOTALL)
    try:
        answer = json.loads(m.group(0)) if m else {}
    except ValueError:
        answer = {}
    cites = [str(c) for c in data.get("citations") or []]
    cites += [str(s.get("url")) for s in data.get("search_results") or [] if isinstance(s, dict) and s.get("url")]
    return (answer if isinstance(answer, dict) else {}), cites


def find_posting(company: str, title: str, city: str = "", *, api_key: str | None = None,
                 fetch: Callable[[str], str | None] = fetch_html,
                 ask: Callable[[str, str, str, str], tuple[dict, list[str]]] = ask_perplexity) -> Posting | None:
    """The employer's page for this job, or None (see the module docstring)."""
    key = api_key or os.environ.get(KEY_ENV, "")
    if not key or not (company or "").strip() or not title_words(title):
        return None
    answer, cites = ask(company, title, city, key)
    email_hint = str(answer.get("email") or "").strip().lower()
    seen: set[str] = set()
    candidates = [(str(answer.get("url") or ""), "answer")] + [(c, "citation") for c in cites]
    domains: list[str] = []

    def accept(url: str, html: str, source: str) -> Posting:
        email = email_hint if email_hint and email_hint in html.lower() else ""
        return Posting(url, email, source)

    for url, source in candidates[:MAX_CANDIDATES]:
        url = url.strip()
        if not url or url in seen or not employer_url_ok(url):
            continue
        seen.add(url)
        host = (urlparse(url).hostname or "").lower()
        if host and host not in domains:
            domains.append(host)
        html = fetch(url)
        if not html:
            continue
        if page_is_job(html, url, title, company):
            return accept(url, html, source)
        link = overview_link(html, url, title)
        if link and link not in seen:
            seen.add(link)
            sub = fetch(link)
            if sub and page_is_job(sub, link, title, company):
                return accept(link, sub, "overview")
    for host in domains[:2]:
        if not any(w in _fold(host) for w in company_words(company)):
            continue   # a career path only on the employer's own domain
        for path in CAREER_PATHS:
            url = f"https://{host}/{path}"
            if url in seen:
                continue
            seen.add(url)
            html = fetch(url)
            link = overview_link(html, url, title) if html else None
            if link and link not in seen:
                seen.add(link)
                sub = fetch(link)
                if sub and page_is_job(sub, link, title, company):
                    return accept(link, sub, "career_path")
    return None
