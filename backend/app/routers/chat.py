from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from .. import ai_coach, models, schemas
from ..database import get_db
from ..deps import get_current_user

router = APIRouter(prefix="/api/chat", tags=["chat"])


@router.get("/history", response_model=schemas.ChatHistoryResponse)
def get_history(db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    rows = (
        db.query(models.ChatMessage)
        .filter(models.ChatMessage.user_id == user.id)
        .order_by(models.ChatMessage.created_at)
        .all()
    )
    return schemas.ChatHistoryResponse(messages=rows)


@router.post("", response_model=schemas.ChatResponse)
def post_chat(
    payload: schemas.ChatRequest,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    try:
        reply, plan_changes = ai_coach.handle_chat_message(db, user, payload.message)
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc

    return schemas.ChatResponse(reply=reply, plan_changes=plan_changes)
