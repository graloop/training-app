# Training App (Garmin-integrated)

A self-hosted training calendar for you and a few friends. Each person logs
in with an account the admin created, connects their own Garmin Connect,
and sees their activities, sleep and resting heart rate on a 4-week
calendar next to their planned sessions and objectives.

There is no built-in chatbot and no AI API key: open the page in Chrome and
ask the **Claude in Chrome** extension to plan your week, add objectives or
adjust sessions — it edits the page like you would.

## Features

- **Private accounts** — only people the admin created can log in (username
  + password, no sign-up). Everyone can change their own password in
  ⚙️ Settings. Five wrong passwords lock that username for 15 minutes, and
  twenty from one address stop that address; a password change or reset logs
  out every other session.
- **Admin dashboard** — admin accounts are for monitoring and managing, not
  for training: instead of the calendar they get a dashboard with the
  connections of the last 7 days (logins per day, failed attempts, the list
  of recent connections), every account with its last login, and the tools
  to create an account, reset a password, grant or remove admin rights and
  delete an account (the dashboard lists what the account holds and asks
  for its username to be typed first; deleting can't be undone). Admins have no calendar, plan, objectives or Garmin
  connection, so keep a separate regular account for your own training.
- **Connect Garmin** — after logging in, each person links their own Garmin
  Connect account (once; reconnect if Garmin expires the session). Garmin
  problems never log you out of the app — the page just asks to reconnect.
- **Calendar** — Monday–Sunday weeks covering ~2 weeks back and 2 weeks
  ahead, showing completed Garmin activities (type, duration, training
  effect) and planned sessions (light blue) per day, with each week's
  planned vs. done hours in a column on the right.
- **Plan sessions yourself** — the **+** in the bottom-right of each day
  opens a small window to plan a swim, ride, run or strength session, its
  duration and the workout details (shown under the session).
  **← Earlier / Later →** move the calendar 2 weeks at a time. Click a planned session to edit it, delete it or move it to another day. When a planned
  session is done (same day, same sport) the two merge into one green
  entry: dark green "done as planned" when the time is within 15% of the
  plan, light green with a dashed border "done, other duration" otherwise.
  One activity covers one planned session: two swims on a day are not added
  up to match a longer planned swim.
- **Garmin sync** — runs automatically after login; the "Sync with Garmin"
  button (on both tabs) pulls on demand. It looks back 90 days: activities
  come as one list; sleep, the morning Training Readiness, Training Status
  (acute vs chronic load, load focus), stress and body battery are fetched
  per day, and only for days not stored yet (today and yesterday are always
  refreshed). Each activity is asked once for its perceived effort, feel,
  laps, weather and sweat loss. The sync runs on the server: the button
  starts it and the page checks how it is going every second or two, so a
  long catch-up never hits the proxy's time limit (Cloudflare gives up after
  100 s) and the page stays usable meanwhile. The work is done in batches
  (30 days and 40 activities each) until nothing is waiting, so a first
  sync takes a minute or two and later ones a few seconds; the calendar
  fills in after each batch. Your Garmin profile (zones, thresholds,
  records) is refreshed once a day, and the session Garmin refreshes is
  saved, so later syncs don't have to ask for a new one. None of the extras
  can fail a sync: what Garmin doesn't answer is asked again next time.
- **Background sync** — on the server, every account connected to Garmin is
  also synced every few hours (`AUTO_SYNC_HOURS`, 4 by default in the Docker
  stack, off under `npm start`), so last night's sleep, the morning
  readiness and finished sessions are there without pressing Sync. A Sync
  press waits for a background run of the same account rather than running
  twice at once. The server's "today" follows `TZ` (America/Toronto by
  default in the Docker stack).
- **Multisport races** — a triathlon arrives from Garmin as one
  "multisport" session. Its legs (swim, transitions, bike, run) are fetched
  once, at any age, shown under the session on the calendar, listed in the
  log, and counted per sport in the weekly totals.
- **Older history** — *Import older activities* on the History tab brings in
  up to 3 years of activities (one list from Garmin). The History tab shows
  all of it: every session for the year before the detailed weeks, weekly
  totals back to two years ago, monthly totals before that. If the Garmin session has
  expired the page shows the "Connect your Garmin account" form; you stay
  logged in.
- **Tabs** — **Calendar**, **Athlete**, **Plan**, **Import** and
  **History**.
- **Athlete tab** — everything about the athlete in one place:
  1. **Objectives** (see below).
  2. **Benchmarks and zones** — your own tested numbers: weight, height,
     max / threshold / resting HR, FTP and the day it was tested, power
     zones, run threshold pace and zones, swim CSS and zones, equipment,
     health and injuries, and anything else worth knowing. They override
     Garmin's estimates in the history. Every box is optional.
  3. **From Garmin** — zones, thresholds, records, race predictions and
     training status as Garmin has them (read only, refreshed on each sync).
- **Plan tab** — where future weeks get planned, by hand or with Claude in
  Chrome, without touching the calendar until you confirm. From top to
  bottom:
  1. **Plan preview** — pick the first week to plan (any week up to about
     six months ahead) and how many weeks. The draft is shown as a table
     per week with a weekly split.
  2. **Put this plan on the calendar**, right under the preview — adds the
     draft's sessions and replaces whatever was planned from today to the
     end of those weeks, so re-planning never duplicates. Done activities
     are never touched.
  3. **Draft plan** — the text the preview is made from, one session per
     line: `Tue Oct 13 | run | 1:15 | Track: 2×6×400 m`. Any line the page
     can't read is flagged under the box (a wrong weekday for the date, a
     date outside the chosen weeks, an unreadable duration).
  4. **Rule set** — the rules every plan follows: availability, school or
     work schedule, exam periods, holidays and travel, weekly rhythm, rules
     of thumb. It replaces the older "About you" box and block notes: until
     the rule set is first saved, their text is shown in it, so nothing is
     lost.
- **History tab** — for the AI, not for reading: the whole training
  history as text for Claude in Chrome (26 weeks in full detail by default),
  then the plan being written (the weeks, the line format and the current
  draft). **Copy log for Claude** copies the
  same text for a normal Claude conversation. The history holds, per session:
     duration, distance, pace, average HR, load, and the **perceived effort
     and feel you answered on the watch**. In *Full* detail also time in each
     HR zone, max HR, Garmin's session type and training effect, bike power,
     cadence, climb, weather and sweat loss, interval totals, fastest
     splits, and the **reps** of interval runs (separated from warm-up and
     recoveries) or the laps of steady runs and swim sets (with rests, pool
     length, strokes and SWOLF). Per day: the morning **Training
     Readiness**, sleep score, duration and stages, resting HR, overnight
     HRV, stress and body battery. Plus weekly totals, your objectives, what
     is already planned, the **numbers you entered on the Athlete tab**, an
     **athlete block from Garmin** (max HR, threshold HR and pace, VO2max,
     HR zones, race predictions, personal records), the **rule set**,
     Garmin's **training status** (acute vs chronic load, load by intensity
     against its targets) and the **earlier history**: every session of the
     year before (duration, distance, pace, avg HR, load), then weekly and
     monthly totals for everything imported. A session shows "effort not answered" when the watch
     prompt was skipped and "not synced yet" when its details haven't been
     fetched. *Summary* detail keeps one line per session.
- **Import tab** — copy a friend's planned sessions. Everyone has a **share
  code** shown on the tab, next to their name (**New code** makes the old one
  stop working). With someone's name and code you pick their weeks (past or
  future, 1 to 12) and the weeks of yours to put them on (the same weeks by
  default, so a friend's block from last month can become your next weeks),
  check the preview, then **Import to plan** (it becomes your Plan draft, to
  adjust before confirming) or **Import to calendar** (on your calendar now,
  replacing what was planned on those days, like confirming a plan). Only
  planned sessions are shared: never Garmin activities, sleep or health
  data. Ten wrong name/code pairs in 15 minutes pause importing for that
  account, so codes can't be guessed.
- **Objectives** — **+ Add objective** records a target event (title, event
  date, target time, and notes such as its priority) as a card with a
  countdown; edit or delete it from the card. Shown on the Athlete tab, and
  listed in the history.
- **Sleep & recovery** — each day shows its Garmin sleep score (top right,
  coloured good / fair / poor) and resting heart rate (❤️ bpm); each week's
  column shows the average sleep score and average resting heart rate.
  The morning Training Readiness, overnight HRV, stress and body battery
  are only in the History tab.
- **Training load** — each completed activity shows Garmin's training load
  next to its duration (e.g. "1 h · load 113"), and each week's column
  shows the week's total load.

Something broken? See [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md): where the logs are,
what each error means, and where the code for each part of the app is.

## Stack

| Layer      | Choice                                                              |
|------------|----------------------------------------------------------------------|
| Backend    | Python, FastAPI, SQLAlchemy                                          |
| Database   | Postgres in production; SQLite automatically for local dev            |
| Garmin     | unofficial `garminconnect` / `garth` libraries                       |
| AI         | none built in — use the Claude in Chrome extension on the page        |
| Auth       | Local accounts created by the admin (scrypt hashes), JWT session tokens |
| Frontend   | plain HTML/CSS/JS, served directly by FastAPI — no build step        |
| Deployment | one Docker Compose stack (`db` + `backend`), deployable via Portainer |

There's no separate frontend server/container: FastAPI serves the API under
`/api/*` and the static frontend at `/` from the same process.

## Project structure

```
.
├── package.json          npm wrapper: npm start, npm test, npm run lint / format (see "Working on the code")
├── .github/workflows/    CI: tests, lint and the Docker build on every push
├── scripts/dev.sh         local dev launcher (venv, .env, uvicorn --reload)
├── docker-compose.yml     production stack: postgres + backend (+ optional Cloudflare Tunnel)
├── .env.example           template for secrets/config (copy to .env)
├── docs/TROUBLESHOOTING.md  logs, error messages, account rescue, backups
└── backend/
    ├── Dockerfile
    ├── requirements.txt    the app's Python dependencies (requirements-dev.txt adds pytest and ruff)
    ├── pyproject.toml      pytest and ruff settings
    ├── tests/              pytest, one file per area (npm test); test_structure.py keeps this layout true
    ├── app/
    │   ├── main.py         FastAPI app: security headers, startup, mounts routers + static frontend
    │   ├── config.py       env-driven settings
    │   ├── database.py     SQLAlchemy engine/session, tiny forward-only migration
    │   ├── models.py       database tables: User, Activity, SleepRecord, DailyMetrics, PlannedTraining, PlanDraft, Objective, LoginEvent
    │   ├── schemas.py      Pydantic request/response models (what the API sends and accepts)
    │   ├── security.py     scrypt password hashing, JWT, Fernet encryption
    │   ├── deps.py         auth dependencies: any user / regular member / admin
    │   ├── visitor.py      visitor address and https, as reported by the proxy or tunnel
    │   ├── accounts.py     first-admin bootstrap, test accounts, login lockout, connection log
    │   ├── sharing.py      share codes, someone's planned sessions as draft lines (Import tab)
    │   ├── logs.py         log format and level (LOG_LEVEL)
    │   ├── cli.py          account rescue: python -m app.cli list-users / reset-password
    │   ├── routers/        HTTP only, one file per URL prefix: auth, admin, garmin, calendar,
    │   │                   athlete, objectives, planned, plan, share. They never import each other.
    │   ├── planning/       the training log and the draft plan
    │   │   ├── basics.py         days, durations, sports
    │   │   ├── activity_text.py  one activity as text (pace, HR, effort, laps, weather)
    │   │   ├── training_log.py   the History tab's log (build_log, one function per section)
    │   │   ├── draft_parser.py   "date | sport | duration | details" lines -> sessions or errors
    │   │   └── drafts.py         a draft as the page sees it, putting sessions on the calendar
    │   └── garmin/         everything that talks to Garmin Connect
    │       ├── client.py     the only file that calls Garmin (wraps garth/garminconnect)
    │       ├── sync.py       one sync of one account: what is fetched and how it is stored
    │       └── autosync.py   background syncs every few hours + the jobs the Sync button starts
    └── frontend/             static, no build step (native ES modules)
        ├── index.html        every screen's markup; loads css/*.css and js/main.js
        ├── css/              base.css (shared) + one file per screen: calendar, athlete, plan, history, import, admin
        └── js/
            ├── main.js        entry point: log in/out, show the tabs or the admin dashboard
            ├── core/          shared by every view: api.js (fetch + errors), session.js (token, logged-in user),
            │                  tabs.js, ui.js, format.js (dates, durations), sports.js (names, icons)
            └── views/         one file per part of the page: calendar, session-dialog, garmin (connect + sync),
                               objectives, athlete, plan, plan-preview, history, import, admin, settings
```

Something broken on one tab? Its code is in `frontend/js/views/<tab>.js`
and its styles in `frontend/css/<tab>.css`. Views never import each other in a
circle: anything two views need lives in `core/`.

## Running it locally (`npm start`)

You need Python 3.11+ and Node/npm on your machine (npm is just used as a
task runner here — the app itself is Python, not Node).

```bash
npm start
```

The first run will:

1. Create a Python virtualenv at `backend/.venv` and install
   `backend/requirements.txt` into it.
2. Copy `.env.example` → `.env` and auto-generate `JWT_SECRET`,
   `FERNET_KEY`, and `POSTGRES_PASSWORD` for you.
3. Start the app with `uvicorn --reload` against a local SQLite database
   (`backend/dev.db`), regardless of the Postgres settings in `.env` (those
   are only used by the Docker deployment).

Then open **http://localhost:8000**.

`npm start` creates two generic accounts for testing and prints them:

| Username | Password     | Role         |
|----------|--------------|--------------|
| `admin`  | `adminadmin` | admin        |
| `user`   | `useruser`   | regular user |

They are re-applied on every start (changed passwords and roles go back to
the table above) and exist **only** under `npm start` on the local SQLite
database: the Docker/Portainer stack never creates them. Put
`SEED_TEST_ACCOUNTS=false` in `.env` to turn them off; the account named by
`ADMIN_USERNAME` / `ADMIN_PASSWORD` in `.env` works either way.
Subsequent runs reuse the existing venv and `.env` and start in a couple of
seconds.

To reset local state (fresh database, fresh generated secrets):

```bash
rm -f .env backend/dev.db
npm start
```

## Working on the code

| Command | What it does |
|---|---|
| `npm start` | runs the app locally with auto-reload (see above) |
| `npm test` | the backend tests, plus checks that the page's scripts and HTML agree and that the layout rules below hold |
| `npm run lint` | [ruff](https://docs.astral.sh/ruff/) on the Python, [Prettier](https://prettier.io) on the page's JavaScript (`npm install` once first) |
| `npm run format` | fixes what both can fix by themselves |

GitHub runs the tests, the lint and a Docker build on every push
(`.github/workflows/ci.yml`): a red cross on a commit means one failed, and
the run's log says which.

Two rules keep each part of the app in one place, and tests enforce them:
routers only handle HTTP and never import each other (shared logic lives in
`planning/`, `garmin/` or another module), and the page's modules never
import each other in a circle (what two views share lives in `js/core/`).
When something breaks, [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md)
says where to look.

## Deploying to your server (Docker / Portainer)

1. Choose the hosted app's admin login and the secrets. These five are
   required — the stack refuses to deploy, with a message naming the missing
   one, until they're all set:

   | Variable            | What to put                                         |
   |---------------------|-----------------------------------------------------|
   | `ADMIN_USERNAME`    | the admin username you want (lowercase, e.g. `sam`) |
   | `ADMIN_PASSWORD`    | the admin password you want — at least 12 characters, not reused |
   | `POSTGRES_PASSWORD` | any long random string                              |
   | `JWT_SECRET`        | `python -c "import secrets; print(secrets.token_hex(32))"` |
   | `FERNET_KEY`        | `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |

2. Deploy:
   - **Portainer:** Stacks → Add stack → point it at this repository (or
     paste `docker-compose.yml`), then in the **Environment variables**
     section add the five names above with your values (or use *Load
     variables from .env file* with a filled-in copy of `.env.example`) →
     Deploy the stack.
   - **Command line:** copy `.env.example` to `.env`, fill it in, then
     ```bash
     docker compose up -d --build
     ```

   The admin account is created on the first start. `ADMIN_PASSWORD` is only
   used to create it, so changing the password in the app later sticks. The
   generic `admin` / `user` test accounts from `npm start` are never created
   here.
3. The stack publishes the app on port **8010** of the server (the app
   itself listens on 8000 inside the container): open
   `http://<server-ip>:8010` on your own network. To open it to friends
   elsewhere, see [Letting friends reach it from outside your
   home](#letting-friends-reach-it-from-outside-your-home).

4. Log in as the admin: you land on the dashboard. Under **Create an
   account**, make one for yourself (your training account) and one for each
   friend (**Generate** makes a random password). Send each friend their
   username, password and the URL; they connect their own Garmin on first
   login and can change their password in ⚙️ Settings.

**The admin account is not a training account.** It only shows the
dashboard, so don't set `ADMIN_USERNAME` to an account that holds training
data: that account would be made an admin on every start and lose its
calendar view (the data itself is kept, and comes back with **Make user**).

**Upgrading from the Garmin-login version:** old accounts are named after
their Garmin email and have no password, so they can't log in until the
admin gives them one (dashboard → **Reset password**). Everyone has to log in
again once after the upgrade. The tables of the removed chat (messages and
stored AI API keys) are deleted on the first start.

### Letting friends reach it from outside your home

You need a domain name and HTTPS. The login page takes passwords (and, once,
each person's Garmin password), so never forward the plain `http://` port
8010 from your router to the internet.

Whichever option you pick, set **`APP_DOMAIN`** in the stack to the name
friends will open, for example `training.example.com`. The app then only
answers on `https://training.example.com`: a visit to any other address, or
over plain http, is sent there, and browsers are told to keep using HTTPS
for it. On the server itself `http://localhost:8010` keeps working.

**Option 1 — Cloudflare Tunnel (recommended).** Nothing to open on your
router, your home IP address stays hidden, and Cloudflare provides the
HTTPS certificate. The domain's DNS has to be managed by Cloudflare (the
free plan is enough).

1. In the Cloudflare dashboard go to **Networking → Tunnels → Create a
   tunnel** (older layout: Zero Trust → Networks → Tunnels) and give it a
   name. Cloudflare shows an install command: the long text after
   `--token` is the tunnel token.
2. Add these to the stack's environment variables and redeploy:

   | Variable                  | Value                       |
   |---------------------------|-----------------------------|
   | `APP_DOMAIN`              | `training.example.com`      |
   | `COMPOSE_PROFILES`        | `tunnel`                    |
   | `CLOUDFLARE_TUNNEL_TOKEN` | the token from step 1       |

   `COMPOSE_PROFILES=tunnel` starts one more container, `cloudflared`,
   which connects out to Cloudflare. The tunnel then shows as healthy in
   the dashboard.
3. In the tunnel, **Routes → Add route → Published application** (older
   layout: the *Public Hostname* tab): subdomain `training`, your domain,
   service URL `http://backend:8000`. Leave the other settings as they are;
   in particular *HTTP Host Header* must stay empty, or every visit is sent
   back to the same address in a loop.
4. Open `https://training.example.com`.

If the page stays blank, check that Cloudflare's **Rocket Loader** (Speed
settings) is off: it rewrites the page's scripts, which the page's
security policy then blocks.

If no `cloudflared` container appears after redeploying, your Portainer
doesn't pass `COMPOSE_PROFILES` on. Run the `docker run
cloudflare/cloudflared …` command Cloudflare showed in step 1 instead, and
use `http://<server-ip>:8010` as the service URL in step 3.

For a second lock in front of the login page, add a **Cloudflare Access**
application for the same name with a rule listing your friends' email
addresses. They get a one-time code by email before the app is shown at
all, so strangers never reach the login page.

**Option 2 — your own reverse proxy.** If the server already runs Nginx
Proxy Manager, Caddy or Traefik: add a host `training.example.com` →
`http://<server-ip>:8010` with a Let's Encrypt certificate and HTTPS
forced, create a public DNS record for the name that points at your home
IP address, and forward port 443 (only) from the router to the proxy.
Nobody sits between your friends and your server, but your home IP
address is visible, it has to stay the same (or be kept up to date by a
dynamic-DNS updater), and it doesn't work when your internet provider
shares one address between several customers (CGNAT).

**Option 3 — a private network.** With Tailscale or WireGuard each friend
installs a VPN app and reaches the server's private address; nothing is
public. The most private choice, with more setup per friend. Leave
`APP_DOMAIN` empty.

#### Giving friends access safely

- Create each account from the dashboard with **Generate** and send the
  username and password privately, one friend at a time. A generated
  password saved in their browser is stronger than one they would pick.
- Choose an admin username other than `admin`, and an `ADMIN_PASSWORD`
  you use nowhere else. The admin can reset anyone's password, so that
  one password protects everyone's data.
- Look at the dashboard now and then: failed attempts and logins you
  don't expect show up there. **Reset password** logs that person out
  everywhere; **Delete** removes someone who has left.
- Publish this app's name only. Portainer, the database and any other
  service on the server stay off the tunnel and off the router.
- Keep `ENABLE_API_DOCS=false` and `CORS_ALLOW_ORIGINS` empty (the
  defaults).
- Back up the database volume and the stack's variables. Without
  `FERNET_KEY` the stored Garmin sessions can't be read and everyone has
  to reconnect Garmin.

### Planning with Claude in Chrome

Open the app in Chrome, log in, open the Claude extension and ask in plain
words, for example:

- "Read the **History** tab, then on the **Plan** tab plan the next 4 weeks in
  the draft box, one session per line in the format shown. Save and
  preview, and tell me the weekly totals." Then discuss, let it adjust the draft, and press **Put this
  plan on the calendar** yourself when you're happy.
- "Read my objectives and the last two weeks, then plan next week
  with 3 runs, 2 swims and a long ride. Put the workout details in each
  session."
- "Move Thursday's run to Friday and make it 45 minutes easy."
- "Add an objective: Montreal half marathon on 2027-04-25, target 1:45:00."

Every control has a readable label (e.g. the **+** buttons are "Plan a
session on Mon, Oct 12, 2026 (2026-10-12)") so the extension can find the
right day. Deleting an objective takes two clicks instead of a browser
confirm popup, which would block the extension.

The stack is just two services: `db` (Postgres, with a named volume for
persistence) and `backend` (the FastAPI app, built from `backend/Dockerfile`,
serving both the API and the static frontend).

> **Don't reuse a local SQLite-backed `.env` as-is in production without
> checking it.** The generated secrets are fine to reuse, but local dev
> ignores the `POSTGRES_*` values entirely (it uses SQLite), so make sure
> `POSTGRES_PASSWORD` in your deployed `.env` is actually a value you're
> comfortable putting on your server, not a throwaway.

## Environment variables

Set in `.env` (loaded by both `npm start` and Docker Compose):

| Variable                    | Used by        | Purpose                                                             |
|------------------------------|-----------------|-----------------------------------------------------------------------|
| `POSTGRES_USER`              | docker-compose  | Postgres username (prod only; local dev uses SQLite)                 |
| `POSTGRES_PASSWORD`          | docker-compose  | Postgres password — **required** for the Docker stack                |
| `POSTGRES_DB`                | docker-compose  | Postgres database name                                               |
| `JWT_SECRET`                 | backend         | Signs session tokens — **required**                                  |
| `ADMIN_USERNAME`             | backend         | First admin account, created on startup — **required** for the Docker stack |
| `ADMIN_PASSWORD`             | backend         | Its password, at least 12 characters — **required** for the Docker stack; only used to *create* the account, change it later in the app |
| `SEED_TEST_ACCOUNTS`         | `npm start` only | `true` (the `npm start` default) creates `admin`/`adminadmin` and `user`/`useruser` for local testing; never passed to Docker, refused on Postgres |
| `ENABLE_API_DOCS`            | backend         | `true` serves Swagger UI at `/docs` (default `false`)                |
| `FERNET_KEY`                 | backend         | Encrypts the stored Garmin session at rest — **required**            |
| `GARMIN_SYNC_LOOKBACK_DAYS`  | backend         | How far back a sync looks (default 90); only missing days are requested |
| `AUTO_SYNC_HOURS`            | backend         | Hours between background syncs of every account connected to Garmin; 4 in the Docker stack, 0 (off) otherwise |
| `LOG_LEVEL`                  | backend         | How much the app prints in `docker logs` / the `npm start` terminal: `INFO` (default), `DEBUG` to also see what Garmin didn't answer, `WARNING` for problems only |
| `CORS_ALLOW_ORIGINS`         | backend         | Empty = same-origin only (default); comma-separated origins, or `*`  |
| `APP_DOMAIN`                 | backend         | The public name friends open (e.g. `training.example.com`); when set the app only answers on `https://` that name. Empty (default) = no check |
| `COMPOSE_PROFILES`           | docker-compose  | `tunnel` starts the optional Cloudflare Tunnel container             |
| `CLOUDFLARE_TUNNEL_TOKEN`    | docker-compose  | Token of the Cloudflare Tunnel, only with `COMPOSE_PROFILES=tunnel`  |

## Data model

- **User** — username (stored in the historical `email` column), scrypt
  password hash, admin flag, token version (bumped on password change to
  log out old sessions), last login time, Garmin email + encrypted Garmin
  session token, the athlete profile Garmin keeps (zones, thresholds,
  records, race predictions), the numbers the athlete entered on the
  Athlete tab (FTP, zones, CSS…) and the Plan tab's rule set.
- **LoginEvent** — one row per login attempt (account, success or not,
  time), kept 90 days, for the admin dashboard. A failed attempt on a name
  that isn't an account is stored as "(unknown account)", never with the
  typed text. At most 20 of those are stored per hour, so guessing
  usernames can't fill the database.
- **Objective** — title, event date, target time, notes; added and edited on
  the page.
- **DailyMetrics** — per day: morning Training Readiness, Garmin's training
  status, acute and chronic load and their ratio, load by intensity, VO2max,
  stress and body battery.
- **PlanDraft** — one per user: the weeks being planned, the session lines as
  typed and the block notes. Not on the calendar until confirmed.
- **Activity** — one row per completed Garmin activity: type, start time,
  duration, distance, avg HR, aerobic/anaerobic training effect, training load, calories,
  perceived effort (1-10) and feel, the laps of runs and swims, weather and sweat loss,
  plus the raw Garmin payload for anything not modeled explicitly.
- **SleepRecord** — per-night sleep: total/deep/REM/light minutes, sleep
  score, resting heart rate, and the few parts of Garmin's answer that are
  read (overnight HRV and its status, body battery). The rest of that
  answer, about 100 KB a night of minute-by-minute data, isn't kept; nights
  stored whole by older versions are trimmed on startup.
- **PlannedTraining** — future sessions: date, activity type, planned
  duration and workout details.

New nullable columns are added to existing databases on startup
(`add_missing_columns` in `database.py`).

## API surface

| Endpoint                | Method | Notes                                                       |
|--------------------------|--------|---------------------------------------------------------------|
| `/api/auth/login`        | POST   | Username/password → JWT (429 after 5 failures for a username, or 20 from one address, in 15 min) |
| `/api/auth/me`           | GET    | Who's logged in, admin flag, Garmin status                    |
| `/api/auth/password`     | POST   | Change your own password; returns a new token                 |
| `/api/admin/users`       | GET/POST | Admin: list users / create a user                            |
| `/api/admin/users/{id}`  | PATCH/DELETE | Admin: reset password, grant/remove admin / delete user and their data (`?confirm=<username>` required) |
| `/api/admin/activity`    | GET    | Admin: logins and failed attempts per day (`?days=7&tz_offset=`), totals, recent connections |
| `/api/garmin/connect`    | POST/DELETE | Link your Garmin (email/password sent once to Garmin) / unlink |
| `/api/garmin/sync/start` | POST   | Start syncing on the server (batches until nothing waits) and answer straight away; 409 = connect Garmin first |
| `/api/garmin/sync/status`| GET    | How that sync is going: `idle`, `waiting`, `running`, `done` or `failed`, with the totals so far |
| `/api/garmin/sync`       | POST   | One batch, answered when done: activities, then missing days (sleep, readiness, status, stress) and activity details; `more_to_sync` says to call again; 409 = connect Garmin first |
| `/api/garmin/import-history` | POST | `{"months": 12}`: older activities, for the long-term weekly totals |
| `/api/calendar`          | GET    | `?start=&end=` (default: today ±14 days), grouped by day, with multisport legs |
| `/api/objectives`        | GET/POST | List / add objectives (title, event date, target time)      |
| `/api/objectives/{id}`   | PATCH/DELETE | Edit or remove an objective                             |
| `/api/plan/log`          | GET    | Training log as text (`?weeks=6&detail=full|summary`): sessions, effort/feel, laps, sleep and recovery, athlete profile, objectives, what's planned |
| `/api/athlete`           | GET/PUT | The athlete's own numbers (FTP, zones, CSS, equipment…), plus what Garmin knows as text |
| `/api/plan/rules`        | GET/PUT | The rule set: free text the plan follows, added to the history |
| `/api/plan/draft`        | GET/PUT | The draft plan (first week, number of weeks, session lines, notes), returned parsed with errors and weekly totals |
| `/api/plan/draft/confirm`| POST   | Put the draft on the calendar, replacing planned sessions from today to the draft's last day |
| `/api/share`             | GET    | Your username and share code (created on first use) |
| `/api/share/code`        | POST   | A new share code; the old one stops working |
| `/api/share/import/preview` | POST | `{username, share_code, source_start, weeks, target_start}`: their planned sessions moved onto your weeks, as a weekly preview. Changes nothing |
| `/api/share/import/plan` | POST   | Same body: make them your Plan draft (block notes kept) |
| `/api/share/import/calendar` | POST | Same body: put them on your calendar, replacing your planned sessions from today to the last imported day |
| `/api/planned`           | POST   | Plan a session (date, sport, duration in minutes)              |
| `/api/planned/{id}`      | PATCH/DELETE | Edit (a new `date` moves it), or remove a planned session |

The Garmin, calendar, objectives and planned endpoints are for regular
accounts; an admin account gets a 403 there. The `/api/admin/*` endpoints are
admin-only.

Interactive API docs are at `/docs` (Swagger UI) when `ENABLE_API_DOCS=true`.

## Security notes

- Garmin credentials are sent once to log in and are **never stored** —
  only the resulting session token, encrypted with Fernet, is persisted.
- App passwords are stored as salted scrypt hashes. Sessions are signed JWTs
  (1 week) that stop working when the password changes or the account is
  deleted. There is no sign-up endpoint.
- Whoever runs the server holds everyone's Garmin sessions (encrypted with
  `FERNET_KEY`, which is on the same server) — so friends are trusting the
  admin with read access to their Garmin data. Back up `.env` and keep it
  private.
- Guessing is limited twice: five wrong passwords lock a username for 15
  minutes, and twenty wrong passwords from one address (whatever names it
  tried) stop that address for as long. The visitor's address is read from
  the headers Cloudflare or the reverse proxy adds. Someone who knows a
  username can still keep it locked by sending it wrong passwords;
  Cloudflare Access in front of the login page prevents that.
- The app refuses to create the first admin with a password shorter than
  12 characters (the container stops, with the reason in its log).
- With `APP_DOMAIN` set, the app only answers on `https://` that name and
  sends `Strict-Transport-Security` (30 days, that name only).
- Responses carry `X-Frame-Options: DENY`, `nosniff`, `no-store` on the
  API, `X-Robots-Tag: noindex`, and a `Content-Security-Policy` that only
  lets the page load this app's own script and stylesheet.
- Using the unofficial `garminconnect`/`garth` library (logging in with your
  real Garmin email/password against reverse-engineered endpoints) is
  against Garmin's ToS, though it's standard practice for self-hosted Garmin
  dashboards (Home Assistant, Grafana, etc.). Acceptable for a personal
  project; know the tradeoff.

## Known limitations

The Garmin side has been used with real accounts (login, resumed sessions,
activities, sleep, recovery, effort and feel, laps), through `garth` 0.4.x
and `garminconnect` 0.2.x. What to know:

- Garmin accounts with two-step verification (MFA) can't connect yet —
  `/api/garmin/connect` returns an "MFA not supported" error for those.
- Garmin's API is unofficial and undocumented, so a change on Garmin's side
  can break part of the sync until the libraries catch up.
- Garmin sessions do eventually expire; `/api/garmin/sync` surfaces expiry
  as a 409 and the page shows the "Connect your Garmin account" form, but
  there's no proactive refresh.
- FastAPI is held at 0.115, which pins an older Starlette. Its published
  advisories don't apply to how this app uses it (no forms or uploads, not
  on Windows, Range headers dropped); upgrading both is the next
  maintenance step.
