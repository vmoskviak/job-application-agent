#!/usr/bin/env python3
"""Employer pages for job ads a board hides (Indeed's captcha): the morning run's Step 3.

    js scripts/resolve_employer.py var/run/indeed_leads_YYYY-MM-DD.json --append var/run/urls_alerts_YYYY-MM-DD.txt

The input is a JSON list of {"url", "company", "title", "city"} written from the alert e-mails. For every
lead bot.apply.employer_lookup.find_posting looks for this job on the employer's own site or ATS; a found
page is appended to the urls file (once), so discover → filter → tailoring → queue treat it like any
employer posting. Prints one JSON line {"resolved": [...], "unresolved": [...]} and writes the same next to
the input as <input>.resolved.json; the unresolved ones belong to the manual list of the report.
Without PERPLEXITY_API_KEY nothing resolves and every lead is listed as unresolved.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ENGINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE))

from bot.apply.employer_lookup import find_posting


def resolve(leads: list[dict], urls_file: Path | None, lookup=find_posting) -> dict:
    present = set(urls_file.read_text(encoding="utf-8").split()) if urls_file and urls_file.exists() else set()
    resolved, unresolved, new = [], [], []
    for lead in leads:
        company, title = str(lead.get("company") or ""), str(lead.get("title") or "")
        posting = lookup(company, title, str(lead.get("city") or "")) if company and title else None
        row = {"url": lead.get("url", ""), "company": company, "title": title}
        if posting is None:
            unresolved.append(row)
            continue
        resolved.append({**row, "employer_url": posting.url, "email": posting.email, "source": posting.source})
        if posting.url not in present:
            present.add(posting.url)
            new.append(posting.url)
    if urls_file and new:
        with urls_file.open("a", encoding="utf-8") as f:
            for url in new:
                f.write(url + "\n")
    return {"resolved": resolved, "unresolved": unresolved, "appended": len(new)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("leads", type=Path, help="JSON list of {url, company, title, city}")
    ap.add_argument("--append", type=Path, default=None, help="urls file of this morning's discover")
    args = ap.parse_args()
    leads = json.loads(args.leads.read_text(encoding="utf-8"))
    out = resolve(list(leads), args.append)
    args.leads.with_suffix(".resolved.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
