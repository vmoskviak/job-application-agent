#!/usr/bin/env python3
"""Pre-commit check: did data of real clients leak into the engine?

    check_no_private_data.py [--companies] [--strict] [--baseline FILE]

Collects from every folder in JOBSEARCH_CLIENTS_DIR the names, addresses, phone numbers and
employers from the CVs, plus the job ids from the trackers and application folders (StepStone,
XING, LinkedIn, Interamt, Indeed), and looks for them in the files of this repository (what git
sees: tracked and untracked, not ignored). `--companies` adds the company names from the trackers;
many of those are well-known firms or generic first words («Stadt», «Reply»), so it is a separate,
noisier mode.

Independent of the clients there is a structural check: email addresses that are neither on a
placeholder domain nor on a platform domain, and German phone numbers that do not look like a
stub. It catches foreign contacts in tests and fixtures when no client data is around (the CI of
the public copy).

`--strict` for CI: without a clients folder it does not stop with code 2 but runs the structural
check only. LICENSE is never checked: the author's name is there by design.

`--baseline FILE` (implies `--companies`): a ratchet for the company mode. The file lists the
accepted generic hits, one term per line (`#` comments and blank lines ignored, case does not
matter); a company term in it is not reported, every other term still is, and a name, contact or
job id is never accepted even if it stands in the file. A new term is either an employer that
leaked into a docstring or a new generic word to add to the file by hand. scripts/sync_public.sh
and the pre-push hook of the public clone run it against .companies-baseline, which never leaves
the private engine (.public-exclude).

Exit code 1 if something was found; 2 if there are no clients and not `--strict`.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ENGINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE))

from bot import client as _client

GENERIC = {"gmbh", "ag", "se", "kg", "co", "und", "the", "group", "deutschland",
           "germany", "digital", "service", "services", "consulting", "management"}
# Вигадані дані прикладів не є витоком
EXAMPLE_SLUGS = {"erika", "jordan"}
# Файли, де чужі контакти стоять за задумом: ім'я автора в ліцензії, негативні
# приклади в тесті цього скрипта
SKIP_FILES = {"LICENSE", "test_check_no_private_data.py", ".companies-baseline"}


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
    from bot.filters.rules import parse_tracker_applications
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


# ── структурна перевірка: контакти без прив'язки до клієнта ──────────────────

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)")
# Домени тестових заглушок: example/beispiel/muster/test/firma тощо, службові
# TLD, одно-дволітерні домени на кшталт a.de
_PLACEHOLDER_DOMAIN_RE = re.compile(
    r"(?:^|[.-])(?:example|beispiel|muster\w*|test|firma|foo|fremde\w*|kleinbetrieb"
    r"|nothays|email|gmail|github|anthropic|localhost)(?:[.-]|$)"
    r"|\.(?:example|test|invalid|beispiel|localhost)$"
    r"|^[a-z]{1,2}\.[a-z]+$",
    re.IGNORECASE,
)
# Платформи, чиї адреси рушій знає сам: алерти порталів, ATS, біржі, агентури
# як платформи. Це не роботодавці конкретної заявки.
_PLATFORM_DOMAINS = (
    "linkedin.com", "xing.com", "stepstone.de", "indeed.com", "glassdoor.com",
    "talent.com", "get-in.de", "jobijoba.com", "arbeitsagentur.de", "interamt.de",
    "softgarden.io", "greenhouse-mail.io", "lever.co", "malt.com", "upwork.com",
    "freelancermap.de",
)
_PHONE_RE = re.compile(r"\+49[\s/().-]*\d(?:[\d\s/().-]{6,}\d)")
# Телефонні заглушки: субскрайберська частина з нулів або 1234567
_PHONE_PLACEHOLDER_RE = re.compile(r"(?:0{5,}|1234567)$")


def platform_domains() -> set[str]:
    domains = set(_PLATFORM_DOMAINS)
    try:
        from bot.ats import PLATFORM_LOGIN_HOSTS
        from bot.generators.inbox import PLATFORM_SENDERS
        from bot.platforms import PLATFORM_HOSTS
    except ImportError:
        return domains
    for hosts in list(PLATFORM_SENDERS.values()) + list(PLATFORM_HOSTS.values()):
        domains.update(hosts)
    domains.update(PLATFORM_LOGIN_HOSTS)
    return {d.strip(".").lower() for d in domains}


def is_placeholder_email(addr: str, platforms: set[str]) -> bool:
    """Адреса на домені-заглушці або на домені платформи."""
    domain = addr.rsplit("@", 1)[-1].lower()
    if _PLACEHOLDER_DOMAIN_RE.search(domain):
        return True
    return any(domain == p or domain.endswith("." + p) for p in platforms)


def is_placeholder_phone(number: str) -> bool:
    digits = re.sub(r"\D", "", number)[2:]
    return bool(_PHONE_PLACEHOLDER_RE.search(digits))


def structural_hits(files: list[Path]) -> dict[str, list[str]]:
    """Чужі контакти в репозиторії без списку клієнтів: email не на заглушці
    й не на платформі, телефон +49 не схожий на заглушку."""
    platforms = platform_domains()
    hits: dict[str, list[str]] = {}
    for path in files:
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        rel = str(path.relative_to(ENGINE))
        for m in _EMAIL_RE.finditer(text):
            if not is_placeholder_email(m.group(0), platforms):
                hits.setdefault(f"email {m.group(0)}", []).append(rel)
        for m in _PHONE_RE.finditer(text):
            if not is_placeholder_phone(m.group(0)):
                hits.setdefault(f"phone {m.group(0).strip()}", []).append(rel)
    return {k: sorted(set(v)) for k, v in hits.items()}


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


def read_baseline(path: Path) -> set[str]:
    """Accepted company terms, casefolded: one per line, `#` comments and blank lines ignored.
    A missing file accepts nothing, so the first run lists every term as new."""
    if not path.exists():
        return set()
    lines = (ln.strip() for ln in path.read_text(encoding="utf-8").splitlines())
    return {ln.casefold() for ln in lines if ln and not ln.startswith("#")}


def split_baselined(hits: dict[str, list[str]], company_terms: set[str], other_terms: set[str],
                    accepted: set[str]) -> tuple[dict[str, list[str]], list[str]]:
    """Drop the hits of accepted company terms. A term that is also a name, contact or job id
    (other_terms) stays reported whatever the baseline says."""
    kept: dict[str, list[str]] = {}
    skipped: list[str] = []
    for term, where in hits.items():
        if term in company_terms and term not in other_terms and term.casefold() in accepted:
            skipped.append(term)
        else:
            kept[term] = where
    return kept, sorted(skipped)


def baseline_arg(argv: list[str]) -> Path | None:
    if "--baseline" not in argv:
        return None
    i = argv.index("--baseline")
    if i + 1 >= len(argv) or argv[i + 1].startswith("--"):
        raise SystemExit("--baseline needs a file path")
    return Path(argv[i + 1]).expanduser()


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    baseline = baseline_arg(argv)
    with_companies = "--companies" in argv or baseline is not None
    strict = "--strict" in argv
    clients = [c for c in _client.list_clients() if c.slug not in EXAMPLE_SLUGS]
    if not clients and not strict:
        print(f"клієнтів не знайдено в {_client.clients_dir()} — нема з чим порівнювати")
        return 2
    files = [f for f in repo_files() if f.name not in SKIP_FILES]
    terms: set[str] = set()
    company_terms: set[str] = set()
    for c in clients:
        terms |= personal_terms(c)
        terms |= job_ids(c)
        if with_companies:
            # rules.py reads the active client's config; any client will do for parsing a tracker
            import os
            os.environ.setdefault(_client.ENV_CLIENT, c.slug)
            company_terms |= tracker_companies(c)
    other_terms = set(terms)
    terms |= company_terms
    hits = scan(terms, files) if terms else {}
    hits.update(structural_hits(files))
    skipped: list[str] = []
    if baseline is not None:
        hits, skipped = split_baselined(hits, company_terms, other_terms, read_baseline(baseline))
    for term in sorted(hits):
        print(f"{term}:")
        for where in hits[term]:
            print(f"    {where}")
    if not clients:
        print("клієнтів нема: лише структурна перевірка (email, телефони)")
    if baseline is not None:
        new = sorted(t for t in hits if t in company_terms)
        print(f"companies: {len(skipped)} term(s) accepted by {baseline}; "
              f"new: {', '.join(new) if new else 'none'}")
    print(f"перевірено {len(files)} файлів на {len(terms)} термінів "
          f"({len(clients)} клієнт(ів)): знайдено {len(hits)}")
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())
