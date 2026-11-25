"""One planned session on the calendar: found, added or edited. Used by the
calendar's dialog (routers/planned.py), a coach on an athlete's calendar
(routers/coach.py) and Claude's connector (mcp/server.py); whole plans go
through drafts.put_on_calendar instead.

`source` says who wrote the row: "manual" (the page), "mcp" (Claude), "plan"
(a confirmed draft), "coach" (with planned_by). The caller commits."""

from sqlalchemy.orm import Session

from .. import models, schemas


def own(db: Session, user: models.User, plan_id: int) -> models.PlannedTraining | None:
    """The planned session with this id, if it is this account's."""
    return (
        db.query(models.PlannedTraining)
        .filter(models.PlannedTraining.id == plan_id, models.PlannedTraining.user_id == user.id)
        .first()
    )


def add(
    db: Session, user: models.User, payload: schemas.PlannedTrainingCreate, source: str, planned_by: str | None = None
) -> models.PlannedTraining:
    """`planned_by`: the coach's name when a coach plans it (source "coach")."""
    row = models.PlannedTraining(user_id=user.id, source=source, planned_by=planned_by, **payload.model_dump())
    db.add(row)
    return row


def edit(
    row: models.PlannedTraining, payload: schemas.PlannedTrainingUpdate, source: str, planned_by: str | None = None
) -> None:
    """Only the fields sent change; the row now belongs to whoever edited it: the
    athlete (planned_by cleared) or a coach (planned_by = that coach)."""
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(row, key, value)
    row.source = source
    row.planned_by = planned_by
