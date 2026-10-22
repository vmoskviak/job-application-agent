import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from bot.generators.anschreiben_templates import (
    get_template,
    CATEGORIES_WITH_TEMPLATES,
    ALL_CATEGORIES,
)


def test_all_buero_categories_have_templates():
    for cat in ["B1_hausverwaltung", "B2_foerderwesen", "B3_versicherung",
                "B4_assistenz", "B5_hr", "B6_logistik", "B7_oeffentlich", "B8_dolmetschen"]:
        tmpl = get_template(cat)
        assert tmpl is not None, f"Missing template for {cat}"
        assert len(tmpl["anlass"]) > 50, f"{cat}: anlass too short"
        assert len(tmpl["fit"]) > 50, f"{cat}: fit too short"
        assert "[FIRMA]" in tmpl["anlass"] or "[FIRMA]" in tmpl["fit"], \
            f"{cat}: must contain [FIRMA] placeholder"


def test_get_template_returns_none_for_it_categories():
    assert get_template("T1_ba") is None
    assert get_template("T4_trainee") is None


def test_get_template_returns_none_for_unknown():
    assert get_template("unknown") is None


def test_all_categories_lists_only_buero():
    assert set(CATEGORIES_WITH_TEMPLATES) == {
        "B1_hausverwaltung", "B2_foerderwesen", "B3_versicherung",
        "B4_assistenz", "B5_hr", "B6_logistik", "B7_oeffentlich", "B8_dolmetschen",
        "B9_empfang",
    }


def test_template_has_salary_hint():
    tmpl = get_template("B3_versicherung")
    assert tmpl["salary_hint"] == "48000"


def test_template_fit_mentions_zeugnis_evidence():
    for cat in CATEGORIES_WITH_TEMPLATES:
        tmpl = get_template(cat)
        combined = tmpl["anlass"] + tmpl["fit"] + tmpl["transfer"]
        assert any(word in combined for word in [
            "Beispiel Logistik", "Muster Versicherung"
        ]), f"{cat}: template must reference at least one confirmed employer"
