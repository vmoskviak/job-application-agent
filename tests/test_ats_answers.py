from datetime import date

import pytest

from bot import client
from bot.apply.ats_answers import (
    Answer,
    AnswerContext,
    answer_for,
    clean_llm_answer,
    earliest_start,
    llm_allowed,
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


def test_earliest_start_notice_words():
    """Фінальне рев'ю, M4: голе «notice» не термін; «no notice period» і «immediately» це сьогодні."""
    today = date(2026, 10, 27)
    assert earliest_start(today, "no notice period") == today
    assert earliest_start(today, "available immediately") == today
    assert earliest_start(today, "sofort, kein notice period") == today
    assert earliest_start(today, "notice period 3 months") == date(2026, 11, 24)
    assert earliest_start(today, "6 Wochen Kündigungsfrist") == date(2026, 11, 24)
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
