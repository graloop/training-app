"""The days of a calendar: for each day what was done (Garmin activities), the
night's sleep and what is planned. Used by the Calendar tab (routers/calendar.py)
and by a coach looking at one of their athletes (routers/coach.py), who gets
the same days without sleep (coaching.py says what a coach sees)."""

import datetime

from sqlalchemy.orm import Session

from .. import models, schemas


def calendar_days(
    db: Session, user: models.User, start: datetime.date, end: datetime.date, with_sleep: bool = True
) -> list[schemas.CalendarDay]:
    """Every day from `start` to `end`, both included. The caller limits the span."""
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
        if with_sleep
        else []
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
    # Counted, not stepped with `day += 1 day` until past `end`: that step
    # overflows when end is 9999-12-31.
    for offset in range((end - start).days + 1):
        day = start + datetime.timedelta(days=offset)
        days.append(
            schemas.CalendarDay(
                date=day,
                activities=activities_by_date.get(day, []),
                sleep=sleep_by_date.get(day),
                planned=planned_by_date.get(day, []),
            )
        )
    return days
