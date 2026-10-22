"""Settle-цикл браузерного фетчу: чекати тіло вакансії, а не шапку SPA.

Регресія 27.07.2026: два XING-ліди Musterfirma приїхали зі скрейпу залогіненого
профілю з описом «Navigation überspringen / Jobs finden / Deine Jobs / …»,
400 символів меню й футера. Цикл виходив на першому такті без приросту,
бо навігаційна шапка вже не росла, а тіло вакансії ще не відрендерилось.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from bot.scrapers.browser import MIN_BODY_CHARS, settle_body

NAV_SHELL = (
    "Navigation überspringen\nJobs finden\nDeine Jobs\nNetzwerk\nInsights\n"
    "Nachrichten\n12\nMitteilungen\nSuche\nProdukte\nUnternehmen\n"
    "© New Work SE | Alle Rechte vorbehalten\nImpressum\nAGB\nDatenschutz"
)
JOB_BODY = NAV_SHELL + "\n" + ("Assistenz / Office Management in Düsseldorf. " * 60)


class FakePage:
    """Віддає задану послідовність body-текстів, по одному на такт."""

    def __init__(self, snapshots: list[str]):
        self.snapshots = snapshots
        self.reads = 0
        self.waits = 0

    def wait_for_timeout(self, ms: int) -> None:
        self.waits += 1

    def inner_text(self, selector: str) -> str:
        idx = min(self.reads, len(self.snapshots) - 1)
        self.reads += 1
        return self.snapshots[idx]


def test_nav_shell_is_below_floor():
    assert len(NAV_SHELL) < MIN_BODY_CHARS
    assert len(JOB_BODY) >= MIN_BODY_CHARS


def test_settle_waits_out_a_stable_nav_shell():
    """Шапка не росте три такти поспіль — це не привід зупинятись."""
    page = FakePage([NAV_SHELL, NAV_SHELL, NAV_SHELL, JOB_BODY])
    assert settle_body(page, settle_ms=15000, text=NAV_SHELL) == JOB_BODY


def test_settle_returns_as_soon_as_real_body_is_stable():
    page = FakePage([JOB_BODY])
    assert settle_body(page, settle_ms=15000, text=JOB_BODY) == JOB_BODY
    assert page.reads == 1


def test_settle_gives_up_at_deadline_on_a_genuinely_short_page():
    """Коротка сторінка (закрита вакансія) не має крутити цикл до вічності."""
    page = FakePage([NAV_SHELL])
    assert settle_body(page, settle_ms=30, text=NAV_SHELL) == NAV_SHELL


def test_settle_disabled_returns_input():
    page = FakePage([JOB_BODY])
    assert settle_body(page, settle_ms=0, text=NAV_SHELL) == NAV_SHELL
    assert page.reads == 0
