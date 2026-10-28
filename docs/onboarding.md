# How to onboard a new client

From "there is a person who needs a job" to daily automatic applications. Part
of it the operator does by hand (access), part is done by Claude (config from
documents).

## 0. What to ask the person for

Ask in one message, put everything into `clients/<slug>/inbox/`:

- a current CV in any format, better several versions if they exist;
- Arbeitszeugnisse, diplomas, certificates (language ones too), recognition of diplomas;
- a photo for the CV;
- in free form: which roles they want, which definitely not, city and radius,
  whether remote is fine, salary, when they can start, full-time or part-time;
- citizenship or type of work permit, driving licence;
- what in their experience must not be embellished (language level, titles,
  unfinished studies).

And access:

- **a separate mailbox for the job search** (`imja.prizvyshche.jobs@gmail.com`),
  created by the person or by the operator together with them;
- access to the personal mailbox listed in the CV: employers reply there;
- StepStone, XING, LinkedIn accounts (existing ones or new ones on the jobs mailbox).

The person must understand and agree: applications go out in their name
without their review, the agent reads both mailboxes, interviews they attend
themselves.

## 1. Skeleton

```bash
export JOBSEARCH_CLIENTS_DIR=~/Projects/jobsearch-clients/clients
python -m bot.onboard init <slug>      # slug: lowercase latin letters, e.g. anna-k
```

`clients/<slug>/` appears with YAML templates and `status: paused`.

## 2. Config from documents (Claude)

Open Claude Code in `jobsearch-clients/clients/<slug>/` and ask it to fill the
config from `inbox/`. Order and rules:

1. **`profile.yaml`**: contacts from the CV. Whatever is missing, ask, do not
   invent. `report_email` is the person's personal mailbox (usually the one in
   their CV): after every run with submitted applications a short report in
   German goes there, with a copy to the operator. Manual submissions and
   errors are seen by the operator only.
2. **`candidate_brief.md`**: free text. What the person actually did, what they
   want, what must not be claimed. This is the main source for the letters.
3. **`cv.yaml`**: experience, education, skills. Start with a single variant
   `standard`. Add a second variant when the tracks are so different that the
   same experience has to be described in other words (e.g. `buero` and
   `sachbearbeitung`). Every bullet must be backed by the CV or a Zeugnis.
4. **`attachments/`**: PDFs named like `01_Zeugnis_Firma_2025.pdf`, one per
   document, listed in `cv.yaml → attachments` with captions for the
   "Anlagen" block. Set `portal: true` on the two most recent Zeugnisse.
5. **`tracks.yaml`**: 3–6 tracks. A track is a group of role titles with one CV
   variant. Take keywords from real job ads, as stems (`Dolmetsch`, not
   `Dolmetscherin`). Roles that look similar by wording but do not fit go into
   a separate track with `excluded: true`.
6. **`targeting.yaml`**: cities within the radius, all spellings (with umlauts,
   without them, the English name of the state). Exclusions by role title.
   Conditions that never appear in the title (Teilzeit, Homeoffice) are set on
   the job text: `description_bonus` moves such jobs up the queue, and
   `excluded_description_patterns` with `unless` drops e.g. Vollzeit without a
   mention of Teilzeit.
7. **`search.yaml`**: 2–4 queries per active track, one each with
   `site:linkedin.com/jobs` and `site:xing.com/jobs`. Career pages of the
   city's large employers.
8. **`accounts.yaml`**: addresses of both mailboxes. The engine works with Gmail
   only. If the person's personal mailbox is not on Gmail, remove the `main`
   block and put the jobs address into `profile.yaml → email`: employer
   replies will go there, and the engine reads them there. An old jobs mailbox
   on another provider (GMX etc.) is not migrated, forwarding to Gmail is
   enough.
9. **`CLAUDE.md`** in the client folder, if there are rules specific to this
   person (e.g. "in forms, write citizenship like this", "do not apply to this
   company").

Check:

```bash
js -m bot.onboard check --client <slug> --render
```

There must be zero errors. `--render` puts a sample package for every CV
variant into `var/run/onboarding_sample/`: open the PDF, show it to the person,
fix the wording before the start.

## 3. Access on the server (operator)

Tokens and sessions exist only on the server, they never get into git.

1. **Gmail OAuth** for both mailboxes: `docs/server_setup.md` §Gmail. The
   directories must match `creds_dir` in `accounts.yaml`.
2. **Browser profile**: log in to StepStone, XING, LinkedIn over VNC,
   `docs/server_setup.md` §Re-login. The profile is named like `playwright_profile`.
3. **Job alerts** on the portals to the jobs mailbox, by the role titles from
   the tracks. In the jobs Gmail account create the label `Job Alerts` and a
   filter by senders.
4. Check: `js -m bot.onboard check --client <slug>` without warnings about
   OAuth and the profile, `js scripts/session_health.py --json` shows `live`.

## 4. Rehearsal

```bash
# on the server, status still paused, so cron leaves the client alone
scripts/server/run_agent.sh <slug> evening
APPLY_MODE=dry-run scripts/server/run_agent.sh <slug> morning
```

The evening run sends a digest to the jobs mailbox. The morning run in dry-run
creates draft emails instead of sending them and walks the portal forms up to
the "Absenden" button. Review: are these the right jobs, do the letters sound
like a human, did anything unwanted slip through. Adjust the tracks and the
brief, repeat.

## 5. Launch

`status: active` in `profile.yaml`, commit, push. The next cron picks it up.
Read the morning reports carefully during the first week: the filters almost
always need one or two adjustments.

## Pause and finish

- Pause: `status: paused`. Data stays, cron skips.
- Found a job: `status: placed`, switch off the job alerts on the portals.
- Full deletion at the person's request: delete the folder, remove it from the
  history (`git filter-repo --path clients/<slug> --invert-paths`, force-push),
  on the server wipe `~/.gmail-mcp/<slug>-*` and `~/.playwright-mcp/<slug>`,
  revoke the OAuth access in the Google account settings.
