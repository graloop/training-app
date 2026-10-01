from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from ..deps import get_current_user

router = APIRouter(prefix="/api/objectives", tags=["objectives"])


def _get_own(db: Session, user: models.User, objective_id: int) -> models.Objective:
    row = (
        db.query(models.Objective)
        .filter(models.Objective.id == objective_id, models.Objective.user_id == user.id)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Objective not found")
    return row


@router.get("", response_model=schemas.ObjectivesResponse)
def list_objectives(db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    """Objectives are created by the coach from the chat; the user edits or deletes them here."""
    rows = (
        db.query(models.Objective)
        .filter(models.Objective.user_id == user.id)
        .order_by(models.Objective.event_date.is_(None), models.Objective.event_date, models.Objective.id)
        .all()
    )
    return schemas.ObjectivesResponse(objectives=rows)


@router.patch("/{objective_id}", response_model=schemas.ObjectiveOut)
def update_objective(
    objective_id: int,
    payload: schemas.ObjectiveUpdate,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    row = _get_own(db, user, objective_id)
    data = payload.model_dump(exclude_unset=True)
    if "title" in data and data["title"] is not None:
        row.title = data["title"].strip()
    if "event_date" in data:
        row.event_date = data["event_date"]
    if "target_time" in data:
        row.target_time = (data["target_time"] or "").strip() or None
    db.commit()
    db.refresh(row)
    return row


@router.delete("/{objective_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_objective(
    objective_id: int,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    db.delete(_get_own(db, user, objective_id))
    db.commit()
