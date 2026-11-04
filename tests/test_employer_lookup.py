"""bot.apply.employer_lookup: candidates count only as this job of this employer."""
from bot.apply.employer_lookup import (
    Posting,
    find_posting,
    overview_link,
    page_is_job,
    title_words,
)

TITLE = "Assistenz der Geschäftsführung (m/w/d) – Teilzeit"
POSTING = ("<html><head><title>Assistenz der Geschäftsführung (m/w/d) – Teilzeit - Stellenangebot von Beispiel"
           "</title></head><body><h1>Assistenz der Geschäftsführung (m/w/d) – Teilzeit</h1><p>Beispiel GmbH, Köln. "
           "Bewirb dich: jobs@beispiel.de</p></body></html>")
OTHER_JOB = ("<html><head><title>Trainee Marketing (m/w/d) - Beispiel</title></head>"
             "<body><h1>Trainee Marketing</h1></body></html>")
OVERVIEW = ("<html><head><title>Karriere - Beispiel GmbH</title></head><body><h1>Offene Stellen</h1>"
            "<a href='/karriere/trainee'>Trainee Marketing</a>"
            "<a href='/karriere/assistenz-gf'>Assistenz der Geschäftsführung (m/w/d) Teilzeit</a></body></html>")


def _ask(answer, cites=()):
    calls = []

    def ask(company, title, city, key):
        calls.append((company, title, city))
        return answer, list(cites)
    return ask, calls


def test_title_words_drop_gender_and_filler():
    assert title_words(TITLE) == ["assistenz", "geschaeftsfuehrung"]
    assert title_words("Kaufmännische Assistenz Objektüberwachung (m/w/d)") == ["kaufmaennische", "assistenz",
                                                                                "objektueberwachung"]


def test_answer_page_of_this_job_is_taken_with_its_email():
    ask, calls = _ask({"url": "https://www.beispiel.de/karriere/assistenz-gf", "email": "jobs@beispiel.de"})
    got = find_posting("Beispiel GmbH", TITLE, "Köln", api_key="k", ask=ask, fetch=lambda u: POSTING)
    assert got == Posting("https://www.beispiel.de/karriere/assistenz-gf", "jobs@beispiel.de", "answer")
    assert calls == [("Beispiel GmbH", TITLE, "Köln")]


def test_another_job_is_refused_and_a_citation_of_this_one_wins():
    """02.11.2026: Perplexity answered with the employer's trainee posting instead of this one."""
    pages = {"https://www.beispiel.de/karriere/trainee": OTHER_JOB, "https://www.beispiel.de/karriere/assistenz-gf": POSTING}
    ask, _ = _ask({"url": "https://www.beispiel.de/karriere/trainee"},
                  ["https://de.indeed.com/viewjob?jk=1", "https://www.beispiel.de/karriere/assistenz-gf"])
    got = find_posting("Beispiel GmbH", TITLE, "Köln", api_key="k", ask=ask, fetch=pages.get)
    assert got is not None and got.url == "https://www.beispiel.de/karriere/assistenz-gf" and got.source == "citation"


def test_career_overview_leads_one_hop_on():
    pages = {"https://www.beispiel.de/karriere": OVERVIEW, "https://www.beispiel.de/karriere/assistenz-gf": POSTING}
    ask, _ = _ask({"url": "https://www.beispiel.de/karriere"})
    got = find_posting("Beispiel GmbH", TITLE, "Köln", api_key="k", ask=ask, fetch=pages.get)
    assert got is not None and got.url == "https://www.beispiel.de/karriere/assistenz-gf" and got.source == "overview"


def test_career_path_on_the_employer_domain():
    """An about page has no job; the domain's /karriere has the link."""
    about = "<html><head><title>Über uns - Beispiel</title></head><body><h1>Über uns</h1></body></html>"
    pages = {"https://www.beispiel.de/ueber-uns": about, "https://www.beispiel.de/karriere": OVERVIEW,
             "https://www.beispiel.de/karriere/assistenz-gf": POSTING}
    ask, _ = _ask({"url": "https://www.beispiel.de/ueber-uns"})
    got = find_posting("Beispiel GmbH", TITLE, "Köln", api_key="k", ask=ask, fetch=pages.get)
    assert got is not None and got.source == "career_path"


def test_same_title_at_another_company_is_refused():
    elsewhere = POSTING.replace("Beispiel", "Fremdfirma").replace("beispiel", "fremdfirma")
    ask, _ = _ask({"url": "https://www.fremdfirma.de/jobs/1"})
    assert find_posting("Beispiel GmbH", TITLE, "Köln", api_key="k", ask=ask, fetch=lambda u: elsewhere) is None


def test_boards_and_invented_emails_are_not_taken():
    ask, _ = _ask({"url": "https://de.indeed.com/viewjob?jk=1", "email": "someone@gmail.com"},
                  ["https://www.stepstone.de/stellenangebote--x-1-inline.html", "https://www.beispiel.de/jobs/1"])
    got = find_posting("Beispiel GmbH", TITLE, "Köln", api_key="k", ask=ask, fetch=lambda u: POSTING)
    assert got == Posting("https://www.beispiel.de/jobs/1", "", "citation")


def test_closed_posting_and_no_key():
    closed = POSTING.replace("Bewirb dich", "Diese Stelle ist leider nicht mehr verfügbar.")
    ask, _ = _ask({"url": "https://www.beispiel.de/jobs/1"})
    assert find_posting("Beispiel GmbH", TITLE, "Köln", api_key="k", ask=ask, fetch=lambda u: closed) is None
    ask, calls = _ask({"url": "https://www.beispiel.de/jobs/1"})
    assert find_posting("Beispiel GmbH", TITLE, "Köln", api_key="", ask=ask, fetch=lambda u: POSTING) is None
    assert calls == []


def test_page_and_overview_helpers():
    assert page_is_job(POSTING, "https://www.beispiel.de/x", TITLE, "Beispiel GmbH")
    assert not page_is_job(OTHER_JOB, "https://www.beispiel.de/x", TITLE, "Beispiel GmbH")
    assert overview_link(OVERVIEW, "https://www.beispiel.de/karriere", TITLE) == "https://www.beispiel.de/karriere/assistenz-gf"


def test_a_trainee_posting_of_the_same_name_is_another_job():
    """02.11.2026 live: «Trainee Assistenz der Geschäftsführung» was taken for «Assistenz der Geschäftsführung»."""
    trainee = POSTING.replace("Assistenz der Geschäftsführung", "Trainee Assistenz der Geschäftsführung")
    overview = OVERVIEW.replace("<a href='/karriere/assistenz-gf'>Assistenz der Geschäftsführung (m/w/d) Teilzeit</a>",
                                "<a href='/karriere/trainee-gf'>Trainee Assistenz der Geschäftsführung</a>")
    assert not page_is_job(trainee, "https://www.beispiel.de/x", TITLE, "Beispiel GmbH")
    assert overview_link(overview, "https://www.beispiel.de/karriere", TITLE) is None
    # the qualifier in the job's own title is fine
    assert page_is_job(trainee, "https://www.beispiel.de/x", "Trainee Assistenz der Geschäftsführung", "Beispiel GmbH")


def test_company_only_in_the_body_of_a_non_ats_page_is_not_enough():
    """An aggregator page names the employer in its body; the employer's own site or ATS is needed."""
    agg = POSTING.replace("Stellenangebot von Beispiel", "Jobs in Köln").replace("Beispiel GmbH, Köln", "Ein Arbeitgeber (Beispiel), Köln").replace("jobs@beispiel.de", "")
    assert not page_is_job(agg, "https://www.some-aggregator.example/job/1", TITLE, "Beispiel GmbH")
    assert page_is_job(agg, "https://beispiel.softgarden.io/job/1", TITLE, "Beispiel GmbH")


def test_resolve_script_appends_once_and_lists_the_rest(tmp_path):
    import importlib.util

    spec = importlib.util.spec_from_file_location("resolve_employer", "scripts/resolve_employer.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    urls = tmp_path / "urls.txt"
    urls.write_text("https://www.beispiel.de/jobs/1\n", encoding="utf-8")
    found = {"Beispiel GmbH": Posting("https://www.beispiel.de/jobs/1", "", "answer"),
             "Zweite AG": Posting("https://www.zweite.example/karriere/2", "jobs@zweite.example", "overview")}
    leads = [{"url": "https://de.indeed.com/viewjob?jk=1", "company": "Beispiel GmbH", "title": "Assistenz", "city": "Köln"},
             {"url": "https://de.indeed.com/viewjob?jk=2", "company": "Zweite AG", "title": "Empfang", "city": "Köln"},
             {"url": "https://de.indeed.com/viewjob?jk=3", "company": "Dritte KG", "title": "Büro", "city": "Köln"},
             {"url": "https://de.indeed.com/viewjob?jk=4", "company": "", "title": "Unbekannt", "city": ""}]
    out = mod.resolve(leads, urls, lookup=lambda company, title, city: found.get(company))
    assert [r["employer_url"] for r in out["resolved"]] == ["https://www.beispiel.de/jobs/1", "https://www.zweite.example/karriere/2"]
    assert [r["url"] for r in out["unresolved"]] == ["https://de.indeed.com/viewjob?jk=3", "https://de.indeed.com/viewjob?jk=4"]
    assert urls.read_text(encoding="utf-8").split() == ["https://www.beispiel.de/jobs/1", "https://www.zweite.example/karriere/2"]
    assert out["appended"] == 1
