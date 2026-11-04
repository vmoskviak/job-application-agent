"""Synthetic Upwork "New job alert" mails for the stage 2e tests.

The layout copies the plain-text part of a real alert as Gmail returns it (29.10.2026): a
preheader padded with U+034F and U+00AD, every link a long click-tracking URL in "( URL )",
line breaks in the middle of phrases, the job block only after the header links. All content
(title, description, numbers, names) is invented.
"""
from __future__ import annotations

SENDER = "upwork@t.upwork.com"
TITLE = "Example Fitness Companion App"
SUBJECT = f"New job alert: {TITLE}"
SNIPPET = ("We are looking for an experienced mobile engineer for a focused enhancement phase on an existing product.\n"
           "The scope will include improving personalization and daily-routine ...")
FIXED_BUDGET = "Fixed\nprice:\n$2,000"
META = (" • 3 to 6 months", "    Payment verified", " • 5.0", " • $2k\nspent", " • PRT")
TAGS = ("Full-Stack Development", "iOS", "Android", "Health & Wellness")
_FILLER_LINE = "\u034f \u00ad " * 12


def track(n: int, length: int = 900) -> str:
    """A click-tracking URL of about `length` characters, like every link in the real mail."""
    base = f"https://link.example.com/ls/click?upn=u001.TOKEN{n:02d}-"
    pad = ("Q2xpY2tUb2tlbkV4YW1wbGU" * (length // 20 + 1))[:max(0, length - len(base))]
    return base + pad


def job_block(*, title: str = TITLE, snippet: str = SNIPPET, budget: str = FIXED_BUDGET,
              meta: tuple[str, ...] = META, tags: tuple[str, ...] = TAGS, first_link: int = 7,
              ready_line: str = " Take a look and submit\na proposal when you’re ready.") -> str:
    """The job part: title with its link, snippet with "more", budget, client signals, tags,
    "Posted", "View job details". Links are numbered from `first_link` like in the real mail."""
    n = first_link
    lines = [ready_line, title, f"( {track(n)} )", ""]
    if snippet:
        lines += [snippet, f"more ( {track(n + 1)}", ")", ""]
    if budget:
        lines += [budget, ""]
    for m in meta:
        lines += [m, ""]
    link = n + 2
    for tag in tags:
        lines += [tag, f"( {track(link)} )", ""]
        link += 1
    lines += [" Posted recently.", "", "View job details (", f"{track(n + 6)} )"]
    return "\n".join(lines)


def alert_body(*blocks: str, saved_search: str = "android kotlin", name: str = "Jordan") -> str:
    """The whole plain-text part: preheader, header links, greeting, job block(s), footer."""
    header = [
        "This job matches your", "saved", "search:", f"{saved_search}.", "",
        "      \u034f \u00ad \u034f \u00ad \u034f \u00ad \u034f \u00ad \u034f",
        *([_FILLER_LINE] * 9),
        f"{_FILLER_LINE}Find Work", f"( {track(1)} )",
        "Freelancer", f"Plus ( {track(2)} )",
        "Ad Products", f"( {track(3)} )",
        "Connect via MCP", f"( {track(4)} )",
        " Log In", f"( {track(5)} )",
        f"( {track(6)} ) New job alert",
        "Hi", f"{name},", "",
        " This job matches your saved", f"search: {saved_search}.",
    ]
    footer = [
        "Mobile app Download Upwork App for iPhone", f"( {track(14)} ) Download Upwork App",
        "for Android", f"( {track(15)} )",
        "             Follow us Facebook", f"( {track(16)} ) Linkedin", f"( {track(17)} )",
        "© 2015 -", "2026 Upwork® Global LLC You",
        "received this email because Freelancer Plus members have access",
        "to job alerts. If you don’t want to get email alerts, you can",
        "change your alert settings", f"( {track(18)} ).",
    ]
    return "\n".join([*header, *(blocks or (job_block(),)), *footer])


# ── Freelancer Plus layout (donotreply@upwork.com, first seen 04.11.2026) ────

PLUS_SENDER = "donotreply@upwork.com"
PLUS_JOB_ID = "~021234567890123456789"
PLUS_TITLE = "Reusable Mobile SDK for a Partner Integration Pl..."
PLUS_SUBJECT = "New job alert: Reusable Mobile SDK for a Partner Integration Pl..."


def plus_url(kind: str, n: int) -> str:
    """A direct Upwork link with the tracking query the real mail carries."""
    return (f"https://www.upwork.com/jobs/{PLUS_JOB_ID}?link={kind}&n=null&utm_medium=email"
            f"&utm_source=mailgun&email_uid=1000000000000000000&frkscc=TOKEN{n:02d}")


def skill_url(n: int) -> str:
    return f"https://www.upwork.com/s/emails/nx/search/jobs/?utm_medium=email&email_uid=1&frkscc=SKILL{n:02d}"


def plus_alert_body(*, title: str = PLUS_TITLE, budget: str = "Fixed: $3,500.00",
                    est: str = "Est. time: More than 6 months",
                    snippet: str = "We need an engineer to package our product features as an SDK that partner apps can embed. Mobile experie...",
                    skills: tuple[str, ...] = ("SDK", "Android", "iOS"),
                    client: str = "Payment verified · 4.8 stars · $9K spent · Portugal",
                    views: int = 1, name: str = "Jordan") -> str:
    """The plain-text part of a Freelancer Plus alert as Gmail returns it: "text: URL" links,
    "·" between fields, "----------" around the job. All content is invented."""
    lines = ["", "", "New job alert", "", f"Hi {name},", "",
             "This job was just posted and matches your alert settings.", "", "----------", ""]
    if title:
        lines += [f"{title}: {plus_url('title', 1)}", ""]
    lines += [f"  {' · '.join(x for x in (budget, est, 'Posted Wed, Nov 04') if x)}", ""]
    if snippet:
        lines += [f"{snippet} more: {plus_url('more', 2)}", ""]
    if skills:
        lines += ["Skills:", *(f"{s}: {skill_url(i)}" for i, s in enumerate(skills)), ""]
    if client:
        lines += [client, ""]
    lines += ["    ----------", ""]
    lines += [f"View job details: https://www.upwork.com/jobs/{PLUS_JOB_ID}?frkscc=VIEW{i}" for i in range(views)]
    footer = ("You received this email because Freelancer Plus members have access to job alerts. "
              "If you no longer want to get these alerts via email, you can manage your alert preferences: "
              "https://www.upwork.com/ab/notifications/?tab=job_alerts&settings=true&frkscc=FOOT.")
    lines += ["", footer, ""]
    return "\n".join(lines)
