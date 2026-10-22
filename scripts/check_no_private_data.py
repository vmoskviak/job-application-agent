#!/usr/bin/env python3
"""Перевірка перед комітом: чи не затекли в рушій дані реальних клієнтів.

    check_no_private_data.py [--companies]

Збирає з усіх папок у JOBSEARCH_CLIENTS_DIR імена, адреси, телефони,
роботодавців із CV, а також ID вакансій з трекерів і папок заявок (StepStone,
XING, LinkedIn, Interamt, Indeed) і шукає їх у файлах цього репозиторію (ті,
що бачить git: tracked + untracked, без ignored). `--companies` додає назви
компаній із трекерів: там багато загальновідомих фірм, тому це окремий,
шумніший режим.

Код виходу 1, якщо щось знайдено.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ENGINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE))

from bot import client as _client  # noqa: E402

GENERIC = {"gmbh", "ag", "se", "kg", "co", "und", "the", "group", "deutschland",
           "germany", "digital", "service", "services", "consulting", "management"}
# Вигадані дані прикладу не є витоком
EXAMPLE_SLUGS = {"erika"}


def repo_files() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=ENGINE, capture_output=True, text=True, check=True).stdout
    return [ENGINE / line for line in out.splitlines() if line]


def personal_terms(c: _client.Client) -> set[str]:
    p = c.profile
    terms = {p.get("first_name"), p.get("last_name"), p.get("email"),
             p.get("street"), str(p.get("birth_date") or "")}
    phone = re.sub(r"\D", "", str(p.get("phone") or ""))
    if len(phone) >= 8:
        terms.add(phone[-8:])
    for acc in (c.accounts.get("gmail") or {}).values():
        terms.add(acc.get("address"))
        terms.add(acc.get("creds_dir"))
    for line in p.get("cv_contact_extra") or []:
        if "linkedin" in str(line):
            terms.add(str(line).rsplit("/", 1)[-1])
    for entry in c.cv.get("experience") or []:
        terms.add(str(entry.get("company", "")).split(",")[0].strip())
    for entry in c.cv.get("education") or []:
        terms.add(str(entry.get("inst", "")).split(",")[0].strip())
    for entry in (c.cv.get("ehrenamt") or {}).get("entries") or []:
        terms.add(str(entry.get("org", "")).split(",")[0].strip())
    return {t for t in terms if t and len(str(t)) >= 4}


def tracker_companies(c: _client.Client) -> set[str]:
    from bot.filters.rules import parse_tracker_applications  # noqa: PLC0415
    names = set()
    for app in parse_tracker_applications(c.tracker):
        name = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", app["company"]).strip()
        words = [w for w in re.findall(r"[\wäöüÄÖÜß&-]+", name) if w.lower() not in GENERIC]
        if words and len(" ".join(words)) >= 5:
            names.add(" ".join(words[:2]) if len(words[0]) < 5 else words[0])
    return names


# ID вакансії в URL порталу: самого числа досить, щоб знайти оголошення,
# а отже й зв'язати тест із реальною заявкою клієнта.
_JOB_ID_RES = [
    re.compile(r"stepstone\.de/stellenangebote-[^\s)|\"']*?-(\d{6,})-inline"),
    re.compile(r"xing\.com/jobs/[^\s)|\"']*?-(\d{6,})\b"),
    re.compile(r"linkedin\.com/jobs/view/(?:[^\s)|\"'/]*?-)?(\d{7,})\b"),
    re.compile(r"interamt\.de/koop/app/stelle(?:\?id=|/)(\d{6,})\b"),
    re.compile(r"indeed\.com/[^\s)|\"']*?[?&](?:jk|fromjk)=([0-9a-f]{16})\b"),
]
_APP_FILE_SUFFIXES = {".md", ".txt", ".json", ".py", ".yaml"}


def job_ids(c: _client.Client) -> set[str]:
    """ID вакансій з трекера й папок заявок клієнта."""
    texts: list[str] = []
    if c.tracker.exists():
        texts.append(c.tracker.read_text(encoding="utf-8", errors="ignore"))
    if c.applications.exists():
        for path in c.applications.rglob("*"):
            if path.is_file() and path.suffix in _APP_FILE_SUFFIXES:
                try:
                    texts.append(path.read_text(encoding="utf-8", errors="ignore"))
                except OSError:
                    continue
    ids: set[str] = set()
    for text in texts:
        for rx in _JOB_ID_RES:
            ids.update(rx.findall(text))
    return ids


def scan(terms: set[str], files: list[Path]) -> dict[str, list[str]]:
    hits: dict[str, list[str]] = {}
    patterns = {t: re.compile(r"(?<!\w)" + re.escape(t) + r"(?!\w)", re.IGNORECASE) for t in terms}
    for path in files:
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for term, rx in patterns.items():
            n = len(rx.findall(text))
            if n:
                hits.setdefault(term, []).append(f"{path.relative_to(ENGINE)} ×{n}")
    return hits


def main() -> int:
    with_companies = "--companies" in sys.argv[1:]
    clients = [c for c in _client.list_clients() if c.slug not in EXAMPLE_SLUGS]
    if not clients:
        print(f"клієнтів не знайдено в {_client.clients_dir()} — нема з чим порівнювати")
        return 2
    files = repo_files()
    terms: set[str] = set()
    for c in clients:
        terms |= personal_terms(c)
        terms |= job_ids(c)
        if with_companies:
            # rules.py читає конфіг активного клієнта; для парсингу трекера
            # досить будь-якого
            import os
            os.environ.setdefault(_client.ENV_CLIENT, c.slug)
            terms |= tracker_companies(c)
    hits = scan(terms, files)
    for term in sorted(hits):
        print(f"{term}:")
        for where in hits[term]:
            print(f"    {where}")
    print(f"перевірено {len(files)} файлів на {len(terms)} термінів "
          f"({len(clients)} клієнт(ів)): знайдено {len(hits)}")
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())
