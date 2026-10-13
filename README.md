# Training Coach (Garmin-integrated)

A self-hosted training calendar for you and a few friends. Each person logs
in with an account the admin created, connects their own Garmin Connect,
and sees their activities, sleep and resting heart rate on a 4-week
calendar next to their planned sessions, objectives and notes.

There's no AI API to pay for: open the page in Chrome and ask the
**Claude in Chrome** extension to plan your week, add objectives or adjust
sessions — it edits the page like you would. (The older built-in chat that
calls an AI API is still there, off by default: `AI_CHAT_ENABLED=true`.)

## Features

- **Private accounts** — only people the admin created can log in (username
  + password, no sign-up). The admin manages accounts from **👥 Users**:
  create, reset password, grant admin, delete. Everyone can change their own
  password in ⚙️ Settings. Five wrong passwords lock that username for
  15 minutes; a password change or reset logs out every other session.
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
  **← Earlier / Later →** move the calendar 2 weeks at a time. Click a planned session to edit or delete it. When a planned
  session is done (same day, same sport, time within 15% of the plan) the
  two merge into one dark-green "done as planned" entry.
- **Garmin sync** — runs automatically after login; the "Sync with Garmin"
  button pulls new activities/sleep on demand. If the Garmin session has
  expired you're sent back to the login form.
- **Coach chat** *(only with `AI_CHAT_ENABLED=true`)* — tell it how a session felt; it has context on what you've
  trained, your sleep, your stated objectives, and can use a tool call to
  create/update/delete sessions on your plan, which then shows up on the
  calendar immediately.
  Each successful Garmin sync clears the conversation, so it never grows
  forever (a failed sync keeps it).
- **Pick your AI** *(only with `AI_CHAT_ENABLED=true`)* — the ⚙️ Settings menu (top right) lets each user choose
  the chatbot behind the coach — Claude, ChatGPT, Gemini, Mistral, or **Custom**
  (any OpenAI- or Anthropic-compatible API URL) — pick a
  model, and paste their own API key (stored encrypted, never sent back to
  the browser). A **Test** button checks the server can reach that AI
  before you save.
- **Objectives** — **+ Add objective** records a target event (title, event
  date, target time) as a card with a countdown; edit or delete it from the
  card.
- **Notes for your coach** — free text for availability, injuries,
  preferences and season plan, so whoever plans your week (you, or Claude in
  Chrome) has the context on the page.
- **Sleep & recovery** — each day shows its Garmin sleep score (top right,
  coloured good / fair / poor) and resting heart rate (❤️ bpm); each week's
  column shows the average sleep score and average resting heart rate.
- **Training load** — each completed activity shows Garmin's training load
  next to its duration (e.g. "1 h · load 113"), and each week's column
  shows the week's total load.

## Stack

| Layer      | Choice                                                              |
|------------|----------------------------------------------------------------------|
| Backend    | Python, FastAPI, SQLAlchemy                                          |
| Database   | Postgres in production; SQLite automatically for local dev            |
| Garmin     | unofficial `garminconnect` / `garth` libraries                       |
| AI         | Anthropic SDK for Claude; OpenAI SDK for ChatGPT/Gemini/Mistral; tool use (`update_training_plan`) |
| Auth       | Local accounts created by the admin (scrypt hashes), JWT session tokens |
| Frontend   | plain HTML/CSS/JS, served directly by FastAPI — no build step        |
| Deployment | one Docker Compose stack (`db` + `backend`), deployable via Portainer |

There's no separate frontend server/container: FastAPI serves the API under
`/api/*` and the static frontend at `/` from the same process.

## Project structure

```
.
├── package.json          npm wrapper — `npm start` runs scripts/dev.sh
├── scripts/dev.sh         local dev launcher (venv, .env, uvicorn --reload)
├── docker-compose.yml     production stack: postgres + backend
├── .env.example           template for secrets/config (copy to .env)
└── backend/
    ├── Dockerfile
    ├── requirements.txt
    ├── app/
    │   ├── main.py         FastAPI app, mounts routers + static frontend
    │   ├── config.py       env-driven settings
    │   ├── database.py     SQLAlchemy engine/session
    │   ├── models.py       User, Activity, SleepRecord, PlannedTraining, ChatMessage
    │   ├── schemas.py       Pydantic request/response models
    │   ├── security.py     scrypt password hashing, JWT, Fernet encryption
    │   ├── deps.py          get_current_user auth dependency
    │   ├── garmin_client.py wraps garth/garminconnect for login + sync
    │   ├── ai_coach.py       Claude chat + plan-editing tool loop
    │   ├── accounts.py       first-admin bootstrap + login lockout
    │   └── routers/          auth, admin, profile, garmin, calendar, objectives, planned, chat, ai_settings
    └── frontend/             index.html, app.js, styles.css (static, no build)
```

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

The first run prints the admin login (`admin` / a generated password, also
saved as `ADMIN_PASSWORD` in `.env`). Subsequent runs reuse the existing venv
and `.env` and start in a couple of seconds.

To reset local state (fresh database, fresh generated secrets):

```bash
rm -f .env backend/dev.db
npm start
```

## Deploying to your server (Docker / Portainer)

1. Copy `.env.example` to `.env` and fill in real values — at minimum
   `POSTGRES_PASSWORD`, `JWT_SECRET`, `FERNET_KEY`, `ADMIN_USERNAME` and
   `ADMIN_PASSWORD` (use a long one).
   (If you already generated these via `npm start` locally, you can reuse
   that `.env` — see the warning below first.)
2. From the project root:
   ```bash
   docker compose up -d --build
   ```
   or, in Portainer, create a stack from `docker-compose.yml` and paste the
   same environment variables in the stack's env editor.
3. The app listens on port 8000. Put your own reverse proxy in front of it
   if you want TLS/a domain name — the compose file doesn't include one.

4. Log in as the admin, open **👥 Users** and create an account for each
   friend (**Generate** makes a random password). Send each friend their
   username, password and the URL; they connect their own Garmin on first
   login and can change their password in ⚙️ Settings.

**Upgrading from the Garmin-login version:** old accounts are named after
their Garmin email and have no password, so they can't log in until they get
one. Either set `ADMIN_USERNAME` to your old Garmin email (that account
becomes the admin with `ADMIN_PASSWORD`, keeping its data), or give an old
account a password from 👥 Users → Reset password. Everyone has to log in
again once after the upgrade.

### Letting friends reach it from outside your home

Put it behind your reverse proxy with HTTPS (e.g. a Nginx Proxy Manager host
`trainer.yourdomain.com` → `http://<server>:8010`) — never expose the plain
HTTP port. Then pick how friends get to the proxy:

- **Public hostname** (easiest for friends): a public DNS record for the
  hostname plus a port-forward of 443 to the proxy, or a Cloudflare Tunnel
  (no port-forward, hides your home IP). Only the login page is public;
  everything else needs an account.
- **VPN only** (most private): give each friend a WireGuard peer and keep
  the app LAN-only. More setup per friend.

Keep `ENABLE_API_DOCS=false` and `CORS_ALLOW_ORIGINS` empty when it's
reachable from the internet (the defaults).

### Planning with Claude in Chrome

Open the app in Chrome, log in, open the Claude extension and ask in plain
words, for example:

- "Read my objectives, my notes and the last two weeks, then plan next week
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
| `ADMIN_USERNAME`             | backend         | First admin account, created on startup (default `admin`)            |
| `ADMIN_PASSWORD`             | backend         | Its password — only used to *create* it; change it later in the app  |
| `AI_CHAT_ENABLED`            | backend         | `true` brings back the built-in coach chat + AI settings (default `false`) |
| `ENABLE_API_DOCS`            | backend         | `true` serves Swagger UI at `/docs` (default `false`)                |
| `FERNET_KEY`                 | backend         | Encrypts the stored Garmin session at rest — **required**            |
| `ANTHROPIC_API_KEY`          | backend         | Chat only: fallback Claude key for users who haven't added their own |
| `ANTHROPIC_MODEL`            | backend         | Model used with the fallback key (default `claude-opus-5-5`)         |
| `GARMIN_SYNC_LOOKBACK_DAYS`  | backend         | How many days of history each sync pulls (default 30)                 |
| `CORS_ALLOW_ORIGINS`         | backend         | Empty = same-origin only (default); comma-separated origins, or `*`  |

## Data model

- **User** — username (stored in the historical `email` column), scrypt
  password hash, admin flag, token version (bumped on password change to
  log out old sessions), Garmin email + encrypted Garmin session token, and
  the free-text "notes for your coach".
- **Objective** — title, event date, target time; added and edited on the
  page (or by the chat's `update_objectives` tool when the chat is on).
- **Activity** — one row per completed Garmin activity: type, start time,
  duration, distance, avg HR, aerobic/anaerobic training effect, training load, calories,
  plus the raw Garmin payload for anything not modeled explicitly.
- **SleepRecord** — per-night sleep: total/deep/REM/light minutes, sleep
  score, resting heart rate, raw payload.
- **PlannedTraining** — future sessions: date, activity type, planned
  duration, coaching notes, and whether it was set by the AI or manually.
- **AISettings** — per user: chosen provider, optional model override, and
  their API key (Fernet-encrypted), plus URL and API format for a custom
  provider. New nullable columns are added to existing databases on startup
  (`add_missing_columns` in `database.py`).
- **ChatMessage** — role (user/assistant), content, timestamp — chat history
  and the context window for the coach.

## API surface

| Endpoint                | Method | Notes                                                       |
|--------------------------|--------|---------------------------------------------------------------|
| `/api/auth/login`        | POST   | Username/password → JWT (429 after 5 failures in 15 min)      |
| `/api/auth/me`           | GET    | Who's logged in, admin flag, Garmin status, whether the chat is on |
| `/api/auth/password`     | POST   | Change your own password; returns a new token                 |
| `/api/admin/users`       | GET/POST | Admin: list users / create a user                            |
| `/api/admin/users/{id}`  | PATCH/DELETE | Admin: reset password, grant/remove admin / delete user and their data |
| `/api/profile/notes`     | GET/PUT | Your notes for the coach                                     |
| `/api/garmin/connect`    | POST/DELETE | Link your Garmin (email/password sent once to Garmin) / unlink |
| `/api/garmin/sync`       | POST   | Pulls recent activities + sleep, deduped by Garmin id/date; 409 = connect Garmin first |
| `/api/calendar`          | GET    | `?start=&end=` (default: today ±14 days), grouped by day       |
| `/api/objectives`        | GET/POST | List / add objectives (title, event date, target time)      |
| `/api/objectives/{id}`   | PATCH/DELETE | Edit or remove an objective                             |
| `/api/chat`              | POST   | Chat only (404 unless `AI_CHAT_ENABLED`); may apply plan changes |
| `/api/chat/history`      | GET    | Past chat messages                                             |
| `/api/ai-settings`       | GET/PUT | Chatbot provider, model, API key (only the last 4 chars are returned), custom URL/format |
| `/api/ai-settings/test`  | POST   | Send one tiny request with the form's settings (nothing saved) to check the server reaches the AI |
| `/api/planned`           | POST   | Plan a session (date, sport, duration in minutes)              |
| `/api/planned/{id}`      | PATCH/DELETE | Edit or remove a planned session                       |

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
- Responses carry `X-Frame-Options: DENY`, `nosniff` and `no-store` on the
  API.
- Using the unofficial `garminconnect`/`garth` library (logging in with your
  real Garmin email/password against reverse-engineered endpoints) is
  against Garmin's ToS, though it's standard practice for self-hosted Garmin
  dashboards (Home Assistant, Grafana, etc.). Acceptable for a personal
  project; know the tradeoff.

## Known limitations / things to verify against a real Garmin account

The Garmin integration (`backend/app/garmin_client.py`) has **not** been
exercised against a live Garmin login — there was no real account available
to test against while building this. It was validated locally with `garth`
0.4.x, which exposes both the string-based (`dumps()`/`loads()`) and
directory-based (`dump()`/`load()`) session APIs; the code tries the former
and falls back to the latter for older library versions. Before relying on
it in production:

- Confirm a real login actually round-trips a resumable session end to end.
- Garmin accounts with MFA/2FA enabled can't connect yet —
  `/api/garmin/connect` returns an "MFA not supported" error for those.
- The shape of `get_activities_by_date` / `get_sleep_data` responses (field
  names like `activityType.typeKey`, `dailySleepDTO.sleepScores.overall.value`)
  is based on documented/observed `garminconnect` output but should be
  spot-checked against a real payload, since Garmin's undocumented API can
  drift between library versions.
- Garmin sessions do eventually expire; `/api/garmin/sync` surfaces expiry
  as a 409 and the page shows the "Connect your Garmin account" form, but
  there's no proactive refresh.

The default model ids for ChatGPT (`gpt-5`), Gemini (`gemini-2.5-flash`)
and Mistral (`mistral-large-latest`) live in `backend/app/ai_providers.py`;
users can type any other model id their provider offers in Settings.
