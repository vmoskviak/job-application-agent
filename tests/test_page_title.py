"""Page titles of employer sites and aggregators: role and company apart (bot.job_title.split_page_title).

On 03.11.2026 every duplicate application of the morning runs came from one place: the company
of a discovered job was the whole page title («Teamassistenz bei der … GmbH (m/w/d)») or
«Unbekannt» while the title named it («… | Köln | <Firma>», «… - Karriere bei <Firma>»), so the
check against the tracker never matched. The same titles put «| hokify» into a letter subject.
"""
from bot.generators.intake import parse_text
from bot.job_title import split_page_title


def test_aggregator_suffix_with_company_after_bei():
    assert split_page_title("Assistenz des Geschäftsführers  bei Muster Innenausbau GmbH | hokify") == (
        "Assistenz des Geschäftsführers bei Muster Innenausbau GmbH", "Muster Innenausbau GmbH")


def test_aggregator_suffix_with_company_after_dash():
    assert split_page_title("Personalassistentin (m/w/d) - BEISPIEL ALPHA | BeBee") == (
        "Personalassistentin (m/w/d)", "BEISPIEL ALPHA")


def test_agency_site_name_is_the_company_and_a_reference_is_not():
    assert split_page_title("Office Management (m/w/d) - JN-102026-7000001 | Beispiel Personal") == (
        "Office Management (m/w/d) - JN-102026-7000001", "Beispiel Personal")


def test_city_segment_is_skipped_and_employer_segment_kept():
    assert split_page_title("Empfangskraft (m/w/d) Teilzeit | Köln | MUSTERSERVICE") == (
        "Empfangskraft (m/w/d) Teilzeit", "MUSTERSERVICE")


def test_karriere_bei_names_the_company():
    assert split_page_title("➤ Stellenangebot Bürokauffrau (m/w/d) - Karriere bei Muster Legal") == (
        "➤ Stellenangebot Bürokauffrau (m/w/d)", "Muster Legal")


def test_bei_with_legal_form_without_any_suffix():
    assert split_page_title("Teamassistenz bei der Muster Certification GmbH (m/w/d)") == (
        "Teamassistenz bei der Muster Certification GmbH (m/w/d)", "Muster Certification GmbH")


def test_titles_without_a_company_stay_as_they_are():
    for raw in ("Teamassistenz (m/w/d) - Standort Köln", "Assistenz bei der Geschäftsführung (m/w/d)",
                "Sachbearbeiter (m/w/d)", "Kundenservice (m/w/d) - Teilzeit"):
        assert split_page_title(raw) == (raw, None), raw
    assert split_page_title("Sachbearbeiter (m/w/d) | Indeed") == ("Sachbearbeiter (m/w/d)", None)
    assert split_page_title("Sachbearbeiter (m/w/d) - Teilzeit | StepStone") == (
        "Sachbearbeiter (m/w/d) - Teilzeit", None)


def test_rp_stellenmarkt_still_splits():
    assert split_page_title("Teamassistenz Personalwesen (m/w/d) - Musterland Kultur GmbH | RP Stellenmarkt") == (
        "Teamassistenz Personalwesen (m/w/d)", "Musterland Kultur GmbH")


def test_parse_text_takes_the_company_from_the_title_line():
    job = parse_text("Assistenz des Geschäftsführers  bei Muster Innenausbau GmbH | hokify\nweitere\nJobs anzeigen\n",
                     source_url="https://hokify.de/job/1")
    assert (job.title, job.company) == ("Assistenz des Geschäftsführers bei Muster Innenausbau GmbH",
                                        "Muster Innenausbau GmbH")
    job = parse_text("Teamassistenz bei der Muster Certification GmbH (m/w/d)\nDüsseldorf\n", source_url="https://x.de")
    assert job.company == "Muster Certification GmbH"
    job = parse_text("Empfangskraft (m/w/d) Teilzeit | Köln | MUSTERSERVICE\nZum Hauptinhalt\n", source_url="https://x.de")
    assert (job.title, job.company) == ("Empfangskraft (m/w/d) Teilzeit", "MUSTERSERVICE")


def test_place_before_the_company_in_the_last_segment_goes():
    raw = ("Quereinsteiger Schulbegleitung (m/w/d) - Köln, Deutschland | Brühl, Deutschland "
           "| Neuss, Deutschland - Muster – Rheinland GmbH")
    assert split_page_title(raw)[1] == "Muster – Rheinland GmbH"
