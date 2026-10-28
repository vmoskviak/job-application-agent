"""Сторож транслітерації в німецьких текстах пакета (регресія 22.10.2026:
шість Anschreiben поспіль з «fuer», «Gespraech», «Geschaeftsfuehrung»)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from bot.generators.tailor import TailoredData
from bot.generators.umlauts import check_tailored, transliterations

GOOD = (
    "Sehr geehrte Damen und Herren,\n\nmit großem Interesse bewerbe ich mich für die "
    "Position in Ihrer Geschäftsführung in Düsseldorf. Aktuell arbeite ich manuell und "
    "zuerst strukturiert, Confluence und Jira nutze ich täglich. Ich freue mich auf ein "
    "persönliches Gespräch.\n\nMit freundlichen Grüßen"
)
BAD = (
    "Sehr geehrte Damen und Herren,\n\nmit grossem Interesse bewerbe ich mich fuer die "
    "Position in Ihrer Geschaeftsfuehrung in Duesseldorf. Ich freue mich auf ein "
    "persoenliches Gespraech.\n\nMit freundlichen Gruessen"
)


def _tailored(letter: str, profile: str = "Erfahrene Assistenz mit Organisationstalent.") -> TailoredData:
    return TailoredData(cv_title="Teamassistenz", profile=profile,
                        kernkompetenzen=[("Organisation", "Termine, Reisen")],
                        experience_variant="buero", skills_order="buero", fit_score=70,
                        fit_reason="", salary="42000", anschreiben=letter)


def test_proper_german_passes():
    assert check_tailored(_tailored(GOOD)) == []


def test_transliterated_letter_fails():
    hits = check_tailored(_tailored(BAD))
    assert "fuer" in hits and "Gespraech" in hits


def test_strong_word_in_profile_alone_fails():
    t = _tailored(GOOD, profile="Zuverlaessige Assistenz mit Erfahrung in der Buchhaltung.")
    assert check_tailored(t) == ["Zuverlaessige"]


def test_weak_hits_need_text_without_umlauts():
    # поодинокі справжні ae/oe у тексті з умлаутами: Coesfeld, Soest, Israel
    text = "Ich wohne in Coesfeld, arbeitete in Soest und Israel. Schöne Grüße"
    assert transliterations(text) == []
    # без жодного умлаута три слабкі хіти вже підозра
    assert transliterations("Die Plaene, Loesung und Schluessel sind fertig.") != []


def test_known_names_are_skipped():
    letter = GOOD + "\nBei der Buero Beispiel GmbH in Koeln"
    assert check_tailored(_tailored(letter), known=("Buero Beispiel GmbH", "Koeln")) == []
    assert check_tailored(_tailored(letter)) != []


def test_english_words_do_not_trigger():
    assert transliterations("Value, queue, issue, true blue, due revenue, Tuesday") == []


def test_render_from_data_rejects_transliterated_package(tmp_path, monkeypatch):
    import new_app

    monkeypatch.setattr(new_app, "APPLICATIONS_DIR", tmp_path)
    job = {"title": "Teamassistenz (m/w/d)", "company": "Beispiel GmbH", "location": "Köln",
           "description": "Assistenz", "email": "jobs@beispiel.example", "application_url": None,
           "source_url": "https://beispiel.example/job/1", "is_portal": False}
    tailored = {"cv_title": "Teamassistenz", "profile": "Assistenz", "kernkompetenzen": [],
                "anschreiben": BAD}
    assert new_app.render_from_data(job, tailored) is None
    assert not any(tmp_path.iterdir())  # ні папки, ні рядка трекера
