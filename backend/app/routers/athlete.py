import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from .. import models, planning, schemas
from ..database import get_db
from ..deps import get_current_member

router = APIRouter(prefix="/api/athlete", tags=["athlete"])


def _out(db: Session, user: models.User, today: datetime.date | None) -> schemas.AthleteOut:
    profile = schemas.AthleteProfile(**(user.athlete_profile or {}))
    return schemas.AthleteOut(profile=profile, garmin=planning.garmin_lines(db, user, today or datetime.date.today()))


@router.get("", response_model=schemas.AthleteOut)
def get_athlete(
    today: datetime.date | None = Query(default=None),
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_member),
):
    """The numbers the athlete entered (FTP, zones, CSS...) and what Garmin knows."""
    return _out(db, user, today)


@router.put("", response_model=schemas.AthleteOut)
def save_athlete(
    payload: schemas.AthleteProfile,
    today: datetime.date | None = Query(default=None),
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_member),
):
    data = {}
    for key, value in payload.model_dump(mode="json", exclude_none=True).items():
        value = value.strip() if isinstance(value, str) else value
        if value != "":
            data[key] = value
    user.athlete_profile = data or None
    db.commit()
    return _out(db, user, today)
