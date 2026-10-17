from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from .. import accounts, models, schemas, visitor
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
