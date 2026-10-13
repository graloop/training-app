import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from .. import garmin_client, garmin_details, models, schemas
from ..config import get_settings
from ..database import get_db
from ..deps import get_current_member
from ..security import decrypt_text, encrypt_text
from .auth import me_out

router = APIRouter(prefix="/api/garmin", tags=["garmin"])
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
        # 400, not 401: a failed *Garmin* login must not log the user out of this app.
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    user.garmin_email = garmin_email
    user.garmin_session_encrypted = encrypt_text(session_data)
    db.commit()
    return me_out(user)


@router.delete("/connect", response_model=schemas.MeOut)
def disconnect(db: Session = Depends(get_db), user: models.User = Depends(get_current_member)):
    """Forget the stored Garmin session. Already-synced data stays."""
    user.garmin_session_encrypted = None
    db.commit()
    return me_out(user)


def _garmin_api(user: models.User):
    # 409 (not 401) when Garmin isn't usable: the frontend shows the
    # "Connect Garmin" form instead of logging the user out of the app.
    if not user.garmin_session_encrypted:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Connect your Garmin account first.")
    try:
        return garmin_client.get_authenticated_client(decrypt_text(user.garmin_session_encrypted))
    except (ValueError, garmin_client.GarminAuthError) as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Your Garmin session expired, please reconnect Garmin. ({exc})",
        ) from exc


@router.post("/sync", response_model=schemas.GarminSyncResponse)
def sync(
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_member),
):
    api = _garmin_api(user)

    lookback_days = settings.garmin_sync_lookback_days
    end_date = datetime.date.today()
    start_date = end_date - datetime.timedelta(days=lookback_days)

    try:
        raw_activities = garmin_client.fetch_activities(api, start_date, end_date)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Garmin sync failed: {exc}") from exc

    activities_synced = garmin_details.save_activities(db, user, raw_activities)
    db.commit()

    # Everything below is saved step by step and can't fail the sync: what
    # Garmin doesn't answer now is simply asked again next time.
    days_synced = sleep_synced = days_remaining = details_synced = 0
    try:
        days_synced, sleep_synced, days_remaining = garmin_details.sync_days(db, api, user, start_date, end_date)
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()
    try:
        details_synced = garmin_details.enrich_activities(db, api, user)
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()
        details_synced = 0
    try:
        garmin_details.refresh_profile(db, api, user)
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()

    return schemas.GarminSyncResponse(
        activities_synced=activities_synced,
        sleep_records_synced=sleep_synced,
        lookback_days=lookback_days,
        details_synced=details_synced,
        days_synced=days_synced,
        more_to_sync=days_remaining > 0 or garmin_details.pending_details(db, user) > 0,
    )


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
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Garmin import failed: {exc}") from exc
    imported = garmin_details.save_activities(db, user, raw_activities)
    db.commit()
    return schemas.HistoryImportResponse(activities_imported=imported, since=since)
