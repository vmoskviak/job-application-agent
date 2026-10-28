import json
from pathlib import Path

from bot.apply.ats_fields import (
    FIELD_JS,
    FIELD_SELECTOR,
    Field,
    classify,
    fields_from,
    visible_required,
)


def fixture(name: str):
    return json.loads((Path(__file__).parent / "fixtures" / "ats" / name).read_text(encoding="utf-8"))


# JSON-фікстури згенеровано прогоном tests/test_ats_browser.py по збережених сторінках
# ashby_application.html і greenhouse_application.html (JOBSEARCH_BROWSER_TESTS=1 JOBSEARCH_REGEN_FIXTURES=1).

def test_fields_from_fixture():
    fs = fields_from(fixture("ashby_form_fields.json"))
    assert fs[1].label == "Name" and fs[1].id == "_systemfield_name" and fs[1].required and fs[1].i == 1
    gh = fields_from(fixture("greenhouse_form_fields.json"))
    assert gh[5].visible is False and gh[5].i == 5 and gh[5].required   # aria-hidden дзеркало react-select


def test_classify_ashby():
    fs = [f for f in fields_from(fixture("ashby_form_fields.json")) if f.visible]
    kinds = [classify(f) for f in fs]
    assert kinds[:5] == ["unknown", "full_name", "email", "resume", "linkedin"]
    assert kinds[6:8] == ["country", "choice"] and fs[6].combobox and fs[7].type == "checkbox"
    assert "eeo" in kinds and kinds.count("free_text") == 3   # три textarea з узагальненими питаннями
    assert not fs[3].required   # резюме Ashby не обов'язкове, але класифікується як resume


def test_classify_greenhouse():
    fs = [f for f in fields_from(fixture("greenhouse_form_fields.json")) if f.visible]
    assert [classify(f) for f in fs[:4]] == ["first_name", "last_name", "preferred_name", "email"]
    sponsorship = next(f for f in fs if "sponsorship" in f.label.lower())
    assert classify(sponsorship) == "free_text" and sponsorship.combobox
    assert classify(next(f for f in fs if f.label.startswith("I identify my gender"))) == "eeo"
    files = [classify(f) for f in fs if f.type == "file"]
    assert files == ["resume", "cover_letter_file"]
    assert classify(next(f for f in fs if f.id == "country")) == "country"


def test_visible_required_excludes_eeo():
    fs = fields_from(fixture("ashby_form_fields.json"))
    assert all(classify(f) != "eeo" for f in visible_required(fs))
    assert [f.label[:40] for f in visible_required(fs)] == [
        "Name", "Email", "LinkedIn Profile", "Which country do you intend to work from",
        "Will you now or in the future require sp", "Describe a project you are proud of and ",
        "What is the most recent thing you have s", "How do you communicate a difficult decis",
    ]
    gh = fields_from(fixture("greenhouse_form_fields.json"))
    assert any(classify(f) == "eeo" and f.required for f in gh)   # обов'язкове EEO є, але не блокує
    assert all(classify(f) != "eeo" for f in visible_required(gh))
    assert all(f.visible for f in visible_required(gh))
    # transgender і sexual orientation (Greenhouse 131, 132) теж EEO, не open_required
    for id_ in ("130", "131", "132", "133", "134", "135"):
        f = next(x for x in gh if x.id == id_)
        assert classify(f) == "eeo" and f.required and f not in visible_required(gh)


def test_field_js_is_evaluable():
    assert FIELD_JS.lstrip().startswith("(") and "querySelectorAll" in FIELD_JS
    assert FIELD_SELECTOR in FIELD_JS


def _f(**kw):
    base = {"i": 0, "tag": "input", "type": "text", "name": "", "id": "", "required": False, "label": "",
            "placeholder": "", "visible": True, "value": "", "options": None, "group": ""}
    base.update(kw)
    return Field(**base)


def test_required_cover_letter_file_is_not_resume():
    assert classify(_f(type="file", required=True, label="Cover letter")) == "cover_letter_file"


def test_checkbox_keyword_does_not_win_over_type():
    assert classify(_f(type="checkbox", label="I agree to receive email updates")) == "choice"
    assert classify(_f(type="checkbox", label="I agree to the privacy policy")) == "ack"


def test_select_in_wrapping_label_text_is_excluded_in_js():
    assert 'querySelectorAll("select,option,textarea,input")' in FIELD_JS


def test_checked_state_is_value_for_checkbox_and_radio():
    assert 'el.checked ? (el.value || "on") : (pressed ? "on" : "")' in FIELD_JS
    assert 'attr_value: el.getAttribute("value") || ""' in FIELD_JS


def test_combobox_flag():
    assert "combobox," in FIELD_JS and fields_from([{"i": 0, "tag": "input", "combobox": True}])[0].combobox
    assert fields_from([{"i": 0, "tag": "input"}])[0].combobox is False and _f().combobox is False


def test_field_js_group_and_visibility_rules():
    """Фінальне рев'ю, C3/I5/M2: група з aria-labelledby і aria-required контейнера, label[for=name]
    і label[for=data-field-path] для Ashby, aria-hidden не видиме."""
    assert 'byIds(rg.getAttribute("aria-labelledby"))' in FIELD_JS
    assert 'rg.getAttribute("aria-required") === "true"' in FIELD_JS
    assert 'labelFor(el.name)' in FIELD_JS and 'data-field-path' in FIELD_JS
    assert 'el.getAttribute("aria-hidden") !== "true"' in FIELD_JS
    assert 'button[data-option]' in FIELD_JS


def test_greenhouse_resume_group_is_resume():
    """Фінальне рев'ю, C3: <input id=resume type=file> з підписом «Attach» у групі «Resume/CV»."""
    assert classify(_f(type="file", id="resume", label="Attach", group="Resume/CV")) == "resume"
    assert classify(_f(type="file", id="resume", label="Attach")) == "resume"
    assert classify(_f(type="file", name="cv_upload", label="Attach")) == "resume"
    assert classify(_f(type="file", id="_systemfield_resume", label="")) == "resume"
    assert classify(_f(type="file", id="cover_letter", label="Attach", group="Cover Letter")) == "cover_letter_file"
    # «cv» лише цілим словом між підкресленнями чи пробілами, не всередині «canvas»
    assert classify(_f(type="file", id="canvas_upload", label="Attach")) == "cover_letter_file"


def test_ashby_name_is_full_name():
    """Фінальне рев'ю, I4: Ashby підписує поле імені просто «Name»."""
    assert classify(_f(label="Name", id="_systemfield_name", name="_systemfield_name")) == "full_name"
    assert classify(_f(label="Name")) == "full_name"
    assert classify(_f(label="Company name")) == "unknown"
    assert classify(_f(label="First Name")) == "first_name"


def test_combobox_country_and_eeo_before_long_question():
    """Фінальне рев'ю, C2: для комбобокса country/location/eeo виграють у правила довгого питання."""
    assert classify(_f(label="Which country do you intend to work from?", combobox=True)) == "country"
    assert classify(_f(label="Which country do you intend to work from?")) == "free_text"
    assert classify(_f(label="Where are you based right now, which city?", combobox=True)) == "location"
    assert classify(_f(label="I identify my gender as:", combobox=True)) == "eeo"
    assert classify(_f(label="I identify as transgender:", combobox=True)) == "eeo"
    assert classify(_f(label="I identify my sexual orientation as:", combobox=True)) == "eeo"
    assert classify(_f(label="Will you now or in the future require visa sponsorship?", combobox=True)) == "free_text"


def test_ashby_yes_no_checkbox_is_choice():
    """Фінальне рев'ю, I5: прихований checkbox з name=uuid і підписом питання це choice."""
    q = "Will you now or in the future require sponsorship for employment visa status?"
    f = _f(type="checkbox", name="9dbddae8-2594-40be-b4b9-92420e945f18", label=q, group=q, required=True)
    assert classify(f) == "choice" and f in visible_required([f])


def test_currently_located_is_location():
    """Тіньовий прохід 27.10: комбобокс локації Ashby підписано «Where are you currently located?»."""
    assert classify(_f(label="Where are you currently located?", combobox=True)) == "location"
    assert classify(_f(label="Where are you located?", combobox=True)) == "location"
    assert classify(_f(label="Currently located in", combobox=True)) == "location"
    assert classify(_f(label="Where are you currently located?")) == "location"
    # Yes/No питання з «are you currently located» лишається питанням, не полем локації
    assert classify(_f(label="Are you currently located in the United States?", combobox=True)) == "free_text"


def test_relocation_is_not_location():
    """Рев'ю live-fixes, M4: «relocation» містить «location», але це питання, не поле локації."""
    for combobox in (True, False):
        assert classify(_f(label="Are you open to relocation?", combobox=combobox)) != "location"
        assert classify(_f(label="Relocation assistance needed", combobox=combobox)) != "location"
    assert classify(_f(label="Preferred work location", combobox=True)) == "location"
    assert classify(_f(label="", id="candidate-location", combobox=True)) == "location"


def test_classify_years_question():
    f = _f(label="How many years of work experience do you have with Android Development?", type="text")
    assert classify(f) == "years"
    assert classify(_f(label="Wie viele Jahre Erfahrung haben Sie mit Kotlin?", type="number")) == "years"
    # LinkedIn Easy Apply: aria-label це питання, тип number.
    assert classify(_f(label="How many years of work experience do you have with Linux KVM?",
                       type="number", required=True)) == "years"


def test_years_phrase_in_textarea_stays_free_text():
    f = _f(tag="textarea", type="textarea", label="Describe your years of experience with mobile teams")
    assert classify(f) == "free_text"


def test_years_not_for_combobox_or_select():
    q = "How many years of work experience do you have with Kotlin?"
    assert classify(_f(label=q, combobox=True)) != "years"
    assert classify(_f(label=q, tag="select", type="select")) != "years"
