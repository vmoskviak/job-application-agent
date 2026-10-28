"""Indeed через firecrawl: markdown починається з «Suchen», назва — «#####»,
а в HTML вона лежить в окремому елементі (регресія 22.10.2026: живі вакансії
відпадали як scrape_failed_title). Фікстури вигадані."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from bot.generators.intake import parse_text
from bot.scrapers.adapters import get_adapter
from bot.scrapers.adapters.indeed import IndeedAdapter
from bot.scrapers.models import PageFetch

URL = "https://de.indeed.com/viewjob?jk=0b0b0b0b0b0b0b01"

MARKDOWN = """Suchen

![Beispiel Logistik GmbH logo](https://img.example/header.png)

##### Teamassistenz bei der Beispiel Logistik GmbH (m/w/d)

[Beispiel Holding (Mitte)](https://de.indeed.com/cmp/Beispiel-Holding-(mitte)?campaignid=mobvjcmp&fromjk=0b0b0b0b0b0b0b01)

·

3.1

Musterstraße 5, 40210 Düsseldorf

Teilzeit, Vollzeit

Weiter zur Bewerbung

#### Stellenbeschreibung

Keine Gehaltsinformationen angegeben

#### Vollständige Stellenbeschreibung

# **1/2/2026**

Wir suchen eine Teamassistenz zur Unterstützung unseres Büros in Düsseldorf.
Sie organisieren Termine, betreuen die Korrespondenz und pflegen Ablagen.
"""

HTML = """<html><body><div id="root">
<div data-testid="desktop-job-header">
  <div data-testid="company-info-title-row">
    <h5 aria-level="5" role="heading" data-testid="vj-job-title">Teamassistenz (m/w/d)</h5>
  </div>
  <div data-testid="company-info-metadata">
    <div><a href="https://de.indeed.com/cmp/Beispiel-Holding">Beispiel Holding GmbH</a></div>
    <div><div dir="ltr">Düsseldorf</div></div>
  </div>
</div>
<a href="https://de.indeed.com/cmp/Beispiel-Holding/salaries">Gehaltslink für Beispiel Holding GmbH</a>
</div></body></html>"""


def test_get_adapter_routes_indeed():
    assert isinstance(get_adapter(URL), IndeedAdapter)
    assert isinstance(get_adapter("https://indeed.de/viewjob?jk=1"), IndeedAdapter)


def test_parse_text_takes_h5_title_over_search_button():
    job = parse_text(MARKDOWN, source_url=URL)
    assert job.title == "Teamassistenz bei der Beispiel Logistik GmbH (m/w/d)"
    # /cmp/-лінк з дужками в URL, а не заголовок з «GmbH» чи alt логотипа
    assert job.company == "Beispiel Holding (Mitte)"


def test_adapter_prefers_html_title_company_location():
    page = PageFetch(url=URL, text=MARKDOWN, html=HTML, method="firecrawl", status_code=200)
    job = IndeedAdapter().extract(page)
    assert job.title == "Teamassistenz (m/w/d)"
    assert job.company == "Beispiel Holding GmbH"
    assert job.location == "Düsseldorf"
    assert "Teamassistenz zur Unterstützung" in job.description


def test_adapter_json_ld_and_og_title():
    html = """<html><head>
<meta property="og:title" content="Sachbearbeitung Einkauf (m/w/d) - Köln - Indeed.com">
<script type="application/ld+json">{"@type": "JobPosting", "title": "Sachbearbeitung Einkauf (m/w/d)",
 "hiringOrganization": {"name": "Muster Handel AG"},
 "jobLocation": {"address": {"addressLocality": "Köln"}}}</script>
</head><body><h1>Suchen</h1></body></html>"""
    page = PageFetch(url=URL, text="Suchen\n\nText " * 30, html=html, method="playwright")
    job = IndeedAdapter().extract(page)
    assert (job.title, job.company, job.location) == (
        "Sachbearbeitung Einkauf (m/w/d)", "Muster Handel AG", "Köln")

    og_only = '<html><head><meta property="og:title" content="Empfang (m/w/d) - Köln - Indeed.com"></head></html>'
    page = PageFetch(url=URL, text="Suchen\n\nText", html=og_only, method="playwright")
    assert IndeedAdapter().extract(page).title == "Empfang (m/w/d)"


def test_adapter_without_html_falls_back_to_markdown():
    page = PageFetch(url=URL, text=MARKDOWN, html="", method="firecrawl")
    assert IndeedAdapter().extract(page).title.startswith("Teamassistenz bei der")


def test_intake_pipeline_recovers_junk_title_from_description(monkeypatch):
    """Адаптер дав «XING Job», а опис цілий: назва з рядка з (m/w/d)."""
    import bot.scrapers.intake_pipeline as pipe

    text = ("Navigation überspringen\nJobs finden\nAnmelden\n"
            "Sachbearbeitung Personal (m/w/d)\nMuster Service GmbH\nDüsseldorf\n"
            + "Sie bearbeiten Personalvorgänge und pflegen Stammdaten. " * 10)
    page = PageFetch(url="https://www.xing.com/jobs/duesseldorf-sachbearbeitung-1",
                     text=text, html="<html><body></body></html>", method="playwright")
    monkeypatch.setattr(pipe, "fetch_page", lambda url, use_playwright=True: page)
    res = pipe.intake_url(page.url)
    assert res.liveness == "live"
    assert res.job.title == "Sachbearbeitung Personal (m/w/d)"
