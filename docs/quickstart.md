# Quickstart: run the engine for yourself

This is the shortest path from a clone to a job search that runs on its own
every night. It is written for someone who was not involved in building the
engine. The Ukrainian docs (`docs/onboarding.md`, `docs/server_setup.md`) go
deeper; this page covers the minimum.

## What you need

Honest list. Without the first three nothing runs.

| Need | Why | Cost |
|---|---|---|
| A Linux box that is on at night (a small VM or LXC, Debian 12/13, 2 vCPU, 4 GB) | the runs are scheduled by cron and drive a headless browser | your own |
| A Claude subscription with Claude Code | the nightly runs are `claude -p` sessions that read mail, write cover letters and decide what to apply to | Pro or Max |
| Two Gmail mailboxes: one new one for the search (`you.jobs@gmail.com`) and the one that is on your CV | applications go out from the first, employers answer to the second | free |
| Perplexity API key | web search for new openings (optional, the job-alert emails still work without it) | pay per use |
| Firecrawl API key | fallback scraper for pages Playwright cannot read (optional) | free tier |
| Accounts on StepStone and XING linked to the jobs mailbox | the only portals with full automatic submission (optional) | free |
| A Telegram bot token | instant notifications about interviews, requests and failures (optional) | free |

The engine is built for the German market: documents, portal answers and
most matching are German. A different market means new templates and new
portal modules, not a config change.

## 1. Try it locally (10 minutes)

```bash
git clone https://github.com/vmoskviak/job-application-agent.git jobsearch-autopilot
cd jobsearch-autopilot
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/playwright install chromium
.venv/bin/python -m pytest -q                       # all offline, ~15 s

export JOBSEARCH_CLIENTS_DIR=$PWD/examples/clients
.venv/bin/python -m bot.onboard check --client erika --render
open examples/clients/erika/var/run/onboarding_sample/buero/   # sample CV + letter
```

Erika Mustermann is a fictional candidate. Everything below creates a real one.

## 2. Describe yourself (the config)

Candidates live outside the engine, in a folder the engine finds through
`JOBSEARCH_CLIENTS_DIR`. Keep that folder in a private git repository: the
server pulls it before every run and pushes the tracker back after.

```bash
mkdir -p ~/jobsearch-clients/clients && cd ~/jobsearch-clients && git init
export JOBSEARCH_CLIENTS_DIR=~/jobsearch-clients/clients
cd ~/jobsearch-autopilot && .venv/bin/python -m bot.onboard init me     # slug: lowercase, e.g. anna-k
```

`clients/me/` now holds commented templates. Seven files matter; the order is
the order to fill them in.

| File | What goes in | Where the "keywords" are |
|---|---|---|
| `profile.yaml` | name, address, phone, both e-mail addresses, nationality, salary, availability; `status: paused` until the rehearsal passes | |
| `candidate_brief.md` | free text: what you actually did, what you want, what must never be claimed. The cover letters are written from this | |
| `cv.yaml` | experience, education, skills, attachments (Zeugnisse as PDFs in `attachments/`) | |
| `tracks.yaml` | 3 to 6 tracks. A track is a group of role names with one CV variant and one letter skeleton | `keywords:` under each track: the role words that score a job for this track, as stems (`Dolmetsch`, not `Dolmetscherin`) |
| `targeting.yaml` | cities in reach (every spelling), role words that exclude a job, salary floor, per-run caps | `excluded_title_patterns:` and `description_bonus:` |
| `search.yaml` | what the evening run searches for | `query_groups:` with 2 to 4 search queries per track, plus career pages of local employers |
| `accounts.yaml` | the two mailboxes and the name of the browser profile; optional `telegram:` block | |

The fastest way to fill these is Claude Code itself: put your CV, Zeugnisse and
a paragraph about what you want into `clients/me/inbox/`, open `claude` in
`clients/me/` and ask it to fill the YAML from the documents following
`docs/onboarding.md`. Then:

```bash
.venv/bin/python -m bot.onboard check --client me --render
```

Zero errors means the engine can render your CV and a letter for every track.
Open the PDFs in `clients/me/var/run/onboarding_sample/` before going further;
this is what employers will receive.

## 3. Connect the accounts (on the server)

Install the engine on the box that will run at night. `scripts/server/install.sh`
does the apt packages, the venv, Playwright, Claude Code and the systemd units;
read it first, it is short.

```bash
git clone https://github.com/vmoskviak/job-application-agent.git /opt/jobsearch-autopilot
git clone <your private clients repo> /opt/jobsearch-clients
/opt/jobsearch-autopilot/scripts/server/install.sh
cp /opt/jobsearch-autopilot/scripts/server/claude-env.example /root/.claude-env && chmod 600 /root/.claude-env
```

Fill `/root/.claude-env`:

1. `CLAUDE_CODE_OAUTH_TOKEN`: run `claude setup-token` once and paste the token.
2. `JOBSEARCH_OPERATOR_EMAIL`: where reports and alerts go (you).
3. API keys if you have them.

Gmail: each mailbox gets its own token folder, named like `creds_dir` in
`accounts.yaml`. One OAuth client (Google Cloud → Desktop app, Gmail API
enabled, your addresses as test users) serves both mailboxes.

```bash
mkdir -p ~/.gmail-mcp/me-jobs && cp ~/Downloads/client_secret*.json ~/.gmail-mcp/me-jobs/gcp-oauth.keys.json
GMAIL_OAUTH_PATH=~/.gmail-mcp/me-jobs/gcp-oauth.keys.json GMAIL_CREDENTIALS_PATH=~/.gmail-mcp/me-jobs/credentials.json \
  npx -y @gongrzhe/server-gmail-autoauth-mcp auth          # log in as the JOBS mailbox
# repeat for me-main with the mailbox from your CV
JOBSEARCH_CLIENT=me /opt/jobsearch-autopilot/bin/js scripts/gmail_rest.py check jobs
JOBSEARCH_CLIENT=me /opt/jobsearch-autopilot/bin/js scripts/gmail_rest.py check main
```

On the jobs mailbox create a label `Job Alerts` and a filter that puts the
portal alert e-mails (StepStone, XING, LinkedIn, Indeed) under it. Subscribe to
alerts for the role names from your tracks. This is the cheapest and most
reliable source of openings.

Portals (optional): log in to StepStone and XING once in the server's browser
profile over VNC, see `docs/server_setup.md` → "Re-login". Without this the
engine still applies by e-mail and lists portal jobs for you to submit by hand.

## 4. Rehearse

```bash
cd /opt/jobsearch-autopilot
scripts/server/run_agent.sh me evening                       # discovery, nothing is sent
APPLY_MODE=dry-run scripts/server/run_agent.sh me morning    # drafts instead of sends, forms stop before submit
```

Read the log in `clients/me/var/logs/` and the digest in the jobs mailbox.
Wrong jobs mean the tracks or targeting need a word more or less; letters that
sound off mean the brief needs more truth. Repeat until a dry run looks like
something you would send yourself.

## 5. Switch it on

```bash
crontab /opt/jobsearch-autopilot/scripts/server/crontab.txt    # 20:30 discover, 03:00 apply, Europe/Berlin
```

Set `status: active` in `profile.yaml`, commit, push. From the next evening the
engine searches, writes, applies, reads replies and keeps `tracker.md` in your
clients repo. Every evening you get one summary e-mail. Pausing is `status:
paused`; cron is never edited.

## 6. Optional: Telegram instead of e-mail

Create a bot with BotFather, put the token into `/root/.claude-env` as
`JOBSEARCH_TELEGRAM_TOKEN_OPERATOR`, send the bot `/start`, and add to
`accounts.yaml`:

```yaml
telegram:
  token_env: JOBSEARCH_TELEGRAM_TOKEN_OPERATOR
  chat_id: <your chat id>
  mode: notify
  lang: en
```

Then `systemctl enable --now jobsearch-inbox@me.timer`. Interview invitations,
document requests and engine failures arrive within the hour; the daily
summary e-mail stops.

## Where things end up

| | |
|---|---|
| `clients/me/tracker.md` | every application with status, dates and the employer's reply |
| `clients/me/applications/<date>/<company>/` | the exact CV and letter that went out |
| `clients/me/var/logs/` | one log per run |
| `clients/me/var/run/` | intermediate files, rotated after 14 days |
