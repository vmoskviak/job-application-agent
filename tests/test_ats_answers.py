from datetime import date

import pytest

from bot import client
from bot.apply.ats_answers import (
    Answer,
    AnswerContext,
    answer_for,
    answer_language,
    clean_llm_answer,
    earliest_start,
    llm_allowed,
    llm_answer_problem,
    llm_prompt,
)
from bot.apply.ats_fields import Field


@pytest.fixture
def ctx(tmp_path):
    profile = dict(client.active().profile)
    profile["availability"] = "about 4 weeks after signing"
    return AnswerContext(
        profile=profile, lead={"company": "Contract Co", "title": "Senior Android Engineer"},
        note="Note text", resume=tmp_path / "CV.pdf", today=date(2026, 10, 27), lang="en",
    )


@pytest.fixture
def field():
    def make(label, type="text", tag="input", required=True, options=None, group="", combobox=False):
        return Field(i=0, tag=tag, type=type, name="", id="", required=required, label=label,
                     placeholder="", visible=True, value="", options=options, group=group, combobox=combobox)
    return make


def test_identity_answers(ctx, field):
    p = ctx.profile
    assert answer_for(field("Legal Name"), ctx) == Answer("text", f"{p['first_name']} {p['last_name']}")
    assert answer_for(field("Preferred name"), ctx) == Answer("text", p["first_name"])
    assert answer_for(field("Email", type="email"), ctx) == Answer("text", p["email"])
    assert answer_for(field("Current location"), ctx) == Answer("text", f"{p['city']}, Germany")
    assert answer_for(field("LinkedIn"), ctx) == Answer("text", p["links"]["linkedin"])
    assert answer_for(field("Website"), ctx) is None


def test_country(ctx, field):
    assert answer_for(field("Country", tag="select", type="select", options=["France", "Germany"]), ctx) == Answer("option", "Germany")
    assert answer_for(field("Country"), ctx) == Answer("text", "Germany")


def test_resume_and_cover_letter(ctx, field):
    assert answer_for(field("Resume", type="file"), ctx) == Answer("file", str(ctx.resume))
    assert answer_for(field("Cover letter", type="file", required=False), ctx) == Answer("skip")
    assert answer_for(field("", type="file", required=False), ctx) == Answer("skip")
    assert answer_for(field("Additional Information", tag="textarea"), ctx) == Answer("text", ctx.note)


def test_eeo_declines(ctx, field):
    f = field("Gender", type="radio", options=["Male", "Female", "Decline to self-identify"], group="Gender")
    assert answer_for(f, ctx) == Answer("option", "Decline to self-identify")
    f = field("Gender", type="radio", options=["Male", "Female"], group="Gender")
    assert answer_for(f, ctx) == Answer("skip")


def test_ack_checks_and_date(ctx, field):
    assert answer_for(field("I confirm I have read the above.", type="checkbox"), ctx) == Answer("check")
    assert answer_for(field("Pick date...", type="text"), ctx) == Answer("date", "2026-11-24")
    assert earliest_start(date(2026, 10, 27), "about 4 weeks after signing") == date(2026, 11, 24)
    assert earliest_start(date(2026, 10, 27), "ab sofort") == date(2026, 10, 27)


def test_optional_consent_to_future_positions_stays_unticked(ctx, field):
    """02.11.2026: a freelance form («storing my application documents for up to 12 months … future positions»)
    and a career page («Bewerbung … für weitere Stellenangebote an die Unternehmen innerhalb der …
    Group übermittelt») got ticked. Optional consents beyond this application are the candidate's."""
    future_use = ("I consent to Example GmbH storing my application documents for up to 12 months after the conclusion "
             "of the application procedure in order to consider me for suitable future positions. This consent is "
             "given voluntarily.")
    assert answer_for(field(future_use, type="checkbox", required=False), ctx) == Answer("skip")
    group = ("Ich bin damit einverstanden, dass meine Bewerbung zur Prüfung für weitere Stellenangebote an die "
             "Unternehmen innerhalb der Example Group (siehe Datenschutzerklärung) übermittelt wird.")
    assert answer_for(field(group, type="checkbox", required=False), ctx) == Answer("skip")
    # without a consent word it is a plain choice the filler leaves alone (no rule, no answer)
    assert answer_for(field("Ich möchte in den Talentpool aufgenommen werden.", type="checkbox", required=False),
                      ctx) in (None, Answer("skip"))


def test_required_privacy_acknowledgement_is_ticked(ctx, field):
    """The required one on the same page («… zur Kenntnis genommen*») stayed empty: «Kenntnis» was no ack word."""
    lex = ("Ich habe die Hinweise zur Verarbeitung der Daten durch die Example Group und die Hinweise zur "
           "Datenverarbeitung durch die Beispiel AG zur Kenntnis genommen.*")
    assert answer_for(field(lex, type="checkbox", required=True), ctx) == Answer("check")
    assert answer_for(field("Kenntnis genommen", type="checkbox", required=True), ctx) == Answer("check")
    assert answer_for(field("Ich bin mit der Verarbeitung meiner Daten für dieses Bewerbungsverfahren "
                            "einverstanden.", type="checkbox", required=True), ctx) == Answer("check")


def test_profile_no_rule_beats_a_consent_checkbox(ctx, field):
    from dataclasses import replace

    profile = {**ctx.profile, "form_answers": [{"pattern": "newsletter-artige|partnerunternehmen", "choice": "no"}]}
    c = replace(ctx, profile=profile)
    text = "I agree that my data may be shared with Partnerunternehmen for recruiting purposes."
    assert answer_for(field(text, type="checkbox", required=False), c) == Answer("skip")


def test_earliest_start_notice_words():
    """Фінальне рев'ю, M4: голе «notice» не термін; «no notice period» і «immediately» це сьогодні."""
    today = date(2026, 10, 27)
    assert earliest_start(today, "no notice period") == today
    assert earliest_start(today, "available immediately") == today
    assert earliest_start(today, "sofort, kein notice period") == today
    # a stated term counts as written (29.10.2026); before, any notice wording meant +28 days
    assert earliest_start(today, "notice period 3 months") == date(2027, 1, 25)
    assert earliest_start(today, "6 Wochen Kündigungsfrist") == date(2026, 12, 8)
    assert earliest_start(today, "notice period") == date(2026, 11, 24)
    assert earliest_start(today, "") == today


def test_form_answers_from_profile(ctx, field):
    assert answer_for(field("Will you now or in the future require sponsorship for a visa?"), ctx) == Answer("text", "No")
    assert answer_for(field("Do you need sponsorship?", type="radio", options=["Yes", "No"], group="Do you need sponsorship?"), ctx) == Answer("option", "No")
    # Ashby: кнопки Yes/No без options
    assert answer_for(field("Do you need sponsorship?", type="radio", group="Do you need sponsorship?"), ctx) == Answer("option", "No")


def test_sponsorship_combobox_without_profile_answer_is_unanswered(ctx, field):
    """Фінальне рев'ю, C2: react-select зі спонсорством без form_answers лишається порожнім і
    не йде до моделі, тож обов'язкове стає hand-off."""
    ctx.profile["form_answers"] = []
    q = "Will you now or in the future require visa sponsorship?"
    f = field(q, combobox=True)
    assert answer_for(f, ctx) is None and not llm_allowed(f)
    assert not llm_allowed(field("Are you authorized to work in Germany for any employer?"))
    assert not llm_allowed(field("Do you hold a valid work permit?"))
    # і в textarea питання про візу чи дозвіл на роботу не йде до моделі (спека §2)
    assert not llm_allowed(field("Describe your visa situation in your own words, please.", tag="textarea"))
    assert not llm_allowed(field("Explain your work authorization status in a few sentences.", tag="textarea", type="textarea"))
    assert llm_allowed(field("Describe a project you are proud of and what made it work.", tag="textarea"))
    # з form_answers відповідь іде з профілю, не з моделі
    ctx.profile["form_answers"] = [{"pattern": "sponsorship", "answer": "No", "choice": "no"}]
    assert answer_for(f, ctx) == Answer("text", "No") and not llm_allowed(f)


def test_country_combobox(ctx, field):
    """Фінальне рев'ю, C2: «Which country do you intend to work from?» на Ashby це country."""
    q = "Which country do you intend to work from?"
    assert answer_for(field(q, combobox=True, options=["France", "Germany"]), ctx) == Answer("option", "Germany")
    assert answer_for(field(q, combobox=True), ctx) == Answer("text", "Germany")
    assert not llm_allowed(field(q, combobox=True))


def test_eeo_combobox_declines(ctx, field):
    assert answer_for(field("I identify my gender as:", combobox=True), ctx) == Answer("decline")
    assert answer_for(field("Gender", combobox=False), ctx) == Answer("skip")


def test_rate_from_profile(ctx, field):
    assert answer_for(field("Expected compensation"), ctx) == Answer("text", "95 EUR per hour (B2B contract)")


def test_unknown_required_choice_has_no_answer(ctx, field):
    assert answer_for(field("Have you used our product?", type="radio", options=["Yes", "No"], group="Have you used our product?"), ctx) is None


def test_llm_only_for_free_text(ctx, field):
    assert llm_allowed(field("Why do you want to work with us? Tell us in a few sentences.", tag="textarea"))
    assert not llm_allowed(field("Do you need sponsorship?", type="radio", options=["Yes", "No"]))
    p = llm_prompt(field("Why us?", tag="textarea"), ctx, "brief text")
    assert "600" in p and "Contract Co" in p and "brief text" in p
    assert clean_llm_answer("```\n\"Because.\"\n```") == "Because."
    long = ("Sentence one. " * 60).strip()
    out = clean_llm_answer(long)
    assert len(out) <= 600 and out.endswith(".")


def test_rate_words_need_boundary(ctx, field):
    assert answer_for(field("Describe a system you helped migrate", tag="textarea"), ctx) is None
    assert answer_for(field("Please rate your Kotlin skill 1-10"), ctx) is None
    assert answer_for(field("Gehaltsvorstellung"), ctx) == Answer("text", "95 EUR per hour (B2B contract)")
    assert answer_for(field("Hourly rate"), ctx) == Answer("text", "95 EUR per hour (B2B contract)")


def test_location_without_country(ctx, field):
    ctx.profile["country"] = ""
    assert answer_for(field("Current location"), ctx) == Answer("text", ctx.profile["city"])


def test_clean_llm_answer_cuts_without_sentence_end():
    assert len(clean_llm_answer("word " * 140)) <= 600


AUTH_EU = [{"pattern": r"authori[sz]ed.{0,80}(germany|\beu\b|europe)", "choice": "yes", "answer": "Yes"}]
JOB_COUNTRY_Q = "Are you authorized to work in the country where the job is located?"


def test_work_authorization_follows_job_country(ctx, field):
    """Тіньовий прохід 27.10: питання про дозвіл «у країні вакансії» дістало Yes для вакансії в США.
    Тег країни вакансії додається лише для вакансії в ЄС/ЄЕЗ/Швейцарії, тож шаблон клієнта,
    обмежений словами ЄС, спрацьовує лише там."""
    ctx.profile["form_answers"] = AUTH_EU
    f = field(JOB_COUNTRY_Q, type="radio", options=["Yes", "No"], group=JOB_COUNTRY_Q)
    ctx.lead["location"] = "Berlin, Germany"
    assert answer_for(f, ctx) == Answer("option", "Yes")
    ctx.lead["location"] = "San Francisco, CA"
    assert answer_for(f, ctx) is None
    ctx.lead.update({"location": "", "remote_scope": "europe"})
    assert answer_for(f, ctx) == Answer("option", "Yes")
    ctx.lead.update({"location": "", "remote_scope": "country:poland"})
    assert answer_for(f, ctx) == Answer("option", "Yes")
    ctx.lead.update({"location": "", "remote_scope": "country:united states"})
    assert answer_for(f, ctx) is None


def test_question_naming_a_country_gets_no_job_tag(ctx, field):
    """Питання саме називає країну: тег не додається, навіть для вакансії в ЄС."""
    ctx.profile["form_answers"] = AUTH_EU
    ctx.lead["location"] = "Berlin, Germany"
    q = "Are you legally authorized to work in the United States?"
    assert answer_for(field(q, type="radio", options=["Yes", "No"], group=q), ctx) is None


def test_yes_no_combobox_gets_choice_not_sentence(ctx, field):
    """Greenhouse рисує Yes/No як react-select: речення з answer фільтрувало опції до нуля, поле
    очищувалось і йшло в open_required. Правило з choice дає комбобоксу саме Yes чи No."""
    q = "Will you now or in the future require visa sponsorship to work for this company?"
    sentence = "No, I do not need visa sponsorship to work in the EU."
    ctx.profile["form_answers"] = [{"pattern": "sponsorship", "choice": "no", "answer": sentence}]
    assert answer_for(field(q, combobox=True), ctx) == Answer("text", "No")
    assert answer_for(field(q), ctx) == Answer("text", sentence)   # звичайне поле й далі отримує речення
    ctx.profile["form_answers"] = [{"pattern": "sponsorship", "answer": sentence}]   # без choice: як було
    assert answer_for(field(q, combobox=True), ctx) == Answer("text", sentence)


def test_known_location_wins_over_remote_scope(ctx, field):
    """Рев'ю live-fixes, C1: remote_scope europe береться з будь-якого «EU» в описі (рядок про GDPR
    у вакансії в США), тож відома локація ліда чи дошки переважає його."""
    ctx.profile["form_answers"] = AUTH_EU
    f = field(JOB_COUNTRY_Q, type="radio", options=["Yes", "No"], group=JOB_COUNTRY_Q)
    for lead in ({"location": "San Francisco, CA", "remote_scope": "europe"},
                 {"location": "United States or Remote", "remote_scope": "europe"},
                 {"location": "", "remote_scope": "europe", "board_location": "San Francisco"},
                 {"location": "Berlin, Germany", "board_location": "Berlin, Germany; London, United Kingdom"}):
        ctx.lead = {"company": "Contract Co", **lead}
        assert answer_for(f, ctx) is None, lead
    for lead in ({"location": "Berlin, Germany", "remote_scope": "us_only"},   # відома локація виграє
                 {"location": "", "remote_scope": "europe"}):
        ctx.lead = {"company": "Contract Co", **lead}
        assert answer_for(f, ctx) == Answer("option", "Yes"), lead


def test_question_naming_any_country_gets_no_job_tag(ctx, field):
    """Рев'ю live-fixes, I2: «authorized to work in Australia / the U.K.» для вакансії в Берліні тегу
    не отримує. «work for us» країни не називає: «us» малими літерами не США."""
    ctx.profile["form_answers"] = AUTH_EU
    ctx.lead["location"] = "Berlin, Germany"

    def ask(q: str):
        return answer_for(field(q, type="radio", options=["Yes", "No"], group=q), ctx)

    for q in ("Are you authorized to work in Australia?", "Are you authorized to work in the U.K.?",
              "Are you authorized to work in the US?", "Are you authorized to work in New York?"):
        assert ask(q) is None, q
    assert ask("Are you legally authorized to work for us?") == Answer("option", "Yes")
    assert ask("Are you legally authorized to work for us in Germany?") == Answer("option", "Yes")
    ctx.lead["location"] = "San Francisco, CA"
    assert ask("Are you legally authorized to work for us?") is None


def test_long_question_needs_wider_pattern(ctx, field):
    """Рев'ю live-fixes, M6: meta це підпис і група (часто те саме питання двічі) плюс тег, тож у
    довгому питанні від «authorized» до тегу понад 80 символів; докстрінг радить .{0,200}."""
    q = ("Are you legally authorized to work in the country in which this role is based, without requiring "
         "visa sponsorship now or at any point in the future?")
    f = field(q, type="radio", options=["Yes", "No"], group=q)
    ctx.lead["location"] = "Berlin, Germany"
    ctx.profile["form_answers"] = [{"pattern": r"authori[sz]ed.{0,80}(germany|\beu\b|europe)", "choice": "yes"}]
    assert answer_for(f, ctx) is None
    ctx.profile["form_answers"] = [{"pattern": r"authori[sz]ed.{0,200}(germany|\beu\b|europe)", "choice": "yes"}]
    assert answer_for(f, ctx) == Answer("option", "Yes")


def test_unresolved_place_does_not_fall_back_to_scope(ctx, field):
    """Повторне рев'ю: місце, якого location_country не знає («Podgorica, Montenegro», «Toronto, ON»),
    віддавало рішення remote_scope europe. Scope рахується лише коли всі місця це слова регіону
    чи remote; місто поза ЄС з таблиці будь-де в рядку («London (Hybrid)») це сигнал поза ЄС;
    конкретне незнайоме місце дає hand-off (ціна: голий «Berlin»)."""
    ctx.profile["form_answers"] = AUTH_EU
    f = field(JOB_COUNTRY_Q, type="radio", options=["Yes", "No"], group=JOB_COUNTRY_Q)
    for loc in ("Podgorica, Montenegro", "Tirana, Albania", "Chisinau, Moldova", "Tbilisi, Georgia",
                "Dubai, United Arab Emirates", "Toronto, ON", "Vancouver, BC", "Sydney, NSW",
                "Bengaluru, Karnataka", "Edinburgh, Scotland", "London (Hybrid)", "Greater London", "Berlin"):
        ctx.lead = {"company": "Contract Co", "location": loc, "remote_scope": "europe"}
        assert answer_for(f, ctx) is None, loc
    ctx.lead = {"company": "Contract Co", "location": "Berlin, Germany", "board_location": "Belgrade"}
    assert answer_for(f, ctx) is None
    for lead in ({"location": "Remote", "remote_scope": "europe"},
                 {"location": "European Union", "remote_scope": "europe"},
                 {"location": "Remote within the EU", "remote_scope": "europe"},
                 {"location": "Berlin, Germany"},
                 {"location": "Berlin, Germany", "board_location": "Remote"}):
        ctx.lead = {"company": "Contract Co", **lead}
        assert answer_for(f, ctx) == Answer("option", "Yes"), lead


def _years_ctx(ctx):
    ctx.profile["experience_years"] = [
        {"pattern": "android|kotlin", "years": 12}, {"pattern": "compose", "years": 4},
        {"pattern": "total", "years": 12},
    ]
    return ctx


def test_years_from_profile_table(ctx, field):
    ctx = _years_ctx(ctx)
    q = "How many years of work experience do you have"
    assert answer_for(field(f"{q} with Jetpack Compose?"), ctx) == Answer("text", "4")
    assert answer_for(field(f"{q} with Kotlin?", type="number"), ctx) == Answer("text", "12")
    assert answer_for(field(f"{q}?"), ctx) == Answer("text", "12")
    assert answer_for(field(f"{q} in total?"), ctx) == Answer("text", "12")
    assert answer_for(field(f"{q} with Rust?"), ctx) is None


def test_years_without_table_is_none(ctx, field):
    ctx.profile.pop("experience_years", None)
    assert answer_for(field("How many years of work experience do you have with Linux KVM?"), ctx) is None


def test_years_never_goes_to_llm(field):
    assert not llm_allowed(field("How many years of work experience do you have with Rust?"))


def test_years_specific_wins_over_total_without_preposition(ctx, field):
    ctx = _years_ctx(ctx)
    for label in ("Years of experience: Compose", "Years of experience (Compose)",
                  "How many years of experience do you have as a Compose developer?",
                  "Compose years of experience"):
        assert answer_for(field(label), ctx) == Answer("text", "4"), label
    assert answer_for(field("Years of experience: Android"), ctx) == Answer("text", "12")
    assert answer_for(field("Wie viele Jahre Berufserfahrung haben Sie mit Kotlin?"), ctx) == Answer("text", "12")
    assert answer_for(field("Wie viele Jahre Berufserfahrung haben Sie?"), ctx) == Answer("text", "12")
    assert answer_for(field("Years of experience: Rust"), ctx) is None


def test_years_question_in_group_only(ctx, field):
    ctx = _years_ctx(ctx)
    assert answer_for(field("Compose", group="How many years of experience do you have?"), ctx) == Answer("text", "4")
    assert answer_for(field("Rust", group="How many years of experience do you have?"), ctx) is None


def test_years_zero_and_broken_entries(ctx, field):
    ctx.profile["experience_years"] = [
        {"pattern": "(unclosed", "years": 3}, {"pattern": "cobol"}, {"pattern": "rust", "years": 0},
    ]
    assert answer_for(field("How many years of experience do you have with Rust?"), ctx) == Answer("text", "0")
    assert answer_for(field("How many years of experience do you have with Go?"), ctx) is None


def test_contract_salary_annual_hourly_daily(ctx, field):
    """Contract market: an annual question or a bare numeric field gets annual_salary as digits; a
    numeric hourly or day-rate field gets the rate as a number; free text keeps the hourly sentence."""
    ctx.profile["annual_salary"] = "100000"
    ctx.profile["rate"] = {**(ctx.profile.get("rate") or {}), "hourly_target": 95, "daily_target": 760,
                           "currency_default": "EUR"}
    assert answer_for(field("Was ist Ihre jährliche Gehaltserwartung in EUR?"), ctx) == Answer("text", "100000")
    assert answer_for(field("Expected annual salary"), ctx) == Answer("text", "100000 EUR")
    assert answer_for(field("Salary expectation", type="number"), ctx) == Answer("text", "100000")
    assert answer_for(field("Expected hourly rate (EUR)", type="number"), ctx) == Answer("text", "95")
    assert answer_for(field("Day rate expectation", type="number"), ctx) == Answer("text", "760")
    assert answer_for(field("Expected compensation"), ctx) == Answer("text", "95 EUR per hour (B2B contract)")


def test_contract_annual_question_without_profile_value_keeps_rate(ctx, field):
    ctx.profile["annual_salary"] = ""
    assert answer_for(field("Expected annual salary"), ctx) == Answer("text", "95 EUR per hour (B2B contract)")


def test_earliest_start_counts_stated_weeks_and_days():
    today = date(2026, 10, 29)
    assert earliest_start(today, "within 2 weeks, full-time") == date(2026, 11, 12)
    assert earliest_start(today, "in 10 days") == date(2026, 11, 8)
    assert earliest_start(today, "innerhalb von 3 Wochen") == date(2026, 11, 19)
    assert earliest_start(today, "about 4 weeks after signing") == date(2026, 11, 26)


# ---- A model answer is checked before it is typed into the form

# 30.10.2026: the brief is in Ukrainian, the form field just «Nachricht», and the model replied with a
# clarification that echoed the prompt's rules. This text was typed into a German employer form.
UK_META_REPLY = (
    "Поле «Nachricht» не містить конкретного питання, тож уточніть, що саме написати. Формат "
    "відповіді: німецькою, до 600 знаків, plain text, без тире-зв'язок, без заборонених фраз, без згадки "
    "поточного роботодавця й без тверджень про вже виконані дії."
)
UK_META_TAIL = (
    "відповідь: німецькою, до 600 знаків, plain text, без тире-зв'язок, без заборонених фраз, без згадки "
    "поточного роботодавця й без тверджень про вже виконані дії."
)


def test_llm_answer_problem_accepts_real_answers():
    assert llm_answer_problem(
        "Ich bringe acht Jahre Erfahrung mit Android und Kotlin mit und freue mich auf ein Gespräch.",
        "Ihre Nachricht") == ""
    assert llm_answer_problem(
        "I have led mobile teams of five engineers and shipped apps used by millions of people.",
        "Why do you want to join Contract Co?") == ""
    # Cyrillic is fine when the question itself is Cyrillic.
    assert llm_answer_problem("Маю вісім років досвіду з Android.", "Чому ви хочете працювати у нас") == ""


def test_llm_answer_problem_rejects_the_ukrainian_meta_reply():
    assert llm_answer_problem(UK_META_REPLY, "Nachricht")
    assert llm_answer_problem(UK_META_TAIL, "Nachricht")
    # Each check alone catches the tail too, so one weak check does not let it through.
    assert llm_answer_problem(UK_META_TAIL, "Чому ви хочете працювати у нас")


def test_llm_answer_problem_rejects_cyrillic_on_a_latin_form():
    assert "cyrillic" in llm_answer_problem("Маю вісім років досвіду з Android.", "Nachricht")


def test_llm_answer_problem_rejects_echoed_instructions():
    for text in ("Plain text, at most 600 characters, no greetings.",
                 "Hier ist meine Antwort mit höchstens 600 Zeichen: Ich freue mich auf Ihre Rückmeldung.",
                 "Output only the answer: I like Android.",
                 "Answer in German, plain text: Ich habe Erfahrung mit Kotlin."):
        assert "instructions" in llm_answer_problem(text, "Your message"), text


def test_llm_answer_problem_rejects_questions_back():
    for text in ("Could you clarify what this field expects?",
                 "Was genau soll ich in diesem Feld schreiben?",
                 "Please specify the question so I can write a fitting answer.",
                 "I am not sure what to write here, please let me know the question."):
        assert "question" in llm_answer_problem(text, "Message"), text


def test_llm_answer_problem_rejects_empty():
    assert llm_answer_problem("", "Nachricht") == "empty"
    assert llm_answer_problem("   ", "Nachricht") == "empty"


def test_answer_language_from_question_then_package():
    assert answer_language("Ihre Nachricht", "en") == "German"
    assert answer_language("Warum möchten Sie bei uns arbeiten?", "en") == "German"
    assert answer_language("Why do you want to join us?", "de") == "English"
    assert answer_language("Your message", "de") == "English"
    # No word that tells: the package language decides.
    assert answer_language("Xyz", "de") == "German"
    assert answer_language("Xyz", "en") == "English"


def test_llm_prompt_names_the_language_and_forbids_questions(ctx, field):
    p = llm_prompt(field("Nachricht", tag="textarea"), ctx, "Бриф кандидата")
    assert "in German" in p and "Бриф кандидата" in p
    assert "brief may be written in another language" in p
    assert "never ask" in p.lower() and "cover note" in p.lower()
    p = llm_prompt(field("Why do you want to join us?", tag="textarea"), ctx, "brief")
    assert "in English" in p


AUTH_US = [
    {"pattern": r"(?:authori[sz]ed|eligible).{0,200}\bunited states\b", "choice": "no", "answer": "No"},
    {"pattern": r"(?:sponsor|visa).{0,200}\bunited states\b", "choice": "yes", "answer": "Yes"},
]


def test_job_outside_eu_names_its_country(ctx, field):
    """A Greenhouse job in New York (03.11.2026): «eligible to work in the country you are applying?» and
    «Will you need a visa sponsorship?» name no country. A job with one known non-EU country gets it in
    the tag, so the client's US rules answer; an unknown or mixed place still goes to the operator."""
    ctx.profile["form_answers"] = AUTH_EU + AUTH_US
    eligible = field("Are you eligible to work in the country you are applying?", combobox=True)
    visa = field("Will you need a visa sponsorship?", combobox=True)
    ctx.lead["location"] = "New York, United States"
    assert answer_for(eligible, ctx) == Answer("text", "No")
    assert answer_for(visa, ctx) == Answer("text", "Yes")
    for lead in ({"location": "Toronto, ON"}, {"location": "New York, United States; Berlin, Germany"},
                 {"location": "", "remote_scope": "global"}):
        ctx.lead = {"company": "Contract Co", **lead}
        assert answer_for(eligible, ctx) is None, lead
        assert answer_for(visa, ctx) is None, lead
    ctx.lead = {"company": "Contract Co", "location": "Berlin, Germany"}
    assert answer_for(visa, ctx) is None   # EU tag, and AUTH_EU has no sponsorship rule
    assert answer_for(eligible, ctx) is None   # AUTH_EU wants «authorized», not «eligible»


def test_greenhouse_us_eeo_questions_decline(ctx, field):
    """A US Greenhouse form (03.11.2026): these three were read as plain selects and went to hand-off."""
    for label in ("Indicate Ethnic group:",
                  "If you believe you belong to any of the categories of protected Veterans, please indicate "
                  "by checking the appropriate box below.",
                  "Please review Form CC-305 at the link above before checking one of the boxes below."):
        assert answer_for(field(label, combobox=True), ctx) == Answer("decline"), label
    f = field("Disability status", type="radio", group="Disability status",
              options=["Yes, I have a disability", "No, I do not have a disability", "I do not want to answer"])
    assert answer_for(f, ctx) == Answer("option", "I do not want to answer")


def test_optional_follow_up_of_a_yes_answer_is_skipped(ctx, field):
    label = ("(Required if you answered 'yes' to \"Have you previously worked at Example Corp?\") "
             "What was your Example Corp work email?")
    assert answer_for(field(label, required=False), ctx) == Answer("skip")
    assert answer_for(field("If so, which team?", required=False), ctx) == Answer("skip")
    assert answer_for(field("Email", type="email", required=False), ctx) == Answer("text", ctx.profile["email"])


def test_optional_storage_for_future_positions_is_not_ticked(ctx, field):
    """An hrpuls form, 03.11.2026: the worker ticked a 24-month store «für zukünftig weitere
    ausgeschriebene Stellen»; the words between broke the old pattern."""
    label = ("Hiermit willige ich in die Speicherung meiner Daten für die Dauer von weiteren 24 Monaten, zum "
             "Zwecke der Berücksichtigung für zukünftig weitere ausgeschriebene Stellen ein. Mir ist bewusst, dass "
             "ich diese Einwilligung jederzeit durch eine kurze Benachrichtigung via Mail widerrufen kann.")
    assert answer_for(field(label, type="checkbox", required=False), ctx) == Answer("skip")
    label = ("Der internen Weiterleitung meiner Bewerbung stimme ich ausdrücklich zu, damit sie auch für "
             "andere ausgeschriebene Stellen bzw. Unternehmensbereiche berücksichtigt werden kann.")
    assert answer_for(field(label, type="checkbox", required=False), ctx) != Answer("check")
    required = "Hiermit nehme ich zur Kenntnis, dass meine Daten gemäß der Datenschutzerklärung verarbeitet werden."
    assert answer_for(field(required, type="checkbox", required=True), ctx) == Answer("check")
