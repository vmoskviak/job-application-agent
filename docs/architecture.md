# Architecture

## Engine and data apart

The code is public, people's data is private, so these are two repositories.
The engine finds the data through `JOBSEARCH_CLIENTS_DIR` (by default the
sibling directory `../jobsearch-clients/clients`). The client's data holds
everything about the person, the engine holds nothing.

Tokens and browser sessions live in neither repository: only on the machine
that runs, in `~/.gmail-mcp/<creds_dir>/` and `~/.playwright-mcp/<profile>/`.
`accounts.yaml` stores only the names of these directories.

## One process, one client

`bot/client.py` provides `active()`: the client selected by the
`JOBSEARCH_CLIENT` variable. `bot/__init__.py` takes `--client <slug>` out of
argv into this variable before the rest of the package is imported, so the
flag works in every entry point.

Modules that used to hold the data as constants (`bot/apply/candidate.py`,
`bot/generators/keywords.py`, `bot/filters/rules.py`, `cv_engine.py`,
`anschreiben_templates.py`, `tailor.py`) read the same constants from YAML at
import time. The constant names are kept, so the rest of the code and the
tests did not change.

The price of this decision: one process cannot handle two clients one after
another. For this project that is not a limitation, because there is one run
per client anyway (separate lock, separate browser profile, separate log). If
it is ever needed, the constants will have to be replaced by an explicit
context parameter.

## Data flow over a day

```
evening  search (Claude) → urls.txt → --discover (liveness, scoring)
         → bot.filters.rules (exclusions, location, dedup) → tailoring (Claude)
         → batch.json → digest by email
morning  job alerts from email → the same discover/filter
         → --batch-render (PDF/DOCX) → email applications
         → bot.apply.runner (portals) → employer replies
         → follow-ups → cleanup → report to the operator → git push of the data
hourly   (contract, phase 2a) inbox watcher: replies and platform mail → class
         → status in the tracker → card in Telegram with a draft (claude -p under a lock)
         → follow-ups by application age → git push of the data
bot      Telegram long polling: buttons → dispatcher → reply in the thread via gmail_rest
hourly   (employment market, telegram.mode: notify) the same watcher without tracker and drafts:
         interview/request → text to the operator in Telegram within the same hour; after the nightly
         run notify_run.py sends new interview/offer/action_needed from the tracker
```

Intermediate files live in `<client>/var/run/` with the date in the name and
rotate after 14 days. Into the data git go the tracker, `generate.py` of every
package, `email_draft.json`, `apply_result.json` and the confirmation
screenshots. PDF and DOCX are not committed: `generate.py` reproduces them
from the same data.

## Running the agent

`scripts/server/run_all.sh <mode>` walks the clients with `status: active` and
calls `run_agent.sh <slug> <mode>` for each:

1. takes the shared machine lock (the subscription limit and the display are
   shared by all);
2. pulls both repositories;
3. `scripts/render_run.py` renders the prompt from `docs/agents/*.md.j2` and
   the client's config, the MCP config for their mail and profile, settings
   with the hook;
4. starts `claude -p` with the working directory in the client folder;
5. after the run checks the Msg-IDs the agent wrote into the tracker and scans
   the mail for unclosed invitations; on failures sends an alert directly via
   Gmail REST.

For the contract client two systemd units live next to cron
(`scripts/server/systemd/`): the service `jobsearch-telegram@<slug>` (bot) and
the timer `jobsearch-inbox@<slug>` (hourly `scripts/inbox_watch.py`). Both
start through `scripts/server/run_service.sh`, which takes the machine
environment from the same file as `run_agent.sh`. The phase 2a state lives in
`<client>/var/state/`.

## Safeguards

| Risk | What is in place |
|---|---|
| Mass submission through a filter failure | `max_submissions_per_run`, the runner exits with code 3 |
| Repeated application | dedup against the tracker by company and title similarity; `apply_result.json` in the package |
| Deletion of an important email | hook on the Gmail MCP delete tools, `may_delete` per account |
| Invented statuses | Msg-ID validator after every run |
| Missed invitation | `employer_scan.py` over both mailboxes, alert to the operator |
| Cleanup on a broken tracker | `cleanup_min_tracker_rows`, limit of 15 deletions per run |
| Attachments promised but not sent | the "Anlagen" list is built from the same source as the attachments |
