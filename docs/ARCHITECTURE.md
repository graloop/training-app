# How the code fits together

For someone opening this code for the first time. The [README](../README.md)
says what the app does and how to run and deploy it;
[TROUBLESHOOTING](TROUBLESHOOTING.md) says what to do when something breaks.
This page says where things are and why they are built that way.

## What the app is

A self-hosted training calendar for a handful of friends. Each one connects
their own Garmin account; the server syncs their activities, sleep and
recovery, writes it all up as text, and puts the plan they agree on with
Claude on their calendar.

There is no AI inside the app. The History tab is text written *for* Claude
(Claude in Chrome reads the page, or the athlete copies it into a Claude
conversation), and the Plan tab reads Claude's answer back as one session per
line. Text out, text in.

One Python process (FastAPI) serves both the API (`/api/...`) and the page
(plain HTML, CSS and JavaScript modules, no build step). Postgres in Docker,
SQLite under `npm start`.

## The big picture

```
 Garmin Connect (unofficial API)
        │   garmin/client.py: the only file that calls Garmin
        ▼
 garmin/sync.py: one batch for one account
        │   activities, then day by day (sleep, readiness, training status, stress),
        │   then each activity's details, multisport legs, 5-second traces, the profile
        │
        │   started by: the Sync button (a job in garmin/autosync.py, the page polls it)
        │               every few hours for every account (the autosync thread)
        ▼
 database (models.py)
        │
        ├──▶ GET /api/calendar ─────────────────────────────▶ Calendar tab
        │
        └──▶ planning/training_log.py (build_log) ──▶ GET /api/plan/log ──▶ History tab, as plain text
                                                                              │
                                    Claude in Chrome reads it, or "Copy log for Claude"
                                                                              ▼
                                    draft lines:  Tue Oct 13 | run | 1:15 | Track: 2×6×400 m
                                    in the Plan tab ──▶ PUT /api/plan/draft
                                              planning/draft_parser.py: each line → a session or an error
                                              planning/drafts.py: the weekly preview
                                                                              ▼
                                    "Put this plan on the calendar" ──▶ POST /api/plan/draft/confirm
                                              drafts.put_on_calendar: replaces planned sessions from today
                                                                              ▼
                                    PlannedTraining rows ──▶ Calendar tab (planned vs done)
```

The Import tab joins the same path: `sharing.draft_lines` turns a friend's
planned sessions into draft lines, which go through the same parser.

## The life of a request

1. **The page** calls `api()` in `frontend/js/core/api.js`. It adds the
   session token and turns an error answer into an `Error` with the server's
   message and `err.status`. A 401 means the app session ended: back to the
   login window.
2. **`main.py`, `security_headers` middleware.** With `APP_DOMAIN` set, a
   request on any other address or over plain http is sent to
   `https://APP_DOMAIN` (`visitor.py` reads what the proxy or Cloudflare says
   about the visitor). Every answer gets the security headers; `/api/...`
   answers are never cached.
3. **A router** (`routers/<prefix>.py`, one per URL prefix) only does HTTP:
   it reads the request through a model in `schemas.py`, checks that the row
   belongs to the caller, and answers with another model from `schemas.py`.
4. **Who may call it** comes from `deps.py`: `get_current_user` (a valid token
   whose version matches, see below), `get_current_member` (regular accounts
   only: admins get a 403) or `get_current_admin`.
5. **The work** is done outside the routers: `planning/`, `garmin/`,
   `accounts.py`, `sharing.py`.
6. **The database session** comes from `database.get_db`, one per request.
   Routers commit; helpers that write say "the caller commits".

The page itself: `GET /` is `main.index()`, which adds `?v=<file time>` to
every script and stylesheet `index.html` links, so a browser never runs a
stale copy. Every other file is served from `backend/frontend/` with
`Cache-Control: no-cache`.

**Tokens.** A login returns a JWT (`security.py`) holding the account id and
its `token_version`. Changing or resetting a password bumps the version, so
every older token stops working; a deleted account isn't found. Tokens last
a week.

## Where each thing is

Backend, under `backend/app/`:

| File | What it holds |
|---|---|
| `main.py` | The FastAPI app: startup, security headers and https redirect, routers, the page |
| `config.py` | Every setting, read from the environment or `.env` |
| `database.py` | Engine, sessions, and `add_missing_columns` (the only "migration") |
| `models.py` | The tables |
| `schemas.py` | What the API accepts and answers (Pydantic), with the limits on each field |
| `security.py` | Password hashing (scrypt), session tokens (JWT), encryption of Garmin sessions (Fernet) |
| `deps.py` | Who may call an endpoint |
| `visitor.py` | The visitor's address and whether they came over https, as the proxy reports them |
| `accounts.py` | The first admin, the local test accounts, the login log, the login lockout |
| `sharing.py` | Share codes, and a friend's planned sessions as draft lines |
| `logs.py` | Log format and level |
| `cli.py` | Account rescue: `python -m app.cli list-users` / `reset-password` |
| `version.py` | The version in the footer and on the first log line |
| `routers/` | HTTP only, one file per URL prefix: `auth`, `admin`, `garmin`, `calendar`, `athlete`, `objectives`, `planned`, `plan`, `share` |
| `planning/basics.py` | Days, durations and sports, written the same way everywhere |
| `planning/activity_text.py` | One activity as text: the summary line, laps, reps, detail lines |
| `planning/session_detail.py` | What a session's 5-second trace adds, and one session in full |
| `planning/training_log.py` | The History tab's log, one function per section (`build_log` puts them together) |
| `planning/draft_parser.py` | Draft lines → sessions or errors. Never touches the database |
| `planning/drafts.py` | A draft as the page sees it, and putting sessions on the calendar |
| `garmin/client.py` | Every call to Garmin, through the `garth` / `garminconnect` libraries |
| `garmin/sync.py` | One sync of one account: what is fetched, in what order, and how it is stored |
| `garmin/activity_file.py` | An activity's original FIT file → heart rate, speed, power and cadence every 5 seconds (no positions) |
| `garmin/autosync.py` | When syncs run: the background thread, the jobs the Sync button starts, one sync per account at a time |

Page, under `backend/frontend/`: see [The page](#the-page) below.

Tests, under `backend/tests/`: one file per area, each with a docstring
saying what it covers. `conftest.py` explains the fixtures.

## Rules that keep it findable

Each rule has a test, so breaking it fails `npm test`:

| Rule | Enforced by |
|---|---|
| Routers never import each other: what two routers share lives in `planning/`, `garmin/` or another module | `tests/test_structure.py` |
| No module imports another module's `_private` names | `tests/test_structure.py` |
| The page's modules never import each other in a circle: what two views share goes in `js/core/` | `tests/test_frontend.py` |
| Every element a script looks up exists in `index.html`, ids are unique, every linked file and import exists | `tests/test_frontend.py` |
| No script names a variable `t` (it would hide the translation function) | `tests/test_frontend.py` |
| The Sync button never waits on one long request | `tests/test_frontend.py` |

And one rule by convention: only `garmin/client.py` talks to Garmin. The
tests replace its functions with fakes, so nothing else has to be faked.

## Work done outside requests

- **Startup** (`main.startup`): create missing tables and columns, trim old
  sleep rows, delete traces past 28 days, create the first admin, the local
  test accounts if asked, then start the background sync.
- **Background sync** (`autosync.start`, `AUTO_SYNC_HOURS`, 4 in Docker, off
  under `npm start`): a daemon thread, first run 5 minutes after a start, then
  every account connected to Garmin, one after the other, a few batches each.
- **Sync jobs.** The Sync button calls `POST /api/garmin/sync/start`, which
  starts a thread running up to 8 batches and answers at once; the page asks
  `GET /api/garmin/sync/status` every 1.5 s. This is because Cloudflare cuts
  any request after 100 seconds, and a first sync takes longer.
- **One sync per account at a time**: `autosync.user_lock`, shared by the
  button, the background and `POST /api/garmin/sync`.
- **Kept in memory, not in the database**: the login lockout counters, wrong
  share codes, sync jobs and the per-account locks. So **the app must run as
  one process** (uvicorn without `--workers`), and a restart forgets every
  lockout (the troubleshooting guide relies on that).

A sync never fails because of one missing piece: only the activity list can
fail it. Everything after that is in its own `try`, saved on its own, and
what Garmin didn't answer is asked again next sync, a few times at most.

## The database and how it changes

There is no migration tool. On every start, `Base.metadata.create_all`
makes the tables that don't exist yet, and `add_missing_columns` adds the
columns a model gained, **only if they are nullable**. Nothing is ever
renamed, removed or changed in type. So:

- A new column must be nullable (`Mapped[... | None]`, `nullable=True`).
  Anything else needs an `ALTER TABLE` run by hand on every deployment.
- Old names stay: the login name (`User.username`) is stored in the column
  `email`, from when accounts were Garmin emails.
- An earlier version still runs on a newer database: it ignores the new
  columns. That is what makes rolling back safe.

Worth knowing about the data:

- `Activity.start_time` is Garmin's *local* start time, so an activity
  belongs to the athlete's own day. `created_at` and the like are UTC
  (`schemas.UtcDatetime` adds the offset SQLite drops).
- JSON columns hold what doesn't need querying: `Activity.raw_json` (Garmin's
  list entry, whole), `laps`, `extras` (weather, sweat, multisport legs),
  `SleepRecord.raw_json` (only the few keys that are read), the Garmin and
  athlete profiles. `none_as_null` makes "nothing yet" a real SQL `NULL`,
  which queries like "details not fetched" rely on.
- `ActivityTrace` rows are big (a few dozen KB) and deleted 28 days after the
  session (`planning/basics.TRACE_DAYS`).
- "Today" comes from the browser when it is within a day of the server's
  (`drafts.client_today`, the `?today=` parameter), so a friend in another
  timezone sees their own today. The server's own today follows `TZ`.

## The page

No build step: `index.html` links its stylesheets, `js/theme-early.js` (a
plain script that picks day or night colours before anything is drawn) and
`js/main.js` (a module that imports the rest).

`index.html` holds the markup of every screen; the scripts show and hide it:

| Part of `index.html` | What it is |
|---|---|
| `header.topbar` | The tabs (`#tabs`), and the menus: `#user-actions` once logged in (account, settings), `#guest-actions` on the landing page (log in, language) |
| `#auth-view` | The landing page for visitors, with the Log in window |
| `#app-view` | A regular account's tabs: `#tab-calendar`, `#tab-athlete`, `#tab-plan`, `#tab-import`, `#tab-history` |
| `#admin-view` | The admin dashboard |
| `#plan-dialog` | The "Plan a session" / "Edit session" dialog |

The scripts:

- `js/main.js`: translates the page, tells `core/tabs.js` how each tab
  loads, logs in, then opens the landing page, the tabs or the dashboard.
- `js/core/`: what every view uses. `api.js` (calls to the server),
  `session.js` (the token, kept in `localStorage`, and who is logged in),
  `tabs.js`, `menus.js` (the top bar's pop-ups), `ui.js` (which screen, status
  lines), `format.js` (dates, durations, plurals), `sports.js` (names, icons),
  `i18n.js` and `fr.js` (English / French).
- `js/views/`: one file per part of the page, and `css/` one stylesheet per
  screen on top of `base.css` (colours, night mode, shared parts).

**Translations.** The English text is written in `index.html` and the
scripts. `fr.js` maps each English text to its French; `t("Connected as
{email}.", { email })` looks it up, and anything missing stays English. An
element with markup inside carries `data-i18n="<key>"` and is replaced
whole. What the server writes (error messages, the training log) stays in
English: the log is read by Claude.

**Written for Claude in Chrome as much as for people.** Every control has a
readable label (the **+** of each day says which day), and deleting or
replacing takes two clicks instead of a `confirm()` popup, which would block
the extension.

**The same rule in two places.** "Done as planned" (same sport, within 15% of
the planned time, one activity per planned session) is computed in Python for
the log (`planning/training_log.matched_plans`, `planning/basics.sport_of`)
and in JavaScript for the calendar (`views/calendar.js`, `matchDay`). The
landing page's mock-up (`views/landing-mocks.js`) uses the same 15%. Change
them together.

## Where to change what

| I want to… | Go to |
|---|---|
| Keep a new field from Garmin's activity list | `garmin/sync.py` `save_activities`, a nullable column in `models.py`, then show it in `planning/activity_text.py`. Tests: `tests/test_garmin_details.py` |
| Ask Garmin for something new | A `fetch_*` function in `garmin/client.py`, called from `garmin/sync.py` inside its own `try` |
| Change what the History log says | `planning/training_log.py` (`_intro_lines`, `_objective_lines`, `_athlete_section`, `_earlier_weeks`, `_week_lines`, `_ahead_lines`); one session's text in `planning/activity_text.py`; what the trace adds in `planning/session_detail.py`. Tests: `tests/test_plan.py`, `tests/test_activity_traces.py` |
| Accept another way of writing a draft line | `planning/draft_parser.py` (`parse_duration`, `_parse_date`, `_SPORT_WORDS`), with a test in `tests/test_plan.py`. The format is also explained in `index.html` (`data-i18n="plan.format"`, and its French in `fr.js`) and in `views/history.js` (`planContextText`) |
| Change how a tab looks or behaves | `frontend/js/views/<tab>.js`, `frontend/css/<tab>.css`, its markup under `#tab-<tab>` in `index.html` |
| Add a sentence to the page | Write it in English, through `t()` in a script; add its French to `js/core/fr.js` (the key is the exact English text) |
| Change login, lockout or password rules | `routers/auth.py`, `accounts.py` (the lockout numbers head its lockout section), `security.py`, `schemas.py` (`PASSWORD_MIN`, `USERNAME_PATTERN`) |
| Change who may call an endpoint | `deps.py`, and the `Depends(...)` of that endpoint |
| Add an endpoint | The router of its URL prefix (or a new file in `routers/` and an `include_router` in `main.py`); its models in `schemas.py`; its logic outside `routers/` |
| Add a setting | `config.py`, then `.env.example`, `docker-compose.yml` and the README's table |
| Change security headers, the content policy or the https redirect | `main.py` (`security_headers`, `CONTENT_SECURITY_POLICY`), `visitor.py` |
| Change the deployment | `docker-compose.yml`, `backend/Dockerfile`, `.github/workflows/ci.yml`; the README's "Deploying" |
| Change the local dev setup | `scripts/dev.sh`, `package.json` |

## Code kept for older versions

Some code only exists for data an older version stored. Each piece is marked
`LEGACY` in a comment, with when it can go; search for that word. At the
time of writing:

| Where | What it is for | When it can go |
|---|---|---|
| `garmin/sync.py` `trim_stored_sleep` (run at startup) | Sleep rows that versions before 15 stored whole (about 100 KB a night) | Once every database you care about has started on version 15 or later |
| `garmin/sync.py` `_needs_details`, the `extras IS NULL` part | Activities whose details were fetched before weather and sweat were kept (version 9, 2026-10-13) | Details are only fetched 90 days back: nothing left to do after about 2027-01-11 |
| `garmin/sync.py` `backfill_ride_laps` | Rides whose details were fetched before ride laps were kept (2026-11-03) | Same 90-day window: after about 2027-02-01 |
| `planning/training_log.py` `rule_set_text`, `User.athlete_notes`, `PlanDraft.notes` (`DraftIn.notes`) | The older "About you" box and block notes stand in for the rule set until it is first saved | When every account has saved a rule set. Removing it earlier hides text people wrote |
| `frontend/js/main.js`, `#logs` | Old bookmarks of the History tab, once called Logs | Any time |

Permanent leftovers, harmless: the login name in the column `email`, and
planned sessions whose `source` says `ai` (from the chatbot removed in
version 7).

## Conventions

- **Comments say why**, in plain English and short sentences. Every Python
  module starts with a docstring and every script and stylesheet with a
  comment saying what it holds and who uses it. A constant carries its reason
  on the same line. Endpoint docstrings also appear in the API docs (`/docs`,
  with `ENABLE_API_DOCS=true`).
- **Errors the page shows** are `HTTPException`s whose `detail` is a whole
  sentence: `api.js` shows it as is. Garmin trouble is a 409 (or 400 for a
  failed Garmin login), never a 401, which would log the person out of the
  app.
- **Logging**: `logging.getLogger("trainer.<area>")`; see `logs.py` and the
  troubleshooting guide.
- **Tests** run the real app against a throwaway SQLite database, with Garmin
  faked (`conftest.py`). One file per area; test names are sentences that say
  the behaviour.
- **Before pushing**: `npm test` and `npm run lint` (ruff for the Python,
  Prettier for the page's JavaScript; `npm run format` fixes what they can).
  GitHub runs both, then builds the Docker image.
- **Releases**: bump `VERSION` in `backend/app/version.py` and name the commit
  `vN: ...`. Every push to `main` publishes the image `ghcr.io/graloop/training-app`
  (tags `latest` and `sha-<commit>`), which the server pulls.
