from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from ..deps import get_current_user

router = APIRouter(prefix="/api/objectives", tags=["objectives"])


@router.get("", response_model=schemas.ObjectivesResponse)
def get_objectives(user: models.User = Depends(get_current_user)):
    return schemas.ObjectivesResponse(text=user.objectives)


@router.post("", response_model=schemas.ObjectivesResponse)
def set_objectives(
    payload: schemas.ObjectivesRequest,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    user.objectives = payload.text
    db.commit()
    return schemas.ObjectivesResponse(text=user.objectives)
