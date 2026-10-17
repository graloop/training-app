#!/usr/bin/env bash
# Local dev runner for the Training App backend (invoked via `npm start`).
# Sets up a Python virtualenv, installs deps, generates a local .env on first
# run, and starts the FastAPI app with SQLite + auto-reload on :8000.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

# The newest Python 3.11+ on PATH: macOS's own python3 is 3.9, too old for the
# pinned dependencies, so prefer a versioned one (e.g. Homebrew's python3.14).
PYTHON=""
for candidate in python3.14 python3.13 python3.12 python3.11 python3; do
  if command -v "$candidate" >/dev/null 2>&1 \
    && "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then
    PYTHON="$candidate"
    break
  fi
done
if [ -z "$PYTHON" ]; then
  echo "Python 3.11+ is required but was not found on PATH (python3 is $(python3 --version 2>&1))." >&2
  echo "Install one, e.g.: brew install python@3.14" >&2
  exit 1
fi

# A virtualenv stores its absolute path and Python: rebuild it when the
# project folder was moved or its Python is too old.
if [ -d backend/.venv ] && ! { backend/.venv/bin/pip --version >/dev/null 2>&1 \
    && backend/.venv/bin/python -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; }; then
  echo "backend/.venv was made for another folder or an older Python: rebuilding it ..."
  rm -rf backend/.venv
fi

if [ ! -d backend/.venv ]; then
  echo "Creating Python virtualenv at backend/.venv with $($PYTHON --version) ..."
  "$PYTHON" -m venv backend/.venv
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

echo "Starting Training App at http://localhost:8000"
cd backend
exec uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
