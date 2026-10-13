#!/usr/bin/env bash
# Local dev runner for the Training Coach backend (invoked via `npm start`).
# Sets up a Python virtualenv, installs deps, generates a local .env on first
# run, and starts the FastAPI app with SQLite + auto-reload on :8000.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 is required but was not found on PATH." >&2
  exit 1
fi

if [ ! -d backend/.venv ]; then
  echo "Creating Python virtualenv at backend/.venv ..."
  python3 -m venv backend/.venv
fi

# shellcheck disable=SC1091
source backend/.venv/bin/activate
pip install -q --disable-pip-version-check -r backend/requirements.txt

if [ ! -f .env ]; then
  echo "No .env found — creating one from .env.example with freshly generated secrets."
  cp .env.example .env
  python3 - <<'PY'
import secrets
from pathlib import Path

from cryptography.fernet import Fernet

path = Path(".env")
text = path.read_text()
text = text.replace("JWT_SECRET=change-me", f"JWT_SECRET={secrets.token_hex(32)}")
text = text.replace("POSTGRES_PASSWORD=change-me", f"POSTGRES_PASSWORD={secrets.token_hex(16)}")
text = text.replace("FERNET_KEY=", f"FERNET_KEY={Fernet.generate_key().decode()}")
admin_password = secrets.token_urlsafe(12)
text = text.replace("ADMIN_PASSWORD=\n", f"ADMIN_PASSWORD={admin_password}\n")
path.write_text(text)
PY
  echo "Generated JWT_SECRET / FERNET_KEY / POSTGRES_PASSWORD / ADMIN_PASSWORD in .env."
fi

set -a
# shellcheck disable=SC1091
source .env
set +a

# Local runs default to SQLite regardless of the Postgres settings in .env
# (those are only used by docker-compose). Override DATABASE_URL yourself if
# you want to point local dev at a real Postgres instance.
export DATABASE_URL="${DATABASE_URL:-sqlite:///./dev.db}"

# Generic accounts for local testing (never created by the Docker deployment).
# Set SEED_TEST_ACCOUNTS=false in .env to turn them off.
export SEED_TEST_ACCOUNTS="${SEED_TEST_ACCOUNTS:-true}"

if [ "$SEED_TEST_ACCOUNTS" = "true" ]; then
  echo "Test logins:  admin / adminadmin  (admin)   ·   user / useruser  (regular user)"
elif [ -z "${ADMIN_PASSWORD:-}" ]; then
  echo "Note: ADMIN_PASSWORD is empty in .env, so no admin account is created. Set it and restart."
fi

echo "Starting Training Coach at http://localhost:8000"
cd backend
exec uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
