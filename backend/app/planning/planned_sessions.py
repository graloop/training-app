"""One planned session on the calendar: found, added or edited. Used by the
calendar's dialog (routers/planned.py) and by Claude's connector
(mcp/server.py); whole plans go through drafts.put_on_calendar instead.

`source` says who wrote the row: "manual" (the page), "mcp" (Claude), "plan"
(a confirmed draft). The caller commits."""

from sqlalchemy.orm import Session

from .. import models, schemas


def own(db: Session, user: models.User, plan_id: int) -> models.PlannedTraining | None:
    """The planned session with this id, if it is this account's."""
    return (
        db.query(models.PlannedTraining)
        .filter(models.PlannedTraining.id == plan_id, models.PlannedTraining.user_id == user.id)
        .first()
    )


def add(db: Session, user: models.User, payload: schemas.PlannedTrainingCreate, source: str) -> models.PlannedTraining:
    row = models.PlannedTraining(user_id=user.id, source=source, **payload.model_dump())
    db.add(row)
    return row


def edit(row: models.PlannedTraining, payload: schemas.PlannedTrainingUpdate, source: str) -> None:
    """Only the fields sent change; the row now belongs to whoever edited it."""
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(row, key, value)
    row.source = source
