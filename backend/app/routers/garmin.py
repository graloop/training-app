import datetime
import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from .. import models, schemas
from ..config import get_settings
from ..database import get_db
from ..deps import get_current_member
from ..garmin import autosync
from ..garmin import client as garmin_client
from ..garmin import sync as garmin_sync
from ..security import decrypt_text, encrypt_text

router = APIRouter(prefix="/api/garmin", tags=["garmin"])
log = logging.getLogger("trainer.garmin")
settings = get_settings()


@router.post("/connect", response_model=schemas.MeOut)
def connect(
    payload: schemas.GarminConnectRequest,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_member),
):
    """Link (or re-link) this user's own Garmin Connect account. The Garmin
    password is sent once to Garmin and never stored; only the resulting
    session is kept, encrypted."""
    garmin_email = payload.garmin_email.lower()
    try:
        session_data = garmin_client.login_and_export_session(garmin_email, payload.garmin_password)
    except garmin_client.GarminAuthError as exc:
        log.warning("Garmin connect failed for %r: %s", user.username, exc)
        # 400, not 401: a failed *Garmin* login must not log the user out of this app.
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    user.garmin_email = garmin_email
    user.garmin_session_encrypted = encrypt_text(session_data)
    db.commit()
    return schemas.MeOut.of(user)


@router.delete("/connect", response_model=schemas.MeOut)
def disconnect(db: Session = Depends(get_db), user: models.User = Depends(get_current_member)):
    """Forget the stored Garmin session. Already-synced data stays."""
    user.garmin_session_encrypted = None
    db.commit()
    return schemas.MeOut.of(user)


def _garmin_api(user: models.User):
    # 409 (not 401) when Garmin isn't usable: the frontend shows the
    # "Connect Garmin" form instead of logging the user out of the app.
    if not user.garmin_session_encrypted:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Connect your Garmin account first.")
    try:
        return garmin_client.get_authenticated_client(decrypt_text(user.garmin_session_encrypted))
    except (ValueError, garmin_client.GarminAuthError) as exc:
        log.warning("Garmin session of %r can't be resumed, asking to reconnect: %s", user.username, exc)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Your Garmin session expired, please reconnect Garmin. ({exc})",
        ) from exc


@router.post("/sync/start", response_model=schemas.SyncJob)
def start_sync(user: models.User = Depends(get_current_member)):
    """Start syncing on the server (as many batches as it takes, up to a few)
    and answer straight away; GET /sync/status says how it is going. Starting
    again while a job runs just returns that job."""
    running = autosync.job_status(user.id)
    if running and running["state"] in ("waiting", "running"):
        return schemas.SyncJob(**running)
    return schemas.SyncJob(**autosync.start_job(user.id, _garmin_api(user)))


@router.get("/sync/status", response_model=schemas.SyncJob)
def sync_status(user: models.User = Depends(get_current_member)):
    job = autosync.job_status(user.id)
    return schemas.SyncJob(**job) if job else schemas.SyncJob(state="idle")


@router.post("/sync", response_model=schemas.GarminSyncResponse)
def sync(
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_member),
):
    """One batch, answered when it is done (the page uses /sync/start instead)."""
    api = _garmin_api(user)
    # The background sync may be working on this account: wait for it rather
    # than run the same steps twice at once.
    lock = autosync.user_lock(user.id)
    if not lock.acquire(timeout=autosync.MANUAL_WAIT_SECONDS):
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="A sync is already running in the background. Try again in a minute.")
    try:
        result = garmin_sync.sync_user(db, api, user, datetime.date.today(), settings.garmin_sync_lookback_days)
        garmin_sync.save_session(db, api, user)
    except Exception as exc:  # noqa: BLE001 - only the activity list can fail a sync
        log.exception("Garmin sync failed for %r", user.username)
        db.rollback()
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Garmin sync failed: {exc}") from exc
    finally:
        lock.release()
    return schemas.GarminSyncResponse(**result)


@router.post("/import-history", response_model=schemas.HistoryImportResponse)
def import_history(
    payload: schemas.HistoryImportRequest,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_member),
):
    """Bring in older activities (one list from Garmin, no per-day requests) so
    the log can show the long-term picture. Sleep and recovery data stay
    limited to what the regular sync covers."""
    api = _garmin_api(user)
    today = datetime.date.today()
    since = today - datetime.timedelta(days=round(payload.months * 30.5))
    until = today - datetime.timedelta(days=settings.garmin_sync_lookback_days)
    if since >= until:
        return schemas.HistoryImportResponse(activities_imported=0, since=since)
    try:
        raw_activities = garmin_client.fetch_activities(api, since, until)
    except Exception as exc:  # noqa: BLE001
        log.exception("Importing older activities failed for %r", user.username)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Garmin import failed: {exc}") from exc
    imported = garmin_sync.save_activities(db, user, raw_activities)
    db.commit()
    return schemas.HistoryImportResponse(activities_imported=imported, since=since)
