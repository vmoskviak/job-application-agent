# Server: install and runbook

Written for Debian 13 in an LXC container (4 vCPU, 8 GB RAM, 32 GB disk),
everything as root. Time zone `Europe/Berlin`.

## Install

Short English version for an outside user: `docs/quickstart.md`;
`scripts/server/install.sh` does packages, venv, Playwright, Claude Code and units.

```bash
apt install git curl gnupg jq locales cron rsync openssh-server \
  fonts-dejavu fonts-liberation2 libpango-1.0-0 libpangocairo-1.0-0 libcairo2 \
  libgdk-pixbuf-2.0-0 libffi-dev python3 python3-venv python3-pip xvfb x11vnc
# Node 22 (NodeSource), then:
npm install -g @anthropic-ai/claude-code

git clone git@github.com:<user>/jobsearch-autopilot.git /opt/jobsearch-autopilot
git clone git@github.com:<user>/jobsearch-clients.git  /opt/jobsearch-clients
cd /opt/jobsearch-autopilot
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/playwright install --with-deps chromium
```

The server both reads and writes the data repository (the tracker after every
morning run), so its deploy key needs write access. Read access is enough for the engine.

## Machine config: `/root/.claude-env` (chmod 600, outside git)

```bash
CLAUDE_CODE_OAUTH_TOKEN=sk-ant-oat01-...
JOBSEARCH_CLIENTS_DIR=/opt/jobsearch-clients/clients
JOBSEARCH_OPERATOR_EMAIL=operator@example.com   # reports, interviews
JOBSEARCH_ALERT_TO=operator@example.com         # failure alerts
JOBSEARCH_OPERATOR_MAIL=evening                 # evening: one summary mail after the evening run, nothing at night; always: a mail after every run
PERPLEXITY_API_KEY=...
FIRECRAWL_API_KEY=...
JOBSEARCH_VNC_HINT='vnc://localhost:5901 (first ssh -f -N -L 5901:127.0.0.1:5900 <server>)'   # where to look: re-login, worker hand-off cards
# CLAUDE_MODEL=opus
# JOBSEARCH_TELEGRAM_TOKEN_<SLUG>=...   the client's Telegram bot token (stage 2a); chat_id in accounts.yaml
# JOBSEARCH_LOCK_WAIT=900               how long run_agent.sh waits for the machine lock, s
# JOBSEARCH_INBOX_DRY_RUN=1             shadow week of the inbox watcher: no tracker, no mail
```

`run_agent.sh` reads this file itself. Without `FIRECRAWL_API_KEY` the fallback
scraping path is silently disabled, the rest works.

## Claude auth

`claude setup-token` prints a long-lived token to the terminal and creates no file.
Run it in a wide tmux pane, otherwise the token gets cut by a line wrap:

```bash
tmux new-session -d -s token -x 400 -y 50 "claude setup-token; sleep 600"
tmux capture-pane -t token -p -J        # URL for authorising in the browser
tmux send-keys -t token "<code>" Enter
tmux capture-pane -t token -p -J | grep -o "sk-ant-oat01[a-zA-Z0-9_-]*" | head -1
tmux kill-session -t token
# check:
set -a; source /root/.claude-env; set +a; claude -p "reply with exactly: AUTH-OK"
```

The default model is Opus: on a weaker model the agent invented application
statuses and Msg-IDs.

## Gmail

Each client mailbox has its own directory `~/.gmail-mcp/<creds_dir>`
(name from `accounts.yaml → creds_dir`) with the token `credentials.json` and the
OAuth client key `gcp-oauth.keys.json`.

The OAuth client key is best created under the client's own Google account: own
Google Cloud project, own consent screen, adds herself to the test users. Then
clients do not depend on each other or on the operator's account. If the client's
directory has no key, the engine takes the shared `~/.gmail-mcp/gcp-oauth.keys.json`
(the old scheme, one project per machine: every new mailbox must be added to the
Test users of that project, otherwise Google returns `403 access_denied`).

Create the key under the client's account (in a browser logged in as her jobs mailbox):

1. console.cloud.google.com → new project, e.g. `jobsearch-<slug>`.
2. APIs & Services → Library → Gmail API → Enable.
3. Google Auth Platform → Branding: app name, support email and
   contact email = her jobs address; Audience: External; Test users: her
   jobs address (and main, if any).
4. Clients → Create client → Desktop app → Download JSON →
   `~/.gmail-mcp/<creds_dir>/gcp-oauth.keys.json` on the server.

Token:

```bash
mkdir -p ~/.gmail-mcp/<creds_dir>
GMAIL_OAUTH_PATH=~/.gmail-mcp/<creds_dir>/gcp-oauth.keys.json \
GMAIL_CREDENTIALS_PATH=~/.gmail-mcp/<creds_dir>/credentials.json \
  npx -y @gongrzhe/server-gmail-autoauth-mcp auth
# open the printed URL, sign in to the RIGHT mailbox
JOBSEARCH_CLIENT=<slug> /opt/jobsearch-autopilot/bin/js scripts/gmail_rest.py check jobs
JOBSEARCH_CLIENT=<slug> /opt/jobsearch-autopilot/bin/js scripts/gmail_rest.py check main
```

`check` prints the mailbox address: confirm the token belongs to the right one.

## Re-login to portals (new client or expired session)

StepStone, XING and LinkedIn logins live in the browser profile
`~/.playwright-mcp/<playwright_profile>`. A human signs in over VNC:

```bash
Xvfb :99 -screen 0 1280x900x24 &
x11vnc -display :99 -rfbauth /root/.vnc/passwd -localhost -forever &   # password: x11vnc -storepasswd
cd /opt/jobsearch-autopilot
DISPLAY=:99 JOBSEARCH_CLIENT=<slug> bin/js scripts/relogin.py
# from the Mac: ssh -f -N -L 5901:127.0.0.1:5900 <server>; open vnc://localhost:5901, sign in in the browser window,
# then Enter in the terminal (with --wait-for <domain> no Enter is needed: the script waits for the login itself)
JOBSEARCH_CLIENT=<slug> bin/js scripts/session_health.py --json
```

**StepStone and XING.** Both sign in automatically in the same browser that
keeps working afterwards, via «Mit Google einloggen», if the portal account is linked
to the jobs Gmail (the first time Google asks for consent, a human gives it over VNC
or the operator does in the client's Chrome profile). Details for XING:

**XING** (since 07.2026) gives no persistent session even with «Eingeloggt
bleiben»: the `login` cookie is a session cookie and disappears with the browser
process. So for XING a manual login over VNC does not help; instead `session_health`
and `bot.apply` sign in to XING themselves, in the same browser that keeps working
(`bot/scrapers/portal_login.py`). The simplest setup:
the client's XING account is linked to the jobs Gmail («Weiter mit Google»), and
the profile has been signed in to Google once over VNC. The Google session is
persistent, and the script signs in to XING through it without any password. Fallback:
a credentials file, server only, chmod 600, path in
`accounts.yaml → portal_credentials`:

```json
{"xing": {"username": "<xing-email>", "password": "<password>"}}
```

Check: `JOBSEARCH_CLIENT=<slug> DISPLAY=:99 bin/js scripts/portal_login.py xing` (separate process, diagnostics only).

Only a human passes reCAPTCHA. A session from another machine can be moved with
`scripts/export_storage_state.py` and `import_storage_state.py`; delete the state
file on both machines after the import, it holds live cookies.

## Cron

Enable after a rehearsal of at least one client (`docs/onboarding.md` §4):

```bash
crontab /opt/jobsearch-autopilot/scripts/server/crontab.txt
```

Clients are processed one after another under a single lock. Who gets picked is
decided by `status: active` in `profile.yaml`: to pause a person, cron does not
need touching.

Manual run of one client:

```bash
scripts/server/run_agent.sh <slug> evening
APPLY_MODE=dry-run scripts/server/run_agent.sh <slug> morning
```

## Telegram bot and inbox watcher (stage 2a)

What it does: an hourly pass over the contract client's mail (replies to applications,
platform mails) updates the tracker, sends the operator cards with drafts in Telegram and
sends follow-ups; the bot accepts buttons (send the draft, skip, pause).
Spec: `docs/superpowers/specs/2026-10-22-contract-phase2-server-automation-design.md`.

One-time operator steps (none of this is automated):

1. BotFather → new bot → token into `/root/.claude-env`:
   `export JOBSEARCH_TELEGRAM_TOKEN_<SLUG>=...` (SLUG in upper case; a different
   variable name can be set in `accounts.yaml → telegram.token_env`).
2. Units and the bot service:
   ```bash
   cp /opt/jobsearch-autopilot/scripts/server/systemd/*.service /opt/jobsearch-autopilot/scripts/server/systemd/*.timer /etc/systemd/system/
   systemctl daemon-reload
   systemctl enable --now jobsearch-telegram@<slug>
   ```
3. Send the bot `/start`: without a `chat_id` it replies only with its own id. Put
   it into `accounts.yaml → telegram.chat_id`, push the data, on the server
   `git -C /opt/jobsearch-clients pull` and `systemctl restart jobsearch-telegram@<slug>`.
   The bot silently ignores other chats. Stop the bot only via `systemctl stop`:
   `Restart=always` brings it back up in 30 s.
4. Shadow week (spec §12): in `/root/.claude-env` the line
   `export JOBSEARCH_INBOX_DRY_RUN_<SLUG>=1` (without the suffix it silences all clients of the machine), then `systemctl enable --now jobsearch-inbox@<slug>.timer`.
   In this mode the tracker and mail are not touched, cards land in
   `<clients>/<slug>/var/state/cards_dry.jsonl`. After a week remove the line:
   the next pass is live. Do not edit the unit: in a oneshot unit an added
   `ExecStart=` from a drop-in does not replace the first one, it adds a second run.
5. Check: `journalctl -u jobsearch-inbox@<slug> -n 50`, `journalctl -u jobsearch-telegram@<slug> -f`,
   state in `var/state/` (`cards.jsonl`, `inbox_seen.json`, `inbox_seen_dry.json`, `queue.jsonl`,
   `pause`, `last_watch`), the line `INBOX OK: …` at the end of every pass. Manual pass before
   enabling the timer (checks that `claude` is in the service's PATH and reads `cards_dry.jsonl`):
   `/opt/jobsearch-autopilot/scripts/server/run_service.sh scripts/inbox_watch.py <slug> --days 2 --dry-run`.

The whole watcher pass runs under the same machine lock as the night runs (a lock busy
for more than `JOBSEARCH_LOCK_WAIT` s means skipping this hour), and `run_agent.sh` now waits
for the lock instead of skipping the run. Drafts are written by `claude -p` without MCP servers and built-in
tools. Before the pass the data is pulled (`git pull --ff-only`), after it
`tracker.md` is committed and pushed; a failure of any of these steps and a failure to read mail
become an alert in the evening summary mail. Pause from the bot (`/pause`) stops only
sending, cards keep coming.

### Notifications for clients with the morning agent (notify mode)

Clients of the `de_employment` market (the tracker is kept by `claude -p` at night) get the same
watcher in notification-only mode: `accounts.yaml → telegram.mode: notify`. Tracker,
drafts, follow-up and the machine lock are not touched; the operator learns about an interview,
a document request or an unknown mail from a company in the tracker within the same hour, with an excerpt
of the mail and a button to Gmail. After every night run `notify_run.py` separately sends
the tracker rows that became `interview*`/`offer*`/`action_needed`, and `send_alert.py`
sends alerts to Telegram instead of the mail queue. The summary mail (`operator_summary.py`)
is not sent at all when Telegram is configured. Trigger: on 28.10.2026 the morning run at 03:11
recorded a phone interview at 17:30 the same day, and the operator would have learned of it at 20:40.

One bot for all such clients, the operator's chat, text with the client's name in front:

```bash
# /root/.claude-env: the same token under a shared name
export JOBSEARCH_TELEGRAM_TOKEN_OPERATOR="$JOBSEARCH_TELEGRAM_TOKEN_<SLUG-with-bot>"
```
```yaml
# the client's accounts.yaml
telegram:
  token_env: JOBSEARCH_TELEGRAM_TOKEN_OPERATOR
  chat_id: <operator chat_id>
  mode: notify
  lang: uk
```
```bash
systemctl enable --now jobsearch-inbox@<slug>.timer
```

Do NOT enable `jobsearch-telegram@<slug>` for such clients: long polling on one
token can be held by only one process, and that is the client's bot with `mode: cards`. Manual
check: `run_service.sh scripts/inbox_watch.py <slug> --days 2` → the line
`INBOX OK: notified=…`. The first pass sees the last `--days` days in full.

## Applications without a tap: queue and dispatcher (stage 2b)

What it does: the evening run no longer sends a digest, it puts the selected leads into
the queue `var/state/queue.jsonl` (`scripts/enqueue_leads.py`: application channel without
LinkedIn login, package `var/run/packages_<date>/<slug>/`, checks before
sending). The dispatcher (`scripts/dispatch_leads.py`, timer every half hour during the day)
sends email leads itself within `targeting.yaml → daily_limits`, writes a row to the tracker
and a receipt to Telegram. A blocked lead stays `held` with reasons and is visible in
`/status`; form leads are submitted by the browser worker (stage 2c, below), platforms with
login stay `queued`. Spec: the same one, §15.

One-time operator steps, in this order:

1. In the data repository: current CV PDFs in `clients/<slug>/attachments/` under
   the names from `tracks.yaml → tracks.<track>.cv_file` and `targeting.yaml →
   agency_attachments`; `profile.yaml → report_mode: none` (no Tagesbericht,
   only a «Hinweis» mail on failures); `targeting.yaml → send_policy: auto`,
   `daily_limits: {email: 5, form: 5}`. Commit, push, on the server
   `git -C /opt/jobsearch-clients pull`.
2. Units and the shadow week:
   ```bash
   cp /opt/jobsearch-autopilot/scripts/server/systemd/jobsearch-dispatch@.* /etc/systemd/system/
   systemctl daemon-reload
   echo 'export JOBSEARCH_DISPATCH_DRY_RUN=1' >> /root/.claude-env
   systemctl enable --now jobsearch-dispatch@<slug>.timer
   systemctl restart jobsearch-telegram@<slug>
   ```
3. After the first evening run: `cat var/state/queue.jsonl` and
   `cat var/run/packages_<date>/*/meta.json`; after the first timer pass:
   `cat var/state/queue_dry.jsonl` (what would have gone out) and `journalctl -u jobsearch-dispatch@<slug> -n 30`.
   Manual pass: `/opt/jobsearch-autopilot/scripts/server/run_service.sh scripts/dispatch_leads.py <slug> --dry-run`.
4. End of the shadow week: remove the `JOBSEARCH_DISPATCH_DRY_RUN` line from
   `/root/.claude-env`; the next timer pass sends.

The dispatcher does not take the machine lock (no LLM, no display). Before the pass `git pull
--ff-only` of the data, after a live application `tracker.md` is committed and pushed.
`/pause` from the bot stops it as well.

## Browser worker: ATS forms without login (stage 2c)

What it does: `scripts/apply_leads.py` (hourly timer during the day at :40, last
start 19:40) takes queued leads of the `form` channel (Ashby,
Greenhouse, Lever, Workable without login) from `var/state/queue.jsonl` within `daily_limits.form` and submits them
in a visible Chromium on the server display `:99`, in the client's Playwright profile.
Fields are filled from `profile.yaml` (`links`, `form_answers`), free text
is written by the model, the CV is taken from the package (a file starting with `CV…`, otherwise the first attachment).
Submitted: a row in the tracker and a receipt in Telegram. Vacancy closed: `held`.
Everything the worker did not bring to a confirmation (a required field without an answer,
reCAPTCHA, validation errors, silence after Submit) becomes a hand-off: the tab
stays open, a card arrives in Telegram. Platforms with login and Easy
Apply stay `queued` until the next stage. The worker takes the machine lock only for
the time of filling the form; while it waits for the operator, the lock is free and the browser profile
is busy. Spec: the same one, §7 (step 2c) and §15.

One-time operator steps, in this order:

1. Xvfb `:99` and x11vnc are already running (see «Re-login to portals»). x11vnc
   listens only on localhost and with a password (`-localhost -rfbauth /root/.vnc/passwd`),
   so from outside the display is reachable only through an SSH tunnel. The hint for the hand-off card in
   `/root/.claude-env`:
   ```bash
   export JOBSEARCH_VNC_HINT='vnc://localhost:5901 (first ssh -f -N -L 5901:127.0.0.1:5900 <server>)'
   ```
   Without it the card does not say where to look. The client's `profile.yaml` has
   `links` and `form_answers` filled in, because without them most forms go to hand-off.
2. Units and the shadow pass:
   ```bash
   cp /opt/jobsearch-autopilot/scripts/server/systemd/jobsearch-apply@.* /etc/systemd/system/
   systemctl daemon-reload
   echo 'export JOBSEARCH_APPLY_DRY_RUN=1' >> /root/.claude-env
   systemctl enable --now jobsearch-apply@<slug>.timer
   systemctl restart jobsearch-telegram@<slug>
   ```
   The bot restart is needed so that the hand-off card buttons also close the lead in the queue.
3. First shadow pass by hand, when there is a form lead in the queue:
   ```bash
   DISPLAY=:99 /opt/jobsearch-autopilot/scripts/server/run_service.sh scripts/apply_leads.py <slug> --dry-run
   ```
   The form is filled without Submit. Look at the screenshot
   `var/run/packages_<date>/<slug>/working/apply_form_filled.png` (here `<slug>`
   is the lead's folder in the packages) and the row in `var/state/queue_dry.jsonl` (`result`:
   status, hand-off reason, filled fields). Shadow mode does not fill the
   same lead twice a day. Timer log: `journalctl -u jobsearch-apply@<slug> -n 30`.
4. Live mode: remove the `JOBSEARCH_APPLY_DRY_RUN` line from `/root/.claude-env`
   (on the 2d deploy the variable is removed in any case, step 2 of the section on
   stage 2d; from then on shadow mode is set only per platform in `targeting.yaml`);
   the next timer pass submits. A `send_policy` other than `auto` still
   keeps the worker in shadow mode.
5. Hand-off. The card names the company, role, reason, URL and the VNC hint. From the Mac:
   ```bash
   ssh -f -N -L 5901:127.0.0.1:5900 <server>
   open vnc://localhost:5901
   ```
   In the Chromium window finish the form and press Submit. The tab waits until 19:55
   (`JOBSEARCH_HANDOFF_UNTIL=HH:MM` in `/root/.claude-env` changes the deadline). If
   the page showed a confirmation, the worker closes the lead as submitted itself.
   The «Submitted» button on the card closes the lead as `sent` with a row in the tracker,
   «Skip» puts it into `held` (`operator_skipped`). Without an answer by the
   deadline the lead becomes `failed`, the card `expired`. One hand-off per pass: while
   it lasts, the remaining forms wait, and the next timer tick does not start.

10 min before the deadline (from 19:45) the worker starts no new live application, the rest of
the queue stays `queued` until the next day (one line about it in the journal);
a hand-off that started earlier still waits until 19:55.
Before the pass `git pull --ff-only` of the data, after a live application `tracker.md`
is committed and pushed. Without `DISPLAY` or with a browser profile held by another
process (`scripts/relogin.py`, MCP), the pass exits with 0 and does not touch the queue;
the profile is taken only when there are form leads in the queue. Without a bot token or
`chat_id` the worker runs only in shadow mode: the hand-off card would not arrive.
A package without attachments lands in `held` (`attachment_none`). A machine lock failure goes
as the alert «Browser-Worker: …» into the summary mail. `/pause` from the bot stops the worker too,
`systemctl stop` closes the browser and releases the profile.

## Form worker for the employment market (de_employment clients)

The same `jobsearch-apply@<slug>.timer` as for the contract client: the morning agent after
`--batch-render` puts `manual_link` leads into the queue (`scripts/enqueue_leads.py --date <today>`,
Step 5b of the prompt), the worker opens the employer's site during the day in the client's profile on display :99,
fills the form in German from profile.yaml (address, Geburtsdatum, Gehaltsvorstellung,
Eintrittstermin, Anschreiben file, Zeugnisse with `portal: true`), presses Submit and moves the row
«🔄 Файли готові» to `portal_submitted`. A stuck form (captcha, login, unclear required
field) → a hand-off card into the shared bot without buttons: the operator finishes it over VNC (the worker sees
the confirmation on the page) or closes the tab (`held operator_skipped`).
Spec: `docs/superpowers/specs/2026-10-28-de-employment-apply-worker-design.md`.

```bash
systemctl enable --now jobsearch-apply@<slug>.timer
DISPLAY=:99 /opt/jobsearch-autopilot/scripts/server/run_service.sh scripts/apply_leads.py <slug> --dry-run
```

Shadow: without `send_policy: auto` in the client's `targeting.yaml` the worker only fills the form, takes
the screenshot `applications/<date>/<package>/working/apply_form_filled.png` and writes `queue_dry.jsonl`.
Live mode: `send_policy: auto` on the operator's word after reviewing the screenshots. Daily limit
`daily_limits.form`. LinkedIn Easy Apply for these clients stays `held platform_off:linkedin`
until `platforms.linkedin: on` appears in their `targeting.yaml`.

## Platforms with login: LinkedIn, freelancermap, GULP (stage 2d)

What works: the same `jobsearch-apply@<slug>.timer` (hourly during the day at :40, last
start 19:40), no new units. Besides the `form` channel the pass takes `linkedin_offsite`,
`linkedin_easy` and `platform_login` (freelancermap, GULP). Order within the pass:
first `linkedin_offsite` (cheap resolution of the external link, the lead becomes `form`
with the ATS by host and is submitted as form in the same pass), then `form`, `linkedin_easy`,
`platform_login`. Resolution submits nothing and does not count against the limits.

Switches and limits in the client's `targeting.yaml`:
```yaml
daily_limits: {email: 5, form: 5, linkedin: 5, freelancermap: 2, gulp: 3}
platforms: {linkedin: on, freelancermap: shadow, gulp: shadow, freelance_de: off}
```
`on` submits, `shadow` fills without Submit (a row in `var/state/queue_dry.jsonl`,
a screenshot in the package), `off` skips the lead without touching it. A platform without a key is off.
Beyond the daily limit: at most 2 LinkedIn applications per pass and a pause of 180 s after
every live attempt on a platform. `freelance_de` is off because a Basic account
cannot respond.

Temporary records live in `var/state/platforms.json`. Priority rule:
`targeting.yaml` is the declared permanent state. `/platform <name> off|shadow`
writes a record until the end of the day; `/platform <name> on` only clears the day's record and does not
raise declared `off` or `shadow` (the bot appends «declared … in targeting.yaml»).
`/platform` without arguments shows one line per platform: state and submitted today /
limit. Switch permanently only by editing `targeting.yaml` in the data repo.

Cards without buttons:
- `login_required`: the session dropped. The lead stays `queued`, the platform is off until
  the end of the day, one card per platform per pass. Sign in over VNC (tunnel and
  `x11vnc` as in «Re-login to portals»), sign in in the browser window (with
  `--wait-for` no Enter in the terminal is needed: the script sees the login itself and
  exits), then `/platform <platform> on`:
  ```bash
  cd /opt/jobsearch-autopilot
  DISPLAY=:99 JOBSEARCH_CLIENT=<slug> bin/js scripts/relogin.py <login-url> --wait-for <domain>
  ```
  Login addresses: LinkedIn `https://www.linkedin.com/login` (`--wait-for linkedin.com`),
  freelancermap `https://www.freelancermap.de/login` (`--wait-for freelancermap.de`),
  GULP `https://www.gulp.de/` and there «Anmelden» (`--wait-for gulp.de`).
- `restricted`: the platform restricted the account; off until the end of the day, check
  the account by hand, then `/platform <platform> on`.
- `platform_quota`: the monthly quota is used up (freelancermap), the lead is `held`,
  the platform is off until the 1st of the month.

**The deploy order must not change: data repo first, then the engine.** If the engine
has been updated and the data repo does not yet have the `platforms` key, every LinkedIn lead and every lead
of another platform lands in `held` with the reason `platform_off`. Recovery: add
`platforms` to the data repo and run `--rechannel` for every date (step 4).
Before the deploy make sure the data repo with `platforms` is pushed from the Mac.

Operator steps, in this order (nothing on the server changes without his word;
the word on removing `JOBSEARCH_APPLY_DRY_RUN` and on LinkedIn first has already been given):

1. Pull the repositories, data first:
   ```bash
   cd /opt/jobsearch-clients && git pull
   cd /opt/jobsearch-autopilot && git pull
   ```
2. Remove the 2c shadow flag (from this moment shadow mode is set only by
   the platforms in `targeting.yaml`):
   ```bash
   cp /root/.claude-env /root/.claude-env.bak-2026-10-28
   sed -i '/JOBSEARCH_APPLY_DRY_RUN/d' /root/.claude-env
   grep -c JOBSEARCH_APPLY_DRY_RUN /root/.claude-env   # must be 0
   ```
3. Restart the bot (new `/platform` command):
   ```bash
   systemctl restart jobsearch-telegram@<slug>
   ```
4. Move the held LinkedIn leads into the new channels, for every date that had
   such leads (here 26 and 27 October):
   ```bash
   cd /opt/jobsearch-autopilot
   scripts/server/run_service.sh scripts/enqueue_leads.py <slug> --date 2026-10-26 --rechannel
   scripts/server/run_service.sh scripts/enqueue_leads.py <slug> --date 2026-10-27 --rechannel
   ```
   `--rechannel` does not touch `failed` records (such a lead may already have gone out: the process crashed
   in the middle of a form or the hand-off expired); their ids are in the summary under `skipped_failed`,
   sort them out by hand.
5. First pass by hand in the daytime window, when the browser profile is free (no
   `relogin.py`, MCP or someone else's hand-off running):
   ```bash
   DISPLAY=:99 /opt/jobsearch-autopilot/scripts/server/run_service.sh scripts/apply_leads.py <slug>
   ```
   Read the line `APPLY OK: {...}`. Expected: `resolved` for offsite leads,
   `sent` for LinkedIn if the lead is clean, rows in `queue_dry.jsonl` for
   freelancermap and GULP (they are in `shadow`).
6. Check the shadow screenshots
   `var/run/packages_<date>/<lead-folder>/working/apply_form_filled.png` against
   the checklist below. When everything is right, switch the platform to `on`: `/platform
   freelancermap on` has no effect on a declared `shadow`, so change in the data repo
   `platforms: {..., freelancermap: on, ...}`, commit and push from the Mac;
   the next pass picks it up via `pull_data`. The same for GULP, separately.
7. Where to look:
   ```bash
   journalctl -u jobsearch-apply@<slug> -n 50
   cat /opt/jobsearch-clients/clients/<slug>/var/state/platforms.json
   ```
   and `/status`, `/platform` in Telegram.

### Checklist for the first live pass

Not verified live, look with your own eyes, do not trust a green status:

- LinkedIn: in the Easy Apply modal the just-uploaded CV file is the one selected
  (radio with `aria-label` = the file name, not a saved CV; otherwise
  `resume_not_selected`); the Follow company checkbox is off (otherwise
  `follow_still_checked`); the confirmation text after Submit matched instead of falling into
  `handoff` through silence. In the queue: `sent` only with a row in the tracker and a receipt.
  Answers to questions about years of experience are taken from `experience_years` in
  `profile.yaml`; the operator confirms the numbers. The first application is better on a lead where
  Follow is visible on the screenshot.
- freelancermap: after `set_input_files` the code checks only `files.length`;
  the rendered attachment list (DOM not captured) must be judged by eye on the
  screenshot: CV and Projektliste in place. The counter «Restliche Bewerbungen für
  diesen Monat: n/total» before and after the first live application went down by one.
  The confirmation text has not been seen live: unknown text will be `handoff`, not `sent`.
- GULP: the dry run does not set the CV file, so on the shadow screenshot the CV field is empty,
  and that is normal. The CV file name in the iframe appears only on a live pass; without
  it Submit is not pressed (`handoff cv_upload_unconfirmed`). The confirmation
  text has never been seen (the button was never pressed), so the first live application
  will almost certainly go to `handoff`: the operator finishes it and presses «Submitted».
- LinkedIn: if the first result is `handoff no_easy_apply` or `held no_apply_link`,
  suspect the page wait, not the lead: the SPA behind the login may not have managed to
  render the Apply button within 15 s. Open the vacancy over VNC and look.
- All platforms: the row in `queue.jsonl` has the key `platform`, `/platform` shows
  the counter «submitted today / limit», hand-off tabs are not stuck on the profile
  (the next pass does not start while one is open).

## Where to look when something is wrong

| What | Where |
|---|---|
| Run log | `<clients>/<slug>/var/logs/<mode>_<date>.log` |
| Intermediate files | `<clients>/<slug>/var/run/` |
| Full transcript of the Claude session | `/root/.claude/projects/` (the log holds only the summary) |
| Cleanup report | `var/run/cleanup_<date>.json`; `unmatched: not_found` is normal |

Mail to the operator: one mail per day, `[<Name>: Tagesbericht]`, after the evening
run. `scripts/server/operator_summary.py` builds it without Claude: counters from
the tracker, the night run's report from `var/run/operator_report_<date>.html`, the
evening run's note and alerts from the queue `var/run/alerts_pending.jsonl`. At night the operator
gets no mail. The state at any moment, without a mail:

```bash
js scripts/server/operator_summary.py
```

The alert «no `MORNING OK` marker» means the agent did not reach the end:
look at the tail of the log. «Claude limit reached»: the run hit the subscription limit,
the next one goes on schedule. «Engine-Pull fehlgeschlagen» or «Datenpull
fehlgeschlagen»: the repository did not pull, the run went on local state
(trackers and configs may have been stale); the mail has `git status --short` of that
repository. Typical cause: the same file was created both on the server and on the Mac.
Compare with `cmp` against `git show origin/main:<path>`, put identical files into a stash,
then `git pull --rebase`. Server changes that differ must not be thrown away.

## Backup

Client data is in the private repository, the server pushes after every morning run.
Unique to the server are only `/root/.claude-env`, `~/.gmail-mcp` and
`~/.playwright-mcp`. Losing the server means repeating OAuth and portal logins
for every client, applications and the tracker stay intact.
