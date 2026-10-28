from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

import httpx
from bs4 import BeautifulSoup

from bot.scrapers.firecrawl import fetch_firecrawl, needs_rescue
from bot.scrapers.liveness import check_liveness
from bot.scrapers.models import PageFetch

PLAYWRIGHT_DOMAINS = (
    "stepstone.de",
    "linkedin.com",
    "xing.com",
    "indeed.com",
    "indeed.de",
    # Angular-SPA Randstad Professional: httpx віддає лише оболонку
    "gulp.de",
)

# Кожен браузерний фетч чекає стабілізації body-тексту (див. settle_body):
# цикл виходить на першому такті без приросту, тож на швидкій сторінці це
# коштує одну зайву секунду, а не весь бюджет. Домени-винятки — повільні SPA.
DEFAULT_SETTLE_MS = 6000
SETTLE_DOMAINS = {
    "linkedin.com": 15000,
    # GULP підвантажує проєкт через XHR після рендера оболонки: на 6 с приїжджала
    # лише шапка «powered by … Registrieren Anmelden» (регресія 21.10.2026)
    "gulp.de": 20000,
    # XING рендерить тіло вакансії довше за LinkedIn-шапку; з нулем сюди
    # приїжджало 400 символів навігації і score 0 (регресія 27.07).
    "xing.com": 12000,
}


def settle_ms_for(url: str) -> int:
    return next((ms for d, ms in SETTLE_DOMAINS.items() if d in url), DEFAULT_SETTLE_MS)


def _on_host(url: str, hosts) -> str | None:
    """Домен зі списку, якому належить хост URL (сам домен чи піддомен), або None."""
    host = (urlparse(url).hostname or "").lower()
    return next((h for h in hosts if host == h or host.endswith("." + h)), None)


# Під логіном (профіль браузера з сесією): firecrawl ходить без сесії і віддав би
# стіну входу чи гостьову версію, тож порятунок тут лише псує текст і коштує гроші.
LOGGED_IN_HOSTS = ("gulp.de", "linkedin.com", "xing.com", "freelancermap.de")


def gulp_ready(text: str) -> bool:
    """Сторінка проєкту GULP відрендерилась: є номер проєкту, а не лише оболонка."""
    return "Job ID" in text or "Projekt-ID" in text


# Додаткова умова готовності body для settle_body, за доменом.
READY_FOR = {"gulp.de": gulp_ready}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
}


# Гостьова сторінка вакансії LinkedIn: plain HTTP віддає повну розмітку (top-card,
# критерії, опис), тоді як headless-браузер без профілю впирається в cookie-банер
# або Cloudflare «Sorry, you have been blocked» (сервер, 21.10.2026). Англійська
# локаль: критерій «Employment type: Contract» замість двозначного «Zeitarbeit».
_LINKEDIN_JOB_VIEW_RE = re.compile(r"linkedin\.com/jobs/view/", re.IGNORECASE)
LINKEDIN_GUEST_MARKER = "show-more-less-html__markup"
LINKEDIN_GUEST_HEADERS = {**HEADERS, "Accept-Language": "en-US,en;q=0.9"}


def is_linkedin_job_view(url: str) -> bool:
    return bool(_LINKEDIN_JOB_VIEW_RE.search(url))


def linkedin_guest_text(html: str) -> str:
    """Текст гостьової сторінки з конкретних елементів: назва, компанія, місто,
    банер закритої вакансії, критерії, опис. Модалка «Join or sign in …» сидить
    усередині top-card, тож цілі секції брати не можна: liveness бачила б login
    wall на живій вакансії."""
    soup = BeautifulSoup(html, "html.parser")
    parts: list[str] = []
    for sel in (".top-card-layout__title", "h1", ".topcard__org-name-link",
                "a[data-tracking-control-name='public_jobs_topcard-org-name']",
                ".topcard__flavor--bullet", ".posted-time-ago__text", ".closed-job",
                ".top-card-layout__cta-container figcaption"):
        el = soup.select_one(sel)
        if el is not None:
            txt = el.get_text(" ", strip=True)
            if txt and txt not in parts:
                parts.append(txt)
    for item in soup.select(".description__job-criteria-list li"):
        parts.append(item.get_text(": ", strip=True))
    markup = soup.select_one(LINKEDIN_GUEST_MARKER and ".show-more-less-html__markup")
    if markup is not None:
        parts.append(markup.get_text("\n", strip=True))
    return "\n".join(parts)


def needs_playwright(url: str) -> bool:
    return any(d in url for d in PLAYWRIGHT_DOMAINS)


def _html_to_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "header", "footer"]):
        tag.decompose()
    return soup.get_text(separator="\n", strip=True)


def fetch_httpx(url: str, timeout: float = 15.0, headers: dict | None = None) -> PageFetch:
    try:
        resp = httpx.get(url, headers=headers or HEADERS, timeout=timeout, follow_redirects=True)
        html = resp.text if resp.status_code == 200 else ""
        text = _html_to_text(html) if html else ""
        return PageFetch(url=url, text=text, html=html, method="httpx", status_code=resp.status_code)
    except Exception:
        return PageFetch(url=url, text="", html="", method="httpx", status_code=None)


_XING_SHARE_RE = re.compile(r"^https?://(?:www\.)?xing\.com/m/", re.IGNORECASE)


def resolve_xing_share(url: str, timeout: float = 15.0) -> str:
    """xing.com/m/<id> з job-мейла → канонічний /jobs/-URL.

    Під браузерним User-Agent XING збиває цей редірект на login.xing.com і
    віддає порожній interstitial — саме через це ~55% XING-лідів приходили
    як «Unbekannt». Канонічний URL при цьому лишається в dest_url, а без UA
    XING віддає сторінку вакансії напряму. Не вдалось — повертаємо як було.
    """
    if not _XING_SHARE_RE.match(url):
        return url
    try:
        resp = httpx.get(url, timeout=timeout, follow_redirects=True)
    except Exception:
        return url

    final = str(resp.url)
    if "login.xing.com" in final:
        dest = parse_qs(urlparse(final).query).get("dest_url", [""])[0]
        return dest or url
    return final if "/jobs/" in final else url


# Коротший текст — це JS-шел (myjobboard.de віддає 200 і ~100 символів
# «Laden…»), а не вакансія: реального опису під цим порогом не буває.
MIN_HTTPX_TEXT = 300


def fetch_page(url: str, use_playwright: bool = True) -> PageFetch:
    url = resolve_xing_share(url)
    if is_linkedin_job_view(url):
        page = fetch_httpx(url, headers=LINKEDIN_GUEST_HEADERS)
        if page.status_code == 200 and LINKEDIN_GUEST_MARKER in page.html:
            page.text = linkedin_guest_text(page.html) or page.text
            return page
    if not needs_playwright(url):
        page = fetch_httpx(url)
        if page.status_code == 200 and len(page.text) >= MIN_HTTPX_TEXT:
            status, _ = check_liveness(page.text, url)
            if status != "blocked":
                return page

    if not use_playwright:
        return fetch_httpx(url)

    from bot.scrapers.browser import fetch_with_browser

    host = _on_host(url, READY_FOR)
    # ready лише там, де він є: решта викликів лишається з тим самим підписом.
    extra = {"ready": READY_FOR[host]} if host else {}
    text, html = fetch_with_browser(url, settle_ms=settle_ms_for(url), **extra)
    page = PageFetch(url=url, text=text, html=html, method="playwright", status_code=200)
    if _on_host(url, LOGGED_IN_HOSTS) or not needs_rescue(page):
        return page

    # Платний шлях: сюди доходять лише сторінки, де браузер із живим профілем
    # віддав шапку або стіну (JS/cookie-wall karriere.nrw, interamt).
    rescued = fetch_firecrawl(url)
    return rescued if len(rescued.text) > len(page.text) else page
