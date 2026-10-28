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
_EEO_DECLINE = r"decline|prefer not|don't wish|do not wish|keine angabe"
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


@dataclass
class Answer:
    kind: Literal["text", "file", "check", "option", "date", "decline", "skip"]
    value: str = ""


def earliest_start(today: date, availability: str) -> date:
    """Найближча дата виходу: +28 днів, якщо в availability є термін повідомлення, інакше сьогодні.

    «no notice period», «sofort», «immediately» означають сьогодні, хоч би там і було слово notice.
    """
    text = availability or ""
    if re.search(_NO_NOTICE_WORDS, text, re.IGNORECASE):
        return today
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
    if re.search(_AUTH_WORDS, meta, re.IGNORECASE) and not _names_place(meta) and _job_in_eu(ctx.lead):
        meta += EU_JOB_TAG

    def text(value) -> Answer | None:
        return Answer("text", str(value)) if value else None

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
            hit = next((o for o in f.options if "germany" in o.lower()), None)
            if hit:
                return Answer("option", hit)
        return Answer("text", "Germany")
    if kind == "resume":
        return Answer("file", str(ctx.resume))
    if kind == "cover_letter_file":
        return Answer("skip")
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
        return Answer("check")
    if kind == "date":
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


def llm_prompt(f: Field, ctx: AnswerContext, brief: str) -> str:
    """Запит до моделі на відповідь для вільного тексту."""
    question = " ".join(f"{f.label} {f.group}".split())
    return (
        f"Vacancy: {ctx.lead.get('title', '')} at {ctx.lead.get('company', '')}.\n"
        f"Question from the application form: {question}\n\n"
        f"Candidate brief:\n{brief}\n\n"
        f"Cover note already sent:\n{ctx.note}\n\n"
        f"Write the answer in the language of the question, at most {MAX_ANSWER_CHARS} characters, "
        "plain text, no greetings, no dashes as connectors, never claim an action that "
        "already happened, never name the current employer. Output only the answer."
    )


def clean_llm_answer(text: str) -> str:
    """Прибирає code fence і лапки, стискає пробіли, ріже до 600 символів по кінцю речення."""
    t = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", text.strip())
    t = " ".join(t.split()).strip("\"'“”«»").strip()
    if len(t) <= MAX_ANSWER_CHARS:
        return t
    cut = t[:MAX_ANSWER_CHARS]
    ends = [m.end() for m in re.finditer(r"[.!?](?=\s|$)", cut)]
    return cut[: ends[-1]].strip() if ends else cut.rstrip()
