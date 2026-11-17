"""/api/plan: the rule set (Athlete tab), and the Plan tab's draft: read
parsed (/draft), edited one session at a time from its calendar
(/draft/sessions), cleared, then put on the calendar (/draft/confirm). Claude's
connector writes whole drafts itself (mcp/server.py, drafts.save). The logic
is in planning/.

A coach plans for one of their athletes with ?athlete_id= on the /draft routes:
the coach's own draft for them (drafts.draft_of), pushed on the athlete's
calendar, or sent to the athlete's own draft (/draft/send). Without a link to
that athlete it is a 404, as for any other account (coaching.py)."""

import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from .. import coaching, models, schemas
from ..database import get_db
from ..deps import get_current_member
from ..planning import drafts
from ..planning.training_log import rule_set_text

router = APIRouter(prefix="/api/plan", tags=["plan"])

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
    """The rule set every plan must follow (Athlete tab); Claude reads it through the connector."""
    user.rule_set = payload.rules.strip()  # "" (not None) = cleared: the older notes don't come back
    db.commit()
    return schemas.RuleSet(rules=user.rule_set)


# --- The draft ---------------------------------------------------------------------------

ATHLETE_ID = Query(default=None, description="A coach planning for one of their athletes")


def _for(db: Session, user: models.User, athlete_id: int | None) -> tuple[models.User, models.User | None]:
    """(whose calendar, the coach planning it or None): yourself, or one of your athletes."""
    if athlete_id is None:
        return user, None
    if not user.is_coach:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Coaches only")
    athlete = coaching.athlete_for(db, user, athlete_id)
    if athlete is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not one of your athletes")
    return athlete, user


@router.get("/draft", response_model=schemas.DraftOut)
def get_draft(
    today: datetime.date | None = Query(default=None),
    athlete_id: int | None = ATHLETE_ID,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_member),
):
    """The saved draft parsed, or an empty one starting this Monday. A draft left
    untouched for drafts.DRAFT_TTL_HOURS comes back empty."""
    athlete, coach = _for(db, user, athlete_id)
    out = drafts.draft_out(db, athlete, drafts.draft_of(db, athlete, coach), drafts.client_today(today))
    db.commit()  # keeps the expiry, if draft_out just emptied it
    return out


@router.post("/draft/confirm", response_model=schemas.ConfirmResult)
def confirm_draft(
    today: datetime.date | None = Query(default=None),
    athlete_id: int | None = ATHLETE_ID,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_member),
):
    """Put the saved draft on the calendar (the athlete's, for a coach: those
    sessions say who planned them). Planned sessions from today (or the draft's
    first day, if later) to its last day are replaced, so re-planning never
    duplicates. Done activities and earlier days are not touched."""
    athlete, coach = _for(db, user, athlete_id)
    try:
        result = drafts.confirm(db, athlete, drafts.client_today(today), coach)
    except drafts.DraftProblem as problem:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(problem)) from problem
    db.commit()
    return result


# --- The draft, one session at a time (the Plan tab's calendar) ---------------------------
# Each answer is the whole draft again, so the page redraws from it and its line
# numbers are always current. A line number that no longer holds a session
# (the draft changed in another tab, or through Claude) is a 409.

_STALE = "The draft changed in the meantime. Reload the page."


def _draft_answer(
    db: Session, athlete: models.User, coach: models.User | None, today: datetime.date | None
) -> schemas.DraftOut:
    db.commit()
    db.refresh(athlete)
    return drafts.draft_out(db, athlete, drafts.draft_of(db, athlete, coach), drafts.client_today(today))


@router.post("/draft/sessions", response_model=schemas.DraftOut)
def add_draft_session(
    payload: schemas.DraftSessionIn,
    today: datetime.date | None = Query(default=None),
    athlete_id: int | None = ATHLETE_ID,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_member),
):
    """Add a session to the draft (its weeks grow to take in the day). Never touches the calendar."""
    athlete, coach = _for(db, user, athlete_id)
    try:
        drafts.add_session(db, athlete, payload, drafts.client_today(today), coach)
    except drafts.DraftProblem as problem:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(problem)) from problem
    return _draft_answer(db, athlete, coach, today)


@router.patch("/draft/sessions/{line}", response_model=schemas.DraftOut)
def edit_draft_session(
    line: int,
    payload: schemas.DraftSessionIn,
    today: datetime.date | None = Query(default=None),
    athlete_id: int | None = ATHLETE_ID,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_member),
):
    """Rewrite the session on that line of the draft: sport, duration, details, day."""
    athlete, coach = _for(db, user, athlete_id)
    try:
        drafts.edit_session(db, athlete, line, payload, drafts.client_today(today), coach)
    except drafts.StaleLine as stale:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=_STALE) from stale
    except drafts.DraftProblem as problem:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(problem)) from problem
    return _draft_answer(db, athlete, coach, today)


@router.delete("/draft/sessions/{line}", response_model=schemas.DraftOut)
def delete_draft_line(
    line: int,
    today: datetime.date | None = Query(default=None),
    athlete_id: int | None = ATHLETE_ID,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_member),
):
    """Remove a session from the draft, or a line it couldn't read."""
    athlete, coach = _for(db, user, athlete_id)
    try:
        drafts.remove_line(db, athlete, line, drafts.client_today(today), coach)
    except drafts.StaleLine as stale:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=_STALE) from stale
    return _draft_answer(db, athlete, coach, today)


@router.delete("/draft", response_model=schemas.DraftOut)
def clear_draft(
    today: datetime.date | None = Query(default=None),
    athlete_id: int | None = ATHLETE_ID,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_member),
):
    """Clear: the draft is emptied, its weeks stay. The calendar is not touched."""
    athlete, coach = _for(db, user, athlete_id)
    drafts.clear(db, athlete, coach)
    return _draft_answer(db, athlete, coach, today)


@router.post("/draft/send", response_model=schemas.SendResult)
def send_draft(
    athlete_id: int = Query(),
    replace: bool = Query(default=False, description="Replace the draft the athlete already has"),
    today: datetime.date | None = Query(default=None),
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_member),
):
    """A coach copies their draft for an athlete into the athlete's own draft (their
    Plan tab), to adjust and push themselves. A 409 when the athlete already has a
    draft: send again with replace=true to replace it."""
    athlete, coach = _for(db, user, athlete_id)
    try:
        sent = drafts.send_to_athlete(db, coach, athlete, drafts.client_today(today), replace)
    except drafts.AthleteHasADraft as problem:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(problem)) from problem
    except drafts.DraftProblem as problem:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(problem)) from problem
    db.commit()
    return schemas.SendResult(sessions=sent)
