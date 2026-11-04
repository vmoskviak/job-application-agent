import json
from pathlib import Path

from bot.apply.ats_fields import (
    FIELD_JS,
    FIELD_SELECTOR,
    Field,
    accepts_file,
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
    assert 'byIds(rg.getAttribute("aria-labelledby"), rg)' in FIELD_JS
    assert 'rg.getAttribute("aria-required") === "true"' in FIELD_JS
    assert 'labelFor(el.name, el)' in FIELD_JS and 'data-field-path' in FIELD_JS
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


def test_generic_message_textarea_takes_the_cover_note():
    """A contact-style form with one «Nachricht» box (30.10.2026): it is the cover-letter slot, so the
    already written note goes there instead of a model call on a question that is not a question."""
    for label in ("Nachricht", "Ihre Nachricht", "Nachricht an uns *", "Mitteilung", "Anmerkung", "Anmerkungen",
                  "Bemerkung", "Bemerkungen", "Kommentar", "Message", "Your message", "Comments", "Comment"):
        assert classify(_f(tag="textarea", type="textarea", label=label)) == "cover_letter_text", label
    assert classify(_f(tag="textarea", type="textarea", label="", name="message")) == "cover_letter_text"
    # Only a textarea: a one-line input named like that is not the cover letter.
    assert classify(_f(label="Nachricht")) != "cover_letter_text"
    # «Messaging» or «commentary» inside other words do not count.
    assert classify(_f(tag="textarea", type="textarea", label="Describe your messaging architecture")) == "free_text"


def test_years_not_for_combobox_or_select():
    q = "How many years of work experience do you have with Kotlin?"
    assert classify(_f(label=q, combobox=True)) != "years"
    assert classify(_f(label=q, tag="select", type="select")) != "years"


def test_consent_radio_with_a_yes_option_is_ack():
    """LinkedIn/JOIN 29.10.2026: «Stimmst du den AGBs von JOIN zu …? ◯ Ja» is a required consent asked as
    one radio; it is acknowledged like a consent checkbox. A No option or a skill question is not."""
    q = "Stimmst du den AGBs von JOIN zu und hast die Datenschutzbestimmungen zur Kenntnis genommen?"
    assert classify(_f(type="radio", label=f"Ja {q}")) == "ack"
    assert classify(_f(type="radio", label=f"Nein {q}")) == "choice"
    assert classify(_f(type="radio", label="Yes", group="Do you have experience with privacy engineering?")) == "choice"
    assert classify(_f(type="radio", label="Yes", group="I agree to the privacy policy")) == "ack"


def test_startdatum_is_a_date():
    assert classify(_f(label="Wann wäre Ihr frühestmögliches Startdatum? *")) == "date"


# ---- German file fields: a generic documents upload, a photo upload

def test_generic_documents_labels_are_documents():
    for label in ("Anlagen", "Unterlagen", "Bewerbungsunterlagen", "Dokumente", "Dateien", "Anhänge",
                  "Weitere Unterlagen", "Datei auswählen", "Dokument hochladen", "Attachments", "Documents"):
        assert classify(_f(type="file", label=label)) == "documents", label
    assert classify(_f(type="file", label="Bewerbungsunterlagen", required=True)) == "documents"
    assert classify(_f(type="file", name="attachments[]")) == "documents"


def test_specific_file_labels_win_over_the_documents_words():
    assert classify(_f(type="file", label="Lebenslauf (Datei)")) == "resume"
    assert classify(_f(type="file", label="Bewerbungsunterlagen (Lebenslauf, Anschreiben, Zeugnisse)")) == "resume"
    assert classify(_f(type="file", label="Anschreiben als Datei")) == "cover_letter_file"
    # Certificates are no place for the CV: the old rule stays.
    assert classify(_f(type="file", label="Zeugnisse (PDF-Datei)")) == "cover_letter_file"
    assert classify(_f(type="file", label="Zertifikate und Nachweise")) == "cover_letter_file"


def test_photo_upload_is_a_photo():
    for label in ("Bewerbungsfoto", "Foto (optional)", "Profile photo", "Lichtbild"):
        assert classify(_f(type="file", label=label)) == "photo", label
    assert classify(_f(type="file", label="Datei", accept="image/*")) == "photo"
    assert classify(_f(type="file", accept=".jpg,.jpeg, .png")) == "photo"
    assert classify(_f(type="file", label="Datei", accept=".pdf,image/*")) == "documents"
    # An input that takes images only can never hold the CV, whatever its label says.
    assert classify(_f(type="file", label="Lebenslauf", accept="image/*")) == "photo"


def test_avatar_upload_is_a_photo():
    """SmartRecruiters 30.10.2026: the CV went into the avatar input, whose accepted types listed .jfif."""
    sr_accept = ".BMP,.GIF,.JFIF,.JPEG,.JPG,.PNG,.TIF,.TIFF,.WEBP"
    assert classify(_f(type="file", accept=sr_accept)) == "photo"
    assert classify(_f(type="file", accept=sr_accept, required=True)) == "photo"
    for label in ("Avatar", "Profile picture", "Profilbild", "Profilfoto", "Bild hochladen", "Headshot"):
        assert classify(_f(type="file", label=label)) == "photo", label
    # The input's own id says avatar: a «Resume» label borrowed from a container does not make it the CV field.
    assert classify(_f(type="file", label="Resume", id="avatar-upload")) == "photo"
    # A visible CV label beats a photo word elsewhere in the text; «Bildung» is not «Bild».
    assert classify(_f(type="file", label="Lebenslauf", group="Foto und Unterlagen")) == "resume"
    assert classify(_f(type="file", label="Weiterbildung")) != "photo"
    assert classify(_f(type="file", label="Resume", accept=".pdf,.doc,.docx")) == "resume"


def test_accepts_file():
    assert accepts_file("", "/x/CV.pdf")
    assert accepts_file(".pdf,.doc,.docx", "/x/CV.pdf")
    assert accepts_file(".PDF", "/x/cv.pdf")
    assert accepts_file("application/pdf", "/x/CV.pdf")
    assert accepts_file("application/*", "/x/CV.pdf")
    assert accepts_file("*/*", "/x/CV.pdf")
    assert accepts_file("pdf, doc", "/x/CV.pdf")   # sloppy markup without dots
    assert not accepts_file(".BMP,.GIF,.JFIF,.JPEG,.JPG,.PNG,.TIF,.TIFF,.WEBP", "/x/CV.pdf")
    assert not accepts_file("image/*", "/x/CV.pdf")
    assert not accepts_file(".doc,.docx", "/x/CV.pdf")
    assert accepts_file("image/*", "/x/photo.jpg")


def test_unlabelled_file_input_is_still_unknown():
    assert classify(_f(type="file")) == "unknown"


def test_fields_from_reads_multiple_accept_and_the_ashby_autofill_mark():
    f = fields_from([{"i": 0, "tag": "input", "type": "file", "multiple": True, "accept": ".pdf",
                      "ashby_autofill": True}])[0]
    assert f.multiple and f.accept == ".pdf" and f.ashby_autofill
    f = fields_from([{"i": 0, "tag": "input", "type": "file"}])[0]
    assert not f.multiple and f.accept == "" and not f.ashby_autofill
    assert "el.multiple" in FIELD_JS and 'getAttribute("accept")' in FIELD_JS
    assert "ashby-application-form-autofill" in FIELD_JS


def _raw(**kw):
    base = {"i": 0, "tag": "input", "type": "radio", "name": "q", "id": "", "required": False, "label": "Ja",
            "placeholder": "", "visible": True, "value": "", "options": None, "group": ""}
    return {**base, **kw}


def test_required_marked_only_by_text_counts():
    """onlyfy (02.11.2026): «Arbeiten Sie aktuell bei der Musterklinik? *» had no required attribute;
    the worker submitted without an answer and onlyfy came back with an empty form."""
    from bot.apply.ats_fields import fields_from

    marked = [_raw(group="Arbeiten Sie aktuell bei der Musterklinik? *"),
              _raw(group="Do you currently work at Example Hospital? required"),
              _raw(type="text", label="Vorname *", group=""),
              _raw(type="checkbox", label="Ich stimme der Datenschutzerklärung zu. Pflichtfeld")]
    assert [f.required for f in fields_from(marked)] == [True, True, True, True]
    plain = [_raw(group="Möchten Sie in den Talentpool?"), _raw(type="text", label="Titel", group=""),
             _raw(type="text", label="Required skills for the role", group="")]
    assert [f.required for f in fields_from(plain)] == [False, False, False]


def test_house_number_is_its_own_kind():
    from bot.apply.ats_answers import split_street
    from bot.apply.ats_fields import fields_from

    hn, combined, street = fields_from([_raw(type="text", label="Hausnummer*"),
                                        _raw(type="text", label="Straße und Hausnummer"),
                                        _raw(type="text", label="Strasse*")])
    assert (classify(hn), classify(combined), classify(street)) == ("house_number", "street", "street")
    assert split_street("Musterstraße 1") == ("Musterstraße", "1")
    assert split_street("Am Ring 12a") == ("Am Ring", "12a")
    assert split_street("Hauptstr. 5-7") == ("Hauptstr.", "5-7")
    assert split_street("Musterweg") == ("Musterweg", "")
