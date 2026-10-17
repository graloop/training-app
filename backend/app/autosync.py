"""Background sync: every few hours, each account connected to Garmin is synced
on the server, so last night's sleep, the morning readiness and finished
sessions are there without anyone pressing Sync.

On in the Docker stack (AUTO_SYNC_HOURS, default 4), off under `npm start`.
"""

import datetime
import logging
import threading
import time

from . import garmin_client, garmin_details, models
from .config import get_settings
from .database import SessionLocal
from .security import decrypt_text

log = logging.getLogger("trainer.autosync")

FIRST_RUN_DELAY_SECONDS = 5 * 60  # not right at startup: restarts shouldn't hit Garmin
ROUNDS_PER_ACCOUNT = 3  # a sync works in batches; a few are enough to catch up
PAUSE_BETWEEN_ACCOUNTS_SECONDS = 10
MANUAL_WAIT_SECONDS = 120  # the Sync button waits this long for a background run

_locks: dict[int, threading.Lock] = {}
_locks_guard = threading.Lock()
_started = False


def user_lock(user_id: int) -> threading.Lock:
    """One sync at a time per account, whether from the button or the background."""
    with _locks_guard:
        return _locks.setdefault(user_id, threading.Lock())


def connected_accounts() -> list[int]:
    with SessionLocal() as db:
        return [
            user_id for (user_id,) in db.query(models.User.id).filter(
                models.User.garmin_session_encrypted.is_not(None),
                (models.User.is_admin.is_(False)) | (models.User.is_admin.is_(None)),
            )
        ]


def sync_account(user_id: int, today: datetime.date | None = None) -> bool:
    """A few sync rounds for one account. False when it was skipped or failed;
    a failure (expired Garmin session, Garmin down) is logged, never raised."""
    lock = user_lock(user_id)
    if not lock.acquire(blocking=False):
        return False  # someone is pressing Sync right now
    try:
        with SessionLocal() as db:
            user = db.get(models.User, user_id)
            if user is None or not user.garmin_session_encrypted or user.is_admin:
                return False
            try:
                api = garmin_client.get_authenticated_client(decrypt_text(user.garmin_session_encrypted))
            except (ValueError, garmin_client.GarminAuthError) as exc:
                log.warning("Background sync skipped for %r: the Garmin session needs reconnecting (%s)", user.username, exc)
                return False
            lookback = get_settings().garmin_sync_lookback_days
            for _ in range(ROUNDS_PER_ACCOUNT):
                result = garmin_details.sync_user(db, api, user, today or datetime.date.today(), lookback)
                if not result["more_to_sync"]:
                    break
            return True
    except Exception as exc:  # noqa: BLE001 - never let one account stop the others
        log.warning("Background sync failed for account %s: %s", user_id, exc)
        return False
    finally:
        lock.release()


def sync_all() -> int:
    """Sync every connected account, one after the other. Returns how many worked."""
    synced = 0
    for i, user_id in enumerate(connected_accounts()):
        if i:
            time.sleep(PAUSE_BETWEEN_ACCOUNTS_SECONDS)
        synced += sync_account(user_id)
    log.info("Background sync done: %s account(s)", synced)
    return synced


def _loop(hours: float) -> None:
    time.sleep(FIRST_RUN_DELAY_SECONDS)
    while True:
        try:
            sync_all()
        except Exception as exc:  # noqa: BLE001
            log.warning("Background sync round failed: %s", exc)
        time.sleep(hours * 3600)


def start(hours: float) -> bool:
    """Start the background sync every `hours` hours (0 = off). Once per process."""
    global _started
    if hours <= 0 or _started:
        return False
    _started = True
    threading.Thread(target=_loop, args=(hours,), name="garmin-autosync", daemon=True).start()
    log.info("Background Garmin sync every %s h", hours)
    return True
