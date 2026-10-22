"""StepstoneAdapter: JSON-LD JobPosting → JobData."""
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from bot.scrapers.adapters.stepstone import StepstoneAdapter
from bot.scrapers.models import PageFetch

_LD = {
    "@context": "https://schema.org/",
    "@type": "JobPosting",
    "title": "Scrum Master (m/w/d)",
    "description": "<p>Wir suchen Verstärkung für unser agiles Team.</p>",
    "hiringOrganization": {"@type": "Organization", "name": "Muster Software GmbH"},
    "jobLocation": [{
        "@type": "Place",
        "address": {"@type": "PostalAddress", "addressLocality": "Hamburg", "addressCountry": "DE"},
    }],
}


def _page(ld: dict) -> PageFetch:
    html = f'<html><body><script type="application/ld+json">{json.dumps(ld)}</script></body></html>'
    return PageFetch(url="https://www.stepstone.de/stellenangebote--Scrum-Master-m-w-d-Hamburg--1--inline.html",
                     text="Scrum Master", html=html, method="httpx", status_code=200)


def test_stepstone_extracts_location_from_json_ld():
    job = StepstoneAdapter().extract(_page(_LD))
    assert job.title == "Scrum Master (m/w/d)"
    assert job.company == "Muster Software GmbH"
    assert job.location == "Hamburg"
    assert "agiles Team" in job.description


def test_stepstone_telecommute_marks_location_remote():
    ld = {**_LD, "jobLocationType": "TELECOMMUTE"}
    job = StepstoneAdapter().extract(_page(ld))
    assert job.location == "Hamburg (Remote)"
