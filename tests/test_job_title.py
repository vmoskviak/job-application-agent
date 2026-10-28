"""Відновлення назви вакансії з тексту сторінки (bot.job_title)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from bot.job_title import (
    company_from_text, is_junk_company, is_junk_title, recover_title,
)


def test_recover_from_lower_level_heading():
    text = "Suchen\n\n##### Office Manager (m/w/d)\n\n[Muster GmbH](https://de.indeed.com/cmp/Muster)\n"
    assert recover_title(text) == "Office Manager (m/w/d)"
    assert company_from_text(text) == "Muster GmbH"


def test_recover_skips_section_and_date_headings():
    text = "Suchen\n\n#### Stellenbeschreibung\n\n### **01.02.2026**\n\n##### Sekretär\\*in\n"
    assert recover_title(text) == "Sekretär*in"


def test_junk_h1_blocks_recovery():
    # сторінка 404: «## Go to» над «# 404» — навігація, не назва
    text = "## Go to\n\n[Navigation](https://x.example/#nav)\n\n# 404\n\nThe site is on the move."
    assert recover_title(text) is None


def test_expired_banner_is_not_a_title():
    text = ("Suchen\n\n### Diese Stellenanzeige ist auf Indeed abgelaufen\n\n"
            "##### Scrum Master (m/w/d)\n")
    assert recover_title(text) == "Scrum Master (m/w/d)"


def test_marker_line_fallback_and_foreign_jobs_stop():
    plain = "Navigation\nAnmelden\nBürokaufmann (m/w/d) in Teilzeit\nBeispiel Bau GmbH\n"
    assert recover_title(plain) == "Bürokaufmann (m/w/d) in Teilzeit"
    # речення з малої літери — не назва
    assert recover_title("Skip to content\nwir suchen eine Assistenz (m/w/d)\n") is None
    # під «Ähnliche Jobs» — чужі вакансії
    closed = "Jobs finden\nAnmelden\nÄhnliche Jobs\nSekretariat (m/w/d)\nKöln\n"
    assert recover_title(closed) is None


def test_junk_predicates():
    assert is_junk_title("Suchen") and is_junk_title("Unbekannte Stelle")
    assert not is_junk_title("Empfang")
    for junk in ("##### Assistenz bei der Muster GmbH", "!Muster GmbH logo", "Unbekannt", ""):
        assert is_junk_company(junk), junk
    assert not is_junk_company("Muster GmbH")
