"""Firecrawl scrape API — останній рубіж, коли httpx і Playwright віддали порожньо.

Playwright не бере сторінки, які ховаються за cookie/JS-wall до взаємодії
(karriere.nrw, interamt), а firecrawl зі stealth-проксі віддає їх повністю.
Кожен виклик коштує кредити, тож ходимо сюди тільки після невдачі дешевших
шляхів — див. needs_rescue().

Ключ: FIRECRAWL_API_KEY (bot/.env). Без ключа модуль просто мовчить.
"""

from __future__ import annotations

import os

import httpx

from bot.scrapers.browser import MIN_BODY_CHARS
from bot.scrapers.liveness import check_liveness
from bot.scrapers.models import PageFetch

API_URL = "https://api.firecrawl.dev/v2/scrape"
TIMEOUT_SEC = 90.0


def api_key() -> str | None:
    return os.environ.get("FIRECRAWL_API_KEY") or None


def needs_rescue(page: PageFetch) -> bool:
    """Чи є сенс платити за firecrawl після браузера."""
    if len(page.text) < MIN_BODY_CHARS:
        return True

    status, _ = check_liveness(page.text, page.url)
    return status == "blocked"


def fetch_firecrawl(url: str) -> PageFetch:
    key = api_key()
    if not key:
        return PageFetch(url=url, text="", html="", method="firecrawl", status_code=None)

    try:
        resp = httpx.post(
            API_URL,
            json={
                "url": url,
                "formats": ["markdown", "html"],
                "onlyMainContent": True,
                # anti-bot вмикається сам, коли базовий проксі впирається у стіну
                "proxy": "auto",
                "timeout": 60000,
            },
            headers={"Authorization": f"Bearer {key}"},
            timeout=TIMEOUT_SEC,
        )
        resp.raise_for_status()
        payload = resp.json()
    except Exception as exc:  # мережа/ліміт/кредити — деградуємо до порожньої сторінки
        print(f"[firecrawl] {url[:70]}… не вдалось: {exc}")
        return PageFetch(url=url, text="", html="", method="firecrawl", status_code=None)

    data = payload.get("data") or {}
    return PageFetch(
        url=url,
        text=data.get("markdown") or "",
        html=data.get("html") or "",
        method="firecrawl",
        status_code=(data.get("metadata") or {}).get("statusCode"),
    )
