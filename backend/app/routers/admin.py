from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from .. import accounts, models, schemas
from ..database import get_db
from ..deps import get_current_admin
from ..security import hash_password

router = APIRouter(prefix="/api/admin/users", tags=["admin"])


def _out(user: models.User) -> schemas.AdminUserOut:
    return schemas.AdminUserOut(
        id=user.id,
        username=user.username,
        is_admin=bool(user.is_admin),
        has_password=bool(user.hashed_password),
        garmin_connected=bool(user.garmin_session_encrypted),
        created_at=user.created_at,
    )


def _get(db: Session, user_id: int) -> models.User:
    user = db.get(models.User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return user


@router.get("", response_model=schemas.AdminUsersResponse)
def list_users(db: Session = Depends(get_db), _: models.User = Depends(get_current_admin)):
    users = db.query(models.User).order_by(models.User.id).all()
    return schemas.AdminUsersResponse(users=[_out(u) for u in users])


@router.post("", response_model=schemas.AdminUserOut, status_code=status.HTTP_201_CREATED)
def create_user(
    payload: schemas.AdminUserCreate,
    db: Session = Depends(get_db),
    _: models.User = Depends(get_current_admin),
):
    username = accounts.normalize_username(payload.username)
    if db.query(models.User).filter(models.User.username == username).first():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f'"{username}" already exists')
    user = models.User(
        username=username, hashed_password=hash_password(payload.password), is_admin=payload.is_admin, token_version=0
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return _out(user)


@router.patch("/{user_id}", response_model=schemas.AdminUserOut)
def update_user(
    user_id: int,
    payload: schemas.AdminUserUpdate,
    db: Session = Depends(get_db),
    admin: models.User = Depends(get_current_admin),
):
    """Reset a password (logs that user out everywhere) and/or change admin rights."""
    user = _get(db, user_id)
    if payload.password is not None:
        user.hashed_password = hash_password(payload.password)
        user.token_version = (user.token_version or 0) + 1
        accounts.clear_failures(user.username)
    if payload.is_admin is not None:
        if user.id == admin.id and not payload.is_admin:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="You can't remove your own admin rights")
        user.is_admin = payload.is_admin
    db.commit()
    db.refresh(user)
    return _out(user)


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(user_id: int, db: Session = Depends(get_db), admin: models.User = Depends(get_current_admin)):
    """Deletes the account and all its data (activities, sleep, plan, objectives)."""
    user = _get(db, user_id)
    if user.id == admin.id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="You can't delete your own account")
    db.delete(user)
    db.commit()
