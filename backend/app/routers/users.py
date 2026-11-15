"""/api/users/{id}: what one account may see of another. For now, the profile
photo (avatars.py): your own, and every account's for the admin."""

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session, undefer

from .. import avatars, models
from ..database import get_db
from ..deps import get_current_user

router = APIRouter(prefix="/api/users", tags=["users"])


@router.get("/{user_id}/avatar")
def get_avatar(user_id: int, db: Session = Depends(get_db), viewer: models.User = Depends(get_current_user)):
    """The photo itself. 404 when there is none or it isn't yours to see. The URL
    changes with each new photo (?v=), so the browser may keep it."""
    owner = db.query(models.User).options(undefer(models.User.avatar)).filter(models.User.id == user_id).first()
    if owner is None or owner.avatar is None or not avatars.may_see(viewer, owner):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No photo")
    return Response(
        owner.avatar,
        media_type=avatars.image_type(owner.avatar) or "application/octet-stream",
        headers={"Cache-Control": "private, max-age=31536000, immutable"},
    )
