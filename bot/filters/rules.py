"""Codified auto-apply filter rules.

Кодифікує політику відбору вакансій для автономної подачі. Самі значення
(виключені назви ролей і треки, досяжні локації, пріоритети треків,
email-мертві компанії) задає клієнт у `targeting.yaml` і `tracks.yaml`.

CLI:
    python -m bot.filters.rules digest.json [-o filtered.json]
        [--tracker <clients>/<slug>/tracker.md] [--min-score 25]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from bot import client as _client
from bot.textclean import strip_invisible

_cfg = _client.active().targeting
_tracks: dict[str, dict] = _client.active().tracks.get("tracks", {})

# Аварійний запобіжник для одного запуску авто-подачі: більше — це збій
# фільтра, а не нормальний день. Runner аборт + алерт (exit 3).
MAX_SUBMISSIONS_PER_RUN = int(_cfg.get("max_submissions_per_run", 25))

MIN_SCORE = int(_cfg.get("min_score", 25))

# Патерни з жорсткими межами слів: назви ролей, на які не подаємось ніколи
# (targeting.yaml → excluded_title_patterns, регулярні вирази).
EXCLUDED_TITLE_PATTERNS: list[str] = list(_cfg.get("excluded_title_patterns", []))

# Порожній список не має матчити все підряд: (?!) не збігається ніколи.
_EXCLUDED_TITLE_RE = re.compile(
    "|".join(EXCLUDED_TITLE_PATTERNS) or r"(?!)", re.IGNORECASE
)

# Ознаки в тексті вакансії, яких у назві ролі зазвичай нема (Teilzeit,
# Homeoffice-Anteil тощо). targeting.yaml →
#   description_bonus: [{pattern, points}]        бал додається до score
#   excluded_description_patterns: [{pattern, unless}]
#       збіг pattern без збігу unless = не подаємось (reason excluded_description)
DESCRIPTION_BONUS: list[dict] = list(_cfg.get("description_bonus", []))
EXCLUDED_DESCRIPTION_PATTERNS: list[dict] = list(_cfg.get("excluded_description_patterns", []))

# Треки, на які не подаємось узагалі (tracks.yaml → excluded: true).
EXCLUDED_CATEGORIES = {name for name, t in _tracks.items() if t.get("excluded")}

# Компанії, що приймають заявки ЛИШЕ через портал: вкладення з пошти видаляють
# одразу з посиланням на DSGVO. Порівнюється токен-в-токен по
# normalize_company, щоб коротка назва не ловила підрядки.
EMAIL_DEAD_COMPANIES = set(_cfg.get("email_dead_companies", []))

# Досяжні міста/регіони клієнта (lowercase) + маркери remote нижче.
# Портали пишуть ту саму локацію по-різному (умлаути, англійська назва
# землі на LinkedIn), тому в списку мають бути всі написання.
LOCATIONS: list[str] = [str(loc).lower() for loc in _cfg.get("locations", [])]
NRW_LOCATIONS = LOCATIONS  # історична назва, лишена для сумісності

# "Home-Office" через дефіс — саме так пишуть StepStone і XING у власних
# фільтрах, а \s* дефіс не ловить, тож remote-вакансії поза NRW відкидались.
_REMOTE_RE = re.compile(
    r"remote|home[\s-]*office|hybrid|bundesweit|deutschlandweit",
    re.IGNORECASE,
)

# targeting.yaml → remote_policy:
#   any  (дефолт) будь-яка згадка remote/Homeoffice/hybrid рятує чуже місто;
#   full лише повністю віддалена робота: клієнт шукає в одному місті через
#        дорогу, а «2 Tage Homeoffice» у Кельні все одно означає їздити в Кельн.
REMOTE_POLICY: str = str(_cfg.get("remote_policy", "any"))
_REMOTE_POLICIES = ("any", "full")

# Поле location пише remote коротко («Remote», «Home-Office», LinkedIn:
# «Köln, …, Deutschland (Remote)»). Та StepStone/XING туди ж пишуть
# «Homeoffice möglich» чи «2 Tage Homeoffice», а це не повна віддаленість.
_REMOTE_MARKER_RE = re.compile(r"remote|home[\s-]*office", re.IGNORECASE)
_PARTIAL_REMOTE_RE = re.compile(
    r"möglich|moeglich|teilweise|anteil|option|hybrid|\d\s*tag", re.IGNORECASE
)
# В описі «Homeoffice» трапляється майже в кожній вакансії як пільга, тому
# потрібне явне формулювання повної віддаленості.
_FULL_REMOTE_DESC_RE = re.compile(
    r"100\s*%\s*(?:remote|home[\s-]*office|mobil)"
    r"|\b(?:full|fully|komplett|vollständig|vollstaendig|ausschließlich|ausschliesslich|rein|reine)"
    r"[\s-]*(?:remote|im\s+home[\s-]*office|home[\s-]*office)"
    r"|remote[\s-]*(?:first|stelle|position|job|only)"
    r"|(?:arbeitsort|standort|einsatzort|dienstsitz)\s*:?\s*(?:remote|home[\s-]*office)\b"
    r"|(?:remote|home[\s-]*office)\s*(?:,\s*)?(?:deutschlandweit|bundesweit|innerhalb\s+deutschlands|aus\s+ganz\s+deutschland)",
    re.IGNORECASE,
)


def _full_remote_location(loc: str) -> bool:
    """Поле location означає повністю віддалену роботу."""
    if _FULL_REMOTE_DESC_RE.search(loc):
        return True
    return bool(_REMOTE_MARKER_RE.search(loc)) and not _PARTIAL_REMOTE_RE.search(loc)


# location = країна без міста (типово LinkedIn intake за login wall)
_GENERIC_COUNTRY_LOCATIONS = {"deutschland", "germany", "bundesrepublik deutschland"}

# По опису шукаємо лише цілі слова: "Interessen" містить "essen",
# тому substring-матч (як по полю location) тут дає хибні хіти.
_NRW_DESC_RE = re.compile(
    r"\b(?:" + ("|".join(re.escape(c) for c in LOCATIONS) or r"(?!)") + r")\b",
    re.IGNORECASE,
)

# Менший ранг = вища пріоритетність (tracks.yaml → priority).
PRIORITY_ORDER = {
    name: int(t["priority"]) for name, t in _tracks.items() if "priority" in t
}
_DEFAULT_PRIORITY = int(_client.active().tracks.get("default_priority", 7))

_COMPANY_NOISE_RE = re.compile(
    r"\b(gmbh|ag|se|kg|ohg|mbh|co|e\.?\s?v\.?|&|und)\b\.?", re.IGNORECASE
)


# Назви, які дає зламаний скрап замість вакансії: shell порталу за
# login-wall («XING Job»), заголовок сторінки пошуку Indeed («Suchen»/«Search»),
# порожнеча. Такий запис не вакансія, скор у нього по сміттю.
_JUNK_TITLES = {
    "xing job", "linkedin job", "suchen", "search", "jobs", "job", "jobsuche",
    "stellenangebote", "stellenangebot", "indeed", "stepstone", "xing", "linkedin",
    "karriere", "careers", "career", "stellen", "unbekannt", "unknown", "none", "",
}


def scrape_failed_title(title: str) -> bool:
    t = (title or "").strip().strip("#*·-–— ").lower()
    return t in _JUNK_TITLES or len(t) < 4


def title_excluded(title: str) -> bool:
    """True якщо назва ролі під забороною (Trainee/PO/Senior/Vertrieb/Dev...)."""
    return bool(_EXCLUDED_TITLE_RE.search(strip_invisible(title)))


def location_ok(location: str, description: str = "") -> bool | None:
    """Досяжна локація або remote → True; інше місто → False; невідомо → None.
    Для location-країни без міста ("Deutschland") і для порожнього location
    реальне місто часто лише в описі — рятуємо через _NRW_DESC_RE. Конкретне
    не-NRW місто в location згадка NRW-міста в описі НЕ перебиває."""
    if REMOTE_POLICY not in _REMOTE_POLICIES:
        raise ValueError(
            f"targeting.yaml: remote_policy '{REMOTE_POLICY}' не з {_REMOTE_POLICIES}")
    if REMOTE_POLICY == "full":
        loc_remote, desc_remote = _full_remote_location, _FULL_REMOTE_DESC_RE.search
    else:
        loc_remote = desc_remote = _REMOTE_RE.search
    loc = (location or "").strip().lower()
    desc = description or ""
    if not loc or loc in ("unbekannt", "unknown", "-", "—"):
        if desc_remote(desc) or _NRW_DESC_RE.search(desc):
            return True
        return None
    # Межі слів обов'язкові й тут: «Borken, Hessen» ловився підрядком «essen»
    if _NRW_DESC_RE.search(loc):
        return True
    if loc_remote(loc) or desc_remote(desc):
        return True
    if loc in _GENERIC_COUNTRY_LOCATIONS and _NRW_DESC_RE.search(desc):
        return True
    return False


def _job_text(entry: dict) -> str:
    return strip_invisible(f"{entry.get('title', '')}\n{entry.get('description', '')}")


def description_bonus(entry: dict, rules: list[dict] | None = None) -> int:
    """Сума балів за ознаки в назві+описі (targeting.yaml → description_bonus).
    Кожне правило рахується один раз, скільки б разів не збіглося."""
    total = 0
    for rule in (DESCRIPTION_BONUS if rules is None else rules):
        if re.search(str(rule["pattern"]), _job_text(entry), re.IGNORECASE):
            total += int(rule.get("points", 0))
    return total


def description_excluded(entry: dict, rules: list[dict] | None = None) -> bool:
    """Чи є в тексті ознака, за якою не подаємось, без рятівного винятку.
    Напр. pattern=\\bvollzeit\\b, unless=teilzeit: Vollzeit-вакансія без згадки
    Teilzeit відпадає, «Vollzeit oder Teilzeit» лишається."""
    text = _job_text(entry)
    for rule in (EXCLUDED_DESCRIPTION_PATTERNS if rules is None else rules):
        if not re.search(str(rule["pattern"]), text, re.IGNORECASE):
            continue
        unless = rule.get("unless")
        if unless and re.search(str(unless), text, re.IGNORECASE):
            continue
        return True
    return False


def email_channel_blocked(company: str) -> bool:
    """True якщо компанія не приймає заявки поштою (лише портал, DSGVO)."""
    tokens = set(normalize_company(company).split())
    return bool(tokens & EMAIL_DEAD_COMPANIES)


def classify_channel(entry: dict) -> str:
    """Канал подачі: email | stepstone_auto | xing_auto | manual_link."""
    url = (entry.get("source_url") or "").lower()
    if entry.get("email") and not email_channel_blocked(entry.get("company", "")):
        return "email"
    if "stepstone.de" in url:
        return "stepstone_auto"
    if "xing.com" in url:
        return "xing_auto"
    # öД: модуль bot/apply/interamt.py.
    # Без живої сесії runner чесно поверне login_required → manual-список.
    if "interamt.de" in url:
        return "interamt_auto"
    return "manual_link"


_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]*\)")


def normalize_company(name: str) -> str:
    """Нормалізує назву компанії для dedup-порівняння."""
    # Markdown-лінк ([Firma](https://…/cmp/…)) з firecrawl-інтейку: без чистки
    # URL в'їжджає в ключ і dedup проти трекера сліпне (регресія 28.07).
    s = _MD_LINK_RE.sub(r"\1", name or "")
    s = _COMPANY_NOISE_RE.sub(" ", s.lower())
    s = re.sub(r"[^\wäöüß]+", " ", s)
    return " ".join(s.split())


def _title_tokens(title: str) -> set[str]:
    t = re.sub(r"\((?:m/w/d|w/m/d|d/w/m|m/w/x)\)", " ", title, flags=re.IGNORECASE)
    return {w for w in re.findall(r"[\wäöüß]{4,}", t.lower())}


def titles_similar(a: str, b: str, threshold: float = 0.5) -> bool:
    ta, tb = _title_tokens(a), _title_tokens(b)
    if not ta or not tb:
        return False
    overlap = len(ta & tb) / min(len(ta), len(tb))
    return overlap >= threshold


def parse_tracker_applications(tracker_path: Path) -> list[dict]:
    """Витягує (company, title) з обох форматів таблиць трекера."""
    apps: list[dict] = []
    if not tracker_path.exists():
        return apps
    date_re = re.compile(r"^\d{4}-\d{2}-\d{2}")
    for line in tracker_path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.split("|")]
        # cells[0] == "" (перед першим |); значущі з cells[1]
        if len(cells) < 5:
            continue
        if date_re.match(cells[1]):
            # legacy-формат: | date | title | company, location | status |
            company = cells[3].split(",")[0].strip()
            apps.append({"company": company, "title": cells[2]})
        elif len(cells) >= 6 and (date_re.match(cells[2]) or cells[2] in ("—", "")):
            # основна таблиця: | # | Date | Company | Position | ...
            apps.append({"company": cells[3], "title": cells[4]})
    return apps


def _company_keys_match(a: str, b: str) -> bool:
    """Портал і трекер часто пишуть ту саму фірму з різною повнотою
    («Beispiel Sprachen» ↔ «Beispiel Sprachen Institut»): підмножина токенів =
    та сама компанія. Хибний матч страхує вимога схожого title у dedup."""
    ta, tb = set(a.split()), set(b.split())
    if not ta or not tb:
        return False
    return ta <= tb or tb <= ta


_URL_RE = re.compile(r"https?://[^\s|)>\]]+")


def normalize_url(url: str) -> str:
    """Ключ для порівняння URL вакансії: без схеми, www, фрагмента й
    хвостового слеша. Query лишається лише там, де в ньому ідентифікатор
    (Indeed `viewjob?jk=…`), бо трекінгові параметри LinkedIn/StepStone
    міняються від листа до листа."""
    u = (url or "").strip().rstrip(".,;")
    u = re.sub(r"#.*$", "", u)
    u = re.sub(r"^https?://(www\.)?", "", u, flags=re.I)
    path, _, query = u.partition("?")
    path = path.rstrip("/").lower()
    if query and re.search(r"(^|&)jk=", query):
        jk = re.search(r"(?:^|&)jk=([^&]+)", query).group(1)
        return f"{path}?jk={jk}"
    return path


def tracker_urls(tracker_path: Path) -> set[str]:
    """Усі URL, що згадані в трекері (у будь-якій клітинці)."""
    if not tracker_path.exists():
        return set()
    text = tracker_path.read_text(encoding="utf-8")
    return {normalize_url(u) for u in _URL_RE.findall(text)}


def dedup_against_tracker(entries: list[dict], tracker_path: Path) -> tuple[list[dict], list[dict]]:
    """Розділяє entries на (нові, дублікати-проти-трекера)."""
    tracked = parse_tracker_applications(tracker_path)
    by_company: dict[str, list[str]] = {}
    for app in tracked:
        by_company.setdefault(normalize_company(app["company"]), []).append(app["title"])
    # Та сама вакансія з company="Unbekannt" (кадрова агенція, сайт канцелярії
    # без назви фірми в тексті) проходила повз порівняння назв і відбиралась
    # удруге (15.10.2026: три вакансії поспіль). URL у трекері надійніший за
    # назву.
    known_urls = tracker_urls(tracker_path)

    fresh, dupes = [], []
    for entry in entries:
        src = normalize_url(entry.get("source_url") or entry.get("url") or "")
        if src and src in known_urls:
            dupes.append(entry)
            continue
        company_key = normalize_company(entry.get("company", ""))
        prior_titles = [
            t for key, titles in by_company.items()
            if _company_keys_match(company_key, key) for t in titles
        ]
        if company_key and any(
            titles_similar(entry.get("title", ""), t) for t in prior_titles
        ):
            dupes.append(entry)
        else:
            fresh.append(entry)
    return fresh, dupes


def _sort_key(entry: dict) -> tuple:
    rank = PRIORITY_ORDER.get(entry.get("category", ""), _DEFAULT_PRIORITY)
    linkedin_bonus = 0 if "linkedin.com" in (entry.get("source_url") or "") else 1
    return (rank, linkedin_bonus, -int(entry.get("score", 0)))


def apply_rules(
    digest: list[dict],
    tracker_path: Path,
    min_score: int = MIN_SCORE,
) -> dict:
    """Головна точка: digest → {selected, excluded}. Selected відсортовані за
    пріоритетом і збагачені полями apply_channel / priority_rank."""
    selected: list[dict] = []
    excluded: list[dict] = []

    def exclude(entry: dict, reason: str) -> None:
        excluded.append({"reason": reason, **entry})

    fresh, dupes = dedup_against_tracker(digest, tracker_path)
    for entry in dupes:
        exclude(entry, "duplicate_in_tracker")

    for entry in fresh:
        title = entry.get("title", "")
        if scrape_failed_title(title):
            # маркер зламаного скрапа (login-wall/shell/заголовок сторінки
            # пошуку), не вакансія — скор у такого запису рахований по сміттю
            exclude(entry, "scrape_failed_title")
            continue
        if title_excluded(title):
            exclude(entry, "excluded_title")
            continue
        if entry.get("category") in EXCLUDED_CATEGORIES:
            exclude(entry, "excluded_category")
            continue
        if description_excluded(entry):
            exclude(entry, "excluded_description")
            continue
        bonus = description_bonus(entry)
        if bonus:
            entry = {**entry, "score": int(entry.get("score", 0)) + bonus,
                     "description_bonus": bonus}
        if int(entry.get("score", 0)) < min_score:
            exclude(entry, f"score_below_{min_score}")
            continue
        loc_ok = location_ok(entry.get("location", ""), entry.get("description", ""))
        if loc_ok is False:
            exclude(entry, "outside_nrw")
            continue

        enriched = dict(entry)
        enriched["apply_channel"] = classify_channel(entry)
        enriched["location_verified"] = bool(loc_ok)
        selected.append(enriched)

    selected.sort(key=_sort_key)

    # Та сама вакансія з двох джерел або з префіксом «Jobangebot:» (XING,
    # 08.10.2026: Kühler & Söhne двічі в одному батчі). Лишаємо перший за
    # пріоритетом/скором, решту в excluded як duplicate_in_run.
    unique: list[dict] = []
    for entry in selected:
        if any(_company_keys_match(normalize_company(entry.get("company", "")),
                                   normalize_company(kept.get("company", "")))
               and titles_similar(entry.get("title", ""), kept.get("title", ""))
               for kept in unique):
            exclude(entry, "duplicate_in_run")
            continue
        unique.append(entry)
    selected = unique

    for i, entry in enumerate(selected):
        entry["priority_rank"] = i + 1

    return {"selected": selected, "excluded": excluded}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Фільтр дайджесту для авто-подачі")
    parser.add_argument("digest", type=Path, help="discover_digest_*.json")
    parser.add_argument("-o", "--output", type=Path, help="куди писати filtered JSON")
    parser.add_argument(
        "--tracker",
        type=Path,
        default=_client.active().tracker,
    )
    parser.add_argument("--min-score", type=int, default=MIN_SCORE)
    args = parser.parse_args(argv)

    digest = json.loads(args.digest.read_text(encoding="utf-8"))
    result = apply_rules(digest, args.tracker, min_score=args.min_score)

    out = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(out, encoding="utf-8")
    else:
        print(out)

    print(
        f"[filters] selected={len(result['selected'])} "
        f"excluded={len(result['excluded'])}",
        file=sys.stderr,
    )
    for e in result["selected"]:
        print(
            f"  #{e['priority_rank']:<2} {e['apply_channel']:<15} "
            f"[{e.get('category', ''):<18}] {e.get('company', '')[:25]:<25} "
            f"{e.get('title', '')[:45]}",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
