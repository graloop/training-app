"""/api/objectives: target events (title, date, target time, notes) on the Athlete tab."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from ..deps import get_current_member
from ..planning import objectives

router = APIRouter(prefix="/api/objectives", tags=["objectives"])


def _get_own(db: Session, user: models.User, objective_id: int) -> models.Objective:
    row = objectives.own(db, user, objective_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Objective not found")
    return row


@router.get("", response_model=schemas.ObjectivesResponse)
def list_objectives(db: Session = Depends(get_db), user: models.User = Depends(get_current_member)):
    """Target events, added and edited on the page."""
    return schemas.ObjectivesResponse(objectives=objectives.listed(db, user))


@router.post("", response_model=schemas.ObjectiveOut, status_code=status.HTTP_201_CREATED)
def create_objective(
    payload: schemas.ObjectiveCreate,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_member),
):
    row = objectives.add(db, user, payload)
    db.commit()
    db.refresh(row)
    return row


@router.patch("/{objective_id}", response_model=schemas.ObjectiveOut)
def update_objective(
    objective_id: int,
    payload: schemas.ObjectiveUpdate,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_member),
):
    row = _get_own(db, user, objective_id)
    objectives.edit(row, payload)
    db.commit()
    db.refresh(row)
    return row


@router.delete("/{objective_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_objective(
    objective_id: int,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_member),
):
    db.delete(_get_own(db, user, objective_id))
    db.commit()
