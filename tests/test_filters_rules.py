import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from bot.filters.rules import (
    apply_rules, classify_channel, dedup_against_tracker, location_ok,
    normalize_company, parse_tracker_applications, title_excluded, titles_similar,
)


# ── title_excluded ────────────────────────────────────────────────────────────

def test_title_excluded_dead_niches():
    assert title_excluded("Trainee IT-Consulting (m/w/d)")
    assert title_excluded("Junior Product Owner MarTech (m/w/d)")
    assert title_excluded("Product Owner / Scrum Master (m/w/d)")
    assert title_excluded("Graduate Program 2026")
    # BA виключено 24.07 (рефокус на Agile/Office/Sprache)
    assert title_excluded("Junior Business Analyst (m/w/d)")
    assert title_excluded("IT Business Analyst Versicherung")


def test_title_excluded_seniority_and_nogo():
    assert title_excluded("Senior Agile Coach")
    assert title_excluded("Team Lead Operations")
    assert title_excluded("Teamleiter Logistik (m/w/d)")
    assert title_excluded("Vertriebsmitarbeiter Außendienst")
    assert title_excluded("Softwareentwickler Java")
    assert title_excluded("Werkstudent Product Management")


def test_title_not_excluded_target_roles():
    assert not title_excluded("Teamassistenz der Geschäftsführung (m/w/d)")
    assert not title_excluded("Scrum Master (m/w/d)")
    assert not title_excluded("Assistenz der Leitung")
    assert not title_excluded("Sachbearbeiter Verwaltung")
    # мовний нішевий канал — не виключати
    assert not title_excluded("Lehrkraft für Deutsch als Fremdsprache (m/w/d)")
    assert not title_excluded("Dolmetscher / Sprachmittler Ukrainisch (m/w/d)")
    assert not title_excluded("Office Manager (m/w/d)")


# ── location ──────────────────────────────────────────────────────────────────

def test_location_nrw_cities_ok():
    assert location_ok("Düsseldorf") is True
    assert location_ok("Köln") is True
    assert location_ok("40213 Düsseldorf, NRW") is True


def test_location_outside_nrw_rejected():
    assert location_ok("München") is False
    assert location_ok("Berlin") is False
    assert location_ok("Hamburg") is False


def test_location_remote_ok_and_unknown_none():
    assert location_ok("Remote") is True
    assert location_ok("München", "100% Homeoffice möglich") is True
    assert location_ok("") is None
    assert location_ok("Unbekannt") is None
    assert location_ok("", "Arbeiten im Homeoffice bundesweit") is True


def test_location_generic_country_rescued_by_description_city():
    """Регресія 15.07: LinkedIn intake ставить location="Deutschland", реальне
    місто лише в описі (DHL Scrum Master, Bonn) → було хибне outside_nrw."""
    assert location_ok("Deutschland", "Standort: Bonn, North Rhine-Westphalia") is True
    assert location_ok("Germany", "Unser Office in Köln direkt am Rhein") is True
    assert location_ok("Deutschland", "Standort München, Bayern") is False


def test_location_description_city_needs_word_boundary():
    # "essen" як підрядок ("Interessen") не має рахуватись за місто Essen
    assert location_ok("Deutschland", "Ihre Interessen sind uns wichtig") is False
    assert location_ok("Deutschland", "Standort Essen, Zentrale") is True


def test_location_unknown_rescued_by_description_city():
    assert location_ok("", "Einsatzort: Düsseldorf, Vollzeit") is True
    assert location_ok("Unbekannt", "keine Ortsangabe im Inserat") is None


def test_location_specific_foreign_city_not_rescued_by_description():
    # конкретне не-NRW місто в location — згадка Köln в описі не рятує
    assert location_ok("München", "weitere Standorte: Köln, Hamburg") is False


def test_location_nrw_smaller_towns_ok():
    """Регресія 25.07: Haan (Kreis Mettmann, 20 км від Дюссельдорфа) відкидався
    як outside_nrw — місто просто не було в NRW_LOCATIONS."""
    assert location_ok("Haan") is True
    assert location_ok("42781 Haan") is True
    for city in ("Viersen", "Willich", "Erkelenz", "Bergheim", "Frechen",
                 "Moers", "Dinslaken", "Hattingen", "Leichlingen",
                 "Troisdorf", "Siegburg", "Wesel"):
        assert location_ok(city) is True, city


def test_location_nrw_english_spelling_from_linkedin():
    """Регресія 25.07: LinkedIn пише локації англійською, а список знав лише
    "nordrhein-westfalen"/"nrw" — та сама вакансія проходила німецькою і
    падала в outside_nrw англійською."""
    assert location_ok("Löhne, North Rhine-Westphalia, Germany") is True
    assert location_ok("Soest, North Rhine-Westphalia, Germany") is True
    assert location_ok("Paderborn, NRW") is True
    # інші землі англійською далі відпадають
    assert location_ok("Munich, Bavaria, Germany") is False
    assert location_ok("Berlin, Berlin, Germany") is False


def test_location_remote_matches_hyphenated_home_office():
    """Регресія 25.07: _REMOTE_RE знав "Homeoffice" і "home office", але не
    "Home-Office" — саме це написання вживають StepStone і XING у своїх
    фільтрах, тому remote-вакансії поза NRW летіли в outside_nrw."""
    assert location_ok("Hamburg", "Arbeiten im Home-Office") is True
    assert location_ok("Berlin", "Teilweise Home-Office möglich") is True
    assert location_ok("Home-Office") is True
    assert location_ok("", "Nur Home-Office, bundesweit") is True


def test_location_full_remote_policy_rejects_partial_homeoffice(monkeypatch):
    """remote_policy: full — для клієнта, якому важлива дорога (пошук лише в
    одному місті): «hybrid» чи «2 Tage Homeoffice» у Франкфурті все одно означає
    їздити у Франкфурт, тож проходить лише повністю віддалена робота."""
    from bot.filters import rules
    monkeypatch.setattr(rules, "REMOTE_POLICY", "full")
    assert location_ok("Frankfurt", "Hybrid: 2 Tage Homeoffice pro Woche") is False
    assert location_ok("Berlin", "Teilweise Home-Office möglich") is False
    assert location_ok("Hamburg", "Wir sind bundesweit an 20 Standorten") is False
    assert location_ok("Frankfurt, hybrid") is False
    assert location_ok("", "Hybrides Arbeiten, Büro in der Innenstadt") is None
    # LinkedIn пише тип робочого місця в дужках: (Hybrid) не повна віддаленість
    assert location_ok("Frankfurt am Main, Hessen, Deutschland (Hybrid)") is False
    # StepStone/XING у полі location: «Homeoffice möglich» — це частково
    assert location_ok("Frankfurt, Homeoffice möglich") is False
    assert location_ok("Berlin (Home-Office teilweise)") is False
    assert location_ok("München, 2 Tage Homeoffice") is False


def test_location_full_remote_policy_keeps_fully_remote(monkeypatch):
    from bot.filters import rules
    monkeypatch.setattr(rules, "REMOTE_POLICY", "full")
    assert location_ok("Remote") is True
    assert location_ok("Home-Office") is True
    assert location_ok("Deutschland (Remote)") is True
    # LinkedIn: (Remote) після міста означає повністю віддалену роботу
    assert location_ok("Köln, Nordrhein-Westfalen, Deutschland (Remote)") is True
    assert location_ok("München", "Die Stelle ist zu 100 % remote") is True
    assert location_ok("Berlin", "Full Remote innerhalb Deutschlands") is True
    assert location_ok("Hamburg", "Du arbeitest vollständig im Home-Office") is True
    assert location_ok("Hamburg", "100% Homeoffice, deutschlandweit") is True
    assert location_ok("Stuttgart", "Arbeitsort: Remote (Deutschland)") is True
    assert location_ok("Leipzig", "Es handelt sich um eine Remote-Stelle") is True
    assert location_ok("Bremen", "komplett remote aus ganz Deutschland") is True
    assert location_ok("", "Remote-first Unternehmen, Arbeitsort frei wählbar") is True
    # досяжне місто не залежить від remote-політики
    assert location_ok("Düsseldorf", "hybrid") is True


def test_location_unknown_remote_policy_rejected(monkeypatch):
    import pytest
    from bot.filters import rules
    monkeypatch.setattr(rules, "REMOTE_POLICY", "sometimes")
    with pytest.raises(ValueError):
        location_ok("Köln")


# ── channel ───────────────────────────────────────────────────────────────────

def test_classify_channel():
    assert classify_channel({"email": "hr@foo.de", "source_url": "https://www.stepstone.de/x"}) == "email"
    assert classify_channel({"email": None, "source_url": "https://www.stepstone.de/stelle/x"}) == "stepstone_auto"
    assert classify_channel({"email": None, "source_url": "https://www.xing.com/jobs/x"}) == "xing_auto"
    assert classify_channel({"email": None, "source_url": "https://www.linkedin.com/jobs/view/1"}) == "manual_link"
    assert classify_channel({"email": None, "source_url": "https://karriere.foo.de/job/1"}) == "manual_link"
    # öД: Interamt має власний apply-модуль (28.07)
    assert classify_channel({"email": None, "source_url": "https://interamt.de/koop/app/stelle?id=1234567"}) == "interamt_auto"


def test_email_dead_companies_blocked():
    from bot.filters.rules import email_channel_blocked
    # 28.07: Beispielwerk і Alpenwerk видаляють email-вкладення (DSGVO), лише портал
    assert email_channel_blocked("Beispielwerk GmbH Wirtschaftsprüfungsgesellschaft")
    assert email_channel_blocked("Beispielwerk Business Services GmbH")
    assert email_channel_blocked("Alpenwerk Konzern AG")
    # токен-матч, не підрядок: інші компанії не зачеплені
    assert not email_channel_blocked("Alpenwerkstatt GmbH")
    assert not email_channel_blocked("Beispiel digital GmbH")
    assert not email_channel_blocked("")


def test_classify_channel_email_dead_company_falls_through():
    # email є, але компанія портал-only → канал НЕ email
    entry = {
        "email": "bewerbung@beispielwerk.de",
        "company": "Beispielwerk Consulting GmbH",
        "source_url": "https://www.stepstone.de/stelle/x",
    }
    assert classify_channel(entry) == "stepstone_auto"
    entry["source_url"] = "https://careers.versa.example/job/1"
    entry["company"] = "Alpenwerk Versicherung AG"
    assert classify_channel(entry) == "manual_link"


# ── dedup ─────────────────────────────────────────────────────────────────────

TRACKER_SAMPLE = """\
| # | Date | Company | Position | Source | Salary range | Status | Last update | Next action | Notes |
|---|---|---|---|---|---|---|---|---|---|
| 65 | 2026-05-20 | Beispiel digital GmbH | Scrum Master (m/w/d) | Portal | — | sent | 2026-05-20 | — | Köln |
| 45 | 2026-05-17 | Personaldienst AG (Kundenunternehmen Düsseldorf) | Assistenz (m/w/d) | Direct | ~40K | sent | 2026-05-17 | — | B4 |

| 2026-05-16 | Teamassistenz im Projektmanagement (m/w/d) | Planwerk Beratende Ingenieure GmbH, Düsseldorf | ✅ Email sent |
"""


def test_normalize_company_strips_legal_forms():
    assert normalize_company("Beispiel Digital GmbH") == normalize_company("Beispiel digital")
    assert normalize_company("Foo AG & Co. KG") == normalize_company("Foo")


def test_normalize_company_strips_markdown_link():
    # Firecrawl-markdown Indeed віддає компанію лінком на /cmp/-сторінку;
    # без чистки dedup проти трекера сліпне (регресія 28.07: St. Beispiel).
    md = "[St. Beispiel Gruppe GmbH](https://de.indeed.com/cmp/St.-Beispiel-Gruppe-Gmbh?campaignid=mobvjcmp)"
    assert normalize_company(md) == normalize_company("St. Beispiel Gruppe")


def test_dedup_blocks_markdown_company_against_tracker(tmp_path):
    tracker = tmp_path / "tracker.md"
    tracker.write_text(
        "| 1 | 2026-07-28 | St. Beispiel Gruppe GmbH | Assistenz der Geschäftsleitung (m/w/d) | Portal | — | ready | — | — | — |\n",
        encoding="utf-8",
    )
    entries = [{
        "company": "[St. Beispiel Gruppe GmbH](https://de.indeed.com/cmp/St.-Beispiel-Gruppe-Gmbh)",
        "title": "Assistenz der Geschäftsleitung (m/w/d)",
    }]
    fresh, dupes = dedup_against_tracker(entries, tracker)
    assert len(dupes) == 1 and not fresh


def test_dedup_matches_company_name_subset(tmp_path):
    # Indeed пише «Beispiel Sprachen», трекер — «Beispiel Sprachen Institut GmbH»:
    # коротка назва — підмножина повної, це та сама фірма (регресія 28.07).
    tracker = tmp_path / "tracker.md"
    tracker.write_text(
        "| 2026-07-28 | Lehrkraft für Integrationskurse DaF/DaZ (d/w/m) – freiberuflich "
        "| Beispiel Sprachen Institut GmbH, Düsseldorf | 🔄 Файли готові |\n",
        encoding="utf-8",
    )
    entries = [
        {"company": "Beispiel Sprachen", "title": "Lehrkraft für Integrationskurse DaF/DaZ (d/w/m) freiberuflich"},
        {"company": "Beispiel Immobilien GmbH", "title": "Teamassistenz (m/w/d)"},
    ]
    fresh, dupes = dedup_against_tracker(entries, tracker)
    assert len(dupes) == 1 and dupes[0]["company"] == "Beispiel Sprachen"
    assert len(fresh) == 1


def test_parse_tracker_both_formats(tmp_path):
    tracker = tmp_path / "tracker.md"
    tracker.write_text(TRACKER_SAMPLE, encoding="utf-8")
    apps = parse_tracker_applications(tracker)
    companies = {a["company"] for a in apps}
    assert "Beispiel digital GmbH" in companies
    assert "Planwerk Beratende Ingenieure GmbH" in companies


def test_dedup_blocks_same_company_similar_title(tmp_path):
    tracker = tmp_path / "tracker.md"
    tracker.write_text(TRACKER_SAMPLE, encoding="utf-8")
    entries = [
        {"company": "Beispiel Digital", "title": "Scrum Master (w/m/d)"},
        {"company": "Neue Firma GmbH", "title": "Teamassistenz (m/w/d)"},
    ]
    fresh, dupes = dedup_against_tracker(entries, tracker)
    assert len(dupes) == 1 and dupes[0]["company"] == "Beispiel Digital"
    assert len(fresh) == 1 and fresh[0]["company"] == "Neue Firma GmbH"


def test_dedup_allows_same_company_different_role(tmp_path):
    tracker = tmp_path / "tracker.md"
    tracker.write_text(TRACKER_SAMPLE, encoding="utf-8")
    entries = [{"company": "Personaldienst AG", "title": "Junior Budgetanalyst (m/w/d)"}]
    fresh, dupes = dedup_against_tracker(entries, tracker)
    assert len(fresh) == 1 and not dupes


def test_titles_similar_ignores_gender_suffix():
    assert titles_similar("Scrum Master (m/w/d)", "Scrum Master (w/m/d)")
    assert not titles_similar("Scrum Master (m/w/d)", "Junior Budgetanalyst (m/w/d)")


# ── apply_rules end-to-end ────────────────────────────────────────────────────

def _entry(**kw) -> dict:
    base = {
        "score": 60, "category": "B4_assistenz", "title": "Teamassistenz (m/w/d)",
        "company": "Neue Firma GmbH", "location": "Düsseldorf", "email": None,
        "is_portal": True, "source_url": "https://www.stepstone.de/stelle/x",
        "description": "Assistenz Aufgaben", "salary_hint": "42000",
    }
    base.update(kw)
    return base


def test_apply_rules_excludes_and_sorts(tmp_path):
    tracker = tmp_path / "tracker.md"
    tracker.write_text(TRACKER_SAMPLE, encoding="utf-8")
    digest = [
        _entry(),
        _entry(title="Trainee Consulting (m/w/d)", company="T GmbH", category="T4_trainee"),
        _entry(title="Senior Consultant", company="S GmbH"),
        _entry(company="B GmbH", location="München"),
        _entry(company="C GmbH", score=10),
        _entry(company="Beispiel Digital", title="Scrum Master (w/m/d)", category="T5_agile"),
        _entry(company="LI GmbH", category="T1_ba", title="Junior Business Analyst",
               source_url="https://www.linkedin.com/jobs/view/123"),
        _entry(company="Agile GmbH", category="T5_agile", title="Scrum Master (m/w/d)"),
    ]
    result = apply_rules(digest, tracker)
    reasons = {e["company"]: e["reason"] for e in result["excluded"]}
    assert reasons["T GmbH"] == "excluded_title"
    assert reasons["S GmbH"] == "excluded_title"
    assert reasons["B GmbH"] == "outside_nrw"
    assert reasons["C GmbH"] == "score_below_25"
    assert reasons["Beispiel Digital"] == "duplicate_in_tracker"
    # BA виключено по title (рефокус 24.07)
    assert reasons["LI GmbH"] == "excluded_title"

    selected = result["selected"]
    assert [e["company"] for e in selected] == ["Neue Firma GmbH", "Agile GmbH"]
    assert selected[0]["priority_rank"] == 1
    assert selected[0]["apply_channel"] == "stepstone_auto"


def test_language_niche_scores_and_selected(tmp_path):
    """Мовний канал: DaF/Dolmetscher роль має набрати бал і бути відібрана."""
    from bot.generators.discover import score_job

    score, cat = score_job(
        "Lehrkraft für Deutsch als Fremdsprache (m/w/d)",
        "Integrationskurs, Alphabetisierung, Sprachförderung für Geflüchtete",
    )
    assert cat == "B8_dolmetschen"
    assert score >= 25

    # хибний хіт: "dafür"/"dazu" в описі не має тягти на B8
    score2, cat2 = score_job(
        "Teamassistenz (m/w/d)",
        "Wir freuen uns dafür auf Sie und dazu ein tolles Team.",
    )
    assert cat2 != "B8_dolmetschen"


def test_title_excluded_apprenticeship_variants():
    assert title_excluded("Apprenticeship Office Manager")
    assert title_excluded("Azubi Kauffrau für Büromanagement")
    assert title_excluded("Auszubildende zur Teamassistenz")
    assert not title_excluded("Office Manager (m/w/d)")


def test_location_hessen_not_matched_by_essen_substring():
    # «Borken, Hessen» проходив NRW-фільтр, бо «essen» — підрядок «Hessen»
    # (регресія 29.07: Beispiel Montage, Кроатisch/Portugiesisch у selected)
    assert location_ok("Borken, Hessen") is False
    assert location_ok("Essen") is True
    assert location_ok("Gelsenkirchen") is True


def test_title_excluded_projektmanager_but_not_assistenz():
    # Рефокус 24.07: Projekt/PMO геть (0/26) — але Projektassistenz і
    # Projektkoordinator лишаються цілями
    assert title_excluded("Projektmanager:in Transformation der Stromnetze (m/w/d)")
    assert title_excluded("IT Projektmanager Digitale Transformation (m/w/d)")
    assert not title_excluded("Projektassistenz (m/w/d)")
    assert not title_excluded("Projektkoordinator / Projektassistenz (m/w/d)")


def test_apply_rules_drops_scrape_failure_titles(tmp_path):
    # title «XING Job»/«LinkedIn Job» = маркер зламаного скрапа, не вакансія
    tracker = tmp_path / "tracker.md"
    tracker.write_text("", encoding="utf-8")
    digest = [_entry(title="XING Job", company="Unbekannt", score=37)]
    result = apply_rules(digest, tracker)
    assert not result["selected"]
    assert result["excluded"][0]["reason"] == "scrape_failed_title"


# ── description_bonus / description_excluded ──────────────────────────────────

_TZ = [{"pattern": r"teilzeit|wochenstunden", "points": 30}]
_VZ = [{"pattern": r"\bvollzeit\b", "unless": r"teilzeit|wochenstunden"}]


def test_description_bonus_counts_each_rule_once():
    from bot.filters.rules import description_bonus
    e = {"title": "Teamassistenz (m/w/d) in Teilzeit", "description": "20 Wochenstunden, Teilzeit"}
    assert description_bonus(e, _TZ) == 30
    assert description_bonus({"title": "Teamassistenz", "description": "Vollzeit"}, _TZ) == 0


def test_description_excluded_respects_unless():
    from bot.filters.rules import description_excluded
    assert description_excluded({"title": "Assistenz", "description": "Vollzeit, 40 Std."}, _VZ)
    assert not description_excluded(
        {"title": "Assistenz", "description": "Vollzeit oder Teilzeit möglich"}, _VZ)
    assert not description_excluded({"title": "Assistenz", "description": "unbefristet"}, _VZ)


def test_apply_rules_uses_description_patterns(tmp_path, monkeypatch):
    """Teilzeit-вакансія з бонусом обганяє Vollzeit-вакансію з вищим базовим
    балом у тому ж треку; чиста Vollzeit без винятку відпадає."""
    from bot.filters import rules
    monkeypatch.setattr(rules, "DESCRIPTION_BONUS", _TZ)
    monkeypatch.setattr(rules, "EXCLUDED_DESCRIPTION_PATTERNS", _VZ)
    tracker = tmp_path / "tracker.md"
    tracker.write_text("| # | Дата | Компанія | Позиція |\n|---|---|---|---|\n")
    digest = [
        {"title": "Teamassistenz (m/w/d)", "company": "Alpha GmbH", "location": "Düsseldorf",
         "description": "Vollzeit, 40 Stunden", "score": 60, "category": "A1", "source_url": "https://a.example/1"},
        {"title": "Teamassistenz (m/w/d)", "company": "Beta GmbH", "location": "Düsseldorf",
         "description": "Teilzeit, 25 Wochenstunden", "score": 40, "category": "A1", "source_url": "https://b.example/2"},
        {"title": "Teamassistenz (m/w/d)", "company": "Gamma GmbH", "location": "Düsseldorf",
         "description": "unbefristet", "score": 50, "category": "A1", "source_url": "https://c.example/3"},
    ]
    res = rules.apply_rules(digest, tracker, min_score=25)
    assert [e["company"] for e in res["selected"]] == ["Beta GmbH", "Gamma GmbH"]
    assert res["selected"][0]["score"] == 70 and res["selected"][0]["description_bonus"] == 30
    assert [(e["company"], e["reason"]) for e in res["excluded"]] == [("Alpha GmbH", "excluded_description")]


def test_scrape_failed_title_catches_search_page_titles():
    """Indeed віддає заголовок сторінки пошуку замість вакансії (репетиція
    07.10.2026: 11 записів «Suchen»/«Search» на вершині черги)."""
    from bot.filters.rules import scrape_failed_title
    for junk in ("Suchen", "Search", "XING Job", "LinkedIn Job", " Jobs ", "", "##", "Indeed"):
        assert scrape_failed_title(junk), junk
    for ok in ("Teamassistenz (m/w/d)", "Sachbearbeiterin Teilzeit", "Empfang"):
        assert not scrape_failed_title(ok), ok


def test_apply_rules_dedups_same_job_within_run(tmp_path):
    """XING віддав ту саму вакансію двічі (з префіксом «Jobangebot:»), 08.10.2026."""
    from bot.filters import rules
    tracker = tmp_path / "tracker.md"
    tracker.write_text("| # | Дата | Компанія | Позиція |\n|---|---|---|---|\n")
    digest = [
        {"title": "Kaufmännische Sachbearbeitung / Backoffice Service (m/w/d)", "company": "Kühler & Söhne GmbH Lüftung Klima Kälte",
         "location": "Düsseldorf", "description": "", "score": 67, "category": "A1", "source_url": "https://x.example/1"},
        {"title": "Jobangebot: Kaufmännische Sachbearbeitung / Backoffice Service", "company": "KÜHLER & SÖHNE GmbH",
         "location": "Düsseldorf", "description": "", "score": 37, "category": "A1", "source_url": "https://x.example/2"},
        {"title": "Empfang / Office Management (m/w/d)", "company": "Kühler & Söhne GmbH",
         "location": "Düsseldorf", "description": "", "score": 50, "category": "A1", "source_url": "https://x.example/3"},
    ]
    res = rules.apply_rules(digest, tracker, min_score=25)
    assert [e["source_url"][-1] for e in res["selected"]] == ["1", "3"]
    assert [(e["source_url"][-1], e["reason"]) for e in res["excluded"]] == [("2", "duplicate_in_run")]


def test_soft_hyphens_do_not_hide_exclusions():
    """Arbeitsagentur ріже слова м'якими переносами; фільтр має бачити слово цілим."""
    from bot.filters.rules import description_excluded
    from bot.generators.intake import parse_text
    from bot.textclean import strip_invisible

    shy = "Arbeit\u00adnehmer\u00adüber\u00adlassung"
    assert strip_invisible(shy) == "Arbeitnehmerüberlassung"
    rules = [{"pattern": r"arbeitnehmerüberlassung"}]
    entry = {"title": "Empfang (m/w/d)", "description": f"Stelle im Rahmen der {shy}"}
    assert description_excluded(entry, rules)
    job = parse_text(f"# Bü\u00adro\u00adas\u00adsis\u00adtenz (m/w/d)\nMuster GmbH\nKöln\n{shy}", "https://example.com/j/1")
    assert "\u00ad" not in job.title and "\u00ad" not in job.description


def test_dedup_matches_source_url_when_company_unknown(tmp_path):
    # 15.10.2026: вакансії агенції й канцелярії прийшли з company="Unbekannt",
    # порівняння назв не спрацювало, і ті самі вакансії відібрались удруге.
    # URL у рядку трекера (legacy-формат, у клітинці статусу) має зупиняти це.
    tracker = tmp_path / "tracker.md"
    tracker.write_text(
        "| 2026-10-15 | Teamassistenz (m/w/d) - JN-012026-6935596 | Beispiel Personalberatung (Düsseldorf) "
        "| 🔄 manual · https://www.beispiel-agentur.de/job-detail/teamassistenz-mwd/ref/jn-012026-6935596 |\n"
        "| 2026-10-15 | Sekretär (w/m/d) | Musterbank AG, Düsseldorf "
        "| manual · https://de.indeed.com/viewjob?jk=0123456789abcdef&from=alert |\n",
        encoding="utf-8",
    )
    entries = [
        {"company": "Unbekannt", "title": "Teamassistenz (m/w/d) - JN-012026-6935596 | Beispiel Personalberatung",
         "source_url": "https://www.beispiel-agentur.de/job-detail/teamassistenz-mwd/ref/jn-012026-6935596/#top"},
        {"company": "Unbekannt", "title": "Suchen",
         "source_url": "https://de.indeed.com/viewjob?jk=0123456789abcdef&tk=abc"},
        {"company": "Unbekannt", "title": "Teamassistenz (m/w/d)",
         "source_url": "https://www.beispiel-agentur.de/job-detail/teamassistenz-mwd/ref/jn-999999-0000000"},
    ]
    fresh, dupes = dedup_against_tracker(entries, tracker)
    assert [d["source_url"] for d in dupes] == [entries[0]["source_url"], entries[1]["source_url"]]
    assert fresh == [entries[2]]
