# Evening Discover Agent Prompt

**Trigger:** cron (server, `scripts/server/run_agent.sh <slug> evening`)
**Goal:** collect new job postings, filter them, prepare the tailoring and send the digest. **There are NO applications here; the morning agent applies.**

## Client of this run

- **Client:** `erika` (Erika Mustermann). All actions are for this person only.
- **Working directory:** `<ENGINE>/examples/clients/erika` (you are already in it). It holds `tracker.md`, `applications/`, `attachments/`, `var/run/`, `candidate_brief.md`, the `*.yaml` config.
- **Engine:** `<ENGINE>`. Every engine command runs through the `js` wrapper (it is already in PATH and knows the client): `js new_app.py ...`, `js -m bot.<module> ...`, `js scripts/<script>.py ...`.
- **Mail** (MCP servers of this run):
  - `gmail` = **erika.mustermann.jobs@example.com** (Sending applications, job alerts)
  - `gmail-main` = **erika.mustermann@example.com** (Reading + replies to employers in the thread), **deleting forbidden**
- **Operator**: ****. In this run do NOT send the operator any email at all: he gets one consolidated email per day, which the wrapper assembles. Everything for him is written to a file (see below).
- New applications go out from `erika.mustermann.jobs@example.com`; the CV and the Anschreiben carry `erika.mustermann@example.com`.
- Client-specific rules: `CLAUDE.md` in the working directory, if it exists. They take priority over the general ones.
- `run_agent.sh` has already done `git pull` of the engine and the data. Do not pull anything yourself.

## Boundaries (strict)

- **The run is non-interactive: there is nobody to ask questions.** There will be no answer to a clarification; the run simply ends there with no result. If a decision is needed, pick the option that matches this prompt and note it in the report.
- **Client status `active`.** `paused` on a manual run means an onboarding rehearsal (the operator started it deliberately): run all steps in full, including the digest to the jobs mailbox. Do not stop and do not propose a "dry rehearsal".
- Claude decides: the search queries, the Anschreiben texts (tailoring), the wording of the digest.
- **The language of all mail is German:** the digest, the subject, the captions in the HTML, and also any emails to the operator. Ukrainian only in the stdout log.
- Python decides: liveness, scoring, filters/dedup (`bot.filters.rules`), rendering.
- **No sending of applications, no deletion of mail** in this run.
- Excluded tracks and role titles are set in the client's `tracks.yaml` / `targeting.yaml`; the filter applies them itself.
- All intermediate files go to `var/run/` (gitignored), with the date in the name.
- **Work ONLY in a single thread: do NOT use Task/subagents/parallel agents** (a headless run is cut off before they finish). Execute the steps sequentially yourself.
- **No background commands.** Do not use `run_in_background` and do not end the turn with "waiting for completion": in a headless run the process exits at that point and the command dies. Run long commands (`--discover`, `bot.apply.runner`, the StepStone scan) as a normal call with a `timeout` of up to 3600000 ms and wait for the output.
- The last line of the final message MUST be the marker `EVENING OK: ...` (see Step 8).

## Step 0: Pre-flight

```bash
js -m bot.scrapers.profile
```
If `LOGIN REQUIRED` → do not send an email. Print the line `LOGIN REQUIRED: Playwright-Login nötig` to stdout and stop without the `EVENING OK` marker: the wrapper itself notifies the operator in the consolidated email.

## Step 1: Search (4 queries)

**Tool: `mcp__perplexity__perplexity_search` with `country: "DE"`, not WebSearch.** WebSearch mostly returns aggregator listings and stale LinkedIn links; Perplexity sees German sources and returns fresher direct links. If the server does not respond, fall back to WebSearch and mention this in the digest.

Main target: Office/Assistenz; Sachbearbeitung as a fallback for volume.

Collect only direct job links (`linkedin.com/jobs/view/`, `xing.com/jobs/`, StepStone, Indeed, Interamt, career portals).

### Office/Assistenz (active target)
1. `"Teamassistenz" OR "Office Manager" OR "Assistenz Geschäftsführung" Düsseldorf 2026`
2. `"Office Manager" OR "Teamassistenz" Düsseldorf site:linkedin.com/jobs 2026`
3. `"Assistenz" OR "Sekretariat" Düsseldorf Köln site:xing.com/jobs 2026`

### Sachbearbeitung (fallback)
4. `"Sachbearbeitung" OR "Sachbearbeiter Verwaltung" NRW 2026`

**Do NOT search (excluded by the filter, would land in the digest for nothing):** Trainee, Senior/Lead, Vertrieb, Entwickler.


## Step 2: WebFetch of career pages

Search words: Assistenz / Office Management / Sekretariat

| # | Source | URL |
|---|---|---|
| 1 | Düsseldorf Stadt | `https://karriere.duesseldorf.de/` |
| 2 | Interamt (öD) | `https://interamt.de/koop/app/trefferliste` |

If WebFetch returned an SPA shell, use `mcp__firecrawl__firecrawl_scrape`, not Playwright: the profile is needed by the intake in Step 4.


## Step 2b: StepStone Berufsfeld facet (Python, not WebSearch)

```bash
DISPLAY=:99 js scripts/stepstone_facet_scan.py --max-pages 3 --json
```
Reads the StepStone results page for the facet from `search.yaml`. **No email alert sees**
these positions: StepStone keeps only what/where/radius in the Job-E-Mail, the facets
are lost.

The script itself deduplicates against `var/state/stepstone_facet_seen.json` and appends the new
URLs to `var/run/urls_facet_<date>.txt`. Add these URLs to the list from Step 3 (the same
`discover` accepts them, no separate handling needed). If `new: 0`, just move on.

## Step 2c: Indeed via firecrawl (the Python fetch cannot get it)

Indeed returns **403** to httpx, while firecrawl with the stealth proxy returns the full results
page. Hit `mcp__firecrawl__firecrawl_scrape` on the listings:

```
https://de.indeed.com/jobs?q=Assistenz+OR+Office+Manager&l=Düsseldorf&radius=50
```

From the markdown extract `jk=<16 hex>` and build direct URLs of the form
`https://de.indeed.com/viewjob?jk=<id>`. Ignore pagead redirects from emails:
they do not resolve.

## Step 3: Collect URLs

Deduplicate, write to `var/run/urls_YYYY-MM-DD.txt` (one URL per line).
Add here also the lines from `var/run/urls_facet_YYYY-MM-DD.txt` (Step 2b), if the file exists.

## Step 4: Discover (Python)

```bash
js new_app.py --discover var/run/urls_YYYY-MM-DD.txt
```
The digest is written next to it: `var/run/discover_digest_YYYY-MM-DD.json` (live only).

## Step 5: Filters (Python, NOT by hand)

```bash
js -m bot.filters.rules var/run/discover_digest_YYYY-MM-DD.json \
    -o var/run/filtered_YYYY-MM-DD.json
```
Do not override the filter's decisions. The only exception: if there is obvious junk among selected (an irrelevant industry with a formally high score), remove it from the batch and note it in the digest with the reason.

## Step 6: Tailoring (Claude)

For each selected: compose the `TailoredData` JSON (templates: `bot/generators/anschreiben_templates.py` by category; rules: `candidate_brief.md`, forbidden phrases in `bot/generators/tailor.py` CANDIDATE_PROFILE). Collect the array `[{job, tailored}]` in `var/run/batch_YYYY-MM-DD.json`. **Do NOT render** (that is the morning agent).

If the posting names an addressee (Ansprechpartner, "Bewerbungen an Frau/Herrn …"), start `anschreiben` with a separate paragraph "Sehr geehrte Frau …," or "Sehr geehrter Herr …,": the engine takes this line as the Anrede of the letter and the e-mail; without it, it puts "Sehr geehrte Damen und Herren,". `job.company` must be the real company name, not "Unbekannt": for a staffing agency write the agency's name, for a company site the name from the posting text.

**`experience_variant` / `skills_order`: take them from `VARIANT_BY_CATEGORY` (`bot/generators/keywords.py`), do not invent.** Allowed: `agile` · `buero` · `sprache` · `sachbearbeitung` (`ba` and `trainee` are excluded tracks). Category `B8_dolmetschen` → **`sprache`**: its own bullets across all employers, the Sprachnachweise line and the Ehrenamt section. The engine silently drops an unknown string to `agile`, which means a language job would go out with a Scrum CV.

## Step 7: Digest email

`mcp__gmail__send_email` → **erika.mustermann.jobs@example.com** (NOT `mcp__claude_ai_Gmail__*`).
Subject: `[Job Digest] YYYY-MM-DD: N Stellen zur Bewerbung` (in German, no dashes).
Body in German: (1) selected with the channel (`email` / `stepstone_auto` / `xing_auto` / `manual_link`), company, role, score, link; (2) excluded with reasons (briefly, in groups); (3) intake statistics.

Send as **HTML** (`mimeType: "multipart/alternative"`, `body` is the plain-text fallback) following the template from `morning_apply_agent.md` Step 9: header (blue `#0969da` instead of green, title "Job Digest"), all captions in German (Ausgewählt / Aussortiert / Geprüft / Geschlossen, Kanal, Gründe), metrics selected/excluded/live/closed, selected as rows with a channel badge and a clickable link, excluded collapsed into reason groups in small grey font.

## Step 7b: Note for the operator's consolidated email

The operator gets one email per day; the wrapper assembles it after this run. Do NOT send him an email. Write a short HTML fragment in German (inline CSS, no outer frame and no header) to the file `var/run/operator_evening_YYYY-MM-DD.html`:
- one line with the numbers: geprüft / ausgewählt / aussortiert;
- the list selected for the night: Firma, Stelle, Kanal (`E-Mail` / `StepStone` / `XING` / `manuell`);
- problems of this run, if any (portal session, scan crashed, a source did not respond), in a yellow block `border-left:4px solid #d4a72c;background:#fff8c5`.
Do not render empty sections. Do not duplicate the exclusion reasons and the intake statistics here; they are in the digest in the jobs mailbox.

## Step 8: Finish

Do NOT do `git add var/ -f` (var/ is gitignored). Finish log: a summary line in stdout (`EVENING OK: selected=N excluded=M`).
