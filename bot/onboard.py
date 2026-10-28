"""Онбординг клієнта: скелет папки й перевірка конфігу.

    python -m bot.onboard init <slug>          створити папку клієнта зі скелетом
    python -m bot.onboard check --client <slug> [--render]
                                               перевірити конфіг; --render ще й
                                               збирає пробний пакет у var/run/

Модуль навмисно не імпортує нічого, що читає конфіг під час імпорту:
`check` має показати всі помилки списком, а не впасти на першій.
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

from bot import client as _client
from bot import market as _market

TEMPLATE_DIR = _client.ENGINE_ROOT / "templates" / "client"
SLUG_RE = re.compile(r"^[a-z][a-z0-9-]{1,30}$")

# Залишено як аліас для scripts/: перелік ринку de_employment.
REQUIRED_PROFILE = list(_market.get("de_employment").required_profile)


def required_profile(market: str) -> list[str]:
    return list(_market.get(market).required_profile)


def init(slug: str, base: Path | None = None) -> Path:
    if not SLUG_RE.match(slug):
        raise _client.ClientError(
            f"slug '{slug}': лише малі латинські літери, цифри й дефіс")
    root = (base or _client.clients_dir()) / slug
    if root.exists():
        raise _client.ClientError(f"папка вже існує: {root}")
    root.mkdir(parents=True)
    for src in sorted(TEMPLATE_DIR.iterdir()):
        text = src.read_text(encoding="utf-8").replace("__SLUG__", slug)
        (root / src.name).write_text(text, encoding="utf-8")
    for sub in ("attachments", "applications", "inbox"):
        (root / sub).mkdir()
        (root / sub / ".gitkeep").touch()
    return root


def check(c: _client.Client) -> tuple[list[str], list[str]]:
    """Повертає (errors, warnings)."""
    errors: list[str] = []
    warnings: list[str] = []
    p, cv = c.profile, c.cv

    for key in required_profile(c.market):
        if not str(p.get(key) or "").strip():
            errors.append(f"profile.yaml: порожнє поле {key}")
    if p.get("birth_date") and not re.match(r"^\d{4}-\d{2}-\d{2}$", str(p["birth_date"])):
        errors.append("profile.yaml: birth_date має бути ISO (1990-01-15)")
    if p.get("default_salary") and not str(p["default_salary"]).isdigit():
        errors.append("profile.yaml: default_salary — лише цифри")
    if p.get("slug") and p["slug"] != c.slug:
        errors.append(f"profile.yaml: slug '{p['slug']}' ≠ назва папки '{c.slug}'")
    if c.photo and not c.photo.exists():
        warnings.append(f"фото не знайдено: {c.photo.name} (CV піде без фото)")
    if not c.report_email:
        warnings.append("profile.yaml: report_email порожній, звіт про подані заявки "
                        "піде лише оператору")
    elif not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", c.report_email):
        errors.append(f"profile.yaml: report_email не схожий на адресу: {c.report_email}")

    variants = list(cv.get("variants") or [])
    if not variants:
        errors.append("cv.yaml: variants порожній")
    default = cv.get("default_variant", variants[0] if variants else None)
    if default not in variants:
        errors.append(f"cv.yaml: default_variant '{default}' нема у variants")
    # source: external: готові PDF, структурованого досвіду й skills у YAML нема
    external = cv.get("source") == "external"
    if not cv.get("experience") and not external:
        errors.append("cv.yaml: experience порожній")
    for i, entry in enumerate(cv.get("experience") or [], 1):
        for key in ("dates", "company", "title", "bullets"):
            if not entry.get(key):
                errors.append(f"cv.yaml: experience[{i}] без {key}")
        bullets = entry.get("bullets")
        if isinstance(bullets, dict):
            unknown = set(bullets) - set(variants) - {"default"}
            if unknown:
                errors.append(f"cv.yaml: experience[{i}] буліти для невідомих варіантів {sorted(unknown)}")
            if "default" not in bullets and default not in bullets:
                errors.append(
                    f"cv.yaml: experience[{i}] без булітів default/{default} — "
                    "частина варіантів лишиться порожньою")
        for lst in (bullets.values() if isinstance(bullets, dict) else [bullets]):
            for b in (lst or []):
                if not isinstance(b, str):
                    errors.append(
                        f"cv.yaml: experience[{i}] буліт не рядок: {b!r} "
                        "(двокрапка без лапок?)")
    skills = cv.get("skills") or {}
    for v in ([] if external else variants):
        if v not in skills:
            (errors if v == default else warnings).append(
                f"cv.yaml: skills без варіанта '{v}'"
                + ("" if v == default else f" (піде '{default}')"))
    if not cv.get("education"):
        warnings.append("cv.yaml: education порожній")
    for a in cv.get("attachments") or []:
        if not (c.attachments / a.get("file", "")).is_file():
            errors.append(f"attachments/{a.get('file')}: файла нема")
        unknown = set(a.get("variants") or []) - set(variants)
        if unknown:
            errors.append(f"cv.yaml: вкладення {a.get('file')} для невідомих варіантів {sorted(unknown)}")
    if not cv.get("attachments"):
        warnings.append("cv.yaml: attachments порожній — email-заявки підуть без Zeugnisse")

    tracks = c.tracks.get("tracks") or {}
    if not tracks:
        errors.append("tracks.yaml: жодного треку")
    active_tracks = 0
    for name, t in tracks.items():
        if not t.get("keywords"):
            errors.append(f"tracks.yaml: {name} без keywords")
        if t.get("variant") not in variants:
            errors.append(f"tracks.yaml: {name} → варіант '{t.get('variant')}' нема в cv.yaml")
        active_tracks += 0 if t.get("excluded") else 1
    if tracks and not active_tracks:
        errors.append("tracks.yaml: усі треки excluded")

    tg = c.targeting
    for pat in tg.get("excluded_title_patterns") or []:
        try:
            re.compile(pat)
        except re.error as e:
            errors.append(f"targeting.yaml: битий regex {pat!r}: {e}")
    if not tg.get("locations"):
        warnings.append("targeting.yaml: locations порожній — пройдуть лише remote-вакансії")

    gmail = c.accounts.get("gmail") or {}
    send_from = c.accounts.get("send_from", "jobs")
    if send_from not in gmail:
        errors.append(f"accounts.yaml: немає акаунта send_from='{send_from}'")
    home = Path.home()
    for alias, acc in gmail.items():
        if not acc.get("address"):
            errors.append(f"accounts.yaml: gmail.{alias}.address порожній")
            continue
        creds = c.credentials_path(alias)
        if not creds.exists():
            warnings.append(f"OAuth ще не зроблено на цій машині: {creds}")
        keys = c.oauth_keys_path(alias)
        if not keys.exists():
            warnings.append(
                f"немає ключа OAuth-клієнта: ні {keys.parent / 'gcp-oauth.keys.json'}, "
                f"ні спільного ~/.gmail-mcp/gcp-oauth.keys.json")
    profile_dir = home / ".playwright-mcp" / c.playwright_profile
    if not profile_dir.exists():
        warnings.append(f"браузерного профілю ще нема на цій машині: {profile_dir}")

    if not c.search.get("query_groups"):
        errors.append("search.yaml: query_groups порожній — вечірній агент не знатиме, що шукати")
    if len(c.brief.strip()) < 200:
        warnings.append("candidate_brief.md майже порожній — tailoring буде загальним")
    if not c.tracker.exists():
        errors.append("tracker.md відсутній")
    return errors, warnings


def render_sample(c: _client.Client) -> Path:
    """Пробний пакет на кожен варіант CV у var/run/onboarding_sample/."""
    from bot.generators.cv_engine import render_application
    from bot.generators.intake import JobData
    from bot.generators.tailor import TailoredData

    out = c.run_dir / "onboarding_sample"
    if out.exists():
        shutil.rmtree(out)
    for variant in c.cv["variants"]:
        job = JobData(
            title="Musterposition (m/w/d)", company="Muster GmbH", location=c.profile["city"],
            description="", email="bewerbung@example.com", application_url=None,
            source_url="https://example.com/job", is_portal=False)
        tailored = TailoredData(
            cv_title="Musterposition", profile="[Profiltext wird pro Stelle geschrieben.]",
            kernkompetenzen=[("Schwerpunkt", "wird pro Stelle gesetzt")],
            experience_variant=variant, skills_order=variant, fit_score=0, fit_reason="",
            salary=str(c.profile["default_salary"]),
            anschreiben="[Anschreiben wird pro Stelle geschrieben.]")
        render_application(job, tailored, out / variant)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_init = sub.add_parser("init", help="створити папку клієнта")
    p_init.add_argument("slug")
    p_check = sub.add_parser("check", help="перевірити конфіг активного клієнта")
    p_check.add_argument("--render", action="store_true", help="зібрати пробний пакет")
    args = parser.parse_args(argv)

    try:
        if args.cmd == "init":
            root = init(args.slug)
            print(f"створено: {root}\nдалі: docs/onboarding.md")
            return 0
        c = _client.active()
    except _client.ClientError as e:
        print(f"ПОМИЛКА: {e}", file=sys.stderr)
        return 2

    errors, warnings = check(c)
    for w in warnings:
        print(f"  ⚠️  {w}")
    for e in errors:
        print(f"  ❌ {e}")
    print(f"{c.slug}: {len(errors)} помилок, {len(warnings)} попереджень")
    if errors:
        return 1
    if args.render:
        print(f"пробний пакет: {render_sample(c)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
