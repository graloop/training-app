"""Creating the first admin, and the login lockout."""

import logging
import threading
import time

from sqlalchemy.orm import Session

from . import models
from .config import get_settings
from .database import SessionLocal
from .security import hash_password

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


def _ensure_admin(db: Session, username: str, password: str) -> None:
    user = db.query(models.User).filter(models.User.username == username).first()
    if user is None:
        user = models.User(username=username, hashed_password=hash_password(password), is_admin=True, token_version=0)
        db.add(user)
        log.info("Created admin account %r", username)
    else:
        user.is_admin = True
        if not user.hashed_password:
            user.hashed_password = hash_password(password)
    db.commit()


# --- Login lockout ---------------------------------------------------------
# In memory (one process, a handful of users): after MAX_FAILURES wrong
# passwords for a username within WINDOW seconds, that username is locked
# until the oldest failure falls out of the window.

MAX_FAILURES = 5
WINDOW_SECONDS = 15 * 60

_failures: dict[str, list[float]] = {}
_lock = threading.Lock()


def _recent(username: str, now: float) -> list[float]:
    times = [t for t in _failures.get(username, []) if now - t < WINDOW_SECONDS]
    _failures[username] = times
    return times


def seconds_locked(username: str) -> int:
    now = time.monotonic()
    with _lock:
        times = _recent(username, now)
        if len(times) < MAX_FAILURES:
            return 0
        return int(WINDOW_SECONDS - (now - times[0])) + 1


def record_failure(username: str) -> None:
    now = time.monotonic()
    with _lock:
        _recent(username, now).append(now)


def clear_failures(username: str) -> None:
    with _lock:
        _failures.pop(username, None)


def reset_all_failures() -> None:  # tests
    with _lock:
        _failures.clear()
