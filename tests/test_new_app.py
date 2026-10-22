import sys, json
from pathlib import Path
from unittest.mock import MagicMock, patch
sys.path.insert(0, str(Path(__file__).parent.parent))


def test_make_folder_name_basic():
    from new_app import make_folder_name
    name = make_folder_name("Test GmbH", "Junior Business Analyst (m/w/d)")
    assert "Test" in name
    assert "Junior" in name or "Business" in name
    assert len(name) < 60
    assert " " not in name


def test_make_folder_name_strips_mwd():
    from new_app import make_folder_name
    name = make_folder_name("Muster AG", "Trainee IT (m/w/d)")
    assert "(m/w/d)" not in name
    assert "mwd" not in name.lower()


def test_write_data_only_generate_py(tmp_path):
    from new_app import write_data_only_generate_py
    from bot.generators.intake import JobData
    from bot.generators.tailor import TailoredData

    job = JobData("Junior BA", "Test GmbH", "Düsseldorf",
                  "Beschreibung", "jobs@test.de", None, "https://test.de", False)
    tailored = TailoredData(
        cv_title="Junior BA | Test",
        profile="Test.",
        kernkompetenzen=[("Methoden", "Requirements Engineering")],
        experience_variant="ba", skills_order="ba",
        fit_score=70, fit_reason="OK", salary="48000",
        anschreiben="Sehr geehrte Damen und Herren,\n\nTest.\n\nMit freundlichen Grüßen",
    )
    write_data_only_generate_py(job, tailored, tmp_path)
    py_path = tmp_path / "generate.py"
    assert py_path.exists()
    content = py_path.read_text()
    assert "render_application" in content
    assert "Junior BA | Test" in content
    assert "cv_engine" in content


def test_write_email_draft_with_email(tmp_path):
    from new_app import write_email_draft
    from bot.generators.intake import JobData
    from bot.generators.tailor import TailoredData

    job = JobData("Junior BA", "Test GmbH", "Düsseldorf",
                  "Beschreibung", "jobs@test.de", None, "https://test.de", False)
    tailored = TailoredData(
        cv_title="Junior BA | Test", profile="Test.",
        kernkompetenzen=[], experience_variant="ba", skills_order="ba",
        fit_score=70, fit_reason="OK", salary="48000",
        anschreiben="Sehr geehrte Damen und Herren,\n\nTest.\n\nMit freundlichen Grüßen",
    )
    write_email_draft(job, tailored, tmp_path)
    draft_path = tmp_path / "email_draft.json"
    assert draft_path.exists()
    draft = json.loads(draft_path.read_text())
    assert draft["to"] == "jobs@test.de"
    assert "Junior BA" in draft["subject"]
    assert draft["from"] == "erika.mustermann.jobs@example.com"
    # CV + Anschreiben з папки заявки, Zeugnisse — зі спільної attachments/.
    # Лічильник береться з ZEUGNIS_FILES, щоб додавання Zeugnis не ламало тест.
    from bot.generators.cv_engine import ZEUGNIS_FILES
    assert len(draft["attachments"]) == 2 + len(ZEUGNIS_FILES)
    assert sum("/to_send/" in a for a in draft["attachments"]) == 2
    assert sum("/attachments/" in a for a in draft["attachments"]) == len(ZEUGNIS_FILES)


def test_write_email_draft_no_email(tmp_path):
    from new_app import write_email_draft
    from bot.generators.intake import JobData
    from bot.generators.tailor import TailoredData

    job = JobData("Junior BA", "Test GmbH", "Düsseldorf",
                  "Beschreibung", None, None, "https://test.de", False)
    tailored = TailoredData(
        cv_title="T", profile="P", kernkompetenzen=[],
        experience_variant="ba", skills_order="ba",
        fit_score=50, fit_reason="OK", salary="48000", anschreiben="A",
    )
    write_email_draft(job, tailored, tmp_path)
    assert not (tmp_path / "email_draft.json").exists()


def test_update_tracker_row_keeps_source_url(tmp_path, monkeypatch):
    # Рядок ручного пакета без URL не давав dedup-у за URL нічого зловити:
    # наступного вечора та сама вакансія з company="Unbekannt" відбиралась знову.
    import new_app
    from bot.generators.intake import JobData

    tracker = tmp_path / "tracker.md"
    tracker.write_text("| Date | Position | Company, Ort | Status / Notes |\n", encoding="utf-8")
    monkeypatch.setattr(new_app, "TRACKER_PATH", tracker)
    job = JobData("Teamassistenz (m/w/d)", "Unbekannt", "Düsseldorf", "Text",
                  None, None, "https://www.beispiel-agentur.de/job-detail/ref/jn-1", False)
    new_app.update_tracker(job, None, tmp_path)
    row = tracker.read_text(encoding="utf-8").splitlines()[-1]
    assert row.endswith("· https://www.beispiel-agentur.de/job-detail/ref/jn-1 |")
    assert "Файли готові" in row


def test_update_tracker_row_escapes_pipes_in_title(tmp_path, monkeypatch):
    # LinkedIn-назва з «|» додавала зайву колонку в рядок трекера (16.10.2026).
    import new_app
    from bot.generators.intake import JobData

    tracker = tmp_path / "tracker.md"
    tracker.write_text("", encoding="utf-8")
    monkeypatch.setattr(new_app, "TRACKER_PATH", tracker)
    job = JobData("Assistenz der Geschäftsführung | Office Manager", "Beispiel e.V.",
                  "Düsseldorf", "Text", None, None, None, False)
    new_app.update_tracker(job, None, tmp_path)
    row = tracker.read_text(encoding="utf-8").strip()
    cells = row.strip("|").split(" | ")
    assert len(cells) == 4
    assert cells[1].strip() == "Assistenz der Geschäftsführung / Office Manager"
