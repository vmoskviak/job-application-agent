"""Чисті класифікатори для ринку contract: тип зайнятості, remote-охоплення,
часовий пояс, US work authorization, ставка, мова. Без I/O і без конфігу клієнта.
Списки сигналів: spec contract-market, розділи R4, R5, R6."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

# --- R4: тип зайнятості ---
_CONTRACT_RE = re.compile(
    # «B2B marketplace», «B2B SaaS» — бізнес-модель компанії, не форма співпраці
    r"\b(contract(?:or|ors)?|freelanc\w*"
    r"|b2b(?![\s-]+(?:marketplace|saas|platform|sales|software|products?|startup|compan(?:y|ies)"
    r"|customers?|clients?|e-?commerce|business|market|tech|fintech|space|leads?))"
    r"|1099|c2c|corp[- ]to[- ]corp"
    r"|day[- ]rate|hourly[- ]rate|freiberuflich\w*|projekt(?:laufzeit)?"
    r"|stundensatz|auslastung)\b",
    re.I,
)
# Формулювання найму, що лише містять слово «contract»: це employment, не contract
_EMPLOYMENT_CONTRACT_RE = re.compile(
    r"(permanent contract|employment contract|contract of employment"
    r"|arbeitsvertrag|unbefristeter vertrag)",
    re.I,
)
# Заперечений freelance не є contract-сигналом
_NEGATED_FREELANCE_RE = re.compile(r"\b(?:kein(?:e|en)?|no)\s+freelanc\w*", re.I)
# Явна відмова фрилансерам перемагає будь-який contract-сигнал, навіть шапку
# «Projektbörse GULP (freiberuflich)». Агентури GULP пишуть це шаблоном
# («Bitte sehen Sie als Freiberufler*innen von Bewerbungen ab», 22.10.2026).
_FREELANCER_REJECTED_RE = re.compile(
    r"(freelancers?\s+(?:should|must|need\s+to)\s+(?:refrain|not\s+apply)"
    r"|(?:cannot|can't|can\s+not|unable\s+to|will\s+not|won't|do\s+not|don't)\s+"
    r"(?:present|consider|accept|hire|work\s+with)\s+(?:any\s+)?freelancers?"
    r"|\bno\s+freelancers?\b"
    r"|(?:only|exclusively)\s+(?:accepting\s+|accept\s+)?(?:applications\s+from\s+)?"
    r"(?:employed|permanent|salaried)\s+(?:staff|employees|candidates)"
    r"|ausschlie(?:ß|ss)lich\s+bewerbungen\s+von\s+(?:fest)?angestellten"
    r"|freiberufler\S*\s+k(?:ö|oe)nnen\s+(?:\w+\s+){0,4}nicht\s+vorgestellt"
    r"|als\s+freiberufler\S*\s+von\s+(?:einer\s+)?bewerbung(?:en)?\s+ab"
    r"|\bkein(?:e|en)?\s+freiberufler\S*"
    r"|\bnur\s+festangestellte)",
    re.I,
)
_EMPLOYMENT_RE = re.compile(
    r"(full[- ]time employee|permanent position|\bw-?2\b|\b401\s?\(?k\)?"
    r"|stock options|equity package|benefits package|festanstellung"
    r"|unbefristet|30 urlaubstage"
    # бенефіти штатної посади; рахуються лише без жодного contract-сигналу
    r"|\bvacation\s+days\b|\bpaid\s+(?:time\s+off|vacation|parental\s+leave)\b"
    r"|\bparental\s+leave\b|\bpto\b)",
    re.I,
)
# HN-пости, які автор позначив закритими («CLOSED NOW», «[CLOSED]»): liveness
# для HN завжди live, тож перевірка лише по тексту.
_CLOSED_RE = re.compile(
    r"(\bclosed\s+now\b|[\[(]\s*closed\s*[\])]|\bupdate:?\s*closed\b"
    r"|\b(?:position|role|job)\s+(?:has\s+been\s+|is\s+(?:now\s+)?)(?:filled|closed)\b"
    r"|\bno\s+longer\s+(?:hiring|accepting\s+(?:new\s+)?applications\b(?!\s+from)))",
    re.I,
)
# Неоплачувана роль: equity-only, commission-only. Голе «unpaid» не рахується
# («collect unpaid invoices» у фінтех-описі).
_UNPAID_RE = re.compile(
    r"\b(equity[- ]only|commission[- ]only|commission[- ]based\s+only"
    r"|unpaid\s+(?:role|position|internship|work)|no\s+salary|sweat\s+equity)\b",
    re.I,
)

# --- R5: remote-охоплення ---
_REMOTE_POS_RE = re.compile(r"(?<!no )\bremote\b", re.I)
_ONSITE_RE = re.compile(
    r"(on-?site only|no remote|in office 5 days|100\s?% vor ort"
    r"|ausschließlich vor ort"
    # HN-формат «Компанія | Роль | Місто | ONSITE | Full-time»
    r"|\bon-?site\s*\||\bon-?site\s+\d\s+days)",
    re.I,
)
_US_ONLY_RE = re.compile(
    r"(must reside in the us|us[- ]based only|us only|us time ?zones? only"
    r"|must be (?:located )?in the (?:us|u\.s\.))",
    re.I,
)
_HYBRID_RE = re.compile(r"(\bhybrid\b|days? (?:per week )?in (?:the )?office)", re.I)

# Уточнення одразу після «remote»: «Remote (Germany)», «Remote - Poland»,
# «remote anywhere from Poland», «REMOTE [Europe / (GMT+2)]».
_REMOTE_QUAL_RE = re.compile(
    r"\bremote\b[ \t]*(?:\((?P<paren>[^()\n]{1,80})\)|\[(?P<brack>[^\[\]\n]{1,80})\]"
    r"|(?P<tail>[^|.;\n()\[\]]{0,60}))",
    re.I,
)
# Уточнення, що відкриває регіон (не обмеження однією країною)
_OPEN_QUAL_RE = re.compile(
    r"\b(europe\w*|eu|emea|cet|cest|gmt|utc|worldwide|global\w*|international"
    r"|anywhere\s+in\s+the\s+world|dach)\b",
    re.I,
)
# Америка як регіон. «US» лише великими: «reach us at» не є країною.
_AMERICAS_QUAL_RE = re.compile(
    r"(\bUS\b|\bU\.S\.|\bUSA\b|(?i:\bunited\s+states\b|\bamericas?\b"
    r"|\bnorth\s+america\b|\bcanada\b|\blatam\b|\blatin\s+america\b))"
)
# Локація США без назви країни (LinkedIn): «San Francisco, CA», «Austin, Texas
# Metropolitan Area», «San Francisco Bay Area». Код штату лише великими після коми
# в кінці рядка. DE пропущено свідомо: «Berlin, DE» трапляється частіше за Delaware.
# Georgia лише кодом GA (країна Грузія пишеться так само).
_US_STATE_CODES = (
    "AL AK AZ AR CA CO CT FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV "
    "NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC"
).split()
_US_STATE_NAMES = (
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado", "connecticut",
    "delaware", "florida", "hawaii", "idaho", "illinois", "indiana", "iowa", "kansas",
    "kentucky", "louisiana", "maine", "maryland", "massachusetts", "michigan", "minnesota",
    "mississippi", "missouri", "montana", "nebraska", "nevada", "new hampshire",
    "new jersey", "new mexico", "new york", "north carolina", "north dakota", "ohio",
    "oklahoma", "oregon", "pennsylvania", "rhode island", "south carolina", "south dakota",
    "tennessee", "texas", "utah", "vermont", "virginia", "washington", "west virginia",
    "wisconsin", "wyoming", "district of columbia", "bay area", "silicon valley",
    "new york city",
)
_US_LOCATION_RE = re.compile(
    r"(?:,\s*(?:" + "|".join(_US_STATE_CODES) + r")\s*$"
    r"|(?i:\b(?:" + "|".join(re.escape(n) for n in _US_STATE_NAMES) + r")\b))"
)
# Службові слова між «remote» і назвою країни
_QUAL_LEAD_RE = re.compile(
    r"^[\s\-–—:,/]*(?:(?:anywhere|only|work|working|position|role|possible)\s+)*"
    r"(?:(?:from|in|within|across|inside|based\s+in)\s+)?(?:the\s+)?",
    re.I,
)
# Країна (канонічна назва англійською) ← варіанти назв EN/DE/місцеві
_COUNTRY_ALIASES: dict[str, tuple[str, ...]] = {
    "germany": ("germany", "deutschland"),
    "austria": ("austria", "österreich", "oesterreich"),
    "switzerland": ("switzerland", "schweiz", "suisse"),
    "poland": ("poland", "polen", "polska"),
    "netherlands": ("the netherlands", "netherlands", "niederlande", "holland"),
    "belgium": ("belgium", "belgien"),
    "france": ("france", "frankreich"),
    "spain": ("spain", "spanien", "españa"),
    "portugal": ("portugal",),
    "italy": ("italy", "italien", "italia"),
    "ireland": ("ireland", "irland"),
    "united kingdom": ("united kingdom", "great britain", "großbritannien", "england", "uk", "u.k."),
    "sweden": ("sweden", "schweden"),
    "norway": ("norway", "norwegen"),
    "denmark": ("denmark", "dänemark"),
    "finland": ("finland", "finnland"),
    "czechia": ("czech republic", "czechia", "tschechien"),
    "slovakia": ("slovakia", "slowakei"),
    "hungary": ("hungary", "ungarn"),
    "romania": ("romania", "rumänien"),
    "bulgaria": ("bulgaria", "bulgarien"),
    "greece": ("greece", "griechenland"),
    "croatia": ("croatia", "kroatien"),
    "serbia": ("serbia", "serbien"),
    "slovenia": ("slovenia", "slowenien"),
    "estonia": ("estonia", "estland"),
    "latvia": ("latvia", "lettland"),
    "lithuania": ("lithuania", "litauen"),
    "ukraine": ("ukraine",),
    "turkey": ("turkey", "türkei", "türkiye"),
    "israel": ("israel",),
    "india": ("india", "indien"),
    "pakistan": ("pakistan",),
    "philippines": ("philippines", "philippinen"),
    "vietnam": ("vietnam",),
    "indonesia": ("indonesia", "indonesien"),
    "singapore": ("singapore", "singapur"),
    "japan": ("japan",),
    "australia": ("australia", "australien"),
    "new zealand": ("new zealand", "neuseeland"),
    "brazil": ("brazil", "brasilien"),
    "mexico": ("mexico", "mexiko"),
    "argentina": ("argentina", "argentinien"),
    "colombia": ("colombia", "kolumbien"),
    "south africa": ("south africa", "südafrika"),
    "nigeria": ("nigeria",),
    "egypt": ("egypt", "ägypten"),
}
_ALIAS_TO_COUNTRY: dict[str, str] = {
    alias: country for country, aliases in _COUNTRY_ALIASES.items() for alias in aliases
}
_COUNTRY_RE = re.compile(
    r"(?<![\w.])(" + "|".join(
        re.escape(a) for a in sorted(_ALIAS_TO_COUNTRY, key=len, reverse=True)
    ) + r")(?![\w])",
    re.I,
)
_EUROPE_RE = re.compile(r"(\beurope\b|\beu\b|\bemea\b|\bcet\b|\bcest\b)", re.I)
_GLOBAL_RE = re.compile(r"(worldwide|\banywhere\b)", re.I)
# голе «global» без «remote» поруч не є ознакою remote-охоплення
_GLOBAL_WORD_RE = re.compile(r"\bglobal(?:ly)?\b", re.I)

_ET_RE = re.compile(
    r"(overlap with (?:us )?(?:east|eastern|et\b|est\b)|\bet/est\b|us east)", re.I
)
_PACIFIC_RE = re.compile(r"(pacific (?:hours|time)|\bpst\b|\bpdt\b)", re.I)

# --- R6: US work authorization ---
_US_AUTH_NEGATION_RE = re.compile(
    r"(\bno\b[^.;,\n]{0,40}?(?:authori[sz]ation|clearance|green card|itar)"
    r"[^.;,\n]{0,20}?\brequired\b"
    r"|authori[sz]ation (?:is )?not required|worldwide)",
    re.I,
)
_US_AUTH_RE = re.compile(
    r"(authori[sz]ed to work in the (?:us|u\.s\.)|us work authori[sz]ation"
    r"|us citizens only|green card|security clearance|\bitar\b)",
    re.I,
)

# --- ставка ---
_RATE_RE = re.compile(
    r"(?P<c1>[$€£])?\s*(?P<lo>\d[\d.,]*)\s*(?P<k1>k\b)?"
    r"(?:\s*[-–]\s*[$€£]?\s*(?P<hi>\d[\d.,]*)\s*(?P<k2>k\b)?)?"
    r"\s*(?P<c2>[$€£]|EUR|USD|GBP|PLN|CHF|euros?|zł|zl)?"
    r"\s*(?:/|per\s+|pro\s+|je\s+|an?\s+)?\s*"
    r"(?P<unit>hour|hr|h|stunde|std|day|tag|month|mo|monat)\b",
    re.I,
)
_CURRENCY = {
    "$": "USD", "usd": "USD", "€": "EUR", "eur": "EUR", "euro": "EUR", "euros": "EUR",
    "£": "GBP", "gbp": "GBP", "pln": "PLN", "zł": "PLN", "zl": "PLN", "chf": "CHF",
}
_UNIT = {"hour": "hour", "hr": "hour", "h": "hour", "stunde": "hour", "std": "hour",
         "day": "day", "tag": "day", "month": "month", "mo": "month", "monat": "month"}

# --- мови, які вакансія вимагає ---
# Префікс слова: «Deutschkenntnisse», «polskiego», «angielskiego». Англійське
# «Polish» лише з великої літери: «UI polish» не є мовою.
_LANG_ALIASES: dict[str, tuple[str, ...]] = {
    "english": ("english", "englisch", "angielsk"),
    "german": ("german", "deutsch", "niemieck"),
    "polish": ("polnisch", "polsk"),
    "dutch": ("dutch", "niederländisch", "nederlands"),
    "french": ("french", "französisch", "français", "francais"),
    "spanish": ("spanish", "spanisch", "español", "espanol"),
    "italian": ("italian", "italienisch", "italiano"),
    "portuguese": ("portuguese", "portugiesisch", "português"),
    "swedish": ("swedish", "schwedisch", "svenska"),
    "danish": ("danish", "dänisch", "dansk"),
    "norwegian": ("norwegian", "norwegisch", "norsk"),
    "finnish": ("finnish", "finnisch"),
    "czech": ("czech", "tschechisch"),
    "hungarian": ("hungarian", "ungarisch"),
    "romanian": ("romanian", "rumänisch"),
    "russian": ("russian", "russisch"),
    "ukrainian": ("ukrainian", "ukrainisch"),
    "turkish": ("turkish", "türkisch"),
    "japanese": ("japanese", "japanisch"),
    "mandarin": ("mandarin", "chinese", "chinesisch"),
    "korean": ("korean", "koreanisch"),
    "hebrew": ("hebrew", "hebräisch"),
    "arabic": ("arabic", "arabisch"),
    "greek": ("greek", "griechisch"),
}
# Слова, після яких назва мови є прикметником країни, а не мовою
_LANG_NOUN_AFTER = (
    r"(?!\w|\s+(?:company|companies|startup|start-up|fintech|clients?|customers?|market|"
    r"team|office|brand|bank|government|law|tax|users?|based))"
)
_LANG_WORD_RE = {
    lang: re.compile(
        r"\b(?:" + "|".join(re.escape(a) for a in aliases) + r")"
        + (r"(?!land|y\b|a\b)" if lang in ("german", "polish") else "")
        + r"\w*" + _LANG_NOUN_AFTER,
        re.I,
    )
    for lang, aliases in _LANG_ALIASES.items()
}
_POLISH_EN_RE = re.compile(r"\b(?:Polish|POLISH)\b" + _LANG_NOUN_AFTER)
# Речення, де мова саме вимога, а не опис компанії
_LANG_REQ_RE = re.compile(
    r"(\bfluen\w*|\bnative\b|mother\s*tongue|\bproficien\w*|\brequired\b|\brequirement"
    r"|\bmust\b|\bmandatory\b|\bessential\b|\blevel\b|\bspeak\w*|\bspoken\b|\bwritten\b"
    r"|working\s+knowledge|command\s+of|\b[abc][12]\b|verhandlungssicher|flie(?:ß|ss)end"
    r"|kenntnis|muttersprach|zwingend|erforderlich|vorausgesetzt"
    r"|znajomo|biegł|biegl|wymagan|języ|jezy)",
    re.I,
)
# Бажано, але не обов'язково
_LANG_OPTIONAL_RE = re.compile(
    r"(\ba\s+plus\b|\bis\s+a\s+plus\b|nice\s+to\s+have|\bbonus\b|\badvantage"
    r"|von\s+vorteil|wünschenswert|mile\s+widzian|\bpreferred\b)",
    re.I,
)
_SENTENCE_SPLIT_RE = re.compile(r"[.!?;\n]+")

_DE_STOPWORDS = frozenset(
    {"und", "der", "die", "das", "mit", "für", "wir", "sie", "nicht", "ein",
     "eine", "ist", "im", "auf", "zu"}
)
_DE_THRESHOLD = 0.04


@dataclass(frozen=True)
class Rate:
    low: int
    high: int
    unit: Literal["hour", "day", "month"]
    currency: Literal["EUR", "USD", "GBP", "PLN", "CHF"]


def classify_engagement(title: str, description: str) -> Literal["contract", "employment", "unknown"]:
    """contract, якщо є будь-який contract-сигнал; employment лише без нього.
    Явна відмова фрилансерам дає employment навіть поруч із contract-сигналом."""
    text = f"{title} {description}"
    if _FREELANCER_REJECTED_RE.search(text):
        return "employment"
    cleaned = _NEGATED_FREELANCE_RE.sub(" ", _EMPLOYMENT_CONTRACT_RE.sub(" ", text))
    if _CONTRACT_RE.search(cleaned):
        return "contract"
    if _EMPLOYMENT_RE.search(text) or _EMPLOYMENT_CONTRACT_RE.search(text) or _NEGATED_FREELANCE_RE.search(text):
        return "employment"
    return "unknown"


def _countries_in(text: str) -> list[str]:
    found: list[str] = []
    for m in _COUNTRY_RE.finditer(text):
        country = _ALIAS_TO_COUNTRY[m.group(1).lower()]
        if country not in found:
            found.append(country)
    return found


def _qualifier_scope(qual: str, whole: bool) -> str | None:
    """Уточнення після «remote» → us_only, country:<a,b> або None (не обмежує).
    whole=True для дужок: країни шукаються по всьому вмісту; для хвоста без
    дужок назва країни має стояти першою («Remote - Poland», «remote from India»)."""
    if _OPEN_QUAL_RE.search(qual):
        return None
    head = qual if whole else _QUAL_LEAD_RE.sub("", qual, count=1)
    americas = _AMERICAS_QUAL_RE.search(head)
    if americas and (whole or americas.start() == 0):
        return "us_only"
    if whole:
        countries = _countries_in(head)
    else:
        m = _COUNTRY_RE.match(head)
        countries = [_ALIAS_TO_COUNTRY[m.group(1).lower()]] if m else []
    return "country:" + ",".join(countries) if countries else None


def _remote_restriction(text: str) -> str | None:
    """Перше обмеження з уточнень після «remote»; уточнення з Європою або
    worldwide знімає обмеження для всього тексту."""
    restricted: str | None = None
    for m in _REMOTE_QUAL_RE.finditer(text):
        if m.group("paren") is not None or m.group("brack") is not None:
            qual, whole = m.group("paren") or m.group("brack"), True
        else:
            qual, whole = m.group("tail") or "", False
        if _OPEN_QUAL_RE.search(qual):
            return None
        scope = _qualifier_scope(qual, whole)
        if scope and restricted is None:
            restricted = scope
    return restricted


def _scope_from(text: str) -> str:
    if _ONSITE_RE.search(text) and not _REMOTE_POS_RE.search(text):
        return "onsite"
    if _US_ONLY_RE.search(text):
        return "us_only"
    restricted = _remote_restriction(text)
    if restricted:
        return restricted
    if _HYBRID_RE.search(text):
        return "hybrid"
    if _EUROPE_RE.search(text):
        return "europe"
    if _GLOBAL_RE.search(text):
        return "global"
    if _GLOBAL_WORD_RE.search(text) and _REMOTE_POS_RE.search(text):
        return "global_weak"   # голе «global» поруч із remote: location може перебити (remote_scope)
    return "unknown"


def _location_scope(location: str) -> str:
    """Охоплення лише з location: us_only, country:<…> або unknown."""
    if not location:
        return "unknown"
    if _AMERICAS_QUAL_RE.search(location):
        return "us_only"
    countries = _countries_in(location)
    if countries:
        return "country:" + ",".join(countries)
    if _US_LOCATION_RE.search(location):
        return "us_only"
    return "unknown"


def remote_scope(title: str, description: str, location: str) -> str:
    """Охоплення remote: global, europe, us_only, onsite, hybrid, unknown або
    country:<країна[,країна]> (remote лише з цієї країни).
    location враховується, якщо опис без сигналу або з самим лише голим словом
    «global» поруч із remote; голе «Poland» або
    «Warsaw, Poland» у location без сигналу в описі означає ту саму країну,
    «San Francisco, CA» чи «Seattle, WA» без сигналу означає США."""
    scope = _scope_from(f"{title} {description}")
    if scope == "global_weak":
        # «global fandom» чи «global team» поруч із remote не означає remote звідусіль:
        # конкретна країна в location («United States», «Bangalore, India») важливіша
        by_location = _location_scope(location or "")
        return by_location if by_location != "unknown" else "global"
    if scope == "unknown":
        scope = _scope_from(location or "")
        if scope == "global_weak":
            scope = "global"
    if scope == "unknown" and location:
        by_location = _location_scope(location)
        if by_location != "unknown":
            return by_location
    return scope


def canonical_country(name: str) -> str | None:
    """«Deutschland» → germany; None, якщо це не відома країна."""
    m = _COUNTRY_RE.fullmatch((name or "").strip())
    return _ALIAS_TO_COUNTRY[m.group(1).lower()] if m else None


def timezone_tag(description: str) -> Literal["et_overlap", "pacific_hours", ""]:
    if _ET_RE.search(description):
        return "et_overlap"
    if _PACIFIC_RE.search(description):
        return "pacific_hours"
    return ""


def us_work_authorization_required(description: str) -> bool:
    """Заперечення («no ... required», «worldwide») вирізаються перед пошуком."""
    cleaned = _US_AUTH_NEGATION_RE.sub(" ", description)
    return bool(_US_AUTH_RE.search(cleaned))


def _to_int(raw: str) -> int:
    if re.fullmatch(r"\d{1,3}(?:[.,]\d{3})+", raw):
        return int(re.sub(r"[.,]", "", raw))
    return int(float(raw.replace(",", ".").rstrip(".")))


def parse_rate(text: str) -> Rate | None:
    """Перша ставка за годину, день або місяць з валютою. Річна зарплата
    (/year, /Jahr) дає None. «€10k/month» → 10000."""
    for m in _RATE_RE.finditer(text):
        cur = m.group("c1") or m.group("c2")
        if not cur:
            continue
        low = _to_int(m.group("lo")) * (1000 if m.group("k1") else 1)
        high = (_to_int(m.group("hi")) * (1000 if m.group("k2") else 1)
                if m.group("hi") else low)
        unit = _UNIT[m.group("unit").lower()]
        return Rate(low, high, unit, _CURRENCY[cur.lower()])  # type: ignore[arg-type]
    return None


def posting_closed(text: str) -> bool:
    """Автор поста сам позначив вакансію закритою або заповненою."""
    return bool(_CLOSED_RE.search(text or ""))


def unpaid_role(text: str) -> bool:
    """equity-only, commission-only і подібне: роль без оплати."""
    return bool(_UNPAID_RE.search(text or ""))


def canonical_language(name: str) -> str | None:
    """«Englisch» → english, «Deutsch» → german; None для невідомої мови."""
    low = (name or "").strip().lower()
    for lang, aliases in _LANG_ALIASES.items():
        if low == lang or any(low.startswith(a) for a in aliases):
            return lang
    return None


def languages_required(text: str) -> set[str]:
    """Мови, які вакансія вимагає: назва мови в реченні з вимогою (fluent,
    native, B2, verhandlungssicher, znajomość…), без «a plus», «mile widziane»."""
    found: set[str] = set()
    for sentence in _SENTENCE_SPLIT_RE.split(text or ""):
        if not _LANG_REQ_RE.search(sentence) or _LANG_OPTIONAL_RE.search(sentence):
            continue
        for lang, rx in _LANG_WORD_RE.items():
            if rx.search(sentence):
                found.add(lang)
        if _POLISH_EN_RE.search(sentence):
            found.add("polish")
    return found


def detect_lang(text: str) -> Literal["de", "en"]:
    tokens = re.findall(r"\w+", text.lower())
    if not tokens:
        return "en"
    ratio = sum(t in _DE_STOPWORDS for t in tokens) / len(tokens)
    return "de" if ratio >= _DE_THRESHOLD else "en"
