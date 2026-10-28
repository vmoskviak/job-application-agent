import sys
from pathlib import Path

import pytest

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
    from bot.scrapers import fetch
    from bot.scrapers.models import PageFetch

    monkeypatch.setattr(fetch, "fetch_httpx", lambda url, timeout=15.0: PageFetch(
        url=url, text="Stellenangebote Laden...", html="<div id=app></div>",
        method="httpx", status_code=200))
    called = {}

    def fake_browser(url, settle_ms=0):
        called["yes"] = True
        return ("Teamassistenz w/m/d " + "opis " * 100, "<html>full</html>")

    from bot.scrapers import browser
    monkeypatch.setattr(browser, "fetch_with_browser", fake_browser)
    page = fetch.fetch_page("https://myjobboard.de/kanzlei/Stellenangebote/abc")
    assert called.get("yes") and page.method == "playwright"


def test_linkedin_job_view_prefers_httpx_guest_page(monkeypatch):
    # Сервер без LinkedIn-профілю (contract market, 21.10.2026): headless-браузер
    # діставав cookie-банер або Cloudflare-стіну, а plain HTTP віддає гостьову
    # сторінку з повним описом. Браузер тут не потрібен узагалі.
    from bot.scrapers import fetch
    from bot.scrapers.models import PageFetch

    seen = {}

    guest_html = (
        '<html><body><nav>Skip to main content Sign in Join now</nav>'
        '<section class="top-card-layout"><h1 class="top-card-layout__title">Senior Android Engineer</h1>'
        '<a class="topcard__org-name-link">Acme</a></section>'
        '<div class="sign-in-modal">Sign in to save this job. Email or phone Password</div>'
        '<section class="description"><div class="show-more-less-html__markup">'
        + "Kotlin desc " * 40 + '</div></section></body></html>'
    )

    def fake_httpx(url, timeout=15.0, headers=None):
        seen["headers"] = headers or {}
        return PageFetch(url=url, text="Skip to main content Sign in Join now Senior Android Engineer " + "Kotlin desc " * 40,
                         html=guest_html, method="httpx", status_code=200)

    monkeypatch.setattr(fetch, "fetch_httpx", fake_httpx)
    from bot.scrapers import browser
    monkeypatch.setattr(browser, "fetch_with_browser",
                        lambda url, settle_ms=0: (_ for _ in ()).throw(AssertionError("browser called")))
    page = fetch.fetch_page("https://www.linkedin.com/jobs/view/4480970911")
    assert page.method == "httpx"
    # текст без модалки входу: liveness інакше бачить login wall на живій вакансії
    from bot.scrapers.liveness import check_liveness
    assert check_liveness(page.text, page.url)[0] == "live"
    assert "Senior Android Engineer" in page.text and "Kotlin desc" in page.text
    assert "Sign in" not in page.text and "Email or phone" not in page.text
    # англійська версія: критерій «Employment type: Contract», не «Zeitarbeit»
    assert seen["headers"].get("Accept-Language", "").startswith("en")


def test_linkedin_job_view_falls_back_to_browser_without_guest_markup(monkeypatch):
    from bot.scrapers import fetch
    from bot.scrapers.models import PageFetch

    monkeypatch.setattr(fetch, "fetch_httpx", lambda url, timeout=15.0, headers=None: PageFetch(
        url=url, text="LinkedIn Sign in " * 50, html="<html>authwall</html>", method="httpx", status_code=200))
    from bot.scrapers import browser
    monkeypatch.setattr(browser, "fetch_with_browser",
                        lambda url, settle_ms=0: ("About the job\n" + "opis " * 100, "<html>full</html>"))
    page = fetch.fetch_page("https://www.linkedin.com/jobs/view/4480970911")
    assert page.method == "playwright"


def test_gulp_project_pages_need_browser_with_long_settle():
    # GULP (Randstad Professional) — Angular-SPA: httpx віддає оболонку, а браузер
    # на 6 с лише шапку «powered by … Registrieren Anmelden» (регресія 21.10.2026);
    # повний текст проєкту з'являється через ~15–20 с.
    from bot.scrapers.fetch import settle_ms_for
    url = "https://www.gulp.de/gulp2/g/projekte/agentur/C00000001"
    assert needs_playwright(url) is True
    assert settle_ms_for(url) >= 20000


# --- GULP: оболонка без проєкту і хости під логіном (2d) ---

# Залогінена оболонка GULP: шапка, меню, футер, понад MIN_BODY_CHARS і не росте,
# а проєкту (Job ID) ще нема. Раніше settle зупинявся саме на ній.
GULP_SHELL = "powered by Dashboard Projects & Jobs Erika Mustermann Profile Settings " * 20
GULP_PROJECT = GULP_SHELL + "Technical Lead Job ID C1 Apply now description " * 30


class FakeBrowserPage:
    """inner_text віддає наступне body з послідовності, останнє повторює."""

    def __init__(self, bodies):
        self.bodies = list(bodies)
        self.reads = 0

    def goto(self, url, wait_until=None, timeout=None):
        self.url = url

    def wait_for_timeout(self, ms):
        pass

    def inner_text(self, sel):
        body = self.bodies[min(self.reads, len(self.bodies) - 1)]
        self.reads += 1
        return body

    def content(self):
        return "<html></html>"

    def close(self):
        pass


def _fake_browser(monkeypatch, bodies):
    import contextlib

    from bot.scrapers import browser

    page = FakeBrowserPage(bodies)
    monkeypatch.setattr(browser, "profile_lock", contextlib.nullcontext)
    monkeypatch.setattr(browser, "_rate_limit", lambda: None)
    monkeypatch.setattr(browser, "get_context", lambda headed=False: type("Ctx", (), {"new_page": lambda self: page})())
    return page


def test_gulp_ready():
    from bot.scrapers.fetch import READY_FOR, gulp_ready
    assert gulp_ready("Technical Lead Job ID C01234567") and gulp_ready("Projekt-ID C01234567")
    assert not gulp_ready(GULP_SHELL)
    assert READY_FOR["gulp.de"] is gulp_ready


def test_settle_body_waits_for_ready_and_gives_up_at_deadline():
    from bot.scrapers.browser import MIN_BODY_CHARS, settle_body
    from bot.scrapers.fetch import gulp_ready
    assert len(GULP_SHELL) >= MIN_BODY_CHARS
    page = FakeBrowserPage([GULP_SHELL, GULP_SHELL, GULP_SHELL, GULP_PROJECT])
    assert settle_body(page, 20000, GULP_SHELL, ready=gulp_ready) == GULP_PROJECT
    # без ready стабільна оболонка й далі приймається одразу
    page = FakeBrowserPage([GULP_SHELL, GULP_PROJECT])
    assert settle_body(page, 20000, GULP_SHELL) == GULP_SHELL
    # бюджет вичерпано: повертає останній текст, хай і без Job ID
    page = FakeBrowserPage([GULP_SHELL])
    assert settle_body(page, 30, GULP_SHELL, ready=gulp_ready) == GULP_SHELL


def test_gulp_shell_keeps_settling_until_job_id(monkeypatch):
    from bot.scrapers import fetch
    _fake_browser(monkeypatch, [GULP_SHELL, GULP_SHELL, GULP_SHELL, GULP_PROJECT])
    monkeypatch.setattr(fetch, "fetch_firecrawl", lambda u: (_ for _ in ()).throw(AssertionError("firecrawl")))
    page = fetch.fetch_page("https://www.gulp.de/gulp2/g/projekte/agentur/C1")
    assert "Job ID" in page.text and page.method == "playwright"


@pytest.mark.parametrize("url", [
    "https://www.gulp.de/gulp2/g/projekte/agentur/C1",
    "https://www.linkedin.com/jobs/search/?keywords=android",
    "https://www.xing.com/jobs/berlin-android-123",
    "https://www.freelancermap.de/projekt/senior-android-engineer",
])
def test_logged_in_hosts_never_go_to_firecrawl(monkeypatch, url):
    from bot.scrapers import browser, fetch
    from bot.scrapers.models import PageFetch

    calls = []
    monkeypatch.setattr(fetch, "fetch_httpx", lambda u, timeout=15.0, headers=None: PageFetch(
        url=u, text="", html="", method="httpx", status_code=200))
    monkeypatch.setattr(fetch, "fetch_firecrawl", lambda u: calls.append(u) or PageFetch(
        url=u, text="x" * 5000, html="", method="firecrawl", status_code=200))
    monkeypatch.setattr(browser, "fetch_with_browser",
                        lambda u, settle_ms=0, ready=None: ("short", "<html/>"))
    page = fetch.fetch_page(url)
    assert calls == [] and page.method == "playwright" and page.text == "short"


def test_only_gulp_fetches_get_a_ready_check(monkeypatch):
    from bot.scrapers import browser, fetch

    seen = {}

    def fake(u, settle_ms=0, **kw):
        seen[u] = kw.get("ready")
        return ("Job ID C1 " + "opis " * 300, "<html/>")

    monkeypatch.setattr(browser, "fetch_with_browser", fake)
    fetch.fetch_page("https://www.gulp.de/gulp2/g/projekte/agentur/C1")
    fetch.fetch_page("https://www.stepstone.de/stellenangebote--x--1-inline.html")
    assert seen["https://www.gulp.de/gulp2/g/projekte/agentur/C1"] is fetch.gulp_ready
    assert seen["https://www.stepstone.de/stellenangebote--x--1-inline.html"] is None
