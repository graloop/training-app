"""/api/auth: logging in (no sign-up: the admin creates accounts), who is
logged in, and the account's own settings: password, username, profile photo.
Lockout and renaming rules are in accounts.py, the photo's in avatars.py."""

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from .. import accounts, avatars, models, schemas, visitor
from ..database import get_db
from ..deps import get_current_user
from ..security import create_access_token, hash_password, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", response_model=schemas.TokenResponse)
def login(payload: schemas.LoginRequest, request: Request, db: Session = Depends(get_db)):
    """Log in with an account the admin created. There is no sign-up."""
    username = accounts.normalize_username(payload.username)
    address = visitor.address(request)
    wait = max(accounts.seconds_locked(username), accounts.seconds_address_blocked(address))
    if wait:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Too many failed attempts. Try again in {max(1, wait // 60)} min.",
        )

    user = db.query(models.User).filter(models.User.username == username).first()
    if not verify_password(payload.password, user.hashed_password if user else None):
        accounts.record_failure(username, address)
        accounts.record_login(db, user, success=False)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Wrong username or password")

    accounts.clear_failures(username)
    accounts.record_login(db, user, success=True)
    return schemas.TokenResponse(access_token=create_access_token(user.id, user.token_version))


@router.get("/me", response_model=schemas.MeOut)
def me(user: models.User = Depends(get_current_user)):
    """The logged-in account; the page calls it on load to pick the screen."""
    return schemas.MeOut.of(user)


@router.post("/password", response_model=schemas.TokenResponse)
def change_password(
    payload: schemas.PasswordChange,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """Change your own password. Logs out your other sessions; this one gets a new token."""
    if not verify_password(payload.current_password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Current password is wrong")
    user.hashed_password = hash_password(payload.new_password)
    user.token_version = (user.token_version or 0) + 1
    db.commit()
    return schemas.TokenResponse(access_token=create_access_token(user.id, user.token_version))


@router.post("/username", response_model=schemas.MeOut)
def change_username(
    payload: schemas.UsernameChange,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """Change your own login name (the password is asked again). You stay logged in."""
    if not verify_password(payload.current_password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Current password is wrong")
    try:
        accounts.rename(db, user, payload.new_username)
    except accounts.RenameProblem as problem:
        code = status.HTTP_409_CONFLICT if problem.taken else status.HTTP_400_BAD_REQUEST
        raise HTTPException(status_code=code, detail=str(problem)) from problem
    db.commit()
    return schemas.MeOut.of(user)


@router.put("/avatar", response_model=schemas.MeOut)
async def set_avatar(request: Request, db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    """Your profile photo: the image itself as the request body (JPEG or PNG, at most 200 KB)."""
    if int(request.headers.get("content-length") or 0) > avatars.MAX_BYTES:
        raise HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail="The photo is too big (200 KB at most)")
    data = await request.body()
    if len(data) > avatars.MAX_BYTES:
        raise HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail="The photo is too big (200 KB at most)")
    if avatars.image_type(data) is None:
        raise HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail="The photo must be a JPEG or PNG")
    user.avatar = data
    user.avatar_updated_at = models.utcnow()
    db.commit()
    return schemas.MeOut.of(user)


@router.delete("/avatar", response_model=schemas.MeOut)
def delete_avatar(db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    """Remove your profile photo."""
    user.avatar = None
    user.avatar_updated_at = None
    db.commit()
    return schemas.MeOut.of(user)
