"""Форми: мовні select-и за профілем, відповіді з form_answers, без вгадування."""
from bot.apply.forms import choose_radio, pick_language_level, text_answer

DE_OPTS = ["Keine", "Grundkenntnisse", "Gut", "Fließend", "Verhandlungssicher", "Muttersprache"]


def test_language_level_from_profile_not_first_option():
    """08.10.2026: бот вибрав «Keine» як першу опцію обов'язкового select-а."""
    meta = "bitte stufen sie ihre deutschkenntnisse ein: *"
    assert pick_language_level(meta, DE_OPTS, {"Deutsch": "C2"}) == "Muttersprache"
    assert pick_language_level(meta, DE_OPTS, {"Deutsch": "B1-B2"}) == "Gut"
    assert pick_language_level("wie gut sind ihre englischkenntnisse?", DE_OPTS, {"Englisch": "B1-B2"}) == "Gut"


def test_language_level_unknown_stays_empty():
    meta = "bitte stufen sie ihre deutschkenntnisse ein"
    assert pick_language_level(meta, DE_OPTS, {}) is None
    assert pick_language_level(meta, ["Stufe 1", "Stufe 2"], {"Deutsch": "C2"}) is None
    assert pick_language_level("führerschein", DE_OPTS, {"Deutsch": "C2"}) is None


def test_text_answer_by_question_pattern():
    rules = [{"pattern": r"vollzeit oder teilzeit|stunden pro woche", "answer": "Teilzeit, 20 bis 25 Stunden pro Woche"}]
    q = "möchten sie in vollzeit oder teilzeit arbeiten? falls teilzeit: wie viel stunden pro woche? *"
    assert text_answer(q, rules) == "Teilzeit, 20 bis 25 Stunden pro Woche"
    assert text_answer("bau & plan standort *", rules) is None


def test_choose_radio_only_for_configured_questions():
    rules = [{"pattern": r"weitere stellen|talentpool", "choice": "no"}]
    q = "Ich bin damit einverstanden, dass meine Bewerbung auch für weitere Stellen berücksichtigt werden darf."
    assert choose_radio(q, ["Yes", "No"], rules) == 1
    assert choose_radio(q, ["Ja", "Nein"], rules) == 1
    assert choose_radio("Besitzen Sie einen Führerschein?", ["Ja", "Nein"], rules) is None
    assert choose_radio(q, ["Vielleicht"], rules) is None


def test_language_level_gut_is_not_sehr_gut():
    opts = ["Sehr gut", "Gut", "Grundkenntnisse"]
    assert pick_language_level("englischkenntnisse", opts, {"Englisch": "B1-B2"}) == "Gut"
