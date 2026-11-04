"""Відповіді на поля ATS-форм із profile.yaml; LLM лише для вільного тексту.

Чиста частина без браузера: `answer_for` вирішує, що вписати в уже
класифіковане поле (`bot.apply.ats_fields.classify`). Усе, що не виходить
визначити з профілю, повертає None, а `llm_allowed` каже, чи можна для цього
поля звертатися до моделі (лише free_text і cover_letter_text).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Literal

from bot.apply.ats_fields import _YEARS, Field, classify
from bot.generators import engagement as _eng
from bot.presend import _NON_EU_CITY_RE, EU_EEA_CH, _scope_countries, location_country

log = logging.getLogger(__name__)

MAX_ANSWER_CHARS = 600
_EEO_DECLINE = r"decline|prefer not|don't wish|do not wish|(?:do not|don't) want to (?:answer|disclose)|keine angabe"
# Лише початкова межа слова: «Gehaltsvorstellung» має збігатися. Голе «rate»
# не береться («rate your skill», «migrate»): лише іменник у парі зі словом ставки.
_RATE_WORDS = (
    r"\b(?:(?:hourly|daily|day|expected|desired|target)\s+rate|rate\s+expectations?"
    r"|salary|compensation|stundensatz|gehalt)|^\s*rate\s*[*?:]?\s*$"
)
# Голе «notice» ловило «no notice period»; термін лише з явним строком чи словом «notice period».
_NOTICE_WORDS = r"notice period|weeks|wochen"
_NO_NOTICE_WORDS = r"no notice|sofort|immediately"
# Дозвіл на роботу й спонсорство візи відповідає лише профіль (спека §2, §15.2), ніколи модель.
_AUTH_WORDS = r"sponsor|visa|authori[sz]|work permit|arbeitserlaubnis|eligib"
# Питання про дозвіл, що само називає країну чи регіон, тегу країни вакансії не отримує
# (разом з країнами engagement і штатами США, див. _names_place). «US» лише великими:
# «work for us» країни не називає.
_PLACE_WORDS = (r"united states|(?-i:\bU\.?S\.?A?\b)|america|canada|united kingdom|\bu\.?k\.?(?!\w)|britain"
                r"|england|scotland|wales|northern ireland|germany|deutschland|europe|european|\beu\b|\beea\b"
                r"|switzerland|schweiz")
EU_JOB_TAG = " [job country: EU]"
# A job outside the EU with one known country gets that country named, e.g. « [job country: United States]»,
# so the client's rules for that country (authorized: no, sponsorship: yes) answer it. The tag never holds an
# EU word: EU rules must not fire for a job in New York.
# Місце без конкретики: слова регіону чи remote і службові слова між ними («Remote within the EU»).
_REGION_WORDS_RE = re.compile(
    r"\b(?:remote|worldwide|global|anywhere|emea|europe|european union|european economic area|eu|eea|dach"
    r"|region|distributed|home ?office|hybrid|multiple locations|various|in|within|the|from|only|based|or|and)\b",
    re.IGNORECASE)


@dataclass
class AnswerContext:
    profile: dict
    lead: dict
    note: str
    resume: Path
    today: date
    lang: str
    attachments: list[Path] = field(default_factory=list)   # усі вкладення пакета (freelancermap); resume: CV
    market: str = "contract"            # de_employment: зарплата на рік, дати німецьким форматом
    cover_letter: Path | None = None    # Anschreiben як файл для поля «Anschreiben hochladen»
    # The client's other mailboxes (accounts.yaml): a form that preselects one of them is already right.
    own_emails: tuple[str, ...] = ()


@dataclass
class Answer:
    kind: Literal["text", "file", "check", "option", "date", "decline", "skip"]
    value: str = ""
    # A file input that takes several files gets all of them; value stays the first (the CV).
    files: list[str] = field(default_factory=list)


_TERM_RE = re.compile(r"(\d+)\s*(days?|tage?n?|weeks?|wochen?|months?|monate?n?)\b", re.IGNORECASE)


def split_street(street: str) -> tuple[str, str]:
    """«Musterstraße 1» → («Musterstraße», «1»); «Am Ring 12a», «Hauptstr. 5-7» likewise. Without a
    trailing number the whole text is the street and the number is empty."""
    m = re.match(r"^(.*?)\s+(\d+\s*[a-zA-Z]?(?:\s*[-/]\s*\d+\s*[a-zA-Z]?)?)$", street.strip())
    return (m.group(1), m.group(2).replace(" ", "")) if m else (street.strip(), "")


def earliest_start(today: date, availability: str) -> date:
    """Earliest start date from the availability text.

    «no notice period», «sofort», «immediately» mean today, even next to the word notice. A stated
    term counts as written («within 2 weeks» is +14 days, «3 Monate» +90); notice words without a
    number mean +28 days; anything else today.
    """
    text = availability or ""
    if re.search(_NO_NOTICE_WORDS, text, re.IGNORECASE):
        return today
    m = _TERM_RE.search(text)
    if m:
        n, unit = int(m.group(1)), m.group(2).lower()
        days = n if unit.startswith(("day", "tag")) else n * 7 if unit.startswith(("week", "woche")) else n * 30
        return today + timedelta(days=days)
    if re.search(_NOTICE_WORDS, text, re.IGNORECASE):
        return today + timedelta(days=28)
    return today


def _country_en(country: str) -> str:
    return "Germany" if country.strip().lower() == "deutschland" else country


def _option_like(options: list[str], text: str) -> str | None:
    """Опція, що збігається з text без урахування регістру."""
    for o in options:
        if o.strip().lower() == text.strip().lower():
            return o
    return None


def _names_place(meta: str) -> bool:
    """Питання саме називає країну чи регіон: _PLACE_WORDS, країна з engagement чи штат США."""
    return bool(re.search(_PLACE_WORDS, meta, re.IGNORECASE) or _eng._countries_in(meta)
                or _eng._US_LOCATION_RE.search(meta))


def _region_only(place: str) -> bool:
    """Місце лише зі слів регіону чи remote («Remote», «Europe (Remote)», «EMEA»): після них нема літер."""
    return not re.search(r"[^\W\d_]", _REGION_WORDS_RE.sub(" ", place))


def _job_in_eu(lead: dict) -> bool:
    """Вакансія в ЄС/ЄЕЗ/Швейцарії. Місця: локація ліда й board_location (кілька через «;»).

    Відома країна місця (location_country, а тут і місто поза ЄС з таблиці будь-де в рядку:
    «London (Hybrid)», «Toronto, ON») переважає remote_scope, і всі відомі мають бути в ЄС:
    remote_scope бере «EU» чи «Europe» з будь-якого місця опису, і рядок про GDPR у вакансії
    в США дає europe. Конкретне місце без відомої країни («Podgorica, Montenegro», голий
    «Berlin») дає False, тобто hand-off, а не здогад. remote_scope (europe або country:<…>
    лише з ЄС) рахується, лише коли всі місця це слова регіону чи remote.
    """
    places = [str(lead.get("location") or "")] + str(lead.get("board_location") or "").split(";")
    known: list[str] = []
    for place in (p for p in places if p.strip()):
        country = location_country(place) or ("non-eu city" if _NON_EU_CITY_RE.search(place) else None)
        if country:
            known.append(country)
        elif not _region_only(place):
            return False
    if known:
        return all(c in EU_EEA_CH for c in known)
    scope = str(lead.get("remote_scope") or "")
    if scope == "europe":
        return True
    countries = _scope_countries(scope) if scope.startswith("country:") else []
    return bool(countries) and all(c in EU_EEA_CH for c in countries)


def _job_country_outside_eu(lead: dict) -> str | None:
    """The one known country of a job outside the EU/EEA/CH, title-cased, else None.

    Every specific place must resolve to that same country: an unknown place, a second country or any
    EU place gives None, so the question stays for the operator instead of a guess."""
    places = [str(lead.get("location") or "")] + str(lead.get("board_location") or "").split(";")
    found: set[str] = set()
    for place in (p for p in places if p.strip()):
        country = location_country(place)
        if country:
            found.add(country)
        elif not _region_only(place):
            return None
    if len(found) != 1:
        return None
    (country,) = found
    return None if country in EU_EEA_CH else country.title()


# Німецькі <select> без правила у form_answers: типові відповіді ринку найму.
_DE_SELECT_DEFAULTS: tuple[tuple[str, str], ...] = (
    (r"k(?:ü|ue)ndigungsfrist", r"^(?:sofort|keine|ohne|nicht)"),
    (r"umzug|relocat", r"^(?:nein|no)\b"),
    (r"w(?:ä|ae)hrung|currency", r"\beur"),
    (r"\bland\b|country", r"^(?:deutschland|germany)"),
)


def _date_text(f: Field, iso: str) -> Answer:
    """type=date хоче ISO, текстове поле німецької форми — dd.mm.yyyy."""
    if f.type == "date":
        return Answer("date", iso)
    y, m, d = iso.split("-")
    return Answer("text", f"{d}.{m}.{y}")


def _salary_text(f: Field, profile: dict) -> Answer | None:
    """Річна зарплата з профілю; числове поле отримує лише цифри."""
    raw = re.sub(r"[^0-9]", "", str(profile.get("default_salary") or ""))
    if not raw:
        return None
    return Answer("text", raw if f.type == "number" else f"{raw} EUR")


_ANNUAL_WORDS = r"annual|yearly|per year|a year|p\.\s?a\.|jährlich|jaehrlich|jahres|pro jahr|im jahr"
_DAY_WORDS = r"day rate|daily rate|per day|a day|tagessatz|pro tag"
_HOUR_WORDS = r"hour|stunde|stündlich|/\s*h\b"
_CURRENCY_WORDS = r"\beur\b|€|euro"
# Contract market only: pay questions the field classifier leaves as unknown or free text
# («Expected annual salary», «compensation», «hourly rate», «Jahresbruttoeinkommen»).
_PAY_WORDS = (r"salary|compensation|gehalt|einkommen|vergütung|verguetung|stundensatz|tagessatz|pay expectation"
              r"|(?:hourly|daily|day|billing|desired|expected|target|contract|your)\s+rates?\b"
              r"|\brates?\s+(?:expectation|requirement|range)")


def _contract_salary(f: Field, profile: dict, meta: str) -> Answer | None:
    """Contract market: an annual question or a bare numeric field gets `annual_salary` (digits when the
    field is numeric or names the currency); a numeric hourly or day-rate field gets that rate as a
    number; anything else the hourly sentence."""
    rate = profile.get("rate") or {}
    annual = re.sub(r"[^0-9]", "", str(profile.get("annual_salary") or ""))
    numeric = f.type == "number"
    if numeric and re.search(_DAY_WORDS, meta, re.IGNORECASE) and rate.get("daily_target"):
        return Answer("text", str(rate["daily_target"]))
    if numeric and re.search(_HOUR_WORDS, meta, re.IGNORECASE) and rate.get("hourly_target"):
        return Answer("text", str(rate["hourly_target"]))
    if annual and (numeric or re.search(_ANNUAL_WORDS, meta, re.IGNORECASE)):
        bare = numeric or re.search(_CURRENCY_WORDS, meta, re.IGNORECASE)
        return Answer("text", annual if bare else f"{annual} EUR")
    r = _rate_text(profile)
    return Answer("text", r) if r else None


def _rate_text(profile: dict) -> str | None:
    rate = profile.get("rate") or {}
    if not rate.get("hourly_target") or not rate.get("currency_default"):
        return None
    return f"{rate['hourly_target']} {rate['currency_default']} per hour (B2B contract)"


_YEARS_FILLER = (
    r"do you have|have you|haben sie|hast du|\bhow\b|\bmany\b|\bmuch\b|\bwork\b|\bexperience\b|\berfahrung\b"
    r"|\bberufserfahrung\b|\bsie\b|\bhaben\b|\byears?\b|\bjahre\b|\bwie\b|\bviele\b|\bberufs\b|\bof\b|\byou\b|\byour\b|\bthe\b"
)


def _years_answer(text: str, table: list[dict]) -> Answer | None:
    """Роки досвіду лише з таблиці профілю `experience_years`, ніколи з моделі.

    Спершу перший запис не-total, чий шаблон знайдено в тексті питання. Інакше запис
    `total`, але лише коли питання не називає технології: після прибирання фрази про
    роки й службових слів не лишається слів від 3 літер, або є total/overall/insgesamt/
    gesamt. Решта: None (hand-off краще за впевнено хибне число). Запис без `years`
    або з хибним regex пропускається з попередженням.
    """
    entries = []
    for e in table:
        if not isinstance(e, dict) or e.get("years") is None or not e.get("pattern"):
            log.warning("experience_years: запис без pattern/years пропущено: %r", e)
            continue
        try:
            re.compile(str(e["pattern"]))
        except re.error:
            log.warning("experience_years: хибний regex пропущено: %r", e["pattern"])
            continue
        entries.append(e)
    for e in entries:
        if str(e["pattern"]).lower() != "total" and re.search(str(e["pattern"]), text, re.IGNORECASE):
            return Answer("text", str(e["years"]))
    rest = re.sub(_YEARS, " ", text, flags=re.IGNORECASE)
    rest = re.sub(_YEARS_FILLER, " ", rest, flags=re.IGNORECASE)
    names_tech = re.search(r"[^\W\d_]{3,}", rest) is not None
    if names_tech and not re.search(r"total|overall|insgesamt|gesamt", text, re.IGNORECASE):
        return None
    for e in entries:
        if str(e["pattern"]).lower() == "total":
            return Answer("text", str(e["years"]))
    return None



# Consents that reach beyond the application at hand (02.11.2026: a 12-month store «for suitable
# future positions», a hand-over «für weitere Stellenangebote an die Unternehmen innerhalb der Group»).
_BEYOND_THIS_APPLICATION = (
    r"future (?:positions?|roles?|vacanc\w+|opportunit\w+|openings?|jobs?)|talent ?pool|talent community"
    # «für zukünftig weitere ausgeschriebene Stellen» (an hrpuls form, 03.11.2026): up to two words between.
    r"|kandidaten-?pool|bewerber-?pool|(?:weitere|andere|zukünftig|künftig)\w*\s+(?:\w+\s+){0,2}stellen"
    r"|newsletter|job ?alerts?|other (?:companies|group companies|employers)"
    r"|andere\w* unternehmen|unternehmen (?:innerhalb )?der .{0,60}(?:gruppe|group)"
    r"|after the conclusion of the application|über das (?:aktuelle )?bewerbungsverfahren hinaus"
    r"|beyond (?:this|the) (?:application|recruitment)")


def answer_for(f: Field, ctx: AnswerContext) -> Answer | None:
    """Відповідь для поля або None, якщо профіль не знає її.

    Дозвіл на роботу й спонсорство: питання, що саме не називає країну чи регіон
    («…in the country where the job is located?»), для вакансії в ЄС/ЄЕЗ/Швейцарії
    (_job_in_eu) отримує тег « [job country: EU]» перед звіркою з form_answers. Тому
    шаблони дозволу й спонсорства в профілі клієнта обмежуються словами ЄС і мають
    choice: `authori[sz]ed.{0,200}(germany|\\beu\\b|europe…)`. Не .{0,80}: meta це підпис і
    група, часто те саме довге питання двічі, і до тегу буває понад 80 символів. Для
    вакансії в ЄС тег такі шаблони задовольняє, для США чи Канади поле лишається без
    відповіді й іде в hand-off.
    """
    # forms імпортує candidate, що читає конфіг клієнта під час імпорту.
    from bot.apply.forms import choose_radio, text_answer

    p = ctx.profile
    kind = classify(f)
    links = p.get("links") or {}
    answers = p.get("form_answers") or []
    meta = f"{f.label} {f.group}".strip()
    if re.search(_AUTH_WORDS, meta, re.IGNORECASE) and not _names_place(meta):
        if _job_in_eu(ctx.lead):
            meta += EU_JOB_TAG
        elif outside := _job_country_outside_eu(ctx.lead):
            meta += f" [job country: {outside}]"

    def text(value) -> Answer | None:
        return Answer("text", str(value)) if value else None

    if (ctx.market == "contract" and kind in ("salary", "unknown", "free_text")
            and f.type not in ("radio", "checkbox") and f.tag != "select"
            and re.search(_PAY_WORDS, meta, re.IGNORECASE)):
        return _contract_salary(f, p, meta)

    # «(Required if you answered 'yes' to …) What was your work email?»: the follow-up of a question the
    # candidate answered no to. A Greenhouse form got the candidate's own mail here (03.11.2026).
    if not f.required and re.search(r"\bif (?:you )?answered\b|\bif (?:yes|so)\b", meta, re.IGNORECASE):
        return Answer("skip")

    if kind == "years":
        question = " ".join(x for x in (f.label, f.group, f.placeholder) if x)
        return _years_answer(question, p.get("experience_years") or [])

    if kind == "unknown" and f.type == "file":
        return Answer("skip")  # безіменний автозаповнювач Ashby
    if kind == "first_name":
        return text(p.get("first_name"))
    if kind == "last_name":
        return text(p.get("last_name"))
    if kind == "full_name":
        return text(f"{p.get('first_name', '')} {p.get('last_name', '')}".strip())
    if kind == "preferred_name":
        return text(p.get("first_name"))
    if kind == "email":
        return text(p.get("email"))
    if kind == "phone":
        return text(p.get("phone"))
    if kind in ("linkedin", "github", "website"):
        return text(links.get(kind))
    if kind == "location":
        city = p.get("city")
        if not city:
            return None
        country = _country_en(str(p.get("country") or "")).strip()
        return text(f"{city}, {country}" if country else city)
    if kind == "country":
        if f.options:
            hit = next((o for o in f.options if re.search(r"germany|deutschland", o, re.IGNORECASE)), None)
            if hit:
                return Answer("option", hit)
        return Answer("text", "Germany")
    if kind == "resume":
        return Answer("file", str(ctx.resume))
    if kind == "cover_letter_file":
        return Answer("file", str(ctx.cover_letter)) if ctx.cover_letter else Answer("skip")
    if kind == "photo":
        return Answer("skip")   # the package has no photo; a required one stays open for the operator
    if kind == "documents":
        # Whether it takes the CV depends on the whole form (ats_form.fill_form). Alone, the old file
        # rules: a required documents field takes the CV, an optional one the cover letter.
        if f.required:
            return Answer("file", str(ctx.resume))
        return Answer("file", str(ctx.cover_letter)) if ctx.cover_letter else Answer("skip")
    if kind == "street":
        return text(p.get("street"))
    if kind == "house_number":
        return text(split_street(str(p.get("street") or ""))[1])
    if kind == "zip":
        return text(p.get("zip_code"))
    if kind == "city":
        return text(p.get("city"))
    if kind == "birth_date":
        raw = str(p.get("birth_date") or "")
        return _date_text(f, raw) if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw) else None
    if kind == "nationality":
        want = str(p.get("nationality") or "")
        if not want:
            return None
        if f.options:
            hit = _option_like(f.options, want) or next(
                (o for o in f.options if o.lower().startswith(want.lower()[:5])), None)
            return Answer("option", hit) if hit else None
        return Answer("text", want)
    if kind == "salutation":
        want = str(p.get("salutation") or "")
        options = f.options or []
        hit = next((o for o in options if o.strip().lower().startswith(want.lower())), None) if want else None
        return Answer("option", hit) if hit else None
    if kind == "salary":
        if ctx.market == "de_employment":
            return _salary_text(f, p)
        return _contract_salary(f, p, meta)
    if kind == "source":
        t = text_answer(meta, answers)
        if f.options:
            hit = _option_like(f.options, t) if t else None
            hit = hit or next((o for o in f.options if re.search(r"stepstone|linkedin|online|internet|jobb(?:ö|oe)rse", o, re.I)), None)
            return Answer("option", hit) if hit else None
        return text(t)
    if kind == "cover_letter_text":
        return text(ctx.note)
    if kind == "eeo":
        for o in f.options or []:
            if re.search(_EEO_DECLINE, o, re.IGNORECASE):
                return Answer("option", o)
        if f.combobox:
            return Answer("decline")  # react-select Greenhouse: опції видно лише після відкриття
        return Answer("skip")
    if kind == "ack":
        # A profile rule with choice 'no' wins; an optional consent that reaches beyond this application
        # (future positions, a talent pool, other companies, a newsletter) is the candidate's call.
        if choose_radio(meta, ["Yes", "No"], answers) == 1:
            return Answer("skip")
        if not f.required and re.search(_BEYOND_THIS_APPLICATION, meta, re.IGNORECASE):
            return Answer("skip")
        return Answer("check")
    if kind == "date":
        if ctx.market == "de_employment":
            from bot.apply.forms import earliest_start_iso
            return _date_text(f, earliest_start_iso(ctx.today))
        return Answer("date", earliest_start(ctx.today, str(p.get("availability", ""))).isoformat())
    if kind == "choice":
        options = f.options
        if options is None and f.group.strip():
            options = ["Yes", "No"]  # Ashby рисує Yes/No кнопками без options
        idx = choose_radio(meta, options or [], answers)
        return Answer("option", options[idx]) if idx is not None and options else None
    if kind == "select":
        options = f.options or []
        idx = choose_radio(meta, options, answers)
        if idx is not None:
            return Answer("option", options[idx])
        t = text_answer(meta, answers)
        hit = _option_like(options, t) if t else None
        if hit is None and ctx.market == "de_employment":
            for pat, opt_re in _DE_SELECT_DEFAULTS:
                if re.search(pat, meta, re.IGNORECASE):
                    hit = next((o for o in options if re.search(opt_re, o.strip(), re.IGNORECASE)), None)
                    break
        return Answer("option", hit) if hit else None
    # unknown | free_text
    if f.combobox:
        # Yes/No react-select Greenhouse: надрукований «Yes» чи «No» лишає точну опцію,
        # а речення з answer відфільтрувало б усі, і поле очистилось би.
        yes_no = ["Yes", "No"]
        idx = choose_radio(meta, yes_no, answers)
        if idx is not None:
            return Answer("text", yes_no[idx])
    t = text_answer(meta, answers)
    if t:
        return Answer("text", t)
    if re.search(_RATE_WORDS, meta, re.IGNORECASE):
        r = _rate_text(p)
        return Answer("text", r) if r else None
    return None


def llm_allowed(f: Field) -> bool:
    """Модель можна викликати лише для вільного тексту.

    Комбобокс (react-select зі спонсорством, країною, EEO) ніколи: модель надрукувала б
    прозу, а драйвер вибрав би першу-ліпшу опцію. Питання про дозвіл на роботу чи візу
    теж ніколи, і в textarea: без відповіді в form_answers воно стає hand-off (спека §2).
    """
    if classify(f) not in ("free_text", "cover_letter_text") or f.combobox:  # years сюди не входить
        return False
    return not re.search(_AUTH_WORDS, f"{f.label} {f.group}", re.IGNORECASE)


def llm_question(f: Field) -> str:
    """The form's own text for a free-text field: what the model answers and what its answer is checked against."""
    return " ".join(f"{f.label} {f.group} {f.placeholder}".split())


# Words that tell a German question from an English one. Umlauts and ß count as German too.
_GERMAN_WORDS = re.compile(
    r"[äöüß]|\b(?:und|oder|sie|ihr|ihre|ihnen|uns|wir|warum|wieso|wie|was|wann|welche[nmrs]?|bitte|bei|für"
    r"|mit|über|zu|der|die|das|ein|eine|nachricht|mitteilung|anmerkung\w*|bemerkung\w*|kommentar\w*"
    r"|anschreiben|bewerbung|erfahrung|möchten|haben|sind)\b", re.IGNORECASE)
_ENGLISH_WORDS = re.compile(
    r"\b(?:the|and|or|you|your|us|we|why|what|how|when|which|please|tell|with|about|for|to|do|does|are|is"
    r"|message|comments?|describe|experience|join|want)\b", re.IGNORECASE)


def answer_language(question: str, lang: str) -> str:
    """The language a free-text answer must be in: the question's own language when its words tell
    German from English, otherwise the package language (German for a German package)."""
    de = len(_GERMAN_WORDS.findall(question))
    en = len(_ENGLISH_WORDS.findall(question))
    if de != en:
        return "German" if de > en else "English"
    return "German" if (lang or "").lower().startswith("de") else "English"


def llm_prompt(f: Field, ctx: AnswerContext, brief: str) -> str:
    """The model request for a free-text answer. The brief is often in the candidate's own language
    (30.10.2026: Ukrainian), so the answer language is named explicitly, and a vague field («Nachricht»)
    gets a short message from the cover note instead of a question back."""
    question = llm_question(f)
    language = answer_language(question, ctx.lang)
    return (
        f"Vacancy: {ctx.lead.get('title', '')} at {ctx.lead.get('company', '')}.\n"
        f"Question from the application form: {question}\n\n"
        f"Candidate brief (the brief may be written in another language; do not answer in it):\n{brief}\n\n"
        f"Cover note already sent:\n{ctx.note}\n\n"
        f"Write the answer in {language}, at most {MAX_ANSWER_CHARS} characters, "
        "plain text, no greetings, no dashes as connectors, never claim an action that "
        "already happened, never name the current employer. If the question is unclear or the field is "
        "a general message box, write a short relevant message to the employer based on the cover note. "
        "Never ask a question back, never ask for clarification, never comment on these rules. "
        "Output only the answer."
    )


_CYRILLIC = re.compile(r"[Ѐ-ӿ]")
# The prompt's own rules coming back in the answer: the length limit, a format word, a prompt heading.
_ECHO = re.compile(
    rf"(?<!\d){MAX_ANSWER_CHARS}(?!\d)|\d+\s*(?:characters|chars|zeichen|знак|символ)"
    r"|plain text|output only|no greetings|dashes as connectors|answer in (?:german|english)"
    r"|candidate brief|cover note already sent|question from the application form",
    re.IGNORECASE)
# A reply that asks back instead of answering. A form answer has no reason to hold a question mark.
_ASKS_BACK = re.compile(
    r"\?|clarif|could you|can you (?:tell|share|specify|provide)|please (?:provide|specify|clarify|share|tell)"
    r"|let me know|not sure what to write|which question|was genau|präzisier|welche frage"
    r"|уточн|що саме|що (?:потрібно|треба) написати",
    re.IGNORECASE)


def llm_answer_problem(answer: str, question: str) -> str:
    """Why a model answer must not be typed into the form, or "" when it may.

    30.10.2026 a German form got a Ukrainian reply that echoed the prompt's rules and asked what to
    write. Rejected: an empty answer, Cyrillic on a form whose question has none, echoed prompt rules,
    and a question back. A rejected answer leaves the field open, so a required one becomes a hand-off.
    """
    text = (answer or "").strip()
    if not text:
        return "empty"
    if _CYRILLIC.search(text) and not _CYRILLIC.search(question or ""):
        return "cyrillic answer on a form without cyrillic"
    if _ECHO.search(text):
        return "echoes the prompt instructions"
    if _ASKS_BACK.search(text):
        return "asks a question back"
    return ""


def clean_llm_answer(text: str) -> str:
    """Прибирає code fence і лапки, стискає пробіли, ріже до 600 символів по кінцю речення."""
    t = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", text.strip())
    t = " ".join(t.split()).strip("\"'“”«»").strip()
    if len(t) <= MAX_ANSWER_CHARS:
        return t
    cut = t[:MAX_ANSWER_CHARS]
    ends = [m.end() for m in re.finditer(r"[.!?](?=\s|$)", cut)]
    return cut[: ends[-1]].strip() if ends else cut.rstrip()
