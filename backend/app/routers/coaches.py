"""/api/coaches: an athlete's own coaches (coaching.py), listed in Settings,
each of whom they can remove."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from .. import avatars, coaching, models, schemas
from ..database import get_db
from ..deps import get_current_member

router = APIRouter(prefix="/api/coaches", tags=["coaches"])


@router.get("", response_model=schemas.CoachesResponse)
def list_coaches(db: Session = Depends(get_db), user: models.User = Depends(get_current_member)):
    """The coaches who can see your calendar and plan for you."""
    return schemas.CoachesResponse(
        coaches=[
            schemas.CoachOut(link_id=link.id, id=coach.id, username=coach.username, avatar_url=avatars.url(coach))
            for link, coach in coaching.coaches_of(db, user)
        ]
    )


@router.delete("/{link_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_coach(link_id: int, db: Session = Depends(get_db), user: models.User = Depends(get_current_member)):
    """That coach stops seeing your calendar. What they already put on it stays."""
    if not coaching.remove_coach(db, user, link_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not one of your coaches")
    db.commit()
