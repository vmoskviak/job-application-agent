import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from bot.scrapers.adapters.linkedin import LinkedInAdapter
from bot.scrapers.models import PageFetch

NAV_JUNK = (
    "0 notifications\nSkip to search\nSkip to main content\n"
    "Home\nMy Network\nJobs\nMessaging\n20\nNotifications\nMe\nFor Business\nHire with AI\n"
)

DESCRIPTION = (
    "Beispielwerk bietet führende Prüfungs- und Beratungsleistungen. "
    "Du analysierst Geschäftsprozesse und begleitest Cloud-Integrationsprojekte "
    "als Business Analyst im agilen Team."
)

# Logged-in SPA: без <h1>, title/company лише у <title>, опис під заголовком "About the job"
LOGGED_IN_HTML = (
    "<html><head><title>Business Analyst / Consultant Cloud Integration (m/w/d) "
    "in Düsseldorf | Beispielwerk | LinkedIn</title></head>"
    "<body><div>app shell</div></body></html>"
)
LOGGED_IN_TEXT = (
    NAV_JUNK
    + "\nBeispielwerk\n\nBusiness Analyst / Consultant Cloud Integration (m/w/d) in Düsseldorf\n"
    + "Düsseldorf, North Rhine-Westphalia, Germany · Reposted 2 days ago · 89 people clicked apply\n"
    + "Hybrid\nFull-time\nApply\nSave\nUse AI to assess how you fit\n"
    + "About the job\n"
    + DESCRIPTION
)

# Guest/public сторінка: класичний markup з <h1> і top-card лінком компанії
GUEST_HTML = (
    "<html><head><title>Beispielwerk hiring Business Analyst in Düsseldorf | LinkedIn</title></head>"
    "<body><h1>Business Analyst (m/w/d)</h1>"
    "<a data-tracking-control-name='public_jobs_topcard-org-name'>Beispielwerk</a>"
    "<div class='show-more-less-html__markup'>" + DESCRIPTION + "</div></body></html>"
)
GUEST_TEXT = "Business Analyst (m/w/d)\nBeispielwerk\n" + DESCRIPTION


def _fetch(html: str, text: str) -> PageFetch:
    return PageFetch(
        url="https://www.linkedin.com/jobs/view/1000000007",
        text=text,
        html=html,
        method="playwright",
        status_code=200,
    )


def test_logged_in_title_company_from_title_tag():
    job = LinkedInAdapter().extract(_fetch(LOGGED_IN_HTML, LOGGED_IN_TEXT))
    assert job.title == "Business Analyst / Consultant Cloud Integration (m/w/d) in Düsseldorf"
    assert job.company == "Beispielwerk"


def test_logged_in_description_starts_at_about_the_job():
    job = LinkedInAdapter().extract(_fetch(LOGGED_IN_HTML, LOGGED_IN_TEXT))
    assert job.description.startswith("About the job")
    assert "Cloud-Integrationsprojekte" in job.description
    assert "Skip to search" not in job.description


def test_logged_in_location_from_topcard():
    # location="Deutschland" + трим опису ховали місто від location_ok (outside_nrw
    # для Düsseldorf-вакансій); топ-кард несе і місто, і Hybrid/Remote-маркер
    job = LinkedInAdapter().extract(_fetch(LOGGED_IN_HTML, LOGGED_IN_TEXT))
    assert job.location == "Düsseldorf, North Rhine-Westphalia, Germany (Hybrid)"


def test_logged_in_location_fallback_without_topcard():
    job = LinkedInAdapter().extract(_fetch(GUEST_HTML, GUEST_TEXT))
    assert job.location == "Deutschland"


def test_guest_page_still_uses_h1_and_topcard():
    job = LinkedInAdapter().extract(_fetch(GUEST_HTML, GUEST_TEXT))
    assert job.title == "Business Analyst (m/w/d)"
    assert job.company == "Beispielwerk"
    assert "Cloud-Integrationsprojekte" in job.description


def test_title_tag_with_pipe_in_job_title():
    html = (
        "<html><head><title>PMO | Digital & Technology Manager | Novatrix "
        "| LinkedIn</title></head><body></body></html>"
    )
    text = NAV_JUNK + "\nAbout the job\n" + DESCRIPTION
    job = LinkedInAdapter().extract(_fetch(html, text))
    assert job.title == "PMO | Digital & Technology Manager"
    assert job.company == "Novatrix"
