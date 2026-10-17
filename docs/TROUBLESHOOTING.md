# Troubleshooting

What to do when something breaks, without needing anyone's help. Start with
the logs (step 1), then find your symptom below. Each one names the file to
open if you need to change code.

Commands are written for the Docker stack run from this folder
(`docker compose ...`). If you deploy with Portainer, either open the
container in Portainer (Containers → `<stack>-backend-1` → **Logs** or
**Console**), or use `docker ps` to find the container names and replace
`docker compose exec backend` with `docker exec -it <stack>-backend-1`.

## 1. Read the logs first

| Where the app runs | How to see its logs |
|---|---|
| Docker, command line | `docker compose logs --tail=200 backend` (add `-f` to keep following) |
| Portainer | Containers → `<stack>-backend-1` → Logs |
| `npm start` | the terminal where it runs |
| The browser | F12 → **Console** for script errors, **Network** for the failing `/api/...` request and its answer |

A log line looks like this:

```
2026-10-17 13:54:37 WARNING trainer.accounts: Too many failed logins: username 'sam' blocked for 15 min
```

The name after the level says where it comes from:

| Logger | File(s) |
|---|---|
| `trainer.main` | `backend/app/main.py` (startup, settings in use) |
| `trainer.accounts` | `backend/app/accounts.py` (admin account, logins, lockouts) |
| `trainer.garmin` | `backend/app/garmin/` and `backend/app/routers/garmin.py` |
| `INFO: 1.2.3.4 - "POST /api/garmin/sync HTTP/1.1" 502` | uvicorn: one line per request, with its status code |

The first line after each start says which version runs, which database it
uses, whether the background sync is on and the public address:

```
INFO    trainer.main: Training App version 15 starting · database postgresql+psycopg2://trainer:***@db:5432/trainer · background sync every 4 h · public address https://training.example.com
```

A `Traceback (most recent call last):` block is a crash report. Read it
**from the bottom**: the last line is the error, and the `File "/app/app/..."`
lines just above it show which of the app's files and lines led there.

Set `LOG_LEVEL=DEBUG` in the stack's variables (or `.env`) and restart to
also see what Garmin didn't answer during a sync. That's normal now and
then, so it is hidden by default.

## 2. Where the code for each part is

| Part of the app | Backend (Python) | Page (JavaScript / CSS) |
|---|---|---|
| Login, logout, password | `routers/auth.py`, `accounts.py`, `security.py` | `js/main.js`, `js/views/settings.js` |
| Admin dashboard | `routers/admin.py` | `js/views/admin.js`, `css/admin.css` |
| Calendar | `routers/calendar.py`, `routers/planned.py` | `js/views/calendar.js`, `js/views/session-dialog.js`, `css/calendar.css` |
| Garmin connect and sync | `garmin/client.py` (calls to Garmin), `garmin/sync.py` (what is fetched and stored), `garmin/autosync.py` (background), `routers/garmin.py` | `js/views/garmin.js` |
| Athlete tab | `routers/athlete.py`, `routers/objectives.py` | `js/views/athlete.js`, `js/views/objectives.js`, `css/athlete.css` |
| Plan tab | `routers/plan.py`, `planning/draft_parser.py`, `planning/drafts.py` | `js/views/plan.js`, `js/views/plan-preview.js`, `css/plan.css` |
| History tab (the log) | `planning/training_log.py`, `planning/activity_text.py` | `js/views/history.js`, `css/history.css` |
| Import tab | `routers/share.py`, `sharing.py` | `js/views/import.js`, `css/import.css` |
| Every request to the server | `main.py` (security headers, redirects) | `js/core/api.js` |
| Database tables | `models.py`, `database.py` | |

Backend paths are under `backend/app/`, page paths under `backend/frontend/`.

## 3. Symptoms

### The page doesn't load, or says "The server didn't answer (error 502...)"

1. `docker compose ps`: is `backend` **running** and **healthy**? If it is
   restarting, its logs say why. The usual causes:
   - A required variable is missing. Docker refuses to start and names it
     (`set JWT_SECRET in .env`, `set ADMIN_PASSWORD...`).
   - `FERNET_KEY` isn't a valid key: generate one with the command in
     `.env.example`. **Changing** it makes every stored Garmin session
     unreadable (everyone has to reconnect Garmin).
   - The database isn't reachable: look at `docker compose logs db`.
2. Through Cloudflare: `docker compose logs cloudflared`. The tunnel's route
   must point at `http://backend:8000`.
3. To restart: `docker compose restart backend` (Portainer: the container →
   **Restart**).

### "This app is only available at https://..." or the page keeps redirecting

`APP_DOMAIN` must be exactly the name typed in the browser. In Cloudflare,
the route's *HTTP Host Header* must stay empty. `http://localhost:8010` on
the server itself always works, whatever `APP_DOMAIN` says.

### The page is blank, or a button does nothing

Open F12 → **Console**. A red error names the file and line
(`views/plan.js:123`): that's where to look (table in section 2).
"Refused to execute ... Content Security Policy" behind Cloudflare means
**Rocket Loader** is on: turn it off in Cloudflare's Speed settings.

### Can't log in

| Message | What to do |
|---|---|
| "Wrong username or password" | Give the account a new password: `docker compose exec backend python -m app.cli reset-password <username>` (it prints the new one) |
| "Too many failed attempts. Try again in N min." | Wait 15 minutes, or restart the backend: the counters are kept in memory |
| Nobody knows the admin password | The same `reset-password` command. `ADMIN_PASSWORD` in the stack is only used to *create* the admin, so changing it doesn't help |
| "Your session expired, please log in again." | Normal after 7 days, after a password change or reset, and for everyone when `JWT_SECRET` changes |

`python -m app.cli list-users` lists every account with its role, Garmin
connection and last login. Locally: `cd backend && .venv/bin/python -m app.cli list-users`.

### Garmin

| Message on the page | Meaning, and what to do |
|---|---|
| "Connect your Garmin account first." | ⚙️ Settings → Reconnect Garmin |
| "Your Garmin session expired, please reconnect Garmin." | Garmin ended the stored session: reconnect. If it happens to **everyone at once** after a redeploy, `FERNET_KEY` changed (the log says "Could not decrypt stored Garmin session") |
| "Garmin login failed: Garmin rejected that email or password." | Check them on connect.garmin.com |
| "...accounts with MFA enabled aren't supported yet." | Turn off two-step verification on the Garmin account, or use another one |
| "...too many attempts, Garmin asks to wait a while" | Garmin's own limit: wait an hour |
| "Garmin sync failed: ..." | Garmin refused the activity list. The log has the full error (`trainer.garmin`, with a traceback). Often temporary: try again later. If it keeps failing for everyone, Garmin may have changed its (unofficial) API: see below |
| "A sync is already running in the background. Try again in a minute." | Wait for the background sync to finish |
| "Older data is still waiting: sync again to continue." | Normal for the first syncs: they catch up in batches |

**When Garmin changes its API.** The app uses the unofficial
`garminconnect` / `garth` libraries (pinned in `backend/requirements.txt`;
read the comment there before changing the versions). Check their GitHub
pages for an issue that matches the error in the log. If a newer `0.2.x`
release fixes it, change the pin, run `npm test`, then redeploy.

**Some days are missing sleep or readiness.** Each day is asked up to 3
times. `LOG_LEVEL=DEBUG` shows "Garmin didn't answer ... for <day>".

**The background sync doesn't happen.** The startup line must say
`background sync every N h` (`AUTO_SYNC_HOURS`, 4 in the Docker stack).
The first run is 5 minutes after a start, then each run logs
`Background sync done: N account(s)`.

### The Plan or History tab shows something wrong

- A draft line flagged in red: the message says what to fix. The parser is
  `planning/draft_parser.py`, and `tests/test_plan.py` shows every format
  it accepts.
- The History log misses or misstates something: `planning/training_log.py`
  builds it with one function per section (`_objective_lines`,
  `_athlete_section`, `_week_lines`...). One activity's line is
  `planning/activity_text.py`.

### "column ... does not exist" after an upgrade

On startup the app adds the **optional** columns a new version needs
(`add_missing_columns` in `database.py`). It never renames, removes or
changes a column. If a new version needs that, run the `ALTER TABLE` by
hand (see section 4).

## 4. The database

Open it:

```bash
docker compose exec db psql -U trainer trainer     # Docker (Postgres)
sqlite3 backend/dev.db                             # npm start (SQLite)
```

Useful queries (the username is in the column `email`, for historical reasons):

```sql
SELECT id, email AS username, is_admin, last_login_at FROM users;
SELECT username, success, created_at FROM login_events ORDER BY id DESC LIMIT 20;
SELECT count(*) FROM activities WHERE user_id = 2;
```

Table names are in `backend/app/models.py` (`__tablename__`).

### Back up and restore

```bash
# Back up (keep the stack's variables too: without FERNET_KEY the Garmin sessions can't be read)
docker compose exec -T db pg_dump -U trainer trainer > backup-$(date +%F).sql

# Restore: stop the app, empty the database, load the backup, start again
docker compose stop backend
docker compose exec db dropdb -U trainer trainer
docker compose exec db createdb -U trainer trainer
docker compose exec -T db psql -U trainer trainer < backup-2026-10-17.sql
docker compose start backend
```

## 5. Going back to an earlier version

Each release is one commit named `vN: ...` (`git log --oneline`), and the
running version is in the page footer and on the first log line. To go back,
put a branch on the earlier commit (`git branch rollback <commit>` then
`git push origin rollback`), point the Portainer stack's Git reference at
`refs/heads/rollback` and redeploy; on the command line,
`git checkout <commit>` then `docker compose up -d --build`. Back up the
database first (section 4). Versions only ever add optional columns, so an
earlier version usually runs on a newer database: it ignores the new columns.

## 6. Before changing code

Run `npm test` and `npm run lint`. The tests cover the backend and check
that the page's scripts and HTML still agree (`tests/test_frontend.py`);
the lint catches mistakes like an unused import or a misspelt name. GitHub
runs both on every push. Two rules keep the
code easy to find your way in, and tests enforce both:

- Routers only handle HTTP and never import each other. Shared logic goes
  in `planning/`, `garmin/` or another module (`tests/test_structure.py`).
- Page modules never import each other in a circle. What two views need
  goes in `js/core/` (`tests/test_frontend.py`).
