from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from .. import garmin_client, models, schemas
from ..database import get_db
from ..security import create_access_token, encrypt_text

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", response_model=schemas.TokenResponse)
def login(payload: schemas.LoginRequest, db: Session = Depends(get_db)):
    """Log in with Garmin Connect credentials. The Garmin account *is* the app
    account: the first successful login creates the user, later logins refresh
    the stored Garmin session. The password itself is never stored."""
    email = payload.email.lower()
    try:
        session_data = garmin_client.login_and_export_session(email, payload.password)
    except garmin_client.GarminAuthError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc

    user = db.query(models.User).filter(models.User.email == email).first()
    if user is None:
        # hashed_password is a legacy NOT NULL column from local accounts;
        # authentication is delegated to Garmin now.
        user = models.User(email=email, hashed_password="")
        db.add(user)

    user.garmin_email = email
    user.garmin_session_encrypted = encrypt_text(session_data)
    db.commit()

    return schemas.TokenResponse(access_token=create_access_token(subject=user.email))
