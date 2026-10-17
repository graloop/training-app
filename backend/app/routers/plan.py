import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from .. import models, planning, schemas
from ..database import get_db
from ..deps import get_current_member

router = APIRouter(prefix="/api/plan", tags=["plan"])

DEFAULT_WEEKS = 4


def _today(client_today: datetime.date | None) -> datetime.date:
    """The browser's date when it's plausible (the server may be in another
    timezone), so "today" matches what the user sees."""
    server = datetime.date.today()
    if client_today and abs((client_today - server).days) <= 1:
        return client_today
    return server


def weeks_preview(sessions: list[planning.DraftSession], start: datetime.date, weeks: int) -> list[schemas.DraftWeekOut]:
    """The sessions as a table per week with minutes per sport (Plan and Import tabs)."""
    preview = []
    for i in range(weeks):
        week_start = start + datetime.timedelta(days=i * 7)
        week_end = week_start + datetime.timedelta(days=6)
        week = [
            schemas.DraftSessionOut(
                line=s.line, date=s.date, activity_type=s.activity_type, sport=planning.sport_of(s.activity_type),
                minutes=s.minutes, details=s.details,
            )
            for s in sessions if week_start <= s.date <= week_end
        ]
        by_sport = {sport: 0.0 for sport in planning.SPORTS}
        for s in week:
            by_sport[s.sport] += s.minutes
        preview.append(schemas.DraftWeekOut(
            start=week_start, end=week_end, sessions=week, minutes_by_sport=by_sport,
            total_minutes=sum(by_sport.values()),
        ))
    return preview


def planned_between(db: Session, user: models.User, first: datetime.date, last: datetime.date):
    return db.query(models.PlannedTraining).filter(
        models.PlannedTraining.user_id == user.id,
        models.PlannedTraining.date >= first,
        models.PlannedTraining.date <= last,
    )


def _out(db: Session, user: models.User, draft: models.PlanDraft | None, today: datetime.date) -> schemas.DraftOut:
    start = draft.start_date if draft else planning.monday_of(today) + datetime.timedelta(days=7)
    weeks = draft.weeks if draft else DEFAULT_WEEKS
    end = start + datetime.timedelta(days=weeks * 7 - 1)
    text = draft.text if draft else ""
    parsed = planning.parse_draft(text, start, end, today)

    replace_from = max(start, today)
    existing = planned_between(db, user, replace_from, end).count()
    return schemas.DraftOut(
        saved=draft is not None,
        start_date=start,
        end_date=end,
        weeks=weeks,
        text=text,
        notes=(draft.notes or "") if draft else "",
        sessions_count=len(parsed.sessions),
        errors=[schemas.DraftErrorOut(line=e.line, text=e.text, message=e.message) for e in parsed.errors],
        weeks_preview=weeks_preview(parsed.sessions, start, weeks),
        replace_from=replace_from,
        existing_planned=existing,
        confirmed_at=draft.confirmed_at if draft else None,
    )


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
    day = _today(today)
    text, start = planning.build_log(db, user, weeks, day, detail)
    return schemas.TrainingLog(text=text, start=start, end=day, weeks=weeks, detail=detail)


@router.get("/rules", response_model=schemas.RuleSet)
def get_rules(user: models.User = Depends(get_current_member)):
    return schemas.RuleSet(rules=planning.rule_set_text(user))


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
    return _out(db, user, user.plan_draft, _today(today))


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
    draft.start_date = planning.monday_of(payload.start_date)
    draft.weeks = payload.weeks
    draft.text = payload.text
    if payload.notes is not None:
        draft.notes = payload.notes.strip() or None
    draft.confirmed_at = None
    db.commit()
    db.refresh(user)
    return _out(db, user, user.plan_draft, _today(today))


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
    day = _today(today)
    end = draft.start_date + datetime.timedelta(days=draft.weeks * 7 - 1)
    parsed = planning.parse_draft(draft.text, draft.start_date, end, day)
    if parsed.errors:
        count = len(parsed.errors)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Fix the {count} line{'s' if count != 1 else ''} with a problem first",
        )
    if not parsed.sessions:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="The draft has no sessions")

    replace_from = max(draft.start_date, day)
    replaced = planned_between(db, user, replace_from, end).delete()
    for s in parsed.sessions:
        db.add(models.PlannedTraining(
            user_id=user.id, date=s.date, activity_type=s.activity_type, planned_duration_minutes=s.minutes,
            notes=s.details, source="plan",
        ))
    draft.confirmed_at = models.utcnow()
    db.commit()
    return schemas.ConfirmResult(added=len(parsed.sessions), replaced=replaced, start_date=replace_from, end_date=end)
