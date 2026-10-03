import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from .. import garmin_client, models, schemas
from ..config import get_settings
from ..database import get_db
from ..deps import get_current_user
from ..security import decrypt_text

router = APIRouter(prefix="/api/garmin", tags=["garmin"])
settings = get_settings()


@router.post("/sync", response_model=schemas.GarminSyncResponse)
def sync(
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    if not user.garmin_session_encrypted:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="No Garmin session stored. Log in again.",
        )

    # Logging in is what (re)connects Garmin, so an unusable session is
    # reported as 401 and the frontend sends the user back to the login form.
    try:
        session_data = decrypt_text(user.garmin_session_encrypted)
        api = garmin_client.get_authenticated_client(session_data)
    except (ValueError, garmin_client.GarminAuthError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Garmin session expired, please log in again: {exc}",
        ) from exc

    lookback_days = settings.garmin_sync_lookback_days
    end_date = datetime.date.today()
    start_date = end_date - datetime.timedelta(days=lookback_days)

    try:
        raw_activities = garmin_client.fetch_activities(api, start_date, end_date)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Garmin sync failed: {exc}") from exc

    activities_synced = 0
    for raw in raw_activities:
        garmin_activity_id = str(raw.get("activityId"))
        if not garmin_activity_id:
            continue

        existing = (
            db.query(models.Activity)
            .filter(models.Activity.user_id == user.id, models.Activity.garmin_activity_id == garmin_activity_id)
            .first()
        )

        start_time_raw = raw.get("startTimeLocal") or raw.get("startTimeGMT")
        try:
            start_time = datetime.datetime.fromisoformat(start_time_raw) if start_time_raw else None
        except ValueError:
            start_time = None
        if start_time is None:
            continue

        activity_type = (raw.get("activityType") or {}).get("typeKey", "unknown")

        fields = dict(
            activity_type=activity_type,
            name=raw.get("activityName"),
            start_time=start_time,
            duration_seconds=raw.get("duration"),
            distance_meters=raw.get("distance"),
            avg_hr=raw.get("averageHR"),
            aerobic_training_effect=raw.get("aerobicTrainingEffect"),
            anaerobic_training_effect=raw.get("anaerobicTrainingEffect"),
            calories=raw.get("calories"),
            training_load=raw.get("activityTrainingLoad"),
            raw_json=raw,
        )

        if existing:
            for key, value in fields.items():
                setattr(existing, key, value)
        else:
            db.add(models.Activity(user_id=user.id, garmin_activity_id=garmin_activity_id, **fields))
        activities_synced += 1

    sleep_synced = 0
    day = start_date
    while day <= end_date:
        raw_sleep = garmin_client.fetch_sleep(api, day)
        day_dto = (raw_sleep or {}).get("dailySleepDTO") or {}
        if day_dto.get("sleepTimeSeconds") is None:
            day += datetime.timedelta(days=1)
            continue

        sleep_score = None
        scores = day_dto.get("sleepScores") or {}
        overall = scores.get("overall") or {}
        if isinstance(overall, dict):
            sleep_score = overall.get("value")

        fields = dict(
            total_sleep_seconds=day_dto.get("sleepTimeSeconds"),
            deep_sleep_seconds=day_dto.get("deepSleepSeconds"),
            rem_sleep_seconds=day_dto.get("remSleepSeconds"),
            light_sleep_seconds=day_dto.get("lightSleepSeconds"),
            sleep_score=sleep_score,
            resting_heart_rate=(raw_sleep or {}).get("restingHeartRate"),
            raw_json=raw_sleep,
        )

        existing = (
            db.query(models.SleepRecord)
            .filter(models.SleepRecord.user_id == user.id, models.SleepRecord.date == day)
            .first()
        )
        if existing:
            for key, value in fields.items():
                setattr(existing, key, value)
        else:
            db.add(models.SleepRecord(user_id=user.id, date=day, **fields))
        sleep_synced += 1
        day += datetime.timedelta(days=1)

    # Each sync starts a fresh coach conversation, so the chat never grows forever.
    # (Same transaction: a failed sync keeps the chat.)
    db.query(models.ChatMessage).filter(models.ChatMessage.user_id == user.id).delete()
    db.commit()

    return schemas.GarminSyncResponse(
        activities_synced=activities_synced,
        sleep_records_synced=sleep_synced,
        lookback_days=lookback_days,
    )
