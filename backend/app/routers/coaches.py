"""/api/coaches: an athlete's own coaches (coaching.py), listed in Settings:
whether each sees their sleep and recovery (the athlete's switch), and
removing one."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from .. import avatars, coaching, models, schemas
from ..database import get_db
from ..deps import get_current_member

router = APIRouter(prefix="/api/coaches", tags=["coaches"])


def _out(link: models.CoachLink, coach: models.User) -> schemas.CoachOut:
    return schemas.CoachOut(
        link_id=link.id, id=coach.id, username=coach.username, avatar_url=avatars.url(coach),
        share_health=bool(link.share_health),
    )


@router.get("", response_model=schemas.CoachesResponse)
def list_coaches(db: Session = Depends(get_db), user: models.User = Depends(get_current_member)):
    """The coaches who can see your calendar and plan for you."""
    return schemas.CoachesResponse(coaches=[_out(link, coach) for link, coach in coaching.coaches_of(db, user)])


@router.patch("/{link_id}", response_model=schemas.CoachOut)
def share_with_coach(
    link_id: int,
    payload: schemas.CoachShare,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_member),
):
    """Share your sleep and recovery with that coach (and their Claude), or stop. Off by default."""
    link = coaching.set_sharing(db, user, link_id, payload.share_health)
    if link is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not one of your coaches")
    db.commit()
    return _out(link, link.coach)


@router.delete("/{link_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_coach(link_id: int, db: Session = Depends(get_db), user: models.User = Depends(get_current_member)):
    """That coach stops seeing your calendar. What they already put on it stays."""
    if not coaching.remove_coach(db, user, link_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not one of your coaches")
    db.commit()
