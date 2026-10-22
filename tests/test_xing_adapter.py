"""XingAdapter: до 25.07 повертав захардкожені company="Unbekannt" /
location="Deutschland" / title="XING Job" — через це ~55% XING-лідів не
набирали score і не доходили до подачі."""
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from bot.scrapers.adapters.xing import XingAdapter
from bot.scrapers.models import PageFetch

_LD = {
    "@context": "https://schema.org/",
    "@type": "JobPosting",
    "title": "Büroleiter (m/w/d)",
    "description": "<p>Für unser Reisebüro suchen wir ab sofort in Vollzeit</p>",
    "hiringOrganization": {"@type": "Organization", "name": "Reisecenter Sonnentours GmbH"},
    "jobLocation": [{
        "@type": "Place",
        "address": {
            "@type": "PostalAddress",
            "addressLocality": "Düsseldorf",
            "addressRegion": "Nordrhein-Westfalen",
            "addressCountry": "DE",
        },
    }],
    "url": "https://www.xing.com/jobs/duesseldorf-bueroleiter-100000209",
}


def _page(html: str, url: str = "https://www.xing.com/m/AbCdEfGhIjKlMnOpQrStUv") -> PageFetch:
    return PageFetch(url=url, text="Büroleiter", html=html, method="httpx", status_code=200)


def test_xing_extracts_company_and_location_from_json_ld():
    html = f'<html><body><h1>Büroleiter (m/w/d)</h1><script type="application/ld+json">{json.dumps(_LD)}</script></body></html>'
    job = XingAdapter().extract(_page(html))
    assert job.title == "Büroleiter (m/w/d)"
    assert job.company == "Reisecenter Sonnentours GmbH"
    assert job.location == "Düsseldorf, Nordrhein-Westfalen"
    assert "Reisebüro" in job.description
    # share-лінк /m/ резолвиться у канонічний URL вакансії
    assert job.source_url.endswith("duesseldorf-bueroleiter-100000209")


def test_xing_region_kept_when_city_unknown_to_nrw_list():
    """Місто поза NRW_LOCATIONS, але земля — NRW: лід має вижити."""
    from bot.filters.rules import location_ok
    ld = json.loads(json.dumps(_LD))
    ld["jobLocation"][0]["address"]["addressLocality"] = "Löhne"
    html = f'<html><body><script type="application/ld+json">{json.dumps(ld)}</script></body></html>'
    job = XingAdapter().extract(_page(html))
    assert job.location == "Löhne, Nordrhein-Westfalen"
    assert location_ok(job.location) is True


def test_xing_falls_back_to_og_description():
    html = (
        '<html><head><meta property="og:description" content="Bewirb Dich als '
        "'Teamassistenz (m/w/d)' bei Muster GmbH in Köln. Branche: IT / "
        'Beschäftigungsart: Vollzeit"/></head>'
        "<body><h1>Teamassistenz (m/w/d)</h1></body></html>"
    )
    job = XingAdapter().extract(_page(html))
    assert job.title == "Teamassistenz (m/w/d)"
    assert job.company == "Muster GmbH"
    assert job.location == "Köln"


def test_xing_survives_page_without_metadata():
    job = XingAdapter().extract(_page("<html><body><p>nichts</p></body></html>"))
    assert job.company == "Unbekannt"
    assert job.location == "Deutschland"
    assert job.title == "XING Job"


def test_xing_telecommute_marks_location_remote():
    """schema.org: jobLocationType=TELECOMMUTE — повністю віддалена вакансія.
    У полі location має бути маркер, інакше remote_policy: full її відкине."""
    ld = json.loads(json.dumps(_LD))
    ld["jobLocationType"] = "TELECOMMUTE"
    ld["jobLocation"][0]["address"]["addressLocality"] = "Berlin"
    html = f'<html><body><script type="application/ld+json">{json.dumps(ld)}</script></body></html>'
    job = XingAdapter().extract(_page(html))
    assert job.location == "Berlin, Nordrhein-Westfalen (Remote)"
