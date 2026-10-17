import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from ..deps import get_current_member
from ..planning import drafts
from ..planning.basics import monday_of
from ..planning.draft_parser import parse_draft
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
    what a plan is written from. `detail=full` adds zones, laps and recovery."""
    day = drafts.client_today(today)
    text, start = build_log(db, user, weeks, day, detail)
    return schemas.TrainingLog(text=text, start=start, end=day, weeks=weeks, detail=detail)


@router.get("/rules", response_model=schemas.RuleSet)
def get_rules(user: models.User = Depends(get_current_member)):
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
    return drafts.draft_out(db, user, user.plan_draft, drafts.client_today(today))


@router.put("/draft", response_model=schemas.DraftOut)
def save_draft(
    payload: schemas.DraftIn,
    today: datetime.date | None = Query(default=None),
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_member),
):
    """Save the draft and return it parsed. Never touches the calendar."""
    draft = user.plan_draft
    if draft is None:
        draft = models.PlanDraft(user_id=user.id)
        db.add(draft)
    draft.start_date = monday_of(payload.start_date)
    draft.weeks = payload.weeks
    draft.text = payload.text
    if payload.notes is not None:
        draft.notes = payload.notes.strip() or None
    draft.confirmed_at = None
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
    draft = user.plan_draft
    if draft is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Save a draft first")
    day = drafts.client_today(today)
    end = draft.start_date + datetime.timedelta(days=draft.weeks * 7 - 1)
    parsed = parse_draft(draft.text, draft.start_date, end, day)
    if parsed.errors:
        count = len(parsed.errors)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Fix the {count} line{'s' if count != 1 else ''} with a problem first",
        )
    if not parsed.sessions:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="The draft has no sessions")

    replace_from = max(draft.start_date, day)
    replaced = drafts.put_on_calendar(db, user, parsed.sessions, replace_from, end, source="plan")
    draft.confirmed_at = models.utcnow()
    db.commit()
    return schemas.ConfirmResult(added=len(parsed.sessions), replaced=replaced, start_date=replace_from, end_date=end)
