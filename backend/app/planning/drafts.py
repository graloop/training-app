"""A draft as the page sees it (parsed, with weekly totals and what it would
replace), and putting sessions on the calendar. Used by the Plan tab
(routers/plan.py) and the Import tab (routers/share.py)."""

import datetime

from sqlalchemy.orm import Query, Session

from .. import models, schemas
from .basics import SPORTS, monday_of, sport_of
from .draft_parser import DraftSession, parse_draft

DEFAULT_WEEKS = 4


def client_today(browser_today: datetime.date | None) -> datetime.date:
    """The browser's date when it's plausible (the server may be in another
    timezone), so "today" matches what the user sees."""
    server = datetime.date.today()
    if browser_today and abs((browser_today - server).days) <= 1:
        return browser_today
    return server


def weeks_preview(sessions: list[DraftSession], start: datetime.date, weeks: int) -> list[schemas.DraftWeekOut]:
    """The sessions as a table per week with minutes per sport (Plan and Import tabs)."""
    preview = []
    for i in range(weeks):
        week_start = start + datetime.timedelta(days=i * 7)
        week_end = week_start + datetime.timedelta(days=6)
        week = [
            schemas.DraftSessionOut(
                line=s.line, date=s.date, activity_type=s.activity_type, sport=sport_of(s.activity_type),
                minutes=s.minutes, details=s.details,
            )
            for s in sessions if week_start <= s.date <= week_end
        ]
        by_sport = {sport: 0.0 for sport in SPORTS}
        for s in week:
            by_sport[s.sport] += s.minutes
        preview.append(schemas.DraftWeekOut(
            start=week_start, end=week_end, sessions=week, minutes_by_sport=by_sport,
            total_minutes=sum(by_sport.values()),
        ))
    return preview


def planned_between(db: Session, user: models.User, first: datetime.date, last: datetime.date) -> Query:
    return db.query(models.PlannedTraining).filter(
        models.PlannedTraining.user_id == user.id,
        models.PlannedTraining.date >= first,
        models.PlannedTraining.date <= last,
    )


def draft_out(db: Session, user: models.User, draft: models.PlanDraft | None, today: datetime.date) -> schemas.DraftOut:
    """The saved draft (or an empty one starting next Monday), parsed."""
    start = draft.start_date if draft else monday_of(today) + datetime.timedelta(days=7)
    weeks = draft.weeks if draft else DEFAULT_WEEKS
    end = start + datetime.timedelta(days=weeks * 7 - 1)
    text = draft.text if draft else ""
    parsed = parse_draft(text, start, end, today)

    replace_from = max(start, today)
    existing = planned_between(db, user, replace_from, end).count()
    return schemas.DraftOut(
        saved=draft is not None,
        start_date=start,
        end_date=end,
        weeks=weeks,
        text=text,
        notes=(draft.notes or "") if draft else "",
        sessions_count=len(parsed.sessions),
        errors=[schemas.DraftErrorOut(line=e.line, text=e.text, message=e.message) for e in parsed.errors],
        weeks_preview=weeks_preview(parsed.sessions, start, weeks),
        replace_from=replace_from,
        existing_planned=existing,
        confirmed_at=draft.confirmed_at if draft else None,
    )


def put_on_calendar(
    db: Session, user: models.User, sessions: list[DraftSession], replace_from: datetime.date, end: datetime.date,
    source: str,
) -> int:
    """Replace the planned sessions from `replace_from` to `end` with
    `sessions`, so planning again never duplicates. Done activities are never
    touched. Returns how many planned sessions were replaced; the caller commits."""
    replaced = planned_between(db, user, replace_from, end).delete()
    for s in sessions:
        db.add(models.PlannedTraining(
            user_id=user.id, date=s.date, activity_type=s.activity_type, planned_duration_minutes=s.minutes,
            notes=s.details, source=source,
        ))
    return replaced
