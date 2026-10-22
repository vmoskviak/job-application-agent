import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from bot.scrapers.fetch import fetch_httpx, needs_playwright


def test_needs_playwright_for_linkedin():
    assert needs_playwright("https://www.linkedin.com/jobs/view/123") is True


def test_needs_playwright_for_stepstone():
    assert needs_playwright("https://www.stepstone.de/stellenangebote--x--1-inline.html") is True


def test_needs_playwright_false_for_interamt():
    assert needs_playwright("https://interamt.de/koop/app/stelle/1000006") is False


def test_fetch_httpx_generic_site(monkeypatch):
    import httpx

    body = "<html><body>" + "<p>Aufgaben Koordination Büro</p>" * 20 + "</body></html>"

    def fake_get(url, **kwargs):
        return httpx.Response(200, text=body, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "get", fake_get)
    page = fetch_httpx("https://www.muster-partner.example/jobs.php?refnr=345641")
    assert page.method == "httpx"
    assert page.status_code == 200
    assert len(page.text) > 200


def test_settle_ms_per_domain():
    from bot.scrapers.fetch import DEFAULT_SETTLE_MS, settle_ms_for
    assert settle_ms_for("https://www.linkedin.com/jobs/view/123") == 15000
    assert settle_ms_for("https://www.xing.com/jobs/duesseldorf-assistenz-1567") == 12000
    # решта доменів більше не отримує 0: без settle зріз ловив шапку SPA
    assert settle_ms_for("https://www.stepstone.de/stelle/x") == DEFAULT_SETTLE_MS


def test_httpx_shell_falls_back_to_playwright(monkeypatch):
    # myjobboard.de (Kanzlei) віддає 200 з JS-шелом на ~100 символів — httpx
    # «успішний», а опису нема; такий короткий текст мусить іти в браузер
    # (регресія 29.07: 3 Kanzlei-ліди впали в unknown без фолбеку)
    import bot.scrapers.fetch as fetch
    from bot.scrapers.models import PageFetch

    monkeypatch.setattr(fetch, "fetch_httpx", lambda url, timeout=15.0: PageFetch(
        url=url, text="Stellenangebote Laden...", html="<div id=app></div>",
        method="httpx", status_code=200))
    called = {}

    def fake_browser(url, settle_ms=0):
        called["yes"] = True
        return ("Teamassistenz w/m/d " + "opis " * 100, "<html>full</html>")

    import bot.scrapers.browser as browser
    monkeypatch.setattr(browser, "fetch_with_browser", fake_browser)
    page = fetch.fetch_page("https://myjobboard.de/kanzlei/Stellenangebote/abc")
    assert called.get("yes") and page.method == "playwright"
