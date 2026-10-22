import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from bot.scrapers.liveness import check_liveness, is_login_wall


def test_closed_careers_portal_pattern():
    text = "Diese Stelle wurde leider bereits besetzt. Vielen Dank für Ihr Interesse."
    status, reason = check_liveness(text, "https://careers.example.com/job/123")
    assert status == "closed"
    assert "besetzt" in reason.lower()


def test_closed_nicht_veroeffentlicht():
    text = "Diese Stelle ist nicht veröffentlicht."
    status, _ = check_liveness(text, "https://www.rheinland.jobs/job/16538912")
    assert status == "closed"


def test_blocked_linkedin_login():
    text = "Sign in Join now Email or phone Password"
    assert is_login_wall(text, "https://www.linkedin.com/jobs/view/123") is True
    status, _ = check_liveness(text, "https://www.linkedin.com/jobs/view/123")
    assert status == "blocked"


def test_live_job_has_substance():
    text = "Teamassistenz (m/w/d)\nBeispiel TechnikService GmbH\nErkrath\n" + ("Aufgaben " * 50)
    status, _ = check_liveness(text, "https://www.stepstone.de/stellenangebote--test--10000004-inline.html")
    assert status == "live"


def test_blocked_stepstone_cookie_only():
    text = "Jobs bei StepStone Datenschutz Cookie-Einstellungen"
    status, _ = check_liveness(text, "https://www.stepstone.de/stellenangebote--x--1-inline.html")
    assert status == "blocked"


def test_live_despite_ref_number_404_in_list():
    text = (
        "Teamassistenz (m/w/d) Office Management - Düsseldorf\n"
        "Im Kundenauftrag suchen wir\n"
        + "\n".join(str(n) for n in range(390, 420))
        + "\n"
        + ("Aufgaben Koordination Büro " * 30)
    )
    status, _ = check_liveness(text, "https://www.muster-partner.example/jobs.php?refnr=345641")
    assert status == "live"


def test_closed_explicit_404_page():
    text = "Seite nicht gefunden. Error 404."
    status, reason = check_liveness(text, "https://example.de/missing")
    assert status == "closed"
    assert "404" in reason


def test_closed_linkedin_abgelaufen():
    # знята LinkedIn-вакансія в німецькому UI (вечірній запуск 21.07: 4 такі
    # пройшли як live, бо в патернах був лише англійський варіант)
    text = (
        "Home\nJobs\nTeamassistenz (m/w/d)\n"
        "Diese Stellenausschreibung ist abgelaufen\n"
        "Das bedeutet, dass der Arbeitgeber keine Bewerbungen mehr annimmt."
    )
    status, reason = check_liveness(text, "https://de.linkedin.com/jobs/view/x-at-y-1000000008")
    assert status == "closed"
    assert "abgelaufen" in reason
