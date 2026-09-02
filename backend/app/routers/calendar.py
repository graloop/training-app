import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from ..deps import get_current_user

router = APIRouter(prefix="/api/calendar", tags=["calendar"])


@router.get("", response_model=schemas.CalendarResponse)
def get_calendar(
    start: datetime.date | None = Query(default=None),
    end: datetime.date | None = Query(default=None),
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    today = datetime.date.today()
    start = start or (today - datetime.timedelta(days=14))
    end = end or (today + datetime.timedelta(days=14))

    activities = (
        db.query(models.Activity)
        .filter(
            models.Activity.user_id == user.id,
            models.Activity.start_time >= datetime.datetime.combine(start, datetime.time.min),
            models.Activity.start_time <= datetime.datetime.combine(end, datetime.time.max),
        )
        .order_by(models.Activity.start_time)
        .all()
    )
    sleep_records = (
        db.query(models.SleepRecord)
        .filter(models.SleepRecord.user_id == user.id, models.SleepRecord.date >= start, models.SleepRecord.date <= end)
        .all()
    )
    planned = (
        db.query(models.PlannedTraining)
        .filter(models.PlannedTraining.user_id == user.id, models.PlannedTraining.date >= start, models.PlannedTraining.date <= end)
        .order_by(models.PlannedTraining.date)
        .all()
    )

    sleep_by_date = {s.date: s for s in sleep_records}
    activities_by_date: dict[datetime.date, list[models.Activity]] = {}
    for a in activities:
        activities_by_date.setdefault(a.start_time.date(), []).append(a)
    planned_by_date: dict[datetime.date, list[models.PlannedTraining]] = {}
    for p in planned:
        planned_by_date.setdefault(p.date, []).append(p)

    days = []
    day = start
    while day <= end:
        days.append(
            schemas.CalendarDay(
                date=day,
                activities=activities_by_date.get(day, []),
                sleep=sleep_by_date.get(day),
                planned=planned_by_date.get(day, []),
            )
        )
        day += datetime.timedelta(days=1)

    return schemas.CalendarResponse(start=start, end=end, days=days)
