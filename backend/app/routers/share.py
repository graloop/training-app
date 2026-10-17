import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from .. import accounts, models, schemas, sharing
from ..planning import drafts
from ..planning.basics import monday_of
from ..planning.draft_parser import parse_draft
from ..database import get_db
from ..deps import get_current_member

router = APIRouter(prefix="/api/share", tags=["share"])

# Same room as a draft typed by hand (DraftIn.text).
MAX_IMPORT_TEXT = 30000


@router.get("", response_model=schemas.ShareInfo)
def my_share_code(db: Session = Depends(get_db), user: models.User = Depends(get_current_member)):
    """Your username and share code, to give to someone who wants to copy your plan."""
    return schemas.ShareInfo(username=user.username, share_code=sharing.ensure_code(db, user))


@router.post("/code", response_model=schemas.ShareInfo)
def new_share_code(db: Session = Depends(get_db), user: models.User = Depends(get_current_member)):
    """A new code: whoever has the old one can't import from you any more."""
    return schemas.ShareInfo(username=user.username, share_code=sharing.new_code(db, user))


def _prepare(db: Session, user: models.User, payload: schemas.ImportRequest, today: datetime.date):
    wait = accounts.seconds_share_blocked(user.id)
    if wait:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Too many wrong names or codes. Try again in {max(1, wait // 60)} min.",
        )
    sharer = sharing.find_sharer(db, payload.username, payload.share_code)
    if sharer is None:
        accounts.record_share_failure(user.id)
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No account with that name and share code.")

    source_start = monday_of(payload.source_start)
    start = monday_of(payload.target_start or payload.source_start)
    end = start + datetime.timedelta(days=payload.weeks * 7 - 1)
    text, skipped = sharing.draft_lines(db, sharer, source_start, payload.weeks, start, today)
    if len(text) > MAX_IMPORT_TEXT:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="That's too much to import at once. Choose fewer weeks.")
    parsed = parse_draft(text, start, end, today)
    return sharer, source_start, start, end, text, skipped, parsed


@router.post("/import/preview", response_model=schemas.ImportPreview)
def preview_import(
    payload: schemas.ImportRequest,
    today: datetime.date | None = Query(default=None),
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_member),
):
    """What importing would bring in. Changes nothing."""
    day = drafts.client_today(today)
    sharer, source_start, start, end, text, skipped, parsed = _prepare(db, user, payload, day)
    replace_from = max(start, day)
    draft = user.plan_draft
    draft_sessions = 0
    if draft is not None:
        draft_end = draft.start_date + datetime.timedelta(days=draft.weeks * 7 - 1)
        draft_sessions = len(parse_draft(draft.text, draft.start_date, draft_end, day).sessions)
    return schemas.ImportPreview(
        from_username=sharer.username,
        source_start=source_start,
        source_end=source_start + datetime.timedelta(days=payload.weeks * 7 - 1),
        start_date=start,
        end_date=end,
        weeks=payload.weeks,
        text=text,
        sessions_count=len(parsed.sessions),
        skipped_past=skipped,
        errors=[schemas.DraftErrorOut(line=e.line, text=e.text, message=e.message) for e in parsed.errors],
        weeks_preview=drafts.weeks_preview(parsed.sessions, start, payload.weeks),
        replace_from=replace_from,
        existing_planned=drafts.planned_between(db, user, replace_from, end).count(),
        draft_sessions=draft_sessions,
    )


@router.post("/import/plan", response_model=schemas.DraftOut)
def import_to_plan(
    payload: schemas.ImportRequest,
    today: datetime.date | None = Query(default=None),
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_member),
):
    """Make their sessions your Plan draft, to edit before it goes on the
    calendar. Your block notes are kept; the calendar isn't touched."""
    day = drafts.client_today(today)
    _, _, start, _, text, _, parsed = _prepare(db, user, payload, day)
    if not parsed.sessions:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="There are no sessions to import in those weeks.")
    draft = user.plan_draft
    if draft is None:
        draft = models.PlanDraft(user_id=user.id)
        db.add(draft)
    draft.start_date = start
    draft.weeks = payload.weeks
    draft.text = text
    draft.confirmed_at = None
    db.commit()
    db.refresh(user)
    return drafts.draft_out(db, user, user.plan_draft, day)


@router.post("/import/calendar", response_model=schemas.ConfirmResult)
def import_to_calendar(
    payload: schemas.ImportRequest,
    today: datetime.date | None = Query(default=None),
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_member),
):
    """Put their sessions straight on your calendar. Like confirming a plan:
    your planned sessions from today to the last imported day are replaced;
    done activities, earlier days and your Plan draft are not touched."""
    day = drafts.client_today(today)
    _, _, start, end, _, _, parsed = _prepare(db, user, payload, day)
    if not parsed.sessions:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="There are no sessions to import in those weeks.")
    replace_from = max(start, day)
    replaced = drafts.put_on_calendar(db, user, parsed.sessions, replace_from, end, source="import")
    db.commit()
    return schemas.ConfirmResult(added=len(parsed.sessions), replaced=replaced, start_date=replace_from, end_date=end)
