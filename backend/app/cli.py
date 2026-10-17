"""Account rescue from the command line, for when nobody can log in to fix it
from the dashboard. Uses the same database as the app (DATABASE_URL).

    python -m app.cli list-users
    python -m app.cli reset-password USERNAME [--password NEW]

In Docker:  docker compose exec backend python -m app.cli list-users
Locally:    cd backend && .venv/bin/python -m app.cli list-users
"""

import argparse
import secrets
import string
import sys

from . import accounts, models
from .database import SessionLocal
from .security import hash_password

MIN_PASSWORD_LENGTH = 8  # same as the dashboard


def list_users() -> int:
    with SessionLocal() as db:
        users = db.query(models.User).order_by(models.User.username).all()
        if not users:
            print("No accounts yet. The admin is created on startup from ADMIN_USERNAME / ADMIN_PASSWORD.")
            return 0
        print(f"{'username':<24} {'role':<6} {'garmin':<10} last login")
        for u in users:
            role = "admin" if u.is_admin else "user"
            garmin = "connected" if u.garmin_session_encrypted else "-"
            last = u.last_login_at.strftime("%Y-%m-%d %H:%M UTC") if u.last_login_at else "never"
            print(f"{u.username:<24} {role:<6} {garmin:<10} {last}")
    return 0


def reset_password(username: str, password: str | None) -> int:
    password = password or "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(16))
    if len(password) < MIN_PASSWORD_LENGTH:
        print(f"The password needs {MIN_PASSWORD_LENGTH}+ characters.", file=sys.stderr)
        return 1
    name = accounts.normalize_username(username)
    with SessionLocal() as db:
        user = db.query(models.User).filter(models.User.username == name).first()
        if user is None:
            print(f"No account named {name!r}. `list-users` shows them.", file=sys.stderr)
            return 1
        user.hashed_password = hash_password(password)
        user.token_version = (user.token_version or 0) + 1  # logs them out everywhere, like the dashboard
        db.commit()
    print(f"Password for {name} is now: {password}")
    print("They are logged out everywhere. If the app said the username was locked, restart it to clear that.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description="Training App account rescue.")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list-users", help="every account, its role, Garmin connection and last login")
    reset = commands.add_parser("reset-password", help="give an account a new password (generated unless --password)")
    reset.add_argument("username")
    reset.add_argument("--password", help="the new password (8+ characters); a random one is generated otherwise")
    args = parser.parse_args(argv)
    if args.command == "list-users":
        return list_users()
    return reset_password(args.username, args.password)


if __name__ == "__main__":
    sys.exit(main())
