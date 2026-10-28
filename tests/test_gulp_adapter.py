"""GULP / Randstad Professional: сторінка проєкту після settle (фікстури — inner_text
двох типів проєктів; контакти, адреси та ID вигадані)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from bot.scrapers.adapters import get_adapter
from bot.scrapers.adapters.gulp import GulpAdapter
from bot.scrapers.models import PageFetch

FIX = Path(__file__).parent / "fixtures" / "gulp"


def _page(name: str, url: str, title: str) -> PageFetch:
    text = (FIX / f"gulp_{name}.txt").read_text(encoding="utf-8")
    html = f'<html><body><h1 class="gp-title xlarge">{title}</h1><pre>{text}</pre></body></html>'
    return PageFetch(url=url, text=text, html=html, method="playwright", status_code=200)


def test_gulp_urls_use_gulp_adapter():
    assert isinstance(get_adapter("https://www.gulp.de/gulp2/g/projekte/direkt/01234567"), GulpAdapter)


def test_direkt_project_fields():
    job = GulpAdapter().extract(_page(
        "direkt", "https://www.gulp.de/gulp2/g/projekte/direkt/0123456789abcdef01234567",
        "Cloud &amp; DevOps Engineer (m/w/d)  Azure / AWS Infrastructure"))
    assert job.title == "Cloud & DevOps Engineer (m/w/d) Azure / AWS Infrastructure"
    assert job.company == "Direkt vom Auftraggeber (GULP)"
    assert job.location == "Köln (Remote-Arbeit möglich)"
    assert job.description.startswith("Projektbörse GULP (freiberuflich)")
    assert "Beginn: 04.10.2026" in job.description and "Auslastung: 100 %" in job.description
    assert "Terraform" in job.description and "Budget: 85 € – 105 € / Std." in job.description
    # шапка й футер сайту не потрапляють в опис
    for junk in ("Registrieren", "Stichwortsuche", "GULP Membership", "Xing\nLinkedIn"):
        assert junk not in job.description, junk
    assert job.is_portal is True and job.source_url.endswith("0123456789abcdef01234567")


def test_agentur_project_fields():
    job = GulpAdapter().extract(_page(
        "agentur", "https://www.gulp.de/gulp2/g/projekte/agentur/C00000001",
        "IT - Entwickler u.a. Kotlin, React, DevOps,Domain, Agil, Microservice, Container, Cloud, etc (w/m/d)"))
    assert job.title.startswith("IT - Entwickler u.a. Kotlin")
    assert job.company == "Randstad Professional (GULP)"
    assert job.location == "60311 Frankfurt am Main"
    assert "Startdatum: 03.11.2026" in job.description and "Dauer: unbefristet" in job.description
    assert "Domain-Driven Design" in job.description
    # контактні дані консультанта в опис не йдуть
    assert "Mustermann" not in job.description and "+49 61" not in job.description
    assert "Über Randstad Professional" not in job.description


def test_gulp_counts_as_contract_for_engagement():
    from bot.generators.engagement import classify_engagement
    job = GulpAdapter().extract(_page(
        "agentur", "https://www.gulp.de/gulp2/g/projekte/agentur/C00000001", "IT - Entwickler"))
    assert classify_engagement(job.title, job.description) == "contract"
