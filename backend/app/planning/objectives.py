"""Target events (title, date, target time, notes): listed, added and edited.
Used by the Athlete tab (routers/objectives.py), the training log
(training_log.py) and Claude's connector (mcp/server.py). The caller commits."""

from sqlalchemy.orm import Session

from .. import models, schemas


def listed(db: Session, user: models.User) -> list[models.Objective]:
    """This account's objectives, soonest first, the undated ones last."""
    return (
        db.query(models.Objective)
        .filter(models.Objective.user_id == user.id)
        .order_by(models.Objective.event_date.is_(None), models.Objective.event_date, models.Objective.id)
        .all()
    )


def own(db: Session, user: models.User, objective_id: int) -> models.Objective | None:
    return (
        db.query(models.Objective)
        .filter(models.Objective.id == objective_id, models.Objective.user_id == user.id)
        .first()
    )


def _text(value: str | None) -> str | None:
    return (value or "").strip() or None


def add(db: Session, user: models.User, payload: schemas.ObjectiveCreate) -> models.Objective:
    row = models.Objective(
        user_id=user.id,
        title=payload.title,  # trimmed, never blank (schemas.ObjectiveCreate)
        event_date=payload.event_date,
        target_time=_text(payload.target_time),
        notes=_text(payload.notes),
    )
    db.add(row)
    return row


def edit(row: models.Objective, payload: schemas.ObjectiveUpdate) -> None:
    """Only the fields sent change; an empty date, target or notes clears it."""
    data = payload.model_dump(exclude_unset=True)
    if "title" in data:  # trimmed, never blank or null (schemas.ObjectiveUpdate)
        row.title = data["title"]
    if "event_date" in data:
        row.event_date = data["event_date"]
    if "target_time" in data:
        row.target_time = _text(data["target_time"])
    if "notes" in data:
        row.notes = _text(data["notes"])
