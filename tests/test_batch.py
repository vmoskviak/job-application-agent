import sys
import json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))


def _make_batch_item(company="Test GmbH", email="jobs@test.de"):
    return {
        "job": {
            "title": "Teamassistenz",
            "company": company,
            "location": "Düsseldorf",
            "description": "Büromanagement",
            "email": email,
            "application_url": None,
            "source_url": "https://test.de",
            "is_portal": False,
        },
        "tailored": {
            "cv_title": "Teamassistenz | Büromanagement · Verwaltung",
            "profile": "Erfahrene Bürokraft mit DSH-3.",
            "kernkompetenzen": [["Büromanagement", "Dokumentenverwaltung · MS Office"]],
            "experience_variant": "buero",
            "skills_order": "buero",
            "fit_score": 72,
            "fit_reason": "Direkte Verwaltungserfahrung",
            "salary": "42000",
            "anschreiben": "Sehr geehrte Damen und Herren,\n\nTest.\n\nMit freundlichen Grüßen",
        },
    }


def test_load_batch_file(tmp_path):
    from bot.generators.batch import load_batch_file
    items = [_make_batch_item("A GmbH"), _make_batch_item("B AG", email=None)]
    batch_file = tmp_path / "batch.json"
    batch_file.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")

    loaded = load_batch_file(batch_file)
    assert len(loaded) == 2
    assert loaded[0]["job"]["company"] == "A GmbH"


def test_split_email_and_portal(tmp_path):
    from bot.generators.batch import split_email_and_portal
    items = [
        _make_batch_item("Email Co",  email="jobs@email.de"),
        _make_batch_item("Portal Co", email=None),
    ]
    email_items, portal_items = split_email_and_portal(items)
    assert len(email_items) == 1
    assert len(portal_items) == 1
    assert email_items[0]["job"]["company"] == "Email Co"
    assert portal_items[0]["job"]["company"] == "Portal Co"


def test_split_strips_dead_email_company(capsys):
    from bot.generators.batch import split_email_and_portal
    items = [
        _make_batch_item("Beispielwerk GmbH", email="bewerbung@beispielwerk.example"),
        _make_batch_item("Email Co",      email="jobs@email.de"),
    ]
    email_items, portal_items = split_email_and_portal(items)
    assert [it["job"]["company"] for it in email_items] == ["Email Co"]
    assert [it["job"]["company"] for it in portal_items] == ["Beispielwerk GmbH"]
    # мутація в самому dict: render_from_data не має побачити email
    assert items[0]["job"]["email"] is None
    assert "мертвий" in capsys.readouterr().out


def test_batch_summary_report(tmp_path):
    from bot.generators.batch import batch_summary_report
    items = [
        _make_batch_item("A GmbH", email="a@a.de"),
        _make_batch_item("B AG",   email=None),
        _make_batch_item("C GmbH", email="c@c.de"),
    ]
    report = batch_summary_report(items, ready=["A GmbH", "C GmbH"], portal=["B AG"])
    assert "A GmbH" in report
    assert "B AG" in report
    assert "2" in report
