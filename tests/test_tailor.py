import sys, json
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch
sys.path.insert(0, str(Path(__file__).parent.parent))

from bot.generators.tailor import parse_tailor_response, TailoredData, determine_salary


def test_parse_tailor_response_valid():
    raw = json.dumps({
        "cv_title": "Junior Business Analyst | Jira · Confluence",
        "profile": "Praxisorientierte Berufseinsteigerin...",
        "kernkompetenzen": [["Methoden", "Requirements Engineering"]],
        "experience_variant": "ba",
        "skills_order": "ba",
        "fit_score": 72,
        "fit_reason": "Guter Match",
        "salary": "48000",
        "anschreiben": "Sehr geehrte Damen und Herren,\n\nTest.\n\nMit freundlichen Grüßen",
    })
    result = parse_tailor_response(raw)
    assert isinstance(result, TailoredData)
    assert result.fit_score == 72
    assert result.experience_variant == "ba"
    assert result.kernkompetenzen == [("Methoden", "Requirements Engineering")]


def test_parse_tailor_response_invalid_json():
    result = parse_tailor_response("not json {{{")
    assert result is None


def test_determine_salary_uses_client_default():
    for variant in ("ba", "trainee", "sachbearbeitung", "buero", "sprache", "agile"):
        assert determine_salary(variant) == "48000"


def test_tailor_calls_claude_api():
    from bot.generators.intake import JobData
    from bot.generators.tailor import tailor

    mock_response = MagicMock()
    mock_response.content = [MagicMock(text=json.dumps({
        "cv_title": "Junior BA | Test",
        "profile": "Test profile",
        "kernkompetenzen": [["Methoden", "Requirements Engineering"]],
        "experience_variant": "ba",
        "skills_order": "ba",
        "fit_score": 65,
        "fit_reason": "OK Match",
        "salary": "48000",
        "anschreiben": "Sehr geehrte Damen und Herren,\n\nTest.\n\nMit freundlichen Grüßen",
    }))]

    job = JobData(
        title="Junior BA", company="Test GmbH", location="Düsseldorf",
        description="Wir suchen einen Junior Business Analyst...",
        email=None, application_url=None, source_url="", is_portal=False,
    )

    pytest.importorskip("anthropic")
    with patch("anthropic.Anthropic") as MockClient:
        mock_client = MockClient.return_value
        mock_client.messages.create.return_value = mock_response
        result = tailor(job, api_key="test-key")

    assert result is not None
    assert result.fit_score == 65
    assert result.cv_title == "Junior BA | Test"


def test_parse_tailor_response_strips_markdown_fences():
    raw = "```json\n" + json.dumps({
        "cv_title": "Test",
        "profile": "P",
        "kernkompetenzen": [],
        "experience_variant": "agile",
        "skills_order": "agile",
        "fit_score": 50,
        "fit_reason": "OK",
        "salary": "48000",
        "anschreiben": "Text",
    }) + "\n```"
    result = parse_tailor_response(raw)
    assert result is not None
    assert result.cv_title == "Test"
