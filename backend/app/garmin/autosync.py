"""Syncing on the server, never inside a page request.

- Every few hours, each account connected to Garmin is synced, so last night's
  sleep, the morning readiness and finished sessions are there without anyone
  pressing Sync. On in the Docker stack (AUTO_SYNC_HOURS, default 4), off
  under `npm start`.
- The Sync button starts a job here and the page asks how it is going. A long
  catch-up then never hits the proxy's time limit (Cloudflare gives up after
  100 s), and the page stays usable meanwhile.
"""

import datetime
import logging
import threading
import time

from .. import models
from ..config import get_settings
from ..database import SessionLocal
from ..security import decrypt_text
from . import client as garmin_client
from . import sync as garmin_sync

log = logging.getLogger("trainer.garmin")

FIRST_RUN_DELAY_SECONDS = 5 * 60  # not right at startup: restarts shouldn't hit Garmin
ROUNDS_PER_ACCOUNT = 3  # a sync works in batches; a few are enough to catch up
PAUSE_BETWEEN_ACCOUNTS_SECONDS = 10
MANUAL_WAIT_SECONDS = 120  # POST /api/garmin/sync waits this long for a background run
JOB_ROUNDS = 8  # a job keeps syncing batches until nothing waits, up to this many
JOB_WAIT_SECONDS = 15 * 60  # a job waits this long for a background run of the same account

_locks: dict[int, threading.Lock] = {}
_locks_guard = threading.Lock()
_started = False
_jobs: dict[int, dict] = {}  # user id -> the latest job started from the page


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
                result = garmin_sync.sync_user(db, api, user, today or datetime.date.today(), lookback)
                if not result["more_to_sync"]:
                    break
            garmin_sync.save_session(db, api, user)
            return True
    except Exception as exc:  # noqa: BLE001 - never let one account stop the others
        log.warning("Background sync failed for account %s: %s", user_id, exc, exc_info=True)
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


# --- Jobs started from the page ---------------------------------------------------


def job_status(user_id: int) -> dict | None:
    with _locks_guard:
        job = _jobs.get(user_id)
        return dict(job) if job else None


def _update(job: dict, **fields) -> None:
    with _locks_guard:
        job.update(fields)


def start_job(user_id: int, api) -> dict:
    """Start syncing this account in the background, unless a job already is.
    Returns the job as it is now (see schemas.SyncJob)."""
    with _locks_guard:
        job = _jobs.get(user_id)
        if job and job["state"] in ("waiting", "running"):
            return dict(job)
        job = {"state": "waiting", "rounds": 0, "activities_synced": 0, "days_synced": 0, "details_synced": 0,
               "more_to_sync": False, "error": None, "started_at": models.utcnow(), "finished_at": None}
        _jobs[user_id] = job
    threading.Thread(target=_run_job, args=(user_id, api, job), name=f"garmin-sync-{user_id}", daemon=True).start()
    return dict(job)


def _run_job(user_id: int, api, job: dict) -> None:
    lock = user_lock(user_id)
    if not lock.acquire(timeout=JOB_WAIT_SECONDS):
        _update(job, state="failed", finished_at=models.utcnow(),
                error="Another sync of this account is taking too long. Try again later.")
        return
    try:
        _update(job, state="running")
        with SessionLocal() as db:
            user = db.get(models.User, user_id)
            if user is not None:
                lookback = get_settings().garmin_sync_lookback_days
                for _ in range(JOB_ROUNDS):
                    result = garmin_sync.sync_user(db, api, user, datetime.date.today(), lookback)
                    _update(job, rounds=job["rounds"] + 1, more_to_sync=result["more_to_sync"],
                            activities_synced=max(job["activities_synced"], result["activities_synced"]),
                            days_synced=job["days_synced"] + result["days_synced"],
                            details_synced=job["details_synced"] + result["details_synced"])
                    if not result["more_to_sync"]:
                        break
                garmin_sync.save_session(db, api, user)
        _update(job, state="done", finished_at=models.utcnow())
    except Exception as exc:  # noqa: BLE001 - reported to the page
        log.warning("Sync job failed for account %s: %s", user_id, exc, exc_info=True)
        _update(job, state="failed", finished_at=models.utcnow(), error=f"Garmin sync failed: {exc}")
    finally:
        lock.release()


def reset_jobs() -> None:  # tests
    with _locks_guard:
        _jobs.clear()


# --- Every few hours ---------------------------------------------------------------


def _loop(hours: float) -> None:
    time.sleep(FIRST_RUN_DELAY_SECONDS)
    while True:
        try:
            sync_all()
        except Exception as exc:  # noqa: BLE001
            log.warning("Background sync round failed: %s", exc, exc_info=True)
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
