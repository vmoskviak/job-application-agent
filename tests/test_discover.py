import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))


def test_keywords_module_importable():
    from bot.generators.keywords import CATEGORIES
    assert isinstance(CATEGORIES, dict)
    assert len(CATEGORIES) >= 12


def test_all_categories_have_keywords():
    from bot.generators.keywords import CATEGORIES
    for name, kws in CATEGORIES.items():
        assert len(kws) >= 3, f"{name} has too few keywords"


def test_score_job_matches_hausverwaltung():
    from bot.generators.discover import score_job
    score, cat = score_job(
        title="Objektverwalterin (m/w/d)",
        description="WEG-Verwaltung, Mieterbetreuung, Hausverwaltung NRW",
    )
    assert score > 0
    assert cat == "B1_hausverwaltung"


def test_score_job_matches_ba():
    from bot.generators.discover import score_job
    score, cat = score_job(
        title="Junior Business Analyst",
        description="Requirements Engineering, Jira, Agile",
    )
    assert score > 0
    assert cat == "T1_ba"


def test_score_job_returns_zero_for_no_match():
    from bot.generators.discover import score_job
    score, cat = score_job(
        title="Köchin Gastro",
        description="Kochen, Küche, Gastro",
    )
    assert score == 0
    assert cat == ""


def test_build_digest_sorts_by_score():
    from bot.generators.discover import build_digest
    results = [
        {"score": 30, "category": "B3_versicherung", "title": "A", "company": "X", "email": None, "is_portal": False, "source_url": ""},
        {"score": 80, "category": "T1_ba",           "title": "B", "company": "Y", "email": "b@y.de", "is_portal": False, "source_url": ""},
        {"score": 50, "category": "B4_assistenz",    "title": "C", "company": "Z", "email": None, "is_portal": True, "source_url": ""},
    ]
    digest = build_digest(results)
    assert digest[0]["score"] == 80
    assert digest[1]["score"] == 50
    assert digest[2]["score"] == 30


def test_extract_urls_from_text():
    from bot.generators.discover import extract_urls
    text = """
    New job: https://www.stepstone.de/stelle/junior-ba-123456
    Also check: https://de.linkedin.com/jobs/view/987654
    And: https://interamt.de/koop/app/stelle?id=111
    """
    urls = extract_urls(text)
    assert len(urls) == 3
    assert any("stepstone" in u for u in urls)
    assert any("linkedin" in u for u in urls)
    assert any("interamt" in u for u in urls)


def test_extract_urls_jobware():
    from bot.generators.discover import extract_urls
    text = "Check this: https://www.jobware.de/stellenangebote/teamassistenz-dusseldorf-12345.html"
    urls = extract_urls(text)
    assert len(urls) == 1
    assert "jobware" in urls[0]


def test_extract_urls_kleinanzeigen():
    from bot.generators.discover import extract_urls
    text = "Found: https://www.kleinanzeigen.de/s-anzeige/buroassistentin/1234567-133-abc"
    urls = extract_urls(text)
    assert len(urls) == 1
    assert "kleinanzeigen" in urls[0]


def test_extract_urls_hokify():
    from bot.generators.discover import extract_urls
    text = "Apply: https://hokify.de/jobs/1234567/teamassistenz-dusseldorf"
    urls = extract_urls(text)
    assert len(urls) == 1
    assert "hokify" in urls[0]


def test_extract_urls_hokify_www():
    from bot.generators.discover import extract_urls
    text = "Apply: https://www.hokify.de/jobs/1234567/teamassistenz-dusseldorf"
    urls = extract_urls(text)
    assert len(urls) == 1
    assert "hokify" in urls[0]


def test_extract_urls_deduplicates():
    from bot.generators.discover import extract_urls
    text = """
    https://www.stepstone.de/stelle/test-123
    https://www.stepstone.de/stelle/test-123
    """
    urls = extract_urls(text)
    assert len(urls) == 1


def test_score_job_title_match_passes_threshold():
    # влучання в назву = пряма ознака ролі, має вистачити на MIN_SCORE=25
    # без вимоги другого синоніма в описі
    from bot.generators.discover import score_job
    score, cat = score_job(
        title="Business Analyst",
        description="Als Banken-IT-Dienstleister suchst du Anforderungen der Fachbereiche.",
    )
    assert score >= 25
    assert cat == "T1_ba"


def test_score_job_prozess_category():
    from bot.generators.discover import score_job
    score, cat = score_job(
        title="Junior Consultant Prozessdesign (m/w/d)",
        description="Konzeption und Prozessoptimierung, Modellierung von Geschäftsprozessen.",
    )
    assert score >= 25
    assert cat == "T6_prozess"


def test_score_job_servicenow():
    from bot.generators.discover import score_job
    score, cat = score_job(
        title="(Junior) ServiceNow Consultant Digitale Transformation",
        description="Du implementierst IT Service Management Workflows in ServiceNow.",
    )
    assert score >= 25
    assert cat == "T2_it"


def _fake_linkedin_result(company: str):
    from bot.generators.intake import JobData
    from bot.scrapers.models import IntakeResult
    return IntakeResult(
        job=JobData(
            title="LinkedIn Job" if company == "Unbekannt" else "Business Analyst",
            company=company,
            location="Deutschland",
            description="x" * 200,
            email=None,
            application_url=None,
            source_url="https://www.linkedin.com/jobs/view/1",
            is_portal=True,
        ),
        liveness="live",
        intake_method="playwright",
    )


def test_canary_warns_on_unbekannt_linkedin(monkeypatch, capsys):
    # 2+ LinkedIn-результати без розпарсеної компанії = розмітка змінилась,
    # деградація має кричати в stderr, а не тихо ковтатись
    import bot.generators.discover as discover
    monkeypatch.setattr(
        discover, "intake_with_meta",
        lambda url, use_playwright=True: _fake_linkedin_result("Unbekannt"),
    )
    discover.process_urls(
        ["https://www.linkedin.com/jobs/view/1", "https://www.linkedin.com/jobs/view/2"],
        verbose=False,
    )
    assert "[canary]" in capsys.readouterr().err


def test_canary_silent_when_companies_parsed(monkeypatch, capsys):
    import bot.generators.discover as discover
    monkeypatch.setattr(
        discover, "intake_with_meta",
        lambda url, use_playwright=True: _fake_linkedin_result("Beispielwerk"),
    )
    discover.process_urls(
        ["https://www.linkedin.com/jobs/view/1", "https://www.linkedin.com/jobs/view/2"],
        verbose=False,
    )
    assert "[canary]" not in capsys.readouterr().err


# --- click.stepstone.de tracking-редіректи (fix 22.07.2026: 16 лідів/день губились у manual) ---

class _FakeResp:
    def __init__(self, status_code=302, location=None):
        self.status_code = status_code
        self.headers = {"location": location} if location else {}


_MAGICLINK = (
    "https://www.stepstone.de/v2/magiclink/exchange?magicLink=eyJhbGc"
    "&returnUrl=%2Fstellenangebote----10000005-inline.html%3FCID%3DR_EM_foo%26lang%3Dde"
    "&offer_position=3"
)


def test_resolve_tracking_magiclink(monkeypatch):
    import bot.generators.discover as discover
    monkeypatch.setattr(
        discover.httpx, "get",
        lambda url, **kw: _FakeResp(302, _MAGICLINK),
    )
    target = discover.resolve_tracking_url("https://click.stepstone.de/f/a/abc~~/AAAmIhA~/xyz")
    assert target == "https://www.stepstone.de/stellenangebote----10000005-inline.html"


def test_resolve_tracking_direct_job_redirect(monkeypatch):
    import bot.generators.discover as discover
    monkeypatch.setattr(
        discover.httpx, "get",
        lambda url, **kw: _FakeResp(302, "https://www.stepstone.de/stellenangebote--Junior-BA-Koeln-999.html?cid=mail"),
    )
    target = discover.resolve_tracking_url("https://click.stepstone.de/f/a/abc")
    assert target == "https://www.stepstone.de/stellenangebote--Junior-BA-Koeln-999.html"


def test_resolve_tracking_footer_link_not_a_job(monkeypatch):
    import bot.generators.discover as discover
    monkeypatch.setattr(
        discover.httpx, "get",
        lambda url, **kw: _FakeResp(302, "https://www.youtube.com/@stepstonede"),
    )
    assert discover.resolve_tracking_url("https://click.stepstone.de/f/a/short") is None


def test_resolve_tracking_network_error_raises(monkeypatch):
    import bot.generators.discover as discover

    def _boom(url, **kw):
        raise discover.httpx.ConnectError("dns fail")

    monkeypatch.setattr(discover.httpx, "get", _boom)
    import pytest
    with pytest.raises(discover.TrackingResolveError):
        discover.resolve_tracking_url("https://click.stepstone.de/f/a/abc")


def test_resolve_urls_mixed_list_dedupes(monkeypatch):
    # два tracking-лінки на одну вакансію (HTML+text частини листа) → один URL;
    # не-tracking URL проходять без змін; футерний редірект тихо відкидається
    import bot.generators.discover as discover
    monkeypatch.setattr(
        discover.httpx, "get",
        lambda url, **kw: _FakeResp(302, _MAGICLINK if "job" in url else "https://www.youtube.com/@stepstonede"),
    )
    urls = [
        "https://www.linkedin.com/jobs/view/111/",
        "https://click.stepstone.de/f/a/job-1",
        "https://click.stepstone.de/f/a/job-1-dup",
        "https://click.stepstone.de/f/a/footer",
    ]
    resolved, failed = discover.resolve_urls(urls)
    assert resolved == [
        "https://www.linkedin.com/jobs/view/111/",
        "https://www.stepstone.de/stellenangebote----10000005-inline.html",
    ]
    assert failed == []


def test_resolve_urls_network_error_goes_to_failed(monkeypatch):
    import bot.generators.discover as discover

    def _boom(url, **kw):
        raise discover.httpx.ConnectError("dns fail")

    monkeypatch.setattr(discover.httpx, "get", _boom)
    resolved, failed = discover.resolve_urls(["https://click.stepstone.de/f/a/abc"])
    assert resolved == []
    assert failed == ["https://click.stepstone.de/f/a/abc"]


def test_extract_urls_click_stepstone():
    from bot.generators.discover import extract_urls
    text = 'href="https://click.stepstone.de/f/a/AbCdEfGhIj~~/AAAmIhA~/KlMnOpQrStUv"'
    urls = extract_urls(text)
    assert urls == ["https://click.stepstone.de/f/a/AbCdEfGhIj~~/AAAmIhA~/KlMnOpQrStUv"]


# ── triage_urls: дешевий slug-фільтр до браузерного intake ────────────────────

def test_triage_drops_non_nrw_city_slug():
    from bot.generators.discover import triage_urls
    kept, dropped = triage_urls([
        "https://www.xing.com/jobs/wien-dolmetscher-100000201",
        "https://www.xing.com/jobs/bremen-fachkraft-sprache-100000202",
        "https://www.xing.com/jobs/duesseldorf-buerokraft-100000203",
    ])
    assert kept == ["https://www.xing.com/jobs/duesseldorf-buerokraft-100000203"]
    assert {d["reason"] for d in dropped} == {"outside_nrw_slug"}


def test_triage_drops_excluded_title_slug():
    from bot.generators.discover import triage_urls
    kept, dropped = triage_urls([
        "https://www.xing.com/jobs/koeln-apprenticeship-office-manager-100000204",
        "https://www.xing.com/jobs/koeln-senior-business-operations-manager-100000205",
        "https://www.xing.com/jobs/koeln-team-assistenz-100000206",
    ])
    assert kept == ["https://www.xing.com/jobs/koeln-team-assistenz-100000206"]
    assert {d["reason"] for d in dropped} == {"excluded_title_slug"}


def test_triage_keeps_remote_slug_despite_foreign_city():
    # remote-маркер у слагу перебиває не-NRW місто (як remote у location_ok)
    from bot.generators.discover import triage_urls
    kept, dropped = triage_urls([
        "https://www.xing.com/jobs/bremen-sachbearbeiter-remote-100000207",
    ])
    assert len(kept) == 1 and not dropped


def test_triage_keeps_unknown_city_and_non_slug_urls():
    # незнайоме місто (дрібне NRW-містечко поза списком) і URL без слага
    # проходять далі — рішення приймає повний location_ok по опису (урок Haan)
    from bot.generators.discover import triage_urls
    urls = [
        "https://www.xing.com/jobs/kevelaer-teamassistenz-100000208",
        "https://karriere.duesseldorf.de/stelle/12345-sachbearbeitung",
        "https://de.indeed.com/viewjob?jk=abc123",
    ]
    kept, dropped = triage_urls(urls)
    assert kept == urls and not dropped


def test_triage_stepstone_slug_city():
    from bot.generators.discover import triage_urls
    kept, dropped = triage_urls([
        "https://www.stepstone.de/stellenangebote--Teamassistenz-Muenchen-12345678-inline.html",
        "https://www.stepstone.de/stellenangebote--Teamassistenz-Bergisch-Gladbach-9999-inline.html",
    ])
    assert kept == ["https://www.stepstone.de/stellenangebote--Teamassistenz-Bergisch-Gladbach-9999-inline.html"]
    assert dropped[0]["reason"] == "outside_nrw_slug"


# --- стан «вже бачені» для алерт-лінків (fix 22.10.2026: Gmail-запит newer_than:2d
# повторював усі вчорашні лінки через скрапінг, фільтр і cover notes) ---

def _live_result(url: str):
    from bot.generators.intake import JobData
    from bot.scrapers.models import IntakeResult
    return IntakeResult(
        job=JobData(title="Android Developer", company="ACME", location="Remote",
                    description="Kotlin " * 40, email=None, application_url=None,
                    source_url=url, is_portal=True),
        liveness="live", intake_method="httpx",
    )


def _failed_result(url: str):
    from bot.scrapers.models import IntakeResult
    return IntakeResult(job=None, liveness="failed", intake_method="httpx",
                        reason="timeout")


def test_seen_key_ignores_query_fragment_and_host_case():
    from bot.generators.discover import seen_key
    a = seen_key("https://WWW.linkedin.com/jobs/view/123?trk=mail#top")
    b = seen_key("https://www.linkedin.com/jobs/view/123/")
    assert a == b == "www.linkedin.com/jobs/view/123"


def test_main_skips_seen_urls_and_records_new_ones(monkeypatch, tmp_path, capsys):
    import bot.generators.discover as discover
    import bot.scrapers.browser as browser
    monkeypatch.setattr(browser, "shutdown_browser", lambda: None)
    calls: list[str] = []

    def fake_intake(url, use_playwright=True):
        calls.append(url)
        if url.endswith("/9"):
            return _failed_result(url)
        return _live_result(url)

    monkeypatch.setattr(discover, "intake_with_meta", fake_intake)
    urls = tmp_path / "urls.txt"
    urls.write_text("https://www.linkedin.com/jobs/view/1\n"
                    "https://www.linkedin.com/jobs/view/9\n", encoding="utf-8")
    state = tmp_path / "state" / "discover_seen.json"

    discover.main(urls, use_playwright=False, seen_state=state)
    assert calls == ["https://www.linkedin.com/jobs/view/1",
                     "https://www.linkedin.com/jobs/view/9"]
    # живий лінк запам'ятали, збій мережі — ні (наступного разу спробуємо знову)
    assert discover.load_seen(state) == {"www.linkedin.com/jobs/view/1"}

    calls.clear()
    urls.write_text("https://www.linkedin.com/jobs/view/1?utm=x\n"
                    "https://www.linkedin.com/jobs/view/9\n"
                    "https://www.linkedin.com/jobs/view/2\n", encoding="utf-8")
    discover.main(urls, use_playwright=False, seen_state=state)
    assert calls == ["https://www.linkedin.com/jobs/view/9",
                     "https://www.linkedin.com/jobs/view/2"]
    assert "вже бачені: 1" in capsys.readouterr().err
    assert discover.load_seen(state) == {"www.linkedin.com/jobs/view/1",
                                         "www.linkedin.com/jobs/view/2"}


def test_main_writes_empty_digest_when_everything_seen(monkeypatch, tmp_path):
    import json
    import bot.generators.discover as discover
    import bot.scrapers.browser as browser
    monkeypatch.setattr(browser, "shutdown_browser", lambda: None)
    monkeypatch.setattr(discover, "intake_with_meta",
                        lambda url, use_playwright=True: (_ for _ in ()).throw(AssertionError("fetch")))
    state = tmp_path / "discover_seen.json"
    discover.commit_seen(state, {"www.linkedin.com/jobs/view/1"})
    urls = tmp_path / "urls.txt"
    urls.write_text("https://www.linkedin.com/jobs/view/1\n", encoding="utf-8")
    discover.main(urls, use_playwright=False, seen_state=state)
    from datetime import date
    out = tmp_path / f"discover_digest_{date.today():%Y-%m-%d}.json"
    assert json.loads(out.read_text(encoding="utf-8")) == []


def test_main_without_state_keeps_old_behaviour(monkeypatch, tmp_path):
    import bot.generators.discover as discover
    import bot.scrapers.browser as browser
    monkeypatch.setattr(browser, "shutdown_browser", lambda: None)
    monkeypatch.setattr(discover, "intake_with_meta",
                        lambda url, use_playwright=True: _live_result(url))
    urls = tmp_path / "urls.txt"
    urls.write_text("https://www.linkedin.com/jobs/view/1\n", encoding="utf-8")
    discover.main(urls, use_playwright=False)
    discover.main(urls, use_playwright=False)
    assert not list(tmp_path.glob("**/discover_seen.json"))
