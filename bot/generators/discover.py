"""Batch intake + keyword scoring + digest builder for --discover workflow."""

from __future__ import annotations
import os
import re
import json
import sys
from urllib.parse import urlsplit, parse_qs, urljoin

import httpx
from dataclasses import dataclass, field
from pathlib import Path
from datetime import date
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from bot.generators.keywords import CATEGORIES, SALARY_BY_CATEGORY, VARIANT_BY_CATEGORY
from bot.generators.intake import intake_with_meta, JobData
from bot.scrapers.models import IntakeResult
from bot.market import description_limit


_URL_PATTERNS = [
    r"https?://(?:www\.)?(?:de\.)?linkedin\.com/(?:comm/)?jobs/view/[^\s\"'?]+",
    r"https?://(?:www\.)?stepstone\.de/stelle/[^\s\"'>]+",
    r"https?://click\.stepstone\.de/f/a/[^\s\"'>]+",
    r"https?://(?:www\.)?indeed\.com/viewjob[^\s\"'>]+",
    r"https?://(?:www\.)?indeed\.de/viewjob[^\s\"'>]+",
    r"https?://interamt\.de/koop/app/stelle[^\s\"'>]+",
    r"https?://(?:www\.)?arbeitsagentur\.de/jobsuche/[^\s\"'>]+",
    r"https?://(?:www\.)?jobware\.de/stellenangebote/[^\s\"'>]+",
    r"https?://(?:www\.)?kleinanzeigen\.de/s-anzeige/[^\s\"'>]+",
    r"https?://(?:www\.)?hokify\.de/jobs/[^\s\"'>]+",
    r"https?://(?:www\.)?xing\.com/jobs/[^\s\"'>]+",
    r"https?://[^\s\"'>]*?/jobs?/[^\s\"'>]{10,}",
]

_URL_RE = re.compile("|".join(_URL_PATTERNS), re.IGNORECASE)


@dataclass
class IntakeStats:
    live: int = 0
    closed: int = 0
    blocked: int = 0
    unknown: int = 0
    failed: int = 0
    httpx_ok: int = 0
    playwright: int = 0
    # URL зі збоєм мережі/фетча: їх не позначаємо «бачені», щоб спробувати знову
    failed_urls: list = field(default_factory=list)

    def record(self, result: IntakeResult) -> None:
        if result.liveness == "live":
            self.live += 1
        elif result.liveness == "closed":
            self.closed += 1
        elif result.liveness == "blocked":
            self.blocked += 1
        elif result.liveness == "unknown":
            self.unknown += 1
        elif result.liveness == "failed":
            self.failed += 1
        if result.intake_method == "httpx":
            self.httpx_ok += 1
        elif result.intake_method == "playwright":
            self.playwright += 1

    def summary_line(self, total: int) -> str:
        return (
            f"[intake] {total} URLs: "
            f"live={self.live} closed={self.closed} blocked={self.blocked} "
            f"unknown={self.unknown} failed={self.failed} "
            f"httpx_ok={self.httpx_ok} playwright={self.playwright}"
        )


_TRACKING_HOST = "click.stepstone.de"

# Заголовки для резолюції редіректів (без Playwright: click.* віддає 302 і curl'у)
_RESOLVE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
}


class TrackingResolveError(Exception):
    """Мережева помилка/немає Location при резолюції tracking-лінка."""


def _stepstone_job_url(url: str) -> Optional[str]:
    """Канонічний stepstone.de URL вакансії без query, або None якщо це не вакансія."""
    parts = urlsplit(url)
    if "stepstone.de" not in parts.netloc:
        return None
    if "/stellenangebote--" in parts.path or "/stelle/" in parts.path:
        return f"https://{parts.netloc}{parts.path}"
    return None


def resolve_tracking_url(url: str, timeout: float = 15.0) -> Optional[str]:
    """Резолвить click.stepstone.de email-редірект у канонічний URL вакансії.

    Один GET без follow: Location — або одразу вакансія, або
    /v2/magiclink/exchange?returnUrl=<шлях вакансії>. Повертає None, якщо
    редірект веде не на вакансію (футерні соцлінки, unsubscribe).
    Кидає TrackingResolveError при мережевій помилці — щоб лід не загубився
    тихо, а потрапив у manual-список (політика no silent caps).
    """
    try:
        resp = httpx.get(
            url, headers=_RESOLVE_HEADERS, timeout=timeout, follow_redirects=False
        )
        location = resp.headers.get("location")
    except Exception as exc:
        raise TrackingResolveError(str(exc)) from exc
    if not location:
        raise TrackingResolveError(f"no redirect (status {resp.status_code})")

    direct = _stepstone_job_url(location)
    if direct:
        return direct

    parts = urlsplit(location)
    if "stepstone.de" in parts.netloc and "magiclink" in parts.path:
        return_url = (parse_qs(parts.query).get("returnUrl") or [None])[0]
        if return_url:
            return _stepstone_job_url(urljoin("https://www.stepstone.de", return_url))
    return None


def resolve_urls(urls: list[str]) -> tuple[list[str], list[str]]:
    """Пре-крок discover: tracking-лінки → канонічні URL, решта — без змін.

    Повертає (resolved, failed): resolved — дедуплікований список для intake
    (не-вакансійні редіректи відкинуто), failed — tracking-лінки, які не
    вдалось зрезолвити (мережа) — їх у manual-список звіту.
    """
    resolved: list[str] = []
    failed: list[str] = []
    for url in urls:
        if _TRACKING_HOST in url:
            try:
                target = resolve_tracking_url(url)
            except TrackingResolveError as exc:
                print(f"[resolve] не зрезолвився: {url[:60]}… ({exc})", file=sys.stderr)
                failed.append(url)
                continue
            if target:
                resolved.append(target)
        else:
            resolved.append(url)
    return list(dict.fromkeys(resolved)), failed


def extract_urls(text: str) -> list[str]:
    """Extract job URLs from raw email/text content."""
    return list(dict.fromkeys(_URL_RE.findall(text)))


def _slugify(name: str) -> str:
    s = name.lower()
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        s = s.replace(a, b)
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def _nrw_slug_set() -> set[str]:
    from bot.filters.rules import NRW_LOCATIONS
    return {_slugify(c) for c in NRW_LOCATIONS}


_NRW_SLUGS = _nrw_slug_set()

# Великі не-NRW міста, які реально приходять у XING/StepStone-дайджестах.
# Тільки однозначні: дрібні/невідомі міста триаж пропускає далі (урок Haan —
# рішення по них приймає location_ok із повним описом).
_NON_NRW_CITY_SLUGS = {
    "berlin", "hamburg", "muenchen", "munich", "frankfurt", "stuttgart",
    "hannover", "bremen", "bremerhaven", "leipzig", "dresden", "nuernberg",
    "mainz", "mannheim", "wiesbaden", "karlsruhe", "augsburg", "regensburg",
    "kiel", "luebeck", "rostock", "magdeburg", "erfurt", "jena", "kassel",
    "giessen", "darmstadt", "heidelberg", "freiburg", "ulm", "wuerzburg",
    "braunschweig", "salzgitter", "hildesheim", "osnabrueck", "oldenburg",
    "wolfsburg", "goettingen", "potsdam", "halle", "chemnitz", "saarbruecken",
    "ingolstadt", "heilbronn", "pforzheim", "offenbach", "fuerth", "bamberg",
    "bayreuth", "forchheim", "trier", "koblenz", "ludwigshafen", "wien",
    "zuerich", "basel", "bern", "graz", "linz", "salzburg",
}

_REMOTE_SLUG_RE = re.compile(r"\b(?:remote|home ?office|hybrid|bundesweit|deutschlandweit)\b")


def _job_slug(url: str) -> Optional[str]:
    """Слаг вакансії з канонічного XING/StepStone URL, або None (не судимо)."""
    parts = urlsplit(url)
    path = parts.path
    if "xing.com" in parts.netloc and path.startswith("/jobs/"):
        slug = path[len("/jobs/"):].strip("/")
        if slug and "/" not in slug and not slug.startswith(("search", "my-jobs")):
            return re.sub(r"-\d+$", "", slug)
        return None
    if "stepstone.de" in parts.netloc and "/stellenangebote--" in path:
        slug = path.split("/stellenangebote--", 1)[1]
        slug = re.sub(r"-inline\.html$", "", slug)
        return re.sub(r"-\d+$", "", slug)
    return None


def triage_urls(urls: list[str]) -> tuple[list[str], list[dict]]:
    """Дешевий пре-фільтр по слагу URL — до браузерного intake.

    Слаг XING/StepStone містить місто й назву ролі: явно чуже місто або
    заборонену роль (Senior/Ausbildung/…) відсіюємо без фетча — інакше
    дайджест на 50+ лінків палить години браузерного бюджету на сміття.
    Невідоме місто, remote-маркер у слагу чи URL без слага — пропускаємо далі.
    """
    from bot.filters.rules import title_excluded

    kept: list[str] = []
    dropped: list[dict] = []
    for url in urls:
        slug = _job_slug(url)
        if slug is None:
            kept.append(url)
            continue
        text = _slugify(slug.replace("-", " "))
        if title_excluded(text):
            dropped.append({"source_url": url, "reason": "excluded_title_slug"})
            continue
        tokens = text.split()
        grams = set(tokens) | {" ".join(p) for p in zip(tokens, tokens[1:])}
        if grams & _NRW_SLUGS or _REMOTE_SLUG_RE.search(text):
            kept.append(url)
        elif grams & _NON_NRW_CITY_SLUGS:
            dropped.append({"source_url": url, "reason": "outside_nrw_slug"})
        else:
            kept.append(url)
    return kept, dropped


def score_job(title: str, description: str) -> tuple[int, str]:
    """Score a job against all keyword categories. Returns (score, best_category)."""
    title_lower = title.lower()
    haystack = title_lower + " " + description.lower()
    best_score = 0
    best_cat = ""
    for cat_name, keywords in CATEGORIES.items():
        hits = sum(1 for kw in keywords if kw.lower() in haystack)
        # влучання в назву = пряма ознака ролі, а не випадкова згадка в описі;
        # інакше вакансія з точною назвою ("Business Analyst") без другого
        # синоніма в тексті набирає 12 < MIN_SCORE і випадає з відбору
        if hits and any(kw.lower() in title_lower for kw in keywords):
            hits += 1
        if hits > best_score:
            best_score = hits
            best_cat = cat_name
    normalised = min(100, int(best_score / 8 * 100)) if best_score > 0 else 0
    return normalised, best_cat


def score_fields(title: str, description: str) -> dict:
    """Поля скорингу запису дайджесту: score, category і підказки треку."""
    score, cat = score_job(title, description)
    return {
        "score": score,
        "category": cat,
        "salary_hint": SALARY_BY_CATEGORY.get(cat, "55000"),
        "variant_hint": VARIANT_BY_CATEGORY.get(cat, "buero"),
    }


def build_digest(scored_results: list[dict]) -> list[dict]:
    """Sort intake+score results by score descending."""
    return sorted(scored_results, key=lambda r: r["score"], reverse=True)


def process_urls(
    urls: list[str],
    verbose: bool = True,
    use_playwright: bool = True,
) -> tuple[list[dict], IntakeStats]:
    """Run intake on each URL, score live jobs, return sorted digest and stats."""
    from bot import client as _client
    desc_limit = description_limit(_client.active().market)
    results: list[dict] = []
    manual_leads: list[dict] = []
    stats = IntakeStats()

    urls, failed_tracking = resolve_urls(urls)
    stats.failed_urls.extend(failed_tracking)
    for url in failed_tracking:
        manual_leads.append({
            "source_url": url,
            "liveness": "unknown",
            "reason": "tracking-лінк не зрезолвився (мережа)",
            "intake_method": "httpx",
            "apply_channel": "manual_link",
        })

    urls, triaged_out = triage_urls(urls)
    if triaged_out:
        by_reason: dict[str, int] = {}
        for d in triaged_out:
            by_reason[d["reason"]] = by_reason.get(d["reason"], 0) + 1
        print(
            "[triage] відсіяно по слагу без фетча: "
            + ", ".join(f"{r}={n}" for r, n in sorted(by_reason.items())),
            file=sys.stderr,
        )

    for i, url in enumerate(urls, 1):
        if verbose:
            print(f"[{i}/{len(urls)}] Збираю: {url[:80]}", file=sys.stderr)
        result = intake_with_meta(url, use_playwright=use_playwright)
        stats.record(result)
        if result.liveness == "failed":
            stats.failed_urls.append(url)

        if result.liveness != "live" or result.job is None:
            # blocked (CAPTCHA/login-wall/cookie-wall, напр. Indeed) та unknown —
            # вакансія могла бути живою, ми лише не змогли її скрапити. Не викидати
            # тихо: у ручний список звіту (політика no silent caps). closed/failed —
            # реально мертві/недоступні, лишаємо відкинутими.
            if result.liveness in ("blocked", "unknown"):
                manual_leads.append({
                    "source_url": url,
                    "liveness": result.liveness,
                    "reason": result.reason,
                    "intake_method": result.intake_method,
                    "apply_channel": "manual_link",
                })
            if verbose:
                print(
                    f"  Пропускаю ({result.liveness}: {result.reason})",
                    file=sys.stderr,
                )
            continue

        job: JobData = result.job
        results.append({
            **score_fields(job.title, job.description),
            "title": job.title,
            "company": job.company,
            "location": job.location,
            "email": job.email,
            "is_portal": job.is_portal,
            "source_url": job.source_url,
            "description": job.description[:desc_limit],
            "liveness": result.liveness,
            "intake_method": result.intake_method,
        })

    unbekannt = sum(
        1 for r in results
        if "linkedin.com" in r["source_url"] and r["company"] == "Unbekannt"
    )
    if unbekannt >= 2:
        # розмітка LinkedIn знову змінилась — скрейпінг деградував; кричати,
        # інакше сміттєві описи тихо валять скоринг і дають 0 selected тижнями
        print(
            f"⚠️ [canary] {unbekannt} LinkedIn-результатів з company=\"Unbekannt\" — "
            f"розмітка LinkedIn змінилась, скрейпінг деградував. Scores цих вакансій "
            f"недостовірні, перевірити bot/scrapers/adapters/linkedin.py",
            file=sys.stderr,
        )

    print(stats.summary_line(len(urls)), file=sys.stderr)
    return build_digest(results), stats, manual_leads


# --- Стан «вже бачені» (var/state/discover_seen.json) ---------------------------
# Алерт-листи читаються за два дні (newer_than:2d), тож без пам'яті кожен лінк
# ішов би через скрапінг, фільтр і cover note двічі, а evening у трекер не пише.
# Ключ = хост + шлях без query/fragment: трекінгові параметри міняються від
# листа до листа, сам проєкт той самий.

def seen_key(url: str) -> str:
    parts = urlsplit(url.strip())
    return f"{parts.netloc.lower()}{parts.path.rstrip('/')}"


def load_seen(path: Path) -> set[str]:
    try:
        return set(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return set()


def commit_seen(path: Path, keys: set[str]) -> None:
    """Атомарно дописує ключі до стану (тимчасовий файл + os.replace)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(sorted(load_seen(path) | set(keys)), ensure_ascii=False),
                   encoding="utf-8")
    os.replace(tmp, path)


def split_seen(urls: list[str], seen: set[str]) -> tuple[list[str], list[str]]:
    fresh, skipped = [], []
    for u in urls:
        (skipped if seen_key(u) in seen else fresh).append(u)
    return fresh, skipped


def main(urls_file: Path, use_playwright: bool = True,
         seen_state: Optional[Path] = None) -> None:
    """CLI entry: read urls.txt, run intake+score, write digest JSON.

    seen_state: шлях до стану «вже бачені»; None = без пам'яті (поведінка
    ринку найму, де дедуп іде проти трекера).
    """
    from bot.scrapers.browser import shutdown_browser

    try:
        urls = [
            u.strip()
            for u in urls_file.read_text(encoding="utf-8").splitlines()
            if u.strip()
        ]
        if not urls:
            print("[discover] urls.txt є порожнім", file=sys.stderr)
            return

        skipped: list[str] = []
        if seen_state is not None:
            urls, skipped = split_seen(urls, load_seen(seen_state))
            if skipped:
                print(f"[discover] вже бачені: {len(skipped)} URL пропущено "
                      f"(стан {seen_state.name})", file=sys.stderr)

        today = date.today().strftime("%Y-%m-%d")
        out_path = urls_file.parent / f"discover_digest_{today}.json"
        if not urls:
            out_path.write_text("[]", encoding="utf-8")
            print(f"[discover] нових URL немає, {out_path.name} порожній", file=sys.stderr)
            return

        print(f"[discover] Обробляю {len(urls)} URL...", file=sys.stderr)
        digest, stats, manual_leads = process_urls(urls, use_playwright=use_playwright)

        out_path.write_text(
            json.dumps(digest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        if seen_state is not None:
            failed = set(stats.failed_urls)
            commit_seen(seen_state, {seen_key(u) for u in urls if u not in failed})

        if manual_leads:
            manual_path = urls_file.parent / f"discover_manual_{today}.json"
            manual_path.write_text(
                json.dumps(manual_leads, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

        print(f"\n{'='*60}")
        print(f"  Digest: {out_path}")
        print(f"  Знайдено: {len(digest)} вакансій (live)")
        if manual_leads:
            print(
                f"  Manual-leads (blocked/unknown, не скрапнулись — у ручний "
                f"список звіту): {len(manual_leads)} -> discover_manual_{today}.json"
            )
        print(f"  {stats.summary_line(len(urls))}")
        if skipped:
            print(f"  Вже бачені раніше (пропущено без фетча): {len(skipped)}")
        print(f"{'='*60}")
        for entry in digest[:10]:
            flag = "email" if entry["email"] else "portal"
            print(
                f"  {entry['score']:3d}  {flag}  [{entry['category']:<20}]  "
                f"{entry['company'][:25]:<25}  {entry['title'][:40]}"
            )
        if len(digest) > 10:
            print(f"  ... та ще {len(digest) - 10} вакансій у файлі")
        print(
            f"\n-> Тепер перегляньте {out_path.name} і відберіть вакансії для tailoring."
        )
    finally:
        shutdown_browser()


if __name__ == "__main__":
    # Пряма точка входу без new_app.py: той тягне bot.apply.candidate,
    # якого контрактний клієнт не має.
    import argparse

    _ap = argparse.ArgumentParser(description="discover: urls.txt -> discover_digest_<дата>.json")
    _ap.add_argument("urls_file", type=Path)
    _ap.add_argument("--no-playwright", action="store_true",
                     help="без Playwright-фолбеку для сторінок, що не віддались httpx")
    _ap.add_argument("--skip-seen", action="store_true",
                     help="пропускати лінки з попередніх запусків "
                          "(стан var/state/discover_seen.json клієнта)")
    _ap.add_argument("--seen-state", type=Path, metavar="PATH",
                     help="свій шлях до стану «вже бачені» (вмикає --skip-seen)")
    _args = _ap.parse_args()
    _state = _args.seen_state
    if _state is None and _args.skip_seen:
        from bot import client as _client
        _state = _client.active().var / "state" / "discover_seen.json"
    main(_args.urls_file, use_playwright=not _args.no_playwright, seen_state=_state)
