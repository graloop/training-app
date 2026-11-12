"""/api/planned: one planned session, added, edited, moved or deleted from the
calendar's dialog (views/session-dialog.js). Whole plans go through plan.py."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from ..deps import get_current_member
from ..planning import planned_sessions

router = APIRouter(prefix="/api/planned", tags=["planned"])


def _get_own(db: Session, user: models.User, plan_id: int) -> models.PlannedTraining:
    row = planned_sessions.own(db, user, plan_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Planned session not found")
    return row


@router.post("", response_model=schemas.PlannedTrainingOut, status_code=status.HTTP_201_CREATED)
def create_planned(
    payload: schemas.PlannedTrainingCreate,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_member),
):
    """A session typed on the calendar (source "manual")."""
    row = planned_sessions.add(db, user, payload, source="manual")
    db.commit()
    db.refresh(row)
    return row


@router.patch("/{plan_id}", response_model=schemas.PlannedTrainingOut)
def update_planned(
    plan_id: int,
    payload: schemas.PlannedTrainingUpdate,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_member),
):
    """Only the fields sent change; editing by hand makes it "manual" again."""
    row = _get_own(db, user, plan_id)
    planned_sessions.edit(row, payload, source="manual")
    db.commit()
    db.refresh(row)
    return row


@router.delete("/{plan_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_planned(
    plan_id: int,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_member),
):
    db.delete(_get_own(db, user, plan_id))
    db.commit()
