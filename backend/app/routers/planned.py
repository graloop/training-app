from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from ..deps import get_current_user

router = APIRouter(prefix="/api/planned", tags=["planned"])


def _get_own(db: Session, user: models.User, plan_id: int) -> models.PlannedTraining:
    row = (
        db.query(models.PlannedTraining)
        .filter(models.PlannedTraining.id == plan_id, models.PlannedTraining.user_id == user.id)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Planned session not found")
    return row


@router.post("", response_model=schemas.PlannedTrainingOut, status_code=status.HTTP_201_CREATED)
def create_planned(
    payload: schemas.PlannedTrainingCreate,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    row = models.PlannedTraining(user_id=user.id, source="manual", **payload.model_dump())
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.patch("/{plan_id}", response_model=schemas.PlannedTrainingOut)
def update_planned(
    plan_id: int,
    payload: schemas.PlannedTrainingUpdate,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    row = _get_own(db, user, plan_id)
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(row, key, value)
    row.source = "manual"
    db.commit()
    db.refresh(row)
    return row


@router.delete("/{plan_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_planned(
    plan_id: int,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    db.delete(_get_own(db, user, plan_id))
    db.commit()
