# AI Training Coach (Garmin-integrated)

A self-hosted personal training coach. It syncs your Garmin Connect
activities and sleep into a 4-week calendar, and includes a Claude-powered
chatbot that discusses how training is going and can directly edit your
upcoming plan (not just suggest changes — it actually rewrites the calendar).

## Features

- **Login** — simple email/password accounts (JWT sessions).
- **Calendar** — 4-week view (2 weeks back, 2 weeks ahead) showing completed
  Garmin activities (type, duration, training effect) and planned upcoming
  sessions side by side, per day.
- **Garmin sync** — a "Sync with Garmin" button. First use prompts for your
  Garmin email/password to establish a session; every sync after that just
  pulls new activities/sleep.
- **Coach chat** — tell it how a session felt; it has context on what you've
  trained, your sleep, your stated objectives, and can use a tool call to
  create/update/delete sessions on your plan, which then shows up on the
  calendar immediately.
- **Objectives** — a free-text goal statement (e.g. "half marathon in
  spring, 3 runs + 2 swims/week") that the coach uses as ongoing context.

## Stack

| Layer      | Choice                                                              |
|------------|----------------------------------------------------------------------|
| Backend    | Python, FastAPI, SQLAlchemy                                          |
| Database   | Postgres in production; SQLite automatically for local dev            |
| Garmin     | unofficial `garminconnect` / `garth` libraries                       |
| AI         | Anthropic Python SDK, tool use (`update_training_plan`)               |
| Auth       | bcrypt-hashed passwords, JWT session tokens                          |
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

Everything works out of the box except the coach chat, which needs an
Anthropic API key. Open `.env`, set:

```
ANTHROPIC_API_KEY=sk-ant-...
```

and restart `npm start`. Subsequent runs reuse the existing venv and `.env`
and start in a couple of seconds.

To reset local state (fresh database, fresh generated secrets):

```bash
rm -f .env backend/dev.db
npm start
```

## Deploying to your server (Docker / Portainer)

1. Copy `.env.example` to `.env` and fill in real values — at minimum
   `POSTGRES_PASSWORD`, `JWT_SECRET`, `FERNET_KEY`, `ANTHROPIC_API_KEY`.
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
| `ANTHROPIC_API_KEY`          | backend         | Enables the coach chat endpoint                                      |
| `ANTHROPIC_MODEL`            | backend         | Model id for chat (default `claude-sonnet-4-6` — verify before deploying, see below) |
| `GARMIN_SYNC_LOOKBACK_DAYS`  | backend         | How many days of history each sync pulls (default 30)                 |
| `CORS_ALLOW_ORIGINS`         | backend         | Comma-separated allowed origins, or `*`                              |

## Data model

- **User** — email, hashed password, encrypted Garmin session token, Garmin
  account email, free-text objectives.
- **Activity** — one row per completed Garmin activity: type, start time,
  duration, distance, avg HR, aerobic/anaerobic training effect, calories,
  plus the raw Garmin payload for anything not modeled explicitly.
- **SleepRecord** — per-night sleep: total/deep/REM/light minutes, sleep
  score, raw payload.
- **PlannedTraining** — future sessions: date, activity type, planned
  duration, coaching notes, and whether it was set by the AI or manually.
- **ChatMessage** — role (user/assistant), content, timestamp — chat history
  and the context window for the coach.

## API surface

| Endpoint                | Method | Notes                                                       |
|--------------------------|--------|---------------------------------------------------------------|
| `/api/auth/signup`       | POST   | Create account, returns JWT                                   |
| `/api/auth/login`        | POST   | Returns JWT                                                    |
| `/api/garmin/connect`    | POST   | First-time Garmin login; stores encrypted session              |
| `/api/garmin/sync`       | POST   | Pulls recent activities + sleep, deduped by Garmin id/date     |
| `/api/calendar`          | GET    | `?start=&end=` (default: today ±14 days), grouped by day       |
| `/api/objectives`        | GET/POST | Free-text training goals                                     |
| `/api/chat`              | POST   | Send a message; may apply plan changes via tool call           |
| `/api/chat/history`      | GET    | Past chat messages                                             |

Interactive API docs are available at `/docs` (Swagger UI) once the app is
running.

## Security notes

- Garmin credentials are sent once to log in and are **never stored** —
  only the resulting session token, encrypted with Fernet, is persisted.
- Passwords are hashed with bcrypt; sessions are signed JWTs.
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
- Garmin accounts with MFA/2FA enabled aren't handled — `/api/garmin/connect`
  will just fail with a generic error for those accounts.
- The shape of `get_activities_by_date` / `get_sleep_data` responses (field
  names like `activityType.typeKey`, `dailySleepDTO.sleepScores.overall.value`)
  is based on documented/observed `garminconnect` output but should be
  spot-checked against a real payload, since Garmin's undocumented API can
  drift between library versions.
- Garmin sessions do eventually expire; `/api/garmin/sync` surfaces expiry
  as a 400 asking the user to reconnect, but there's no proactive refresh.

Also double-check `ANTHROPIC_MODEL` (default `claude-sonnet-4-6`, see
`backend/app/config.py` / `.env.example`) against the current model id in
the [Anthropic docs](https://docs.anthropic.com/) before deploying — model
ids are updated over time.
