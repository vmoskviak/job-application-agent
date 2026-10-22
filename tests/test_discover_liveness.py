import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from bot.generators.discover import process_urls, IntakeStats
from bot.generators.intake import JobData
from bot.scrapers.models import IntakeResult


def _job():
    return JobData(
        title="BA",
        company="Co",
        location="DE",
        description="x" * 200,
        email=None,
        application_url=None,
        source_url="https://x.de",
        is_portal=False,
    )


@patch("bot.generators.discover.intake_with_meta")
def test_process_urls_excludes_closed(mock_intake):
    mock_intake.side_effect = [
        IntakeResult(job=None, liveness="closed", intake_method="httpx", reason="besetzt"),
        IntakeResult(job=_job(), liveness="live", intake_method="httpx", reason="ok"),
    ]
    digest, stats, manual = process_urls(["https://closed.de", "https://live.de"], verbose=False)
    assert len(digest) == 1
    assert stats.closed == 1
    assert stats.live == 1
    assert stats.httpx_ok == 2
    assert stats.playwright == 0
    assert digest[0]["liveness"] == "live"
    assert digest[0]["intake_method"] == "httpx"
    assert manual == []  # closed = confirmed dead, not surfaced to manual


@patch("bot.generators.discover.intake_with_meta")
def test_process_urls_surfaces_blocked_as_manual(mock_intake):
    # Indeed CAPTCHA / login-wall → "blocked": не скрапиться, але міг бути живим.
    mock_intake.side_effect = [
        IntakeResult(job=None, liveness="blocked", intake_method="playwright", reason="matched: captcha"),
        IntakeResult(job=None, liveness="unknown", intake_method="httpx", reason="description too short"),
        IntakeResult(job=_job(), liveness="live", intake_method="httpx", reason="ok"),
    ]
    digest, stats, manual = process_urls(
        ["https://indeed.de/viewjob?jk=1", "https://weird.de/jobs/x", "https://live.de"],
        verbose=False,
    )
    assert len(digest) == 1
    assert stats.blocked == 1 and stats.unknown == 1 and stats.live == 1
    assert len(manual) == 2
    assert manual[0]["source_url"] == "https://indeed.de/viewjob?jk=1"
    assert manual[0]["liveness"] == "blocked"
    assert manual[0]["apply_channel"] == "manual_link"
    assert manual[1]["liveness"] == "unknown"
