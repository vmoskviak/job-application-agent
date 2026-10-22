import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from bot.apply.detect import classify_apply_page

STEPSTONE_URL = "https://www.stepstone.de/stellenangebote--Teamassistenz--123-inline.html"
XING_URL = "https://www.xing.com/jobs/duesseldorf-teamassistenz-100000103"


def test_stepstone_quick_detected():
    html = "<html><button data-at='button-apply'>Schnelle Bewerbung</button></html>"
    assert classify_apply_page(STEPSTONE_URL, html) == "stepstone_quick"


def test_stepstone_without_quick_is_external():
    html = "<html><a href='https://ats.example.com'>Auf Firmenwebsite bewerben</a></html>"
    assert classify_apply_page(STEPSTONE_URL, html) == "external_ats"


def test_xing_easy_detected():
    html = "<html><button data-testid='job-apply-button'>Einfach bewerben</button></html>"
    assert classify_apply_page(XING_URL, html) == "xing_easy"


def test_linkedin_always_manual_tier():
    html = "<html><button>Easy Apply</button></html>"
    assert classify_apply_page("https://www.linkedin.com/jobs/view/1", html) == "linkedin_easy"


def test_captcha_wins_over_everything():
    html = "<html>Schnelle Bewerbung <div class='g-recaptcha'></div></html>"
    assert classify_apply_page(STEPSTONE_URL, html) == "captcha"


def test_login_wall_detected():
    html = "<html>Bitte melden Sie sich an, um fortzufahren</html>"
    assert classify_apply_page(XING_URL, html) == "login_required"


def test_closed_vacancy_detected():
    html = "<html>Diese Stellenanzeige ist nicht mehr verfügbar.</html>"
    assert classify_apply_page(STEPSTONE_URL, html) == "closed"
