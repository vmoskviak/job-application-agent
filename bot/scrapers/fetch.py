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
)

# Кожен браузерний фетч чекає стабілізації body-тексту (див. settle_body):
# цикл виходить на першому такті без приросту, тож на швидкій сторінці це
# коштує одну зайву секунду, а не весь бюджет. Домени-винятки — повільні SPA.
DEFAULT_SETTLE_MS = 6000
SETTLE_DOMAINS = {
    "linkedin.com": 15000,
    # XING рендерить тіло вакансії довше за LinkedIn-шапку; з нулем сюди
    # приїжджало 400 символів навігації і score 0 (регресія 27.07).
    "xing.com": 12000,
}


def settle_ms_for(url: str) -> int:
    return next((ms for d, ms in SETTLE_DOMAINS.items() if d in url), DEFAULT_SETTLE_MS)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
}


def needs_playwright(url: str) -> bool:
    return any(d in url for d in PLAYWRIGHT_DOMAINS)


def _html_to_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "header", "footer"]):
        tag.decompose()
    return soup.get_text(separator="\n", strip=True)


def fetch_httpx(url: str, timeout: float = 15.0) -> PageFetch:
    try:
        resp = httpx.get(url, headers=HEADERS, timeout=timeout, follow_redirects=True)
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
    if not needs_playwright(url):
        page = fetch_httpx(url)
        if page.status_code == 200 and len(page.text) >= MIN_HTTPX_TEXT:
            status, _ = check_liveness(page.text, url)
            if status != "blocked":
                return page

    if not use_playwright:
        return fetch_httpx(url)

    from bot.scrapers.browser import fetch_with_browser

    text, html = fetch_with_browser(url, settle_ms=settle_ms_for(url))
    page = PageFetch(url=url, text=text, html=html, method="playwright", status_code=200)
    if not needs_rescue(page):
        return page

    # Платний шлях: сюди доходять лише сторінки, де браузер із живим профілем
    # віддав шапку або стіну (JS/cookie-wall karriere.nrw, interamt).
    rescued = fetch_firecrawl(url)
    return rescued if len(rescued.text) > len(page.text) else page
