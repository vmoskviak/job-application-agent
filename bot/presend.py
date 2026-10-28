"""Детерміновані перевірки перед відправкою пакета (спека §6, §15.4).

Кожна причина блокує: лід стає held і видно в /status, оператора ніхто не
питає. Порядок причин сталий, кожна не більше одного разу. Жодного конфігу
клієнта на імпорті.
"""
from __future__ import annotations

import re
from pathlib import Path

from bot.generators import engagement as _eng
from bot.packages import Package
from bot.platforms import platform_of

# Канонічні назви країн, як їх повертає engagement.canonical_country.
EU_EEA_CH: frozenset[str] = frozenset({
    "germany", "austria", "switzerland", "poland", "netherlands", "belgium", "france", "spain",
    "portugal", "italy", "ireland", "sweden", "norway", "denmark", "finland", "czechia", "slovakia",
    "hungary", "romania", "bulgaria", "greece", "croatia", "slovenia", "estonia", "latvia", "lithuania",
    "luxembourg", "malta", "cyprus", "iceland", "liechtenstein",
})
ASCII_UMLAUT_RE = re.compile(
    r"\b(?:ueber|fuer|koennen|moechte|groesse|zurueck|naechste|muessen|waehrend|ausserdem)\w*",
    re.IGNORECASE)
# Адресати поза трекером: тут діють targeting.forbidden_names (поточний роботодавець).
FORBIDDEN_CHANNELS = ("platform_login",)
# remote_scope з engagement.remote_scope: global, europe, us_only, onsite, hybrid, unknown, country:<…>
UNREACHABLE_SCOPES = ("us_only", "onsite", "hybrid")
# board_workplace з bot.ats: офіс чи гібрид поза ЄС/ЄЕЗ/CH пропускається (спека §15.4, рішення 26.10).
OFFICE_WORKPLACES = ("hybrid", "onsite")
_US = "united states"
# Міста поза ЄС, які дошки пишуть без країни («San Francisco», «London», «Toronto»). Кожна
# назва поза ЄС за будь-якого прочитання: London в Онтаріо чи Vancouver у штаті Washington теж.
# Лише для локації, що вся є назвою міста: «London or Berlin» країни не дає.
_NON_EU_CITIES: dict[str, str] = {
    **dict.fromkeys(("san francisco", "seattle", "los angeles", "boston", "chicago", "austin", "denver",
                     "san jose", "san diego", "palo alto", "mountain view", "menlo park", "sunnyvale",
                     "cupertino", "redwood city", "san mateo", "santa clara", "atlanta", "miami", "nyc"), _US),
    **dict.fromkeys(("london", "manchester", "edinburgh"), "united kingdom"),
    **dict.fromkeys(("toronto", "vancouver", "montreal"), "canada"),
    **dict.fromkeys(("bangalore", "bengaluru", "hyderabad"), "india"),
    **dict.fromkeys(("sydney", "melbourne"), "australia"),
    "tel aviv": "israel", "tokyo": "japan", "dubai": "united arab emirates",
}
_NON_EU_CITY_RE = re.compile(
    r"\b(" + "|".join(re.escape(c) for c in sorted(_NON_EU_CITIES, key=len, reverse=True)) + r")\b",
    re.IGNORECASE)


def location_country(location: str) -> str | None:
    """Країна з поля локації: відома назва, інакше американське місто/штат → united states,
    інакше голе відоме місто поза ЄС («London» → united kingdom)."""
    text = location or ""
    found = _eng._countries_in(text)
    if found:
        return found[0]
    if _eng._US_LOCATION_RE.search(text) or _eng._AMERICAS_QUAL_RE.search(text):
        return _US
    m = _NON_EU_CITY_RE.fullmatch(text.strip())
    return _NON_EU_CITIES[m.group(1).lower()] if m else None


def _scope_countries(scope: str) -> list[str]:
    return [c.strip().lower() for c in scope.partition(":")[2].split(",") if c.strip()]


def _employment_unreachable(lead: dict) -> bool:
    if lead.get("engagement") != "employment":
        return False
    scope = str(lead.get("remote_scope") or "")
    if scope.startswith("country:"):
        return not any(c in EU_EEA_CH for c in _scope_countries(scope))
    if scope not in UNREACHABLE_SCOPES:
        return False
    country = location_country(lead.get("location") or "")
    return country is not None and country not in EU_EEA_CH


def _office_outside_eu(lead: dict) -> bool:
    """Офіс чи гібрид за дошкою, і кожна його локація у відомій країні поза ЄС/ЄЕЗ/CH.
    Незалежно від engagement: remote_scope ліда з гостьової сторінки LinkedIn буває
    хибним («global»), а дошка знає краще. Локація в ЄС чи невідома (серед кількох
    через «;» досить однієї) пропуску не дає."""
    if lead.get("board_workplace") not in OFFICE_WORKPLACES:
        return False
    countries = [location_country(p) for p in str(lead.get("board_location") or "").split(";") if p.strip()]
    return bool(countries) and all(c is not None and c not in EU_EEA_CH for c in countries)


# Рядок «пакет готовий, не подано» від --batch-render (ринок найму): 🔄 вручну, 🌐 портал,
# 📧 лист ще не пішов, а також ready/manual, які пише агент.
READY_STATUS_RE = re.compile(r"^\W*(?:🔄|🌐 Portal|📧 Пакет готовий|ready\b|manual\b)", re.IGNORECASE)


def _duplicate_in_tracker(pkg: Package, tracker_path: Path) -> bool:
    """Рядок трекера про ту саму подачу. platform_login: лише рядок, у чиїх нотатках є
    apply_url пакета, бо одна агентура на платформі веде багато схожих проєктів.
    Решта каналів: та сама компанія й схожа роль."""
    from bot.ats import same_role
    from bot.filters.rules import (  # конфіг клієнта на імпорті
        normalize_company,
        parse_tracker_applications,
    )

    rows = parse_tracker_applications(tracker_path)
    if pkg.channel == "platform_login":
        return bool(pkg.apply_url) and any(pkg.apply_url in (row.get("notes") or "") for row in rows)
    company = normalize_company(pkg.lead.get("company") or "")
    title = pkg.lead.get("title") or ""
    for row in rows:
        if READY_STATUS_RE.match(row.get("status") or ""):
            continue  # пакет зрендерено, але не подано: саме його ставимо в чергу (ринок найму)
        if normalize_company(row.get("company") or "") == company and same_role(title, row.get("title") or ""):
            return True
    return False


def check_package(pkg: Package, *, tracker_path: Path, expected_lang: str, forbidden_names: list[str],
                  hourly_min: int | None, is_agency: bool, platforms: dict[str, str] | None = None) -> list[str]:
    """Причини, через які пакет не може піти; порожньо = можна слати. platforms: вимикачі
    з bot.platforms.declared; лід платформи, оголошеної off або не оголошеної, стає
    platform_off:<назва>. None (старі виклики): вимикачі не перевіряються."""
    reasons: list[str] = []
    note_lower = (pkg.note or "").lower()
    if pkg.channel == "none":
        reasons.append("channel_none")
    platform = platform_of(pkg.channel, pkg.apply_url)
    if platforms is not None and platform and platforms.get(platform, "off") == "off":
        reasons.append(f"platform_off:{platform}")
    # мова самого тексту, не meta; порожня нотатка (форма без Anschreiben-поля) мови не має
    if (pkg.note or "").strip() and _eng.detect_lang(pkg.note) != expected_lang:
        reasons.append("language_mismatch")
    if is_agency or pkg.channel in FORBIDDEN_CHANNELS:
        for name in forbidden_names:
            if name and name.lower() in note_lower:
                reasons.append(f"forbidden_name:{name}")
    if pkg.lang == "de" and ASCII_UMLAUT_RE.search(pkg.note or ""):
        reasons.append("ascii_umlauts")
    if hourly_min and str(hourly_min) in (pkg.note or ""):
        reasons.append("rate_min_leaked")
    if not pkg.attachments:   # подача без CV не йде ніколи (рев'ю 2b)
        reasons.append("attachment_none")
    for path in pkg.attachments:
        if not Path(path).exists():
            reasons.append(f"attachment_missing:{Path(path).name}")
    if _duplicate_in_tracker(pkg, Path(tracker_path)):
        reasons.append("duplicate_in_tracker")
    liveness = pkg.lead.get("liveness")
    if liveness and liveness != "live":
        reasons.append("not_live")
    if _employment_unreachable(pkg.lead):
        reasons.append("employment_unreachable")
    if _office_outside_eu(pkg.lead):
        reasons.append("office_outside_eu")
    return reasons


def blocking(reasons: list[str]) -> bool:
    """Поки кожна причина блокує; пізніший етап може перевести частину в попередження."""
    return bool(reasons)
