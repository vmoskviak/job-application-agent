"""Guard проти інтерстишлів XING + сторож парсингу в session_health.

25.07: XING-лист несе ~66 /m/-лінків, і перший з них — не вакансія, а
«schau, was unsere KI-gestützte Suche für Dich gefunden hat». Такий лінк
не резолвиться у /jobs/, але XingAdapter однаково робив із нього лід через
og:description-fallback. Плюс перевірка, що сторож у session_health реально
падає на company="Unbekannt" — інакше він знову промовчить тиждень.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from bot.generators.intake import JobData  # noqa: E402
from bot.scrapers import intake_pipeline  # noqa: E402
from bot.scrapers.models import PageFetch  # noqa: E402


def _fetch(url: str) -> PageFetch:
    return PageFetch(url=url, text="x" * 500, html="<html></html>",
                     method="httpx", status_code=200)


def test_unresolved_share_link_is_not_a_job(monkeypatch):
    """/m/, що лишився /m/ після резолвінгу, відкидається до адаптера."""
    monkeypatch.setattr(intake_pipeline, "fetch_page",
                        lambda url, use_playwright=True: _fetch(url))
    res = intake_pipeline.intake_url("https://www.xing.com/m/4wtWWeha-GGXXOVnA3KdgA")
    assert res.job is None
    assert res.liveness == "failed"
    assert "/m/" in res.reason


def test_resolved_job_url_still_passes(monkeypatch):
    """Канонічний /jobs/-URL guard не чіпає."""
    canon = "https://www.xing.com/jobs/duesseldorf-bueroleiter-100000209"
    monkeypatch.setattr(intake_pipeline, "fetch_page",
                        lambda url, use_playwright=True: _fetch(canon))
    monkeypatch.setattr(intake_pipeline, "check_liveness", lambda text, url: ("live", "ok"))
    monkeypatch.setattr(
        intake_pipeline, "get_adapter",
        lambda url: type("A", (), {"extract": staticmethod(lambda page: JobData(
            title="Büroleiter (m/w/d)", company="Reisecenter Sonnentours GmbH",
            location="Düsseldorf, Nordrhein-Westfalen", description="y" * 300,
            email=None, application_url=None, source_url=canon, is_portal=True))})())
    res = intake_pipeline.intake_url(canon)
    assert res.job is not None
    assert res.job.company == "Reisecenter Sonnentours GmbH"


def test_health_check_fails_on_stub_values(monkeypatch):
    """Сторож ловить рівно ті значення, які віддавала стара заглушка."""
    from scripts import session_health as sh

    canon = "https://www.xing.com/jobs/koeln-teamassistenz-123"
    monkeypatch.setattr(sh, "_xing_links_from_mail",
                        lambda *a, **kw: ["https://www.xing.com/m/AAA"])
    monkeypatch.setattr("bot.scrapers.fetch.resolve_xing_share", lambda url, **kw: canon)
    monkeypatch.setattr("bot.scrapers.fetch.fetch_httpx", lambda url, **kw: _fetch(canon))

    stub = JobData(title="Teamassistenz", company="Unbekannt", location="Deutschland",
                   description="z" * 300, email=None, application_url=None,
                   source_url=canon, is_portal=True)
    monkeypatch.setattr("bot.scrapers.adapters.xing.XingAdapter.extract",
                        lambda self, page: stub)
    assert sh.check_xing_parse().startswith("broken:company=Unbekannt")

    good = JobData(title="Teamassistenz", company="Kanzlei Müller",
                   location="Köln, Nordrhein-Westfalen", description="z" * 300,
                   email=None, application_url=None, source_url=canon, is_portal=True)
    monkeypatch.setattr("bot.scrapers.adapters.xing.XingAdapter.extract",
                        lambda self, page: good)
    assert sh.check_xing_parse() == "live"


def test_health_check_skips_when_no_job_links(monkeypatch):
    """Тиша в пошті ≠ зламаний парсинг: skip, не broken."""
    from scripts import session_health as sh

    monkeypatch.setattr(sh, "_xing_links_from_mail", lambda *a, **kw: [])
    assert sh.check_xing_parse().startswith("skip")
