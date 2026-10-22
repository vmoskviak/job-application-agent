"""Firecrawl як останній рубіж фетчу.

Вечір 26.07: три інтейки karriere.nrw віддали JS-обгортку, score 12, 0 selected.
Playwright там не рятує (SPA за cookie/JS-wall), а firecrawl зі stealth-проксі
віддає той самий контент повністю. Дьоргаємо лише коли дешевші шляхи здались:
кожен виклик коштує кредити.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import bot.scrapers.firecrawl as fc
from bot.scrapers.models import PageFetch

JOB_MARKDOWN = "# Sachbearbeiter (m/w/d)\n\n" + ("Ihre Aufgaben: Antragsbearbeitung. " * 80)
NAV_ONLY = "Navigation überspringen\nJobs finden\nImpressum"


class FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def json(self) -> dict:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def test_needs_rescue_on_nav_only_body():
    page = PageFetch(url="https://x", text=NAV_ONLY, html="", method="playwright", status_code=200)
    assert fc.needs_rescue(page) is True


def test_no_rescue_for_a_full_page():
    page = PageFetch(url="https://x", text=JOB_MARKDOWN, html="", method="playwright", status_code=200)
    assert fc.needs_rescue(page) is False


def test_no_rescue_without_api_key(monkeypatch):
    monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)
    assert fc.api_key() is None


def test_scrape_maps_markdown_and_html(monkeypatch):
    captured = {}

    def fake_post(url, json, headers, timeout):
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        return FakeResponse({
            "success": True,
            "data": {
                "markdown": JOB_MARKDOWN,
                "html": "<html><body>ok</body></html>",
                "metadata": {"statusCode": 200},
            },
        })

    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-test")
    monkeypatch.setattr(fc.httpx, "post", fake_post)

    page = fc.fetch_firecrawl("https://karriere.nrw/stelle/1")
    assert page.method == "firecrawl"
    assert page.status_code == 200
    assert page.text == JOB_MARKDOWN
    assert page.html == "<html><body>ok</body></html>"
    assert captured["url"] == "https://api.firecrawl.dev/v2/scrape"
    assert captured["headers"]["Authorization"] == "Bearer fc-test"
    assert captured["json"]["url"] == "https://karriere.nrw/stelle/1"
    assert "html" in captured["json"]["formats"]


def test_scrape_without_key_returns_empty_page(monkeypatch):
    monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)

    def boom(*a, **kw):
        raise AssertionError("не можна ходити в API без ключа")

    monkeypatch.setattr(fc.httpx, "post", boom)
    page = fc.fetch_firecrawl("https://karriere.nrw/stelle/1")
    assert page.text == ""
    assert page.status_code is None


def test_network_error_degrades_quietly(monkeypatch):
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-test")

    def boom(*a, **kw):
        raise RuntimeError("connreset")

    monkeypatch.setattr(fc.httpx, "post", boom)
    assert fc.fetch_firecrawl("https://x").text == ""


def test_fetch_page_rescues_a_junk_browser_result(monkeypatch):
    from bot.scrapers import fetch as fetch_mod

    monkeypatch.setattr(fetch_mod, "needs_playwright", lambda url: True)
    monkeypatch.setattr(
        "bot.scrapers.browser.fetch_with_browser",
        lambda url, settle_ms=0: (NAV_ONLY, "<html></html>"),
    )
    rescued = PageFetch(url="https://karriere.nrw/x", text=JOB_MARKDOWN, html="",
                        method="firecrawl", status_code=200)
    monkeypatch.setattr(fetch_mod, "fetch_firecrawl", lambda url: rescued)

    page = fetch_mod.fetch_page("https://karriere.nrw/x")
    assert page.method == "firecrawl"
    assert page.text == JOB_MARKDOWN


def test_fetch_page_keeps_browser_result_when_it_is_good(monkeypatch):
    from bot.scrapers import fetch as fetch_mod

    monkeypatch.setattr(fetch_mod, "needs_playwright", lambda url: True)
    monkeypatch.setattr(
        "bot.scrapers.browser.fetch_with_browser",
        lambda url, settle_ms=0: (JOB_MARKDOWN, "<html></html>"),
    )

    def boom(url):
        raise AssertionError("firecrawl не мав дьоргатись на здоровій сторінці")

    monkeypatch.setattr(fetch_mod, "fetch_firecrawl", boom)

    page = fetch_mod.fetch_page("https://www.xing.com/jobs/x")
    assert page.method == "playwright"
