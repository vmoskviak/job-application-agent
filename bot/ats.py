"""Канал подачі для ліда без логіну в LinkedIn.

Гостьова сторінка LinkedIn каже лише, чи подача «offsite» (на ATS роботодавця)
чи «onsite» (Easy Apply), а саме зовнішнє посилання ховає за входом. Тому ATS
шукається напряму через публічні API дощок (Greenhouse, Lever, Ashby, Workable)
за назвою компанії й заголовком. Не знайшлась дошка, то канал linkedin_offsite:
зовнішнє посилання дістає воркер зі сторінки під логіном (external_apply_url). Знайдений постинг несе формат роботи й локацію з
дошки: за ними bot.presend пропускає офіс чи гібрид поза ЄС. Жодного конфігу
клієнта на імпорті; HTTP за інтерфейсом BoardClient, у тестах підмінний.
"""
from __future__ import annotations

import html as _html
import re
import unicodedata
from collections.abc import Callable
from typing import Literal, NamedTuple, Protocol
from urllib.parse import parse_qs, urlparse

from bot.generators import engagement as _eng  # класифікатори без конфігу клієнта

ATS = ("greenhouse", "lever", "ashby", "workable")

BOARD_URLS: dict[str, str] = {
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=false",
    "lever": "https://api.lever.co/v0/postings/{slug}?mode=json",
    "ashby": "https://api.ashbyhq.com/posting-api/job-board/{slug}",
    "workable": "https://apply.workable.com/api/v1/widget/accounts/{slug}",
}

PLATFORM_LOGIN_HOSTS = ("freelancermap.de", "gulp.de", "freelance.de", "hays.de", "solcom.de",
                        "malt.de", "malt.com", "malt.fr", "upwork.com", "a.team", "arc.dev", "xing.com")

_ATS_HOSTS = (
    ("greenhouse", ("boards.greenhouse.io", "job-boards.greenhouse.io", "boards.eu.greenhouse.io")),
    ("lever", ("jobs.lever.co", "jobs.eu.lever.co")),
    ("ashby", ("jobs.ashbyhq.com",)),
    ("workable", ("apply.workable.com",)),
)
_LEGAL_FORMS = {"gmbh", "ag", "inc", "ltd", "llc", "se", "kg", "ug", "oy", "ab", "bv", "sas"}
_STOP_WORDS = {"the", "a", "an", "de", "la", "le", "die", "der", "das"}
_MIN_SLUG = 3
# Загальні слова ролі: збіг лише по них не робить постинг тією ж роллю (строгий режим дошки).
_GENERIC_WORDS = {"senior", "junior", "lead", "staff", "principal", "engineer", "developer", "software",
                  "entwickler", "engineering", "remote", "contract", "freelance"}
_STRICT_THRESHOLD = 0.67
# Слова платформи: їх збіг між заголовком ліда й постингом обов'язковий, бо
# titles_similar відкидає короткі токени («ios» має 3 літери) і «Senior Android
# Engineer» виглядає схожим на «Senior iOS Engineer».
_PLATFORM_WORDS = {"android", "ios", "flutter", "backend", "frontend", "fullstack", "web",
                   "data", "devops", "qa", "embedded", "security", "ml"}
_MAX_REQUESTS = 12
_WORKPLACES = ("remote", "hybrid", "onsite")
# Код країни Lever (ISO 3166-1 alpha-2) → назва, яку знає bot.presend.location_country. Невідомий
# код не дописується: голий код у кінці локації («Valletta, MT») читався б як штат США.
_ISO_COUNTRIES = {
    "DE": "Germany", "AT": "Austria", "CH": "Switzerland", "PL": "Poland", "NL": "Netherlands",
    "BE": "Belgium", "FR": "France", "ES": "Spain", "PT": "Portugal", "IT": "Italy", "IE": "Ireland",
    "SE": "Sweden", "NO": "Norway", "DK": "Denmark", "FI": "Finland", "CZ": "Czechia", "SK": "Slovakia",
    "HU": "Hungary", "RO": "Romania", "BG": "Bulgaria", "GR": "Greece", "HR": "Croatia", "SI": "Slovenia",
    "EE": "Estonia", "LV": "Latvia", "LT": "Lithuania", "LU": "Luxembourg", "MT": "Malta", "CY": "Cyprus",
    "IS": "Iceland", "LI": "Liechtenstein",
    "US": "United States", "CA": "Canada", "GB": "United Kingdom", "IL": "Israel", "IN": "India",
    "SG": "Singapore", "JP": "Japan", "AU": "Australia", "NZ": "New Zealand", "BR": "Brazil",
    "MX": "Mexico", "AR": "Argentina", "CO": "Colombia", "ZA": "South Africa", "UA": "Ukraine",
    "TR": "Turkey", "RS": "Serbia", "PH": "Philippines", "VN": "Vietnam", "ID": "Indonesia",
    "PK": "Pakistan", "NG": "Nigeria", "EG": "Egypt",
}
# Рівень ролі: для same_role це загальні слова, але вибір постингу на дошці їх розрізняє
# («Senior Android Engineer» не «Android Engineer» і не «Staff …»); sr і jr зводяться до повних.
_SENIORITY = {"senior": "senior", "sr": "senior", "junior": "junior", "jr": "junior", "lead": "lead",
              "staff": "staff", "principal": "principal"}
# Шум у заголовку ліда з LinkedIn, якого на дошці нема: гендерні позначки, формат роботи,
# зайнятість, регіон. Знімаються лише ці слова, не довільний хвіст заголовка; «hybrid» лише
# окремим сегментом («- Hybrid», «(Hybrid)», «, Hybrid»), бо в «Hybrid Cloud Engineer» це роль.
_LEAD_NOISE_RE = re.compile(
    r"\(\s*(?:all\s+genders?|alle\s+geschlechter|gn\*?|[mwfdx]\s*/\s*[mwfdx](?:\s*/\s*(?:[mwfdx]|div\w*))?)\s*\)"
    r"|\ball\s+genders?\b|(?:(?<=[-–—|,(/])|(?<=[-–—|,(/]\s))hybrid\b|\bhybrid\s*(?=$|[)\],/|])"
    r"|\bon-?site\b|\bfull[- ]?time\b|\bpart[- ]?time\b|\b100\s*%|\b(?:europe|emea|dach)\b", re.IGNORECASE)
# Слова заголовка без уточнення: на щаблі ядра вони не роблять постинг чужим.
_TITLE_FILLER = {"engineer", "developer", "software", "engineering", "entwickler"}


def ats_from_url(url: str) -> str | None:
    """ATS за хостом посилання на вакансію; None, якщо хост невідомий."""
    host = urlparse(url or "").netloc.lower()
    for ats, hosts in _ATS_HOSTS:
        if host in hosts:
            return ats
    return None


def linkedin_apply_kind(html: str) -> Literal["offsite", "onsite", "unknown"]:
    """Вид подачі з гостьової сторінки LinkedIn за маркерами кнопки Apply."""
    if "public_jobs_apply-link-offsite" in (html or ""):
        return "offsite"
    if "public_jobs_apply-link-onsite" in (html or ""):
        return "onsite"
    return "unknown"


SAFETY_GO_RE = re.compile(r'href="(https://www\.linkedin\.com/safety/go/\?[^"]+)"')
# Контрол Apply сторінки вакансії під логіном: <button> для Easy Apply, <a> на сайт компанії.
APPLY_CONTROL_RE = re.compile(r'<(a|button)\b[^>]*\bdata-view-name="job-apply-button"[^>]*>', re.IGNORECASE)
_HREF_RE = re.compile(r'\bhref="([^"]*)"')


def decode_safety_url(href: str) -> str | None:
    """Параметр url посилання safety/go, розкодований; None, якщо параметра нема.
    Між параметрами буває і сирий &, і &amp; з HTML."""
    values = parse_qs(urlparse(_html.unescape(href or "")).query).get("url")
    return values[0] if values and values[0] else None


def external_apply_url(html: str) -> str | None:
    """Зовнішнє посилання Apply зі сторінки LinkedIn під логіном.

    Береться лише контрол `data-view-name="job-apply-button"`: якщо це <a>, його href
    розкодовується, якщо <button> (Easy Apply), зовнішнього Apply нема. Опис вакансії
    теж має посилання safety/go, тож перше таке на сторінці береться лише тоді, коли
    контролу Apply на сторінці немає взагалі (стара розмітка)."""
    control = APPLY_CONTROL_RE.search(html or "")
    if control:
        href = _HREF_RE.search(control.group(0)) if control.group(1).lower() == "a" else None
        return decode_safety_url(href.group(1)) if href else None
    m = SAFETY_GO_RE.search(html or "")
    return decode_safety_url(m.group(1)) if m else None


def _ascii(text: str) -> str:
    """Акценти геть (ü→u, ø→o): дошки називаються латиницею."""
    text = (text or "").replace("ø", "o").replace("Ø", "O").replace("ß", "ss")
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()


def company_slugs(company: str) -> list[str]:
    """Кандидати slug дошки: слова разом, через дефіс, перше слово; без юрформ,
    стоп-слів і слагів коротших за 3 символи (інакше «m» чи «the» ловлять чужу дошку)."""
    words = [w for w in re.findall(r"[a-z0-9]+", _ascii(company).lower()) if w not in _LEGAL_FORMS]
    if words and words[0] in _STOP_WORDS:
        words = words[1:]
    if not words:
        return []
    out = ["".join(words), "-".join(words), words[0]]
    return [s for s in dict.fromkeys(out) if len(s) >= _MIN_SLUG]


class BoardClient(Protocol):
    def get_json(self, url: str) -> object | None: ...


class HttpxBoardClient:
    """GET JSON дошки; будь-яка помилка HTTP або не-JSON тіло дає None."""

    def __init__(self, timeout: float = 10.0) -> None:
        self.timeout = timeout

    def get_json(self, url: str) -> object | None:
        import httpx

        try:
            r = httpx.get(url, timeout=self.timeout, follow_redirects=True,
                          headers={"Accept": "application/json"})
            if r.status_code != 200:
                return None
            return r.json()
        except Exception:  # noqa: BLE001 — мережа й розбір: дошки просто нема
            return None


class AtsPosting(NamedTuple):
    ats: str
    apply_url: str
    title: str
    board_slug: str
    workplace: str | None = None   # remote | hybrid | onsite за дошкою, None якщо дошка не каже
    location: str | None = None    # кілька локацій через «; »


class BoardPosting(NamedTuple):
    """Постинг з відповіді дошки разом із форматом роботи й локацією, якщо дошка їх дає."""
    title: str
    url: str
    workplace: str | None = None
    location: str | None = None


def _get(d: object, *keys: str) -> object:
    """Вкладене поле словника; будь-яка інша форма дає None."""
    for k in keys:
        if not isinstance(d, dict):
            return None
        d = d.get(k)
    return d


def _workplace(value: object) -> str | None:
    """remote | hybrid | onsite з поля дошки («OnSite», «Hybrid», «onsite»); unspecified і null дають None."""
    w = re.sub(r"[^a-z]", "", str(value or "").lower())
    return w if w in _WORKPLACES else None


def _places(places: list) -> str | None:
    """Непорожні локації через «; »; жодної, то None."""
    out = [str(p).strip() for p in places if p and str(p).strip()]
    return "; ".join(out) or None


def _with_country(name: str, country: str) -> str:
    """Назва локації плюс країна, якщо назва її не містить: «London» → «London, United Kingdom»."""
    name, country = name.strip(), country.strip()
    if not country or country.lower() in name.lower():
        return name
    return f"{name}, {country}" if name else country


def _ashby_place(entry: object) -> str:
    """Локація Ashby з країною з address.postalAddress."""
    return _with_country(str(_get(entry, "location") or ""),
                         str(_get(entry, "address", "postalAddress", "addressCountry") or ""))


def _posting(ats: str, j: dict) -> BoardPosting:
    """Один постинг дошки. Ashby isRemote не береться: у гібридних постингів він теж true.
    Greenhouse формату не дає, лише «remote» у назві локації; віджет Workable знає лише
    telecommuting, тож гібрид чи офіс там None."""
    if ats == "greenhouse":
        name = str(_get(j, "location", "name") or "").strip()
        remote = "remote" if re.search(r"\bremote\b", name, re.IGNORECASE) else None
        return BoardPosting(j["title"], j["absolute_url"], remote, name or None)
    if ats == "lever":
        places = _get(j, "categories", "allLocations")
        if not isinstance(places, list) or not places:
            places = [_get(j, "categories", "location")]
        # country Lever стосується основної локації, тож дописується лише до єдиної:
        # «Milano, MI» без країни читалось би як штат Мічиган.
        country = _ISO_COUNTRIES.get(str(j.get("country") or "").strip().upper())
        if country and len(places) == 1:
            places = [_with_country(str(places[0] or ""), country)]
        return BoardPosting(j["text"], j["hostedUrl"], _workplace(j.get("workplaceType")), _places(places))
    if ats == "ashby":
        places = [_ashby_place(j)] + [_ashby_place(e) for e in j.get("secondaryLocations") or []]
        return BoardPosting(j["title"], j["jobUrl"], _workplace(j.get("workplaceType")), _places(places))
    remote = "remote" if j.get("telecommuting") is True else None
    return BoardPosting(j["title"], j["url"], remote, _places([", ".join(
        str(v).strip() for v in (_get(j, "city"), _get(j, "country")) if v and str(v).strip())]))


def _postings(ats: str, data: object) -> list[BoardPosting]:
    """Постинги з відповіді дошки; порожньо, якщо форма відповіді чужа."""
    if ats not in ATS:
        return []
    try:
        return [_posting(ats, j) for j in (data if ats == "lever" else data["jobs"])]
    except (KeyError, TypeError, AttributeError):
        return []


def _board_company(ats: str, data: object) -> str | None:
    """Назва компанії, якщо дошка її віддає (Workable: name; Greenhouse: company_name у jobs)."""
    try:
        if ats == "workable" and data.get("name"):
            return str(data["name"])
        if ats == "greenhouse":
            names = {str(j.get("company_name")) for j in data["jobs"] if j.get("company_name")}
            if len(names) == 1:
                return names.pop()
    except (AttributeError, KeyError, TypeError):
        return None
    return None


def _same_company(a: str, b: str) -> bool:
    from bot.filters.rules import normalize_company  # читає конфіг клієнта на імпорті

    na = " ".join(w for w in normalize_company(_ascii(a)).split() if w not in _LEGAL_FORMS)
    nb = " ".join(w for w in normalize_company(_ascii(b)).split() if w not in _LEGAL_FORMS)
    return bool(na) and na == nb


def _platform_words(title: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]+", (title or "").lower()) if w in _PLATFORM_WORDS}


def _specific_words(title: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]{3,}", (title or "").lower()) if w not in _GENERIC_WORDS}


def same_role(lead_title: str, posting_title: str, strict: bool = False) -> bool:
    """Та сама роль: titles_similar плюс збіг слів платформи (android/ios/data…),
    бо titles_similar рахує «Data Engineer» схожим на «Senior Android Engineer».
    strict (пошук дошки, де помилка чіпляє чужу вакансію): поріг 0.67 і хоча б одне
    спільне неслужбове слово, щоб «Senior Engineer» не збігався з «Senior Account Executive»."""
    from bot.filters.rules import titles_similar  # читає конфіг клієнта на імпорті

    if not titles_similar(lead_title, posting_title, _STRICT_THRESHOLD if strict else 0.5):
        return False
    lead_pw = _platform_words(lead_title)
    if lead_pw and lead_pw != _platform_words(posting_title):
        return False
    return not strict or bool(_specific_words(lead_title) & _specific_words(posting_title))


def _pick_words(title: str) -> set[str]:
    """Специфічні слова заголовка плюс рівень (sr → senior): за ними _board_pick звіряє постинги."""
    words = re.findall(r"[a-z]+", (title or "").lower())
    return _specific_words(title) | {_SENIORITY[w] for w in words if w in _SENIORITY}


def _all_words(title: str) -> set[str]:
    """Усі слова заголовка, і двобуквені («US», «SF»), і remote чи contract; рівень зведений,
    як у _pick_words; без _TITLE_FILLER."""
    words = (_SENIORITY.get(w, w) for w in re.findall(r"[a-z0-9]+", (title or "").lower()))
    return {w for w in words if w not in _TITLE_FILLER}


def _board_pick(title: str, postings: list[BoardPosting], location: str = "") -> BoardPosting | None:
    """Постинг ліда на одній дошці або None, якщо вибір неоднозначний.

    Кандидати: same_role(strict); слова заголовка: _pick_words. Три щаблі по черзі:
    1) точний збіг слів повного заголовка ліда;
    2) точний збіг ядра: заголовок ліда без шуму LinkedIn («(all genders)», «- Hybrid»),
       назв країн і слів локації ліда («Android Engineer, Berlin» з локацією Berlin). До того
       ж у постингу нема жодного слова, якого нема в заголовку ліда (_all_words): уточнення
       дошки на кшталт «, US», «(Contract)» чи «, SF» для _pick_words невидиме, і без цієї
       умови ядро вибирало б постинг для іншої країни чи формату;
    3) рівно один постинг, що містить усі слова повного заголовка («Imaging» відсіює
       «…, Library Team», хоч titles_similar дає 0.75).
    Кілька точних на щаблі (той самий заголовок у кількох локаціях) або кілька кандидатів
    на третьому дають None: пропущена подача краща за подачу не на ту вакансію.
    """
    full = _pick_words(title)
    core = _pick_words(_eng._COUNTRY_RE.sub(" ", _LEAD_NOISE_RE.sub(" ", title))) - _specific_words(location)
    found = [p for p in postings if same_role(title, p.title, strict=True)]
    lead_words = _all_words(title)
    for exact in ([p for p in found if _pick_words(p.title) == full],
                  [p for p in found if _pick_words(p.title) == core and _all_words(p.title) <= lead_words]):
        if exact:
            return exact[0] if len(exact) == 1 else None
    containing = [p for p in found if full <= _pick_words(p.title)]
    return containing[0] if len(containing) == 1 else None


def find_posting(company: str, title: str, client: BoardClient,
                 ats_order: tuple[str, ...] = ATS, location: str = "") -> AtsPosting | None:
    """Постинг ліда з першої дошки компанії, де вибір однозначний (_board_pick); location
    це локація ліда, її слова в заголовку ліда не заважають точному збігу. Не більше 12 запитів."""
    requests = 0
    for slug in company_slugs(company):
        for ats in ats_order:
            if requests >= _MAX_REQUESTS:
                return None
            requests += 1
            data = client.get_json(BOARD_URLS[ats].format(slug=slug))
            if data is None:
                continue
            board_company = _board_company(ats, data)
            if board_company and not _same_company(board_company, company):
                continue   # дошка каже, що вона чужа
            hit = _board_pick(title, _postings(ats, data), location)
            if hit:
                return AtsPosting(ats, hit.url, hit.title, slug, hit.workplace, hit.location)
    return None


class Resolution(NamedTuple):
    channel: Literal["email", "form", "linkedin_easy", "linkedin_offsite", "platform_login", "none"]
    apply_url: str
    ats: str | None
    note: str
    board_workplace: str | None = None   # лише коли постинг знайдено пошуком дошки
    board_location: str | None = None


def _host(url: str) -> str:
    return urlparse(url or "").netloc.lower()


def resolve_channel(entry: dict, fetch_html: Callable[[str], str | None],
                    board: BoardClient, market: str = "contract") -> Resolution:
    """Канал подачі ліда: email → відомий ATS → платформа з логіном → LinkedIn
    (Easy Apply, пошук дошки, інакше linkedin_offsite) → пошук дошки для решти хостів.

    Ринок найму (de_employment): невідомий хост роботодавця це форма для загального
    драйвера (form без ATS), а не none; дошки Ashby/Greenhouse у німецьких фірм
    рідкість, тож пошук дошки там не робиться. application_url з оголошення
    переважає source_url."""
    url = entry.get("source_url") or ""
    if market == "de_employment":
        apply_url = entry.get("application_url") or url
        if entry.get("email"):
            return Resolution("email", apply_url, None, "email address in posting")
        ats = ats_from_url(apply_url)
        if ats:
            return Resolution("form", apply_url, ats, f"{ats} url")
        host = _host(apply_url)
        if "linkedin.com" in host:
            kind = linkedin_apply_kind(fetch_html(apply_url) or "")
            if kind == "onsite":
                return Resolution("linkedin_easy", apply_url, None, "linkedin easy apply")
            return Resolution("linkedin_offsite", apply_url, None, f"linkedin {kind}, employer site unknown")
        if any(host == h or host.endswith("." + h) for h in PLATFORM_LOGIN_HOSTS):
            return Resolution("platform_login", apply_url, None, f"platform {host}")
        return Resolution("form", apply_url, None, f"employer site {host}, generic form")
    company, title = entry.get("company") or "", entry.get("title") or ""
    location = entry.get("location") or ""
    if entry.get("email"):
        return Resolution("email", url, None, "email address in posting")
    ats = ats_from_url(url)
    if ats:
        return Resolution("form", url, ats, f"{ats} url")
    host = _host(url)
    if any(host == h or host.endswith("." + h) for h in PLATFORM_LOGIN_HOSTS):
        return Resolution("platform_login", url, None, f"platform {host}")
    if "linkedin.com" in host:
        kind = linkedin_apply_kind(fetch_html(url) or "")
        if kind == "onsite":
            return Resolution("linkedin_easy", url, None, "linkedin easy apply")
        posting = find_posting(company, title, board, location=location)
        if posting:
            return Resolution("form", posting.apply_url, posting.ats, f"linkedin {kind}, board {posting.board_slug}",
                              posting.workplace, posting.location)
        return Resolution("linkedin_offsite", url, None, f"linkedin {kind}, board not found")
    posting = find_posting(company, title, board, location=location)
    if posting:
        return Resolution("form", posting.apply_url, posting.ats, f"board {posting.board_slug}",
                          posting.workplace, posting.location)
    return Resolution("none", url, None, "no known channel")
