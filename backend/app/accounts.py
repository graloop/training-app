"""Creating the first admin, and the login lockout."""

import datetime
import logging
import threading
import time

from sqlalchemy.orm import Session

from . import models
from .config import get_settings
from .database import SessionLocal
from .security import hash_password, verify_password

log = logging.getLogger("trainer.accounts")


def normalize_username(username: str) -> str:
    return username.strip().lower()


def bootstrap_admin() -> None:
    """Make sure ADMIN_USERNAME exists and is an admin. Its password comes from
    ADMIN_PASSWORD only when the account is created (or has no password yet),
    so a password changed in the app isn't reset by the next restart."""
    settings = get_settings()
    username = normalize_username(settings.admin_username)
    with SessionLocal() as db:
        if not username or not settings.admin_password:
            if not db.query(models.User).filter(models.User.is_admin.is_(True)).first():
                log.warning("No admin account: set ADMIN_USERNAME and ADMIN_PASSWORD to create one.")
            return
        _ensure_admin(db, username, settings.admin_password)


# The admin can reset anyone's password, so whoever guesses this one reaches
# every friend's data.
ADMIN_PASSWORD_MIN = 12


def _ensure_admin(db: Session, username: str, password: str) -> None:
    user = db.query(models.User).filter(models.User.username == username).first()
    if user is None or not user.hashed_password:
        if len(password) < ADMIN_PASSWORD_MIN:
            raise RuntimeError(
                f"ADMIN_PASSWORD is too short: use at least {ADMIN_PASSWORD_MIN} characters, then start the app again."
            )
    if user is None:
        user = models.User(username=username, hashed_password=hash_password(password), is_admin=True, token_version=0)
        db.add(user)
        log.info("Created admin account %r", username)
    else:
        user.is_admin = True
        if not user.hashed_password:
            user.hashed_password = hash_password(password)
    db.commit()


# --- Generic test accounts (local testing only) ----------------------------
# Well-known logins for trying the app as an admin and as a regular user.

TEST_ACCOUNTS = (
    # username, password, is_admin
    ("admin", "adminadmin", True),
    ("user", "useruser", False),
)


def seed_test_accounts() -> None:
    """With SEED_TEST_ACCOUNTS=true, make sure the generic test accounts exist
    with their well-known passwords and roles (re-applied on every start, so
    they stay predictable). Refused unless the database is local SQLite: the
    hosted app runs on Postgres and must never get these public passwords."""
    settings = get_settings()
    if not settings.seed_test_accounts:
        return
    if not settings.database_url.startswith("sqlite"):
        log.error("SEED_TEST_ACCOUNTS is ignored: test accounts are only created on a local SQLite database.")
        return
    with SessionLocal() as db:
        for username, password, is_admin in TEST_ACCOUNTS:
            user = db.query(models.User).filter(models.User.username == username).first()
            if user is None:
                user = models.User(username=username, hashed_password=hash_password(password), token_version=0)
                db.add(user)
            elif not verify_password(password, user.hashed_password):
                user.hashed_password = hash_password(password)
            user.is_admin = is_admin
        db.commit()
    log.warning("Test accounts enabled (local testing only): admin / adminadmin, user / useruser")


# --- Connection log (admin dashboard) --------------------------------------

LOGIN_EVENTS_KEPT_DAYS = 90
UNKNOWN_ACCOUNT = "(unknown account)"
# Attempts on real accounts are limited by the lockout. Attempts on made-up
# names aren't, so only this many are stored per hour: the dashboard still
# shows that someone is guessing, and a stranger can't fill the database.
UNKNOWN_ATTEMPTS_KEPT_PER_HOUR = 20


def record_login(db: Session, user: models.User | None, success: bool) -> None:
    """Log a login attempt. `user` is None when the name isn't an account: the
    typed name is not kept then (people type passwords in the wrong box)."""
    now = models.utcnow()
    if user is None:
        hour_ago = now - datetime.timedelta(hours=1)
        stored = (
            db.query(models.LoginEvent)
            .filter(models.LoginEvent.user_id.is_(None), models.LoginEvent.created_at >= hour_ago)
            .count()
        )
        if stored >= UNKNOWN_ATTEMPTS_KEPT_PER_HOUR:
            return
    db.add(models.LoginEvent(
        user_id=user.id if user else None,
        username=user.username if user else UNKNOWN_ACCOUNT,
        success=success,
        created_at=now,
    ))
    if success:
        user.last_login_at = now
        cutoff = now - datetime.timedelta(days=LOGIN_EVENTS_KEPT_DAYS)
        db.query(models.LoginEvent).filter(models.LoginEvent.created_at < cutoff).delete()
    db.commit()


# --- Login lockout ---------------------------------------------------------
# In memory (one process, a handful of users): after MAX_FAILURES wrong
# passwords for a username within WINDOW seconds, that username is locked
# until the oldest failure falls out of the window.
#
# The same count is kept per visitor address, whatever names were tried: one
# address guessing at every account, or at made-up names, is turned away
# before its password is even checked. The limit is higher because friends in
# the same house or on the same phone network share an address.

MAX_FAILURES = 5
MAX_ADDRESS_FAILURES = 20
MAX_SHARE_FAILURES = 10  # wrong name/share code pairs per account, so codes can't be guessed
WINDOW_SECONDS = 15 * 60
MAX_TRACKED_USERNAMES = 5000

_failures: dict[str, list[float]] = {}
_address_failures: dict[str, list[float]] = {}
_share_failures: dict[str, list[float]] = {}
_lock = threading.Lock()


def _recent(store: dict[str, list[float]], key: str, now: float) -> list[float]:
    times = [t for t in store.get(key, []) if now - t < WINDOW_SECONDS]
    store[key] = times
    return times


def _seconds_over_limit(store: dict[str, list[float]], key: str, limit: int) -> int:
    now = time.monotonic()
    with _lock:
        times = _recent(store, key, now)
        if len(times) < limit:
            return 0
        return int(WINDOW_SECONDS - (now - times[-limit])) + 1


def seconds_locked(username: str) -> int:
    return _seconds_over_limit(_failures, username, MAX_FAILURES)


def seconds_address_blocked(address: str) -> int:
    return _seconds_over_limit(_address_failures, address, MAX_ADDRESS_FAILURES)


def seconds_share_blocked(user_id: int) -> int:
    return _seconds_over_limit(_share_failures, str(user_id), MAX_SHARE_FAILURES)


def record_share_failure(user_id: int) -> None:
    now = time.monotonic()
    with _lock:
        _recent(_share_failures, str(user_id), now).append(now)


def record_failure(username: str, address: str | None = None) -> None:
    now = time.monotonic()
    with _lock:
        for store, key in ((_failures, username), (_address_failures, address)):
            if key is None:
                continue
            if len(store) > MAX_TRACKED_USERNAMES:
                # Forget names whose failures have all aged out (guessing many
                # different names must not grow this forever).
                for name in [n for n, times in store.items() if not times or now - times[-1] >= WINDOW_SECONDS]:
                    del store[name]
            _recent(store, key, now).append(now)


def clear_failures(username: str) -> None:
    """A good login or a password reset unlocks the username. The address keeps
    its count: logging in to one account doesn't buy more guesses at others."""
    with _lock:
        _failures.pop(username, None)


def reset_all_failures() -> None:  # tests
    with _lock:
        _failures.clear()
        _address_failures.clear()
        _share_failures.clear()
