"""Перевірки перед відправкою (спека §6, §15.4): блокують мовчки, лід стає held."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from bot.packages import Package
from bot.presend import _office_outside_eu, blocking, check_package, location_country

HEADER = ("# Application Tracker — Jordan Example\n\n"
          "| # | Date | Company | Position | Source | Salary range | Status | Last update | Next action | Notes |\n"
          "|---|---|---|---|---|---|---|---|---|---|\n")
ROW = "| 1 | 2026-10-20 | Contract Co | Senior Android Developer | LinkedIn |  | sent | 2026-10-20 | wait | Msg x |\n"


@pytest.fixture
def tracker(tmp_path):
    t = tmp_path / "tracker.md"
    t.write_text(HEADER, encoding="utf-8")
    return t


@pytest.fixture
def tracker_with_row(tmp_path):
    t = tmp_path / "tracker_row.md"
    t.write_text(HEADER + ROW, encoding="utf-8")
    return t


@pytest.fixture
def pkg(tmp_path):
    d = tmp_path / "packages" / "contract-co-senior-android-engineer"
    d.mkdir(parents=True)
    cv = tmp_path / "CV_Android.pdf"
    cv.write_bytes(b"%PDF-1.4 test")
    return Package("contract-co-senior-android-engineer", d,
                   {"title": "Senior Android Engineer", "company": "Contract Co", "category": "android",
                    "source_url": "https://www.linkedin.com/jobs/view/1/", "liveness": "live"},
                   "form", "https://boards.greenhouse.io/contractco/jobs/123", "greenhouse", "en",
                   "Contract: Senior Android Engineer",
                   "Hello Contract Co team,\n\nI build Android apps.\n\nBest regards\nJordan Example", [cv])


def _check(pkg, tracker, **kw):
    args = {"tracker_path": tracker, "expected_lang": "en", "forbidden_names": [], "hourly_min": None,
            "is_agency": False}
    args.update(kw)
    return check_package(pkg, **args)


def test_clean_package_passes(pkg, tracker):
    assert _check(pkg, tracker, forbidden_names=["Example Corp"], hourly_min=80) == []
    assert blocking([]) is False and blocking(["x"]) is True


def test_missing_attachment_holds(pkg, tracker):
    pkg.attachments = [pkg.dir / "CV_Nope.pdf"]
    assert "attachment_missing:CV_Nope.pdf" in _check(pkg, tracker)


def test_duplicate_in_tracker_holds(pkg, tracker_with_row):
    assert "duplicate_in_tracker" in _check(pkg, tracker_with_row)
    pkg.lead["title"] = "Data Engineer"      # інша роль у тієї ж компанії не дубль
    assert "duplicate_in_tracker" not in _check(pkg, tracker_with_row)


def test_forbidden_name_only_for_outside_tracker_channels(pkg, tracker):
    pkg.note += " At Example Corp I built things."
    assert _check(pkg, tracker, forbidden_names=["Example Corp"]) == []
    assert "forbidden_name:Example Corp" in _check(pkg, tracker, forbidden_names=["Example Corp"], is_agency=True)
    pkg.channel = "platform_login"
    assert "forbidden_name:example corp" in _check(pkg, tracker, forbidden_names=["example corp"])


def test_language_umlauts_rate(pkg, tracker):
    pkg.lang = "de"
    pkg.note = ("Guten Tag, ich moechte gerne ueber das Projekt sprechen und bringe zwoelf Jahre Android mit, "
                "die letzten Jahre mit Jetpack Compose in einer App mit ueber einer Million Installationen. "
                "Meine Stundensatz liegt bei 80 EUR.")
    reasons = _check(pkg, tracker, hourly_min=80)
    assert reasons[:3] == ["language_mismatch", "ascii_umlauts", "rate_min_leaked"]


def test_employment_unreachable_holds(pkg, tracker):
    pkg.lead.update({"engagement": "employment", "location": "Pittsburgh, PA", "remote_scope": "hybrid"})
    assert "employment_unreachable" in _check(pkg, tracker)
    pkg.lead.update({"remote_scope": "global"})      # US company, remote worldwide → passes
    assert "employment_unreachable" not in _check(pkg, tracker)
    pkg.lead.update({"remote_scope": "country:united states"})
    assert "employment_unreachable" in _check(pkg, tracker)
    pkg.lead.update({"location": "Berlin, Germany", "remote_scope": "hybrid"})
    assert "employment_unreachable" not in _check(pkg, tracker)
    pkg.lead.update({"engagement": "contract", "location": "Pittsburgh, PA", "remote_scope": "us_only"})
    assert "employment_unreachable" not in _check(pkg, tracker)


def test_location_country():
    assert location_country("Pittsburgh, PA") == "united states"
    assert location_country("Berlin, Germany") == "germany"
    assert location_country("Remote") is None


def test_not_live_and_channel_none(pkg, tracker):
    pkg.lead["liveness"] = "closed"
    pkg.channel = "none"
    reasons = _check(pkg, tracker)
    assert reasons[0] == "channel_none" and "not_live" in reasons


def test_no_attachments_holds(pkg, tracker):
    """Рев'ю 2b, Critical 1: порожній список вкладень означає подачу без CV."""
    pkg.attachments = []
    assert "attachment_none" in _check(pkg, tracker)


def test_language_mismatch_detected_from_note_text(pkg, tracker):
    """Рев'ю 2b, Important 4: мова береться з тексту нотатки, не з meta."""
    pkg.lang = "en"
    pkg.note = ("Hallo Team, vielen Dank für die Projektbeschreibung. Ich habe zwölf Jahre Android und "
                "Kotlin, die letzten Jahre mit Jetpack Compose in einer App mit über einer Million Installationen. "
                "Gerne bespreche ich das Projekt in einem kurzen Gespräch.")
    assert "language_mismatch" in _check(pkg, tracker, expected_lang="en")
    assert "language_mismatch" not in _check(pkg, tracker, expected_lang="de")


def test_location_country_knows_non_eu_hubs_without_country():
    """Дошки пишуть «San Francisco» чи «London» без країни."""
    assert location_country("San Francisco") == "united states"
    assert location_country("London") == "united kingdom"
    assert location_country("Toronto") == "canada"


def test_office_outside_eu_holds(pkg, tracker):
    """Рішення оператора 26.10 (спека §15.4): офіс чи гібрид поза ЄС/ЄЕЗ/Швейцарією пропускається
    за даними дошки, незалежно від engagement; remote_scope ліда з LinkedIn тут не рахується."""
    pkg.lead.update({"engagement": "contract", "remote_scope": "global",
                     "board_workplace": "hybrid", "board_location": "San Francisco"})
    assert "office_outside_eu" in _check(pkg, tracker)
    pkg.lead["board_location"] = "Berlin"
    assert "office_outside_eu" not in _check(pkg, tracker)
    pkg.lead.update({"board_workplace": "remote", "board_location": "San Francisco"})
    assert "office_outside_eu" not in _check(pkg, tracker)
    pkg.lead.update({"board_workplace": "onsite", "board_location": "Springfield"})   # країна невідома
    assert "office_outside_eu" not in _check(pkg, tracker)
    pkg.lead["board_location"] = "London, United Kingdom; Berlin, Germany"   # одна з локацій у ЄС
    assert "office_outside_eu" not in _check(pkg, tracker)
    pkg.lead["board_location"] = "New York, NY; London"
    assert "office_outside_eu" in _check(pkg, tracker)
    del pkg.lead["board_workplace"]   # дошка формату не сказала
    assert "office_outside_eu" not in _check(pkg, tracker)


def test_office_outside_eu_right_after_employment_unreachable(pkg, tracker):
    pkg.lead.update({"engagement": "employment", "location": "Pittsburgh, PA", "remote_scope": "hybrid",
                     "board_workplace": "onsite", "board_location": "Pittsburgh, PA"})
    reasons = _check(pkg, tracker)
    assert reasons[-2:] == ["employment_unreachable", "office_outside_eu"]
    assert reasons.count("office_outside_eu") == 1


def test_city_fallback_only_for_a_bare_city():
    """Рев'ю live-fixes, M1: таблиця міст лише для голої назви міста; «London or Berlin» не Британія."""
    assert location_country(" San Francisco ") == "united states"
    for loc in ("London or Berlin", "Amsterdam / London", "Berlin, London"):
        assert location_country(loc) is None, loc
        assert _office_outside_eu({"board_workplace": "hybrid", "board_location": loc}) is False, loc


def test_platform_off_holds_platform_leads(pkg, tracker):
    pkg.channel, pkg.apply_url = "platform_login", "https://www.freelance.de/projekte/projekt-1-x"
    assert "platform_off:freelance_de" in _check(pkg, tracker, platforms={"freelance_de": "off"})
    assert "platform_off:freelance_de" in _check(pkg, tracker, platforms={})      # не оголошена = off
    assert not any(r.startswith("platform_off") for r in _check(pkg, tracker, platforms={"freelance_de": "shadow"}))
    assert not any(r.startswith("platform_off") for r in _check(pkg, tracker, platforms={"freelance_de": "on"}))
    assert not any(r.startswith("platform_off") for r in _check(pkg, tracker))    # без platforms нічого не міняється


def test_platform_off_holds_linkedin_easy_but_not_form(pkg, tracker):
    pkg.channel, pkg.apply_url = "linkedin_easy", "https://www.linkedin.com/jobs/view/1/"
    assert "platform_off:linkedin" in _check(pkg, tracker, platforms={"linkedin": "off"})
    assert "platform_off:linkedin" not in _check(pkg, tracker, platforms={"linkedin": "on"})
    pkg.channel, pkg.apply_url = "form", "https://boards.greenhouse.io/contractco/jobs/123"
    assert _check(pkg, tracker, platforms={}) == []


def test_platform_lead_tracker_duplicate_only_by_url(pkg, tmp_path):
    tracker = tmp_path / "tracker_gulp.md"
    tracker.write_text(HEADER + "| 1 | 2026-10-20 | Agency One (GULP) | Android Dev | contract: gulp |  | sent "
                       "| 2026-10-20 | wait | https://www.gulp.de/gulp2/g/projekte/agentur/C1 |\n", encoding="utf-8")
    pkg.lead.update(company="Agency One (GULP)", title="Android Dev")
    pkg.channel, pkg.apply_url = "platform_login", "https://www.gulp.de/gulp2/g/projekte/agentur/C1"
    assert "duplicate_in_tracker" in _check(pkg, tracker)
    pkg.apply_url = "https://www.gulp.de/gulp2/g/projekte/agentur/C2"
    assert "duplicate_in_tracker" not in _check(pkg, tracker)
    pkg.channel = "form"                       # інші канали: компанія й роль, як було
    assert "duplicate_in_tracker" in _check(pkg, tracker)
