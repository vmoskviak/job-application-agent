import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from bot.generators.intake import extract_email, parse_text, JobData


def test_extract_email_finds_mailto():
    html = '<a href="mailto:jobs@example.de">Bewerben</a>'
    assert extract_email(html, "example.de") == "jobs@example.de"


def test_extract_email_finds_pattern():
    html = "Bitte senden Sie Ihre Bewerbung an bewerbung@firma.de"
    assert extract_email(html, "firma.de") == "bewerbung@firma.de"


def test_extract_email_returns_none():
    html = "Bewerben Sie sich über unser Portal."
    assert extract_email(html, "example.de") is None


def test_parse_text_returns_jobdata():
    text = """Junior Business Analyst (m/w/d)
    Firma: Test GmbH
    Standort: Düsseldorf
    Wir suchen einen Junior Business Analyst für unser Team.
    Kontakt: jobs@test.de
    """
    job = parse_text(text, source_url="https://test.de")
    assert isinstance(job, JobData)
    assert job.description == text
    assert job.source_url == "https://test.de"


def test_parse_text_markdown_h1_beats_site_chrome():
    # Firecrawl-markdown Indeed: перший рядок — шапка-форма сайту, справжня
    # назва в H1 (регресія 28.07: усі Indeed-ліди звались «WasWoJobs finden»).
    text = """WasWoJobs finden

# Lehrkraft für Integrationskurse DaF/DaZ (d/w/m) freiberuflich - direkt am Düsseldorfer Hbf

[Beispiel Sprachen](https://de.indeed.com/cmp/Beispiel-Sprachen?campaignid=mobvjcmp&fromjk=0a0a0a0a0a0a0a01)

Musterstraße 1, 40210 Düsseldorf

## Stellenbeschreibung

Wir suchen Lehrkräfte für Integrationskurse.
"""
    job = parse_text(text, source_url="https://de.indeed.com/viewjob?jk=0a0a0a0a0a0a0a01")
    assert job.title.startswith("Lehrkraft für Integrationskurse")
    assert job.company == "Beispiel Sprachen"


def test_parse_text_company_markdown_link_stripped():
    # Компанія в markdown-лінку з GmbH: у поле має піти текст лінка, не URL —
    # інакше normalize_company дає сміттєвий ключ і dedup проти трекера сліпне.
    text = """WasWoJobs finden

# Assistenz der Geschäftsleitung (m/w/d)

[St. Elisabeth Gruppe GmbH](https://de.indeed.com/cmp/St.-Elisabeth-Gruppe-Gmbh?campaignid=mobvjcmp)

Herne

Wir suchen eine Assistenz der Geschäftsleitung.
"""
    job = parse_text(text, source_url="https://de.indeed.com/viewjob?jk=0a0a0a0a0a0a0a02")
    assert job.title == "Assistenz der Geschäftsleitung (m/w/d)"
    assert job.company == "St. Elisabeth Gruppe GmbH"


def test_parse_text_plain_text_title_unchanged():
    text = "Junior Scrum Master (m/w/d)\nTest GmbH\nDüsseldorf\nWir suchen dich."
    job = parse_text(text)
    assert job.title == "Junior Scrum Master (m/w/d)"
    assert job.company == "Test GmbH"


def test_portal_detection():
    text = "Bewerben Sie sich jetzt über Workday: https://testgmbh.wd3.myworkdayjobs.com/jobs"
    job = parse_text(text, source_url="https://test.de")
    assert job.is_portal is True


def test_no_portal_for_regular_text():
    text = "Junior BA bei Muster GmbH in Düsseldorf. jobs@muster.de"
    job = parse_text(text, source_url="https://muster.de")
    assert job.is_portal is False


def test_split_portal_title_rp_stellenmarkt():
    from bot.generators.intake import split_portal_title
    t, c = split_portal_title("Teamassistenz Personalwesen (m/w/d) Vollzeit / Teilzeit - Musterland Kultur GmbH | RP Stellenmarkt")
    assert t == "Teamassistenz Personalwesen (m/w/d) Vollzeit / Teilzeit" and c == "Musterland Kultur GmbH"
    t, c = split_portal_title("Teamassistenz Energiemanagement und Klimaschutz (w/m/d) - Deutsche Sporthochschule Köln | RP Stellenmarkt")
    assert c == "Deutsche Sporthochschule Köln"
    # без суфікса порталу тире в назві лишається назвою
    assert split_portal_title("Teamassistenz (m/w/d) - Standort Köln") == ("Teamassistenz (m/w/d) - Standort Köln", None)


def test_parse_text_rp_stellenmarkt_company_and_location():
    from bot.generators.intake import parse_text
    text = ("Teamassistenz Personalwesen (m/w/d) Vollzeit / Teilzeit - Musterland Kultur GmbH | RP Stellenmarkt\n"
            "Köln\nWir suchen ... bewerbung@musterland-kultur.example")
    job = parse_text(text, "https://jobs.rp-online.de/jobs/sde-123334/x/")
    assert job.title == "Teamassistenz Personalwesen (m/w/d) Vollzeit / Teilzeit"
    assert job.company == "Musterland Kultur GmbH"
    assert job.location == "Köln"
