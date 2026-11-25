"""/api/coach: a coach's athletes (coaching.py). The list with this week's hours,
adding one by username (no approval), letting one go, one athlete's profile
and calendar (what a coach may see, never sleep or health details), and their
planned sessions, added, edited or deleted on that calendar ("by <coach>").
Drafts for an athlete go through /api/plan/draft?athlete_id= (routers/plan.py)."""

import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from .. import coaching, models, schemas
from ..database import get_db
from ..deps import get_current_coach
from ..planning import planned_sessions
from ..planning.calendar_days import calendar_days
from ..planning.drafts import client_today

router = APIRouter(prefix="/api/coach", tags=["coach"])

MAX_SPAN_DAYS = 366  # as GET /api/calendar


def _athlete(db: Session, coach: models.User, athlete_id: int) -> models.User:
    athlete = coaching.athlete_for(db, coach, athlete_id)
    if athlete is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not one of your athletes")
    return athlete


@router.get("/athletes", response_model=schemas.CoachAthletesResponse)
def list_athletes(
    today: datetime.date | None = Query(default=None),
    db: Session = Depends(get_db),
    coach: models.User = Depends(get_current_coach),
):
    """Your athletes: next objective, this week's planned and done hours, last Garmin sync."""
    day = client_today(today)
    return schemas.CoachAthletesResponse(
        athletes=[coaching.athlete_summary(db, a, day) for a in coaching.athletes_of(db, coach)]
    )


@router.post("/athletes", response_model=schemas.CoachAthleteOut, status_code=status.HTTP_201_CREATED)
def add_athlete(
    payload: schemas.CoachAthleteAdd,
    today: datetime.date | None = Query(default=None),
    db: Session = Depends(get_db),
    coach: models.User = Depends(get_current_coach),
):
    """Add an athlete by username. The link works straight away; they see you in their Settings."""
    try:
        athlete = coaching.add_athlete(db, coach, payload.username)
    except coaching.CoachingProblem as problem:
        raise HTTPException(status_code=problem.status, detail=str(problem)) from problem
    db.commit()
    return coaching.athlete_summary(db, athlete, client_today(today))


@router.delete("/athletes/{athlete_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_athlete(athlete_id: int, db: Session = Depends(get_db), coach: models.User = Depends(get_current_coach)):
    """Stop coaching this athlete. Your draft for them goes too; what you pushed stays on their calendar."""
    if not coaching.remove_athlete(db, coach, athlete_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not one of your athletes")
    db.commit()


@router.get("/athletes/{athlete_id}", response_model=schemas.CoachAthleteProfile)
def athlete_profile(athlete_id: int, db: Session = Depends(get_db), coach: models.User = Depends(get_current_coach)):
    """Their objectives, benchmarks and zones, Garmin's zones and records, and their rule set."""
    return coaching.athlete_profile(db, _athlete(db, coach, athlete_id))


@router.get("/athletes/{athlete_id}/calendar", response_model=schemas.CalendarResponse)
def athlete_calendar(
    athlete_id: int,
    start: datetime.date = Query(),
    end: datetime.date = Query(),
    db: Session = Depends(get_db),
    coach: models.User = Depends(get_current_coach),
):
    """Their planned and done sessions from `start` to `end`, as the Calendar tab has them, without sleep."""
    athlete = _athlete(db, coach, athlete_id)
    if not 0 <= (end - start).days < MAX_SPAN_DAYS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"Ask for 1 to {MAX_SPAN_DAYS} days at a time."
        )
    return schemas.CalendarResponse(start=start, end=end, days=calendar_days(db, athlete, start, end, with_sleep=False))


def _their_session(db: Session, athlete: models.User, plan_id: int) -> models.PlannedTraining:
    row = planned_sessions.own(db, athlete, plan_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Planned session not found")
    return row


@router.post("/athletes/{athlete_id}/planned", response_model=schemas.PlannedTrainingOut,
             status_code=status.HTTP_201_CREATED)
def add_planned(
    athlete_id: int,
    payload: schemas.PlannedTrainingCreate,
    db: Session = Depends(get_db),
    coach: models.User = Depends(get_current_coach),
):
    """Plan a session on the athlete's calendar; it says "by <coach>"."""
    row = planned_sessions.add(db, _athlete(db, coach, athlete_id), payload, source="coach", planned_by=coach.username)
    db.commit()
    db.refresh(row)
    return row


@router.patch("/athletes/{athlete_id}/planned/{plan_id}", response_model=schemas.PlannedTrainingOut)
def edit_planned(
    athlete_id: int,
    plan_id: int,
    payload: schemas.PlannedTrainingUpdate,
    db: Session = Depends(get_db),
    coach: models.User = Depends(get_current_coach),
):
    """Change one of the athlete's planned sessions (a new `date` moves it); it then says "by <coach>"."""
    row = _their_session(db, _athlete(db, coach, athlete_id), plan_id)
    planned_sessions.edit(row, payload, source="coach", planned_by=coach.username)
    db.commit()
    db.refresh(row)
    return row


@router.delete("/athletes/{athlete_id}/planned/{plan_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_planned(
    athlete_id: int, plan_id: int, db: Session = Depends(get_db), coach: models.User = Depends(get_current_coach)
):
    db.delete(_their_session(db, _athlete(db, coach, athlete_id), plan_id))
    db.commit()
