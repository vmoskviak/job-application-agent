# Rules for working in this repository

This is the engine. Client data lives separately, in `JOBSEARCH_CLIENTS_DIR/<slug>/`
(locally that is the sibling repository `../jobsearch-clients/clients/`). The
rules of a specific person are written in the `CLAUDE.md` of their folder and
take priority over these.

## Client

- Every command works with one client: `JOBSEARCH_CLIENT=<slug>` or
  `--client <slug>`. Without it the engine stops with an error, and that is correct.
- From the client folder, commands run through the `bin/js` wrapper:
  `js new_app.py ...`, `js -m bot.filters.rules ...`, `js scripts/gmail_rest.py ...`.
- New person: `docs/onboarding.md`. Server: `docs/server_setup.md`.
- Nothing about real people gets into this repository: no names, no
  addresses, no names of companies they applied to. Tests and examples are
  written on the fictional `examples/clients/erika`. Before a commit: `scripts/check_no_private_data.py`.

## Mail

The standard scheme per client: `jobs` (a separate mailbox for the job search,
MCP server `gmail`) and `main` (personal, MCP server `gmail-main`).

- On an account with `may_delete: false` never delete anything: no trash, no
  archive. The hook `scripts/hooks/guard_gmail_delete.py` blocks it.
- Before any deletion on `jobs`, read the email via `read_email`, not just
  the subject. If the email contains a suitable job, do not delete it.
- `batch_delete_emails` is forbidden. Delete one at a time, after the report.
- New applications go out from the `send_from` account. From `main` only a
  reply to an employer in an existing thread is allowed.
- Eingangsbestätigung: on `jobs` it may go to the trash after the tracker is
  updated, on `main` the email stays.
- Search for alerts by label: `label:job-alerts`, without `in:inbox` and
  without a space in the label name.

## Applications

- Before generating, check `tracker.md`: whether an application to this
  company and role was already made.
- Do not bring the Anschreiben and CV text for approval. Generate PDF and DOCX right away.
- Take the CV variant from the track in `tracks.yaml`. An unknown variant is
  silently reduced to the default by the engine.
- Three application channels:
  1. **Email**: sent automatically after the package is generated.
  2. **Portals and supported ATS**: only through `bot.apply`
     (`js new_app.py --apply-batch ...`). Do not fill forms by hand through
     Playwright MCP. A new ATS is first written as a module in `bot/apply/`:
     dry-run, screenshot, then live.
  3. **LinkedIn Easy Apply and unsupported ATS**: the package plus a direct
     link to the operator.
- Companies from `email_dead_companies` do not accept applications by email,
  the channel is switched off for them automatically.
- The "Anlagen:" block in a letter without the email channel contains only
  "Lebenslauf": the portal sends only the CV and Anschreiben, and promised
  Zeugnisse would look forgotten.
- Do not clean application folders and `var/run` by hand, `js new_app.py --cleanup` does that.
- Write into the tracker only a Msg-ID that was really received from the mail.
  An invented Msg-ID is worse than an empty cell.

## Scraping

- Order: WebFetch, if the content is incomplete, then Playwright. StepStone,
  Interamt and corporate portals go through Playwright right away.
- `--discover` takes the client's Playwright profile under a lock. Do not run
  it in parallel with Playwright MCP on the same profile.
- Only jobs with `liveness == "live"` get into the digest.

## Code

- Tests: `.venv/bin/python -m pytest -q`. The `bot.*` modules read the client
  config at import time, so `tests/conftest.py` sets the client `erika` before the imports.
- Constants such as `candidate.EMAIL` or `rules.PRIORITY_ORDER` are filled
  from the client's YAML. A new personal value is added to the YAML and
  `templates/client/`, not to the code.
- Comments, docstrings, docs and config comments in English. Existing Ukrainian
  comments are translated when the surrounding code is touched.
