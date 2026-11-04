"""Upwork "New job alert" mail → one job record (contract phase 2e).

The server never opens Upwork (operator decision, 29.10.2026): everything the card and the
proposal draft know comes from this mail. Gmail returns the plain-text part with every link
as "( <tracking URL> )", line breaks in the middle of phrases ("Fixed\\nprice:\\n$2,000") and a
preheader padded with invisible characters. Lines are grouped into paragraphs at blank lines
and joined with spaces, which undoes the broken phrases; the job sits between the title link
and the single "View job details" link: snippet ending in "more ( URL )", budget, client
signals, tags ("Name ( URL )", the first one is the category), "Posted …".

A second layout (Freelancer Plus alerts from donotreply@upwork.com, first seen 04.11.2026, "This
job was just posted and matches your alert settings") writes every link as "text: URL" with a
direct www.upwork.com/jobs/~0… address, separates fields with "·" and has no saved search:
title link, "Fixed: $5,000.00 · Est. time: … · Posted …", snippet ending in "more: URL",
"Skills:" with one link per skill, "Payment verified · 4.95 stars · $11K spent · <country>",
"View job details: URL". parse_alert picks the layout by the form of "View job details".

The parser fails closed: a mail without the anchors it knows (subject prefix, exactly one
"View job details", a title followed by its link) raises UnknownFormat instead of guessing,
so the unseen "Jobs picked for you" digest (several jobs per mail) never becomes a card with
mixed-up fields. Optional fields it does not recognise stay empty. No client config at import.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from bot.textclean import strip_invisible

SUBJECT_PREFIX = "new job alert:"
_LINK = r"\(\s*(https?://[^\s()]+)\s*\)"
_VIEW_RE = re.compile(r"View job details\s*" + _LINK, re.IGNORECASE)
_READY_RE = re.compile(r"submit a proposal when you(?:'|’| a)re ready\.\s*([^\n]+?)\s*" + _LINK,
                       re.IGNORECASE)
_SAVED_RE = re.compile(r"matches your saved search:\s*([^\n]+?)\.(?=\s|$)", re.IGNORECASE)
_MORE_RE = re.compile(r"\s*\bmore\s*" + _LINK + r"\s*$", re.IGNORECASE)
_TAG_RE = re.compile(r"^([^•$\n]{1,80}?)\s*" + _LINK + r"$")
_BUDGET_RE = re.compile(r"^(?:fixed[\s-]*price|hourly)\b", re.IGNORECASE)
_AMOUNT_RE = re.compile(r"\$\s?(\d[\d,]*(?:\.\d+)?)\s*([kKmM])?(?![a-zA-Z])")
_DURATION_RE = re.compile(r"\b(?:(?:less|more) than (?:a|\d+)|\d+ to \d+) months?\b", re.IGNORECASE)
_WORKLOAD_RE = re.compile(r"\b(?:(?:less|more) than \d+ hrs/week|hours to be determined)\b", re.IGNORECASE)
_PAYMENT_RE = re.compile(r"\bpayment\s+(verified|unverified|not\s+verified)\b", re.IGNORECASE)
_RATING_RE = re.compile(r"^[0-5](?:\.\d{1,2})?$")
_SPENT_RE = re.compile(r"(\$\s?\d[\d,.]*\s*[kKmM]?\+?)\s+spent\b", re.IGNORECASE)
_COUNTRY_RE = re.compile(r"^[A-Z]{2,3}$")
_POSTED_RE = re.compile(r"^posted\b", re.IGNORECASE)
_TITLE_MAX = 300
_UNITS = {"k": 1_000, "m": 1_000_000}

# Freelancer Plus layout: "text: URL" links, "·" between fields
_PLUS_URL = r"(https?://\S+)"
_PLUS_VIEW_RE = re.compile(r"View job details:\s*" + _PLUS_URL, re.IGNORECASE)
_PLUS_TITLE_RE = re.compile(r"^(.+?):\s*(https?://\S*/jobs/~0\S*)$")
_PLUS_MORE_RE = re.compile(r"\s*\bmore:\s*https?://\S+$", re.IGNORECASE)
_PLUS_SKILL_RE = re.compile(r"\s*([^:]+?):\s*https?://\S+")
_PLUS_BUDGET_RE = re.compile(r"^(?:fixed(?:[\s-]*price)?|hourly)\s*:", re.IGNORECASE)
_PLUS_EST_RE = re.compile(r"^est\.?\s*time:\s*(.+)$", re.IGNORECASE)
_STARS_RE = re.compile(r"^([0-5](?:\.\d{1,2})?)\s+stars?$", re.IGNORECASE)
_PLACE_RE = re.compile(r"^[A-Za-z][A-Za-z .'()-]{1,40}$")
_SEPARATOR_RE = re.compile(r"^-{3,}$")

BudgetType = Literal["fixed", "hourly", "unknown"]


class UnknownFormat(ValueError):
    """The mail is not the one-job alert this parser knows; nothing in it is trusted."""


@dataclass
class UpworkJob:
    message_id: str = ""
    date: str = ""                   # the mail's Date header
    title: str = ""
    snippet: str = ""                # the description excerpt Upwork cut with "..."
    budget_type: BudgetType = "unknown"
    budget_min: float | None = None  # USD; fixed price: min == max
    budget_max: float | None = None
    duration: str = ""               # "3 to 6 months", plus the weekly workload of hourly jobs
    payment_verified: bool | None = None   # None: the mail says neither verified nor unverified
    rating: float | None = None
    spent: str = ""                  # as shown: "$2k", "$10K+"
    country: str = ""                # as shown: a 3-letter code such as "PRT"
    category: str = ""
    skills: list[str] = field(default_factory=list)
    saved_search: str = ""
    job_link: str = ""               # the "View job details" tracking URL, never resolved by the server


def _paragraphs(body: str) -> list[str]:
    """Non-empty lines grouped at blank lines, each group joined with single spaces."""
    paras: list[str] = []
    cur: list[str] = []
    for raw in strip_invisible(body).splitlines():
        line = " ".join(raw.split())
        if line:
            cur.append(line)
        elif cur:
            paras.append(" ".join(cur))
            cur = []
    if cur:
        paras.append(" ".join(cur))
    return paras


def _title(flat: str, subject_title: str) -> tuple[str, int]:
    """The title and the end of its link: after "submit a proposal when you're ready.",
    else the subject's title found in the body right before a link."""
    m = _READY_RE.search(flat)
    if m and m.group(1).strip():
        return m.group(1).strip(), m.end()
    if subject_title:
        words = r"\s+".join(re.escape(w) for w in subject_title.split())
        m = re.search(words + r"\s*" + _LINK, flat)
        if m:
            return subject_title, m.end()
    raise UnknownFormat("job title with its link not found")


def _meta_start(line: str) -> bool:
    return bool(_BUDGET_RE.match(line) or line.startswith("•") or _PAYMENT_RE.match(line)
                or _TAG_RE.match(line) or _POSTED_RE.match(line))


def _snippet(block: list[str]) -> tuple[str, list[str]]:
    """The description excerpt and the lines after it. The excerpt ends with "more ( URL )";
    a short description without that link ends at the first budget, signal or tag line."""
    for i, line in enumerate(block):
        m = _MORE_RE.search(line)
        if m:
            parts = [*block[:i], line[:m.start()]]
            return " ".join(p for p in parts if p).strip(), block[i + 1:]
    for i, line in enumerate(block):
        if _meta_start(line):
            return " ".join(block[:i]).strip(), block[i:]
    return " ".join(block).strip(), []


def _usd(number: str, unit: str) -> float:
    return float(number.replace(",", "")) * _UNITS.get(unit.lower(), 1)


def _set_budget(job: UpworkJob, seg: str) -> None:
    """"Fixed price: $2,000", "Hourly: $40-$80" → budget type and amounts."""
    job.budget_type = "fixed" if seg.lower().startswith("fixed") else "hourly"
    amounts = [_usd(n, u) for n, u in _AMOUNT_RE.findall(seg)]
    # "Hourly: $0-$0" is how the mail shows a job without a budget: unknown, not zero.
    if any(amounts):
        job.budget_min = amounts[0]
        job.budget_max = amounts[0] if job.budget_type == "fixed" else amounts[min(1, len(amounts) - 1)]


def _durations(seg: str) -> list[str]:
    return [m.group(0) for m in (_DURATION_RE.search(seg), _WORKLOAD_RE.search(seg)) if m]


def _apply_meta(job: UpworkJob, lines: list[str]) -> None:
    """Budget and client signals; each segment between bullets is matched on its own."""
    durations: list[str] = []
    for seg in (s.strip() for line in lines for s in line.split("•")):
        if not seg:
            continue
        if _BUDGET_RE.match(seg) and job.budget_type == "unknown":
            _set_budget(job, seg)
            continue
        durations += _durations(seg)
        pay = _PAYMENT_RE.search(seg)
        if pay:
            job.payment_verified = pay.group(1).lower() == "verified"
        spent = _SPENT_RE.search(seg)
        if spent:
            job.spent = spent.group(1).replace(" ", "")
        if _RATING_RE.match(seg):
            job.rating = float(seg)
        elif _COUNTRY_RE.match(seg):
            job.country = seg
    job.duration = ", ".join(dict.fromkeys(durations))


def parse_alert(subject: str, body: str, message_id: str = "", date: str = "") -> UpworkJob:
    """One "New job alert" mail → UpworkJob; UnknownFormat when the anchors are missing."""
    subj = " ".join(strip_invisible(subject).split())
    if not subj.lower().startswith(SUBJECT_PREFIX):
        raise UnknownFormat(f"subject is not 'New job alert: ...': {subj[:80]!r}")
    flat = "\n".join(_paragraphs(body))
    views = list(_VIEW_RE.finditer(flat))
    if not views and _PLUS_VIEW_RE.search(flat):
        return _parse_plus(flat, subj[len(SUBJECT_PREFIX):].strip(), message_id, date)
    if len(views) != 1:
        raise UnknownFormat(f"{len(views)} 'View job details' links, expected 1")
    title, title_end = _title(flat, subj[len(SUBJECT_PREFIX):].strip())
    if len(title) > _TITLE_MAX or title_end > views[0].start():
        raise UnknownFormat("job title not where the alert puts it")
    job = UpworkJob(message_id=message_id, date=date, title=title, job_link=views[0].group(1))
    saved = _SAVED_RE.search(flat)
    if saved:
        job.saved_search = saved.group(1).strip()
    block = [ln for ln in flat[title_end:views[0].start()].split("\n") if ln.strip()]
    job.snippet, rest = _snippet([ln.strip() for ln in block])
    meta: list[str] = []
    tags: list[str] = []
    for line in rest:
        if _POSTED_RE.match(line):
            break
        tag = _TAG_RE.match(line)
        if tag:
            tags.append(tag.group(1).strip())
        elif not tags:   # client signals come before the tags; free text after them is ignored
            meta.append(line)
    _apply_meta(job, meta)
    if tags:
        job.category, job.skills = tags[0], tags[1:]
    return job


def _plus_client(job: UpworkJob, para: str) -> None:
    """"Payment verified · 4.95 stars · $11K spent · USA": the country is the one segment left."""
    for seg in (x.strip() for x in para.split("·")):
        pay = _PAYMENT_RE.search(seg)
        stars = _STARS_RE.match(seg)
        spent = _SPENT_RE.search(seg)
        if pay:
            job.payment_verified = pay.group(1).lower() == "verified"
        elif stars:
            job.rating = float(stars.group(1))
        elif spent:
            job.spent = spent.group(1).replace(" ", "")
        elif _PLACE_RE.match(seg) and not job.country:
            job.country = seg


def _parse_plus(flat: str, subject_title: str, message_id: str, date: str) -> UpworkJob:
    """The Freelancer Plus layout (see the module docstring); same fail-closed anchors."""
    views = list(_PLUS_VIEW_RE.finditer(flat))
    if len(views) != 1:
        raise UnknownFormat(f"{len(views)} 'View job details' links, expected 1")
    paras = [p.strip() for p in flat[:views[0].start()].split("\n") if p.strip()]
    stem = _norm(subject_title.rstrip(".").removesuffix("…"))[:40]

    def is_title(m: re.Match) -> bool:
        # the title link carries link=title; without that mark only text that starts like the
        # subject's (truncated) title counts, so the "more: URL" snippet never becomes the title
        if "link=title" in m.group(2):
            return True
        return bool(stem) and not _PLUS_MORE_RE.search(m.group(0)) and _norm(m.group(1)).startswith(stem[:20])

    found = next(((i, m) for i, m in ((i, _PLUS_TITLE_RE.match(p)) for i, p in enumerate(paras))
                  if m and is_title(m)), None)
    if found is None:
        raise UnknownFormat("job title with its link not found")
    i, m = found
    title = m.group(1).strip()
    if len(title) > _TITLE_MAX:
        raise UnknownFormat("job title not where the alert puts it")
    job = UpworkJob(message_id=message_id, date=date, title=title, job_link=views[0].group(1))
    durations: list[str] = []
    rest: list[str] = []
    for para in paras[i + 1:]:
        if _SEPARATOR_RE.match(para):
            continue
        if _PLUS_BUDGET_RE.match(para):
            for seg in (x.strip() for x in para.split("·")):
                if _PLUS_BUDGET_RE.match(seg):
                    _set_budget(job, seg)
                elif not _POSTED_RE.match(seg):
                    found = _durations(seg)
                    est = _PLUS_EST_RE.match(seg)
                    durations += found or ([est.group(1).strip()] if est else [])
        elif para.lower().startswith("skills:"):
            job.skills = [s.strip() for s in _PLUS_SKILL_RE.findall(para[len("skills:"):]) if s.strip()]
        elif _PAYMENT_RE.search(para) or _SPENT_RE.search(para):
            _plus_client(job, para)
        elif _PLUS_MORE_RE.search(para):
            job.snippet = _PLUS_MORE_RE.sub("", para).strip()
        else:
            rest.append(para)
    if not job.snippet and rest:   # a short description has no "more" link
        job.snippet = rest[0]
    job.duration = ", ".join(dict.fromkeys(durations))
    return job


def _amount(x: float | None) -> str:
    return "" if x is None else f"{x:g}"


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[\W_]+", " ", text.casefold()).split())


def fingerprint(job: UpworkJob) -> str:
    """The same job from two saved searches (two mails): normalised title, budget, country and
    the start of the description excerpt, which is identical in both mails. Without the excerpt
    two different "Android Developer" jobs with the same budget and country would merge."""
    budget = f"{job.budget_type}:{_amount(job.budget_min)}-{_amount(job.budget_max)}"
    return f"{_norm(job.title)}|{budget}|{job.country.upper()}|{_norm(job.snippet)[:80]}"


def money(x: float) -> str:
    """$2,000 / $40 / $40.50."""
    return f"${x:,.0f}" if float(x).is_integer() else f"${x:,.2f}"


def rating_text(rating: float) -> str:
    """5.0 / 4.9 / 4.95, the way Upwork shows a client rating."""
    text = f"{rating:.2f}".rstrip("0")
    return text + "0" if text.endswith(".") else text


def budget_text(job: UpworkJob) -> str:
    """Language-neutral budget for the state file, the prompt and tracker notes:
    "fixed $2,000", "hourly $40-$80", "hourly", "unknown"."""
    lo, hi = job.budget_min, job.budget_max
    if job.budget_type == "fixed":
        return f"fixed {money(hi)}" if hi is not None else "fixed"
    if job.budget_type == "hourly":
        if lo is not None and hi is not None and lo != hi:
            return f"hourly {money(lo)}-{money(hi)}"
        return f"hourly {money(hi)}" if hi is not None else "hourly"
    return "unknown"
