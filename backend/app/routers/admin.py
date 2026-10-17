import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import accounts, models, schemas
from ..database import get_db
from ..deps import get_current_admin
from ..security import hash_password

router = APIRouter(prefix="/api/admin", tags=["admin"])

UTC = datetime.UTC
RECENT_LIMIT = 100


_DATA_TABLES = {
    "activities": models.Activity,
    "sleep_nights": models.SleepRecord,
    "planned_sessions": models.PlannedTraining,
    "objectives": models.Objective,
}


def _account_data(db: Session) -> dict[int, schemas.AccountData]:
    """Per user id: how much each account holds (shown before deleting one)."""
    data: dict[int, schemas.AccountData] = {}
    for field, model in _DATA_TABLES.items():
        for user_id, count in db.query(model.user_id, func.count()).group_by(model.user_id):
            setattr(data.setdefault(user_id, schemas.AccountData()), field, count)
    return data


def _out(user: models.User, data: schemas.AccountData | None = None) -> schemas.AdminUserOut:
    return schemas.AdminUserOut(
        id=user.id,
        username=user.username,
        is_admin=bool(user.is_admin),
        has_password=bool(user.hashed_password),
        garmin_connected=bool(user.garmin_session_encrypted),
        data=data or schemas.AccountData(),
        created_at=user.created_at,
        last_login_at=user.last_login_at,
    )


def _get(db: Session, user_id: int) -> models.User:
    user = db.get(models.User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return user


@router.get("/activity", response_model=schemas.AdminActivity)
def activity(
    days: int = Query(default=7, ge=1, le=90),
    # What JavaScript's Date.getTimezoneOffset() returns: minutes to add to
    # local time to get UTC (e.g. 240 for UTC-4).
    tz_offset: int = Query(default=0, ge=-14 * 60, le=14 * 60),
    db: Session = Depends(get_db),
    _: models.User = Depends(get_current_admin),
):
    """Connections per day (in the admin's timezone) over the last `days` days,
    today included, plus the most recent attempts."""
    shift = datetime.timedelta(minutes=tz_offset)
    today_local = (datetime.datetime.now(UTC) - shift).date()
    first_day = today_local - datetime.timedelta(days=days - 1)
    start_utc = datetime.datetime.combine(first_day, datetime.time.min, tzinfo=UTC) + shift

    events = (
        db.query(models.LoginEvent)
        .filter(models.LoginEvent.created_at >= start_utc)
        .order_by(models.LoginEvent.created_at.desc(), models.LoginEvent.id.desc())
        .all()
    )

    def local_day(event: models.LoginEvent) -> datetime.date:
        at = event.created_at if event.created_at.tzinfo else event.created_at.replace(tzinfo=UTC)
        return (at - shift).date()

    per_day = {first_day + datetime.timedelta(days=i): [0, 0] for i in range(days)}
    for event in events:
        bucket = per_day.get(local_day(event))
        if bucket is not None:
            bucket[0 if event.success else 1] += 1

    return schemas.AdminActivity(
        days=[schemas.ActivityDay(date=day, logins=ok, failed=bad) for day, (ok, bad) in per_day.items()],
        totals=schemas.ActivityTotals(
            accounts=db.query(models.User).count(),
            active_accounts=len({e.user_id for e in events if e.success}),
            logins=sum(1 for e in events if e.success),
            failed=sum(1 for e in events if not e.success),
        ),
        recent=[
            schemas.LoginEventOut(at=e.created_at, username=e.username, success=e.success)
            for e in events[:RECENT_LIMIT]
        ],
    )


@router.get("/users", response_model=schemas.AdminUsersResponse)
def list_users(db: Session = Depends(get_db), _: models.User = Depends(get_current_admin)):
    users = db.query(models.User).order_by(models.User.id).all()
    data = _account_data(db)
    return schemas.AdminUsersResponse(users=[_out(u, data.get(u.id)) for u in users])


@router.post("/users", response_model=schemas.AdminUserOut, status_code=status.HTTP_201_CREATED)
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


@router.patch("/users/{user_id}", response_model=schemas.AdminUserOut)
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
    return _out(user, _account_data(db).get(user.id))


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(
    user_id: int,
    confirm: str = Query(default="", description="The account's username, typed to confirm"),
    db: Session = Depends(get_db),
    admin: models.User = Depends(get_current_admin),
):
    """Deletes the account and all its data (activities, sleep, plan, objectives).
    It can't be undone, so the caller has to send the username back."""
    user = _get(db, user_id)
    if user.id == admin.id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="You can't delete your own account")
    if accounts.normalize_username(confirm) != user.username:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f'Type the username "{user.username}" to confirm deleting it',
        )
    db.delete(user)
    db.commit()
