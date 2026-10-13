from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from ..deps import get_current_user

router = APIRouter(prefix="/api/profile", tags=["profile"])


@router.get("/notes", response_model=schemas.ProfileNotes)
def get_notes(user: models.User = Depends(get_current_user)):
    """Free-text notes for the coach: availability, injuries, preferences, season plan."""
    return schemas.ProfileNotes(notes=user.objectives or "")


@router.put("/notes", response_model=schemas.ProfileNotes)
def put_notes(payload: schemas.ProfileNotes, db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    user.objectives = payload.notes.strip() or None
    db.commit()
    return schemas.ProfileNotes(notes=user.objectives or "")
