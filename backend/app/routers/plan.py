"""/api/plan: the Plan and History tabs. The training log as text (/log), one
session in detail (/sessions), the rule set, and the draft: saved and parsed
(/draft), then put on the calendar (/draft/confirm). The logic is in planning/."""

import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from ..deps import get_current_member
from ..planning import drafts
from ..planning.session_detail import session_choices, session_text
from ..planning.training_log import build_log, rule_set_text

router = APIRouter(prefix="/api/plan", tags=["plan"])

@router.get("/log", response_model=schemas.TrainingLog)
def training_log(
    weeks: int = Query(default=6, ge=1, le=26),
    detail: str = Query(default="full", pattern="^(full|summary)$"),
    today: datetime.date | None = Query(default=None),
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_member),
):
    """The last `weeks` weeks as text (sessions with pace/HR/load/effort, sleep,
    weekly totals, objectives, the athlete's profile, what's already planned):
    what a plan is written from. `detail=full` adds zones, laps and recovery
    over the last 4 weeks, and the reps of older sessions."""
    day = drafts.client_today(today)
    text, start = build_log(db, user, weeks, day, detail)
    return schemas.TrainingLog(text=text, start=start, end=day, weeks=weeks, detail=detail)


@router.get("/sessions", response_model=list[schemas.SessionChoice])
def list_sessions(
    today: datetime.date | None = Query(default=None),
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_member),
):
    """The sessions that can be opened in detail, newest first."""
    choices = session_choices(db, user, drafts.client_today(today))
    return [schemas.SessionChoice(id=activity_id, label=label) for activity_id, label in choices]


@router.get("/sessions/{activity_id}", response_model=schemas.SessionDetail)
def get_session(activity_id: int, db: Session = Depends(get_db), user: models.User = Depends(get_current_member)):
    """Everything stored about one session, as text: every lap and, while it is kept, its trace."""
    activity = db.query(models.Activity).filter_by(id=activity_id, user_id=user.id).first()
    if activity is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    return schemas.SessionDetail(id=activity.id, text=session_text(activity, activity.trace))


@router.get("/rules", response_model=schemas.RuleSet)
def get_rules(user: models.User = Depends(get_current_member)):
    """The rule set (or, until it is first saved, the older notes standing in for it)."""
    return schemas.RuleSet(rules=rule_set_text(user))


@router.put("/rules", response_model=schemas.RuleSet)
def save_rules(
    payload: schemas.RuleSet,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_member),
):
    """The rule set the plan must follow; shown in the history."""
    user.rule_set = payload.rules.strip()  # "" (not None) = cleared: the older notes don't come back
    db.commit()
    return schemas.RuleSet(rules=user.rule_set)


@router.get("/draft", response_model=schemas.DraftOut)
def get_draft(
    today: datetime.date | None = Query(default=None),
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_member),
):
    """The saved draft parsed, or an empty one starting next Monday."""
    return drafts.draft_out(db, user, user.plan_draft, drafts.client_today(today))


@router.put("/draft", response_model=schemas.DraftOut)
def save_draft(
    payload: schemas.DraftIn,
    today: datetime.date | None = Query(default=None),
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_member),
):
    """Save the draft and return it parsed. Never touches the calendar."""
    drafts.save(db, user, payload)
    db.commit()
    db.refresh(user)
    return drafts.draft_out(db, user, user.plan_draft, drafts.client_today(today))


@router.post("/draft/confirm", response_model=schemas.ConfirmResult)
def confirm_draft(
    today: datetime.date | None = Query(default=None),
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_member),
):
    """Put the saved draft on the calendar. Planned sessions from today (or the
    draft's first day, if later) to its last day are replaced, so re-planning
    never duplicates. Done activities and earlier days are not touched."""
    try:
        result = drafts.confirm(db, user, drafts.client_today(today))
    except drafts.DraftProblem as problem:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(problem)) from problem
    db.commit()
    return result
