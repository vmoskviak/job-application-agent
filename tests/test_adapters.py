import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from bot.scrapers.adapters import get_adapter


def test_get_adapter_stepstone():
    assert get_adapter("https://www.stepstone.de/stellenangebote--x--1-inline.html").NAME == "stepstone"


def test_get_adapter_linkedin():
    assert get_adapter("https://www.linkedin.com/jobs/view/1").NAME == "linkedin"


def test_get_adapter_generic():
    assert get_adapter("https://www.musterbau.example/jobs.php?refnr=1").NAME == "generic"
