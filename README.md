# AI Training Coach (Garmin-integrated)

A self-hosted personal training coach. It syncs your Garmin Connect
activities and sleep into a 4-week calendar, and includes a Claude-powered
chatbot that discusses how training is going and can directly edit your
upcoming plan (not just suggest changes — it actually rewrites the calendar).

## Features

- **Login with Garmin** — your Garmin Connect email/password *is* your app
  login. The first login creates your account; every login refreshes the
  stored Garmin session and immediately syncs your data (JWT sessions).
- **Calendar** — Monday–Sunday weeks covering ~2 weeks back and 2 weeks
  ahead, showing completed Garmin activities (type, duration, training
  effect) and planned sessions (light blue) per day, with each week's
  planned vs. done hours in a column on the right.
- **Plan sessions yourself** — the **+** in the bottom-right of each day
  opens a small window to plan a swim, ride, run or strength session and its
  duration. Click a planned session to edit or delete it.
- **Garmin sync** — runs automatically after login; the "Sync with Garmin"
  button pulls new activities/sleep on demand. If the Garmin session has
  expired you're sent back to the login form.
- **Coach chat** — tell it how a session felt; it has context on what you've
  trained, your sleep, your stated objectives, and can use a tool call to
  create/update/delete sessions on your plan, which then shows up on the
  calendar immediately.
- **Pick your AI** — the ⚙️ Settings menu (top right) lets each user choose
  the chatbot behind the coach — Claude, ChatGPT, Gemini, Mistral, or **Custom**
  (any OpenAI- or Anthropic-compatible API URL) — pick a
  model, and paste their own API key (stored encrypted, never sent back to
  the browser).
- **Objectives** — tell the coach about your target event in the chat
  ("Montreal half marathon on April 20, aiming for 1:45:00") and it records
  the title, event date and target time as an objective card, with a
  countdown. You can edit or delete it from the card.
- **Sleep** — each day shows its Garmin sleep score (top right, coloured
  good / fair / poor), and each week's column shows the average.

## Stack

| Layer      | Choice                                                              |
|------------|----------------------------------------------------------------------|
| Backend    | Python, FastAPI, SQLAlchemy                                          |
| Database   | Postgres in production; SQLite automatically for local dev            |
| Garmin     | unofficial `garminconnect` / `garth` libraries                       |
| AI         | Anthropic SDK for Claude; OpenAI SDK for ChatGPT/Gemini/Mistral; tool use (`update_training_plan`) |
| Auth       | Garmin Connect login (delegated), JWT session tokens                 |
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
    │   ├── security.py     password hashing, JWT, Fernet encryption
    │   ├── deps.py          get_current_user auth dependency
    │   ├── garmin_client.py wraps garth/garminconnect for login + sync
    │   ├── ai_coach.py       Claude chat + plan-editing tool loop
    │   └── routers/          auth, garmin, calendar, objectives, chat
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

Everything works out of the box. For the coach chat, open ⚙️ Settings in
the top right, choose a chatbot and paste your API key. (Optionally, set a
server-wide fallback `ANTHROPIC_API_KEY=sk-ant-...` in `.env` and restart
`npm start`; users without their own key will then use Claude with it.) Subsequent runs reuse the existing venv and `.env`
and start in a couple of seconds.

To reset local state (fresh database, fresh generated secrets):

```bash
rm -f .env backend/dev.db
npm start
```

## Deploying to your server (Docker / Portainer)

1. Copy `.env.example` to `.env` and fill in real values — at minimum
   `POSTGRES_PASSWORD`, `JWT_SECRET`, `FERNET_KEY` (`ANTHROPIC_API_KEY` is optional).
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
| `FERNET_KEY`                 | backend         | Encrypts the stored Garmin session at rest — **required**            |
| `ANTHROPIC_API_KEY`          | backend         | Optional fallback Claude key for users who haven't added their own   |
| `ANTHROPIC_MODEL`            | backend         | Model used with the fallback key (default `claude-opus-5-5`)         |
| `GARMIN_SYNC_LOOKBACK_DAYS`  | backend         | How many days of history each sync pulls (default 30)                 |
| `CORS_ALLOW_ORIGINS`         | backend         | Comma-separated allowed origins, or `*`                              |

## Data model

- **User** — Garmin account email, encrypted Garmin session token
  (plus legacy free-text goals, still passed to the coach if present).
- **Objective** — title, event date, target time; created by the coach's
  `update_objectives` tool, editable by the user. (`hashed_password` is a legacy column, left empty.)
- **Activity** — one row per completed Garmin activity: type, start time,
  duration, distance, avg HR, aerobic/anaerobic training effect, calories,
  plus the raw Garmin payload for anything not modeled explicitly.
- **SleepRecord** — per-night sleep: total/deep/REM/light minutes, sleep
  score, raw payload.
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
| `/api/auth/login`        | POST   | Garmin email/password; creates/updates the user, returns JWT  |
| `/api/garmin/sync`       | POST   | Pulls recent activities + sleep, deduped by Garmin id/date     |
| `/api/calendar`          | GET    | `?start=&end=` (default: today ±14 days), grouped by day       |
| `/api/objectives`        | GET    | Objectives (title, event date, target time), set by the coach |
| `/api/objectives/{id}`   | PATCH/DELETE | Edit or remove an objective                             |
| `/api/chat`              | POST   | Send a message; may apply plan changes via tool call           |
| `/api/chat/history`      | GET    | Past chat messages                                             |
| `/api/ai-settings`       | GET/PUT | Chatbot provider, model, API key (only the last 4 chars are returned), custom URL/format |
| `/api/planned`           | POST   | Plan a session (date, sport, duration in minutes)              |
| `/api/planned/{id}`      | PATCH/DELETE | Edit or remove a planned session                       |

Interactive API docs are available at `/docs` (Swagger UI) once the app is
running.

## Security notes

- Garmin credentials are sent once to log in and are **never stored** —
  only the resulting session token, encrypted with Fernet, is persisted.
- The app has no passwords of its own: login is checked by Garmin, and the
  password is discarded after use. Sessions are signed JWTs.
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
- Garmin accounts with MFA/2FA enabled can't log in yet — `/api/auth/login`
  returns an "MFA not supported" error for those accounts.
- The shape of `get_activities_by_date` / `get_sleep_data` responses (field
  names like `activityType.typeKey`, `dailySleepDTO.sleepScores.overall.value`)
  is based on documented/observed `garminconnect` output but should be
  spot-checked against a real payload, since Garmin's undocumented API can
  drift between library versions.
- Garmin sessions do eventually expire; `/api/garmin/sync` surfaces expiry
  as a 401 and the app sends the user back to the Garmin login, but there's no proactive refresh.

The default model ids for ChatGPT (`gpt-5`), Gemini (`gemini-2.5-flash`)
and Mistral (`mistral-large-latest`) live in `backend/app/ai_providers.py`;
users can type any other model id their provider offers in Settings.
