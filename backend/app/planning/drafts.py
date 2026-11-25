"""A draft as the page sees it (parsed, with weekly totals and what it would
replace), editing it one session at a time, and putting sessions on the
calendar. Used by the Plan tab (routers/plan.py), the Import tab
(routers/share.py) and Claude's connector (mcp/server.py).

The Plan tab shows the draft as a calendar: adding, editing or deleting a
session there rewrites one line of the text (add_session, edit_session,
remove_line), found by its line number (DraftSession.line).

A draft is kept until it is cleared or replaced. Its sessions on days already
past stay visible but are never pushed, and drop out once KEEP_PAST_DAYS old
(drop_old_sessions): every read and write here does that first, so nothing has
to run on a timer and it works even after the server was off.

Whose draft: the editing functions take the athlete whose calendar is planned
and, for a coach planning for them, the coach (draft_of). Without a coach it is
the athlete's own draft (User.plan_draft), the one their Plan tab and the
athlete's own Claude use. A coach's Claude writes the coach's draft
(save(..., coach=, by="claude")). send_to_athlete copies a coach's draft into
the athlete's own.

Every change records who made it (PlanDraft.written_by: "claude" from the
connector, else "coach" or "self" from the page) and clears confirmed_at and
sent_at: a draft changed after it was pushed or sent shows as not pushed or
not sent.

A draft is plain text (PlanDraft.text) parsed again on every read: nothing
parsed is stored until put_on_calendar() turns the sessions into
PlannedTraining rows. "today" is the browser's day (client_today), so a session
the user sees as tomorrow isn't refused as past by a server in another timezone."""

import datetime

from sqlalchemy.orm import Query, Session

from .. import models, schemas
from .basics import SPORTS, fmt_minutes, monday_of, sport_of
from .draft_parser import DraftSession, parse_draft

DEFAULT_WEEKS = 4
KEEP_PAST_DAYS = 21  # a past session stays in the draft for 3 weeks, for reference
SUMMARY_MAX = 1500  # PlanDraft.summary


class DraftProblem(ValueError):
    """Why a draft can't go on the calendar, as a sentence for the person (or Claude)."""


def client_today(browser_today: datetime.date | None) -> datetime.date:
    """The browser's date when it's plausible (the server may be in another
    timezone), so "today" matches what the user sees."""
    server = datetime.date.today()
    if browser_today and abs((browser_today - server).days) <= 1:
        return browser_today
    return server


def weeks_preview(
    sessions: list[DraftSession], start: datetime.date, weeks: int, past: list[DraftSession] = ()
) -> list[schemas.DraftWeekOut]:
    """The sessions as a table per week with minutes per sport (Plan and Import
    tabs). `past` ones are marked and counted in their week's totals."""
    preview = []
    marked = [(s, False) for s in sessions] + [(s, True) for s in past]
    marked.sort(key=lambda pair: (pair[0].date, pair[0].line))
    for i in range(weeks):
        week_start = start + datetime.timedelta(days=i * 7)
        week_end = week_start + datetime.timedelta(days=6)
        week = [
            schemas.DraftSessionOut(
                line=s.line, date=s.date, activity_type=s.activity_type, sport=sport_of(s.activity_type),
                minutes=s.minutes, details=s.details, past=is_past,
            )
            for s, is_past in marked if week_start <= s.date <= week_end
        ]
        by_sport = {sport: 0.0 for sport in SPORTS}
        for s in week:
            by_sport[s.sport] += s.minutes
        preview.append(schemas.DraftWeekOut(
            start=week_start, end=week_end, sessions=week, minutes_by_sport=by_sport,
            total_minutes=sum(by_sport.values()),
        ))
    return preview


def planned_between(db: Session, user: models.User, first: datetime.date, last: datetime.date) -> Query:
    """The user's planned sessions from `first` to `last`, both included, as a
    query: callers .count() it (what confirming would replace) or .delete() it."""
    return db.query(models.PlannedTraining).filter(
        models.PlannedTraining.user_id == user.id,
        models.PlannedTraining.date >= first,
        models.PlannedTraining.date <= last,
    )


def _end(draft: models.PlanDraft) -> datetime.date:
    return draft.start_date + datetime.timedelta(days=draft.weeks * 7 - 1)


def drop_old_sessions(draft: models.PlanDraft | None, today: datetime.date) -> bool:
    """Remove the sessions dated KEEP_PAST_DAYS ago or more, and the weeks left
    with nothing ahead of them (the draft then starts later; when no week is
    left, it is DEFAULT_WEEKS from this Monday). Not a change by the athlete:
    a pushed draft stays pushed. True when something went. The caller commits."""
    if draft is None:
        return False
    cutoff = today - datetime.timedelta(days=KEEP_PAST_DAYS)
    old = {s.line for s in parse_draft(draft.text, draft.start_date, _end(draft), today).past if s.date <= cutoff}
    first = monday_of(cutoff + datetime.timedelta(days=1))  # the earliest week that can still hold one
    if not old and draft.start_date >= first:
        return False
    if old:
        draft.text = "\n".join(line for n, line in enumerate(draft.text.splitlines(), start=1) if n not in old)
    if draft.start_date < first:
        end = _end(draft)
        if end < first:
            draft.start_date, draft.weeks = monday_of(today), DEFAULT_WEEKS
        else:
            draft.start_date, draft.weeks = first, (monday_of(end) - first).days // 7 + 1
    return True


def draft_out(db: Session, user: models.User, draft: models.PlanDraft | None, today: datetime.date) -> schemas.DraftOut:
    """The saved draft (or an empty one, DEFAULT_WEEKS from this Monday), parsed."""
    drop_old_sessions(draft, today)
    start = draft.start_date if draft else monday_of(today)
    weeks = draft.weeks if draft else DEFAULT_WEEKS
    end = start + datetime.timedelta(days=weeks * 7 - 1)
    text = draft.text if draft else ""
    parsed = parse_draft(text, start, end, today)

    replace_from = max(start, today)
    existing = planned_between(db, user, replace_from, end).count()
    return schemas.DraftOut(
        saved=draft is not None,
        start_date=start,
        end_date=end,
        weeks=weeks,
        text=text,
        sessions_count=len(parsed.sessions),
        errors=[schemas.DraftErrorOut(line=e.line, text=e.text, message=e.message) for e in parsed.errors],
        weeks_preview=weeks_preview(parsed.sessions, start, weeks, parsed.past),
        replace_from=replace_from,
        existing_planned=existing,
        confirmed_at=draft.confirmed_at if draft else None,
        past_count=len(parsed.past),
        written_by=draft.written_by if draft else None,
        summary=draft.summary if draft else None,
        updated_at=draft.updated_at if draft else None,
        sent_at=draft.sent_at if draft else None,
    )


def put_on_calendar(
    db: Session, user: models.User, sessions: list[DraftSession], replace_from: datetime.date, end: datetime.date,
    source: str, planned_by: str | None = None,
) -> int:
    """Replace the planned sessions from `replace_from` to `end` with
    `sessions`, so planning again never duplicates. Done activities are never
    touched. Returns how many planned sessions were replaced; the caller commits."""
    replaced = planned_between(db, user, replace_from, end).delete()
    for s in sessions:
        db.add(models.PlannedTraining(
            user_id=user.id, date=s.date, activity_type=s.activity_type, planned_duration_minutes=s.minutes,
            notes=s.details, source=source, planned_by=planned_by,
        ))
    return replaced


def save(
    db: Session, user: models.User, payload: schemas.DraftIn, coach: models.User | None = None,
    by: str | None = None, summary: str | None = None,
) -> None:
    """Replace the whole draft: `user`'s own, or `coach`'s draft for them. `by` is
    who wrote it (_changed); `summary` replaces the reviewer's note when given.
    Never touches the calendar, nor any other draft. The caller commits."""
    draft = draft_of(db, user, coach)
    if draft is None:
        draft = models.PlanDraft(user_id=user.id, coach_id=coach.id if coach else None)
        db.add(draft)
        if coach is None:
            user.plan_draft = draft  # so whoever reads user.plan_draft next sees it
    draft.start_date = monday_of(payload.start_date)
    draft.weeks = payload.weeks
    _changed(draft, payload.text.splitlines(), by)
    if summary is not None:
        draft.summary = summary.strip() or None


def confirm(
    db: Session, user: models.User, today: datetime.date, coach: models.User | None = None
) -> schemas.ConfirmResult:
    """Put the saved draft on `user`'s calendar (a coach's draft for them, with
    `coach`). Planned sessions from today (or the draft's first day, if later) to
    its last day are replaced, so re-planning never duplicates. Raises
    DraftProblem when it can't. The caller commits."""
    draft = draft_of(db, user, coach)
    drop_old_sessions(draft, today)
    if draft is None:
        raise DraftProblem("Save a draft first")
    end = draft.start_date + datetime.timedelta(days=draft.weeks * 7 - 1)
    parsed = parse_draft(draft.text, draft.start_date, end, today)
    if parsed.errors:
        count = len(parsed.errors)
        raise DraftProblem(f"Fix the {count} line{'s' if count != 1 else ''} with a problem first")
    if not parsed.sessions:
        raise DraftProblem("The draft has no sessions")

    replace_from = max(draft.start_date, today)
    replaced = put_on_calendar(
        db, user, parsed.sessions, replace_from, end,
        source="coach" if coach else "plan", planned_by=coach.username if coach else None,
    )
    draft.confirmed_at = models.utcnow()
    return schemas.ConfirmResult(added=len(parsed.sessions), replaced=replaced, start_date=replace_from, end_date=end)


# --- One session at a time (the Plan tab's calendar) -----------------------------------

# The word the parser reads back as each activity type (draft_parser._SPORT_WORDS).
_SPORT_WORD = {"running": "run", "cycling": "bike", "swimming": "swim", "strength_training": "strength"}


def draft_line(day: datetime.date, activity_type: str, minutes: float, details: str | None = None) -> str:
    """ "2026-11-17 | run | 1:15 | Tempo run 3×10 min": one draft line, which the
    parser reads back as the same session. The Plan tab's calendar and the
    Import tab (sharing.py) both write their sessions with it."""
    sport = _SPORT_WORD.get(activity_type, " ".join(activity_type.replace("|", "/").split()))
    line = f"{day.isoformat()} | {sport} | {fmt_minutes(minutes)}"
    text = " ".join((details or "").split())  # a session is one line
    return f"{line} | {text}" if text else line


def session_line(session: schemas.DraftSessionIn) -> str:
    return draft_line(session.date, session.activity_type, session.minutes, session.details)


def draft_of(db: Session, user: models.User, coach: models.User | None = None) -> models.PlanDraft | None:
    """`user`'s own draft, or `coach`'s draft for them. None when there is none yet."""
    if coach is None:
        return user.plan_draft
    return db.query(models.PlanDraft).filter_by(user_id=user.id, coach_id=coach.id).first()


def _draft_for(db: Session, user: models.User, today: datetime.date, coach: models.User | None = None) -> models.PlanDraft:
    """The saved draft, or a new empty one (DEFAULT_WEEKS from this Monday). The caller commits."""
    draft = draft_of(db, user, coach)
    if draft is None:
        draft = models.PlanDraft(
            user_id=user.id, coach_id=coach.id if coach else None, start_date=monday_of(today), weeks=DEFAULT_WEEKS,
            text="",
        )
        db.add(draft)
        if coach is None:
            user.plan_draft = draft
    drop_old_sessions(draft, today)
    return draft


def _cover(draft: models.PlanDraft, day: datetime.date) -> None:
    """Widen the draft's weeks to take in `day`, up to schemas.MAX_PLAN_WEEKS."""
    start = min(draft.start_date, monday_of(day))
    end = max(draft.start_date + datetime.timedelta(days=draft.weeks * 7 - 1), day)
    weeks = (monday_of(end) - start).days // 7 + 1
    if weeks > schemas.MAX_PLAN_WEEKS:
        raise DraftProblem(f"A draft covers at most {schemas.MAX_PLAN_WEEKS} weeks: {day.isoformat()} is too far from the others.")
    draft.start_date, draft.weeks = start, weeks


def _changed(draft: models.PlanDraft, lines: list[str], by: str | None = None) -> None:
    """New text, and who wrote it: `by`, or by default the page's user (the coach
    for a coach's draft, else the athlete). Not pushed or sent any more."""
    draft.text = "\n".join(lines)
    draft.written_by = by or ("coach" if draft.coach_id else "self")
    draft.confirmed_at = None
    draft.sent_at = None
    draft.updated_at = models.utcnow()


def _session_lines(draft: models.PlanDraft, today: datetime.date) -> set[int]:
    end = draft.start_date + datetime.timedelta(days=draft.weeks * 7 - 1)
    return {s.line for s in parse_draft(draft.text, draft.start_date, end, today).sessions}


def add_session(
    db: Session, user: models.User, session: schemas.DraftSessionIn, today: datetime.date,
    coach: models.User | None = None,
) -> None:
    """A new line at the end of the draft; its weeks grow to take in the day. The caller commits."""
    draft = _draft_for(db, user, today, coach)
    _cover(draft, session.date)
    lines = draft.text.splitlines()
    while lines and not lines[-1].strip():
        lines.pop()
    _changed(draft, [*lines, session_line(session)])


def edit_session(
    db: Session, user: models.User, line: int, session: schemas.DraftSessionIn, today: datetime.date,
    coach: models.User | None = None,
) -> None:
    """Rewrite the session on that line (moving it to another day widens the weeks if
    needed). Raises StaleLine when that line isn't a session any more. The caller commits."""
    draft = draft_of(db, user, coach)
    drop_old_sessions(draft, today)
    if draft is None or line not in _session_lines(draft, today):
        raise StaleLine()
    _cover(draft, session.date)
    lines = draft.text.splitlines()
    lines[line - 1] = session_line(session)
    _changed(draft, lines)


def remove_line(
    db: Session, user: models.User, line: int, today: datetime.date, coach: models.User | None = None
) -> None:
    """Delete a session, or a line the parser couldn't read. Raises StaleLine when
    that line holds neither any more. The caller commits."""
    draft = draft_of(db, user, coach)
    drop_old_sessions(draft, today)
    if draft is None:
        raise StaleLine()
    end = draft.start_date + datetime.timedelta(days=draft.weeks * 7 - 1)
    parsed = parse_draft(draft.text, draft.start_date, end, today)
    if line not in {s.line for s in parsed.sessions + parsed.past} | {e.line for e in parsed.errors}:
        raise StaleLine()
    lines = draft.text.splitlines()
    del lines[line - 1]
    _changed(draft, lines)


def clear(db: Session, user: models.User, coach: models.User | None = None) -> None:
    """Empty the draft and keep its weeks (the Plan tab's Clear). The caller commits."""
    draft = draft_of(db, user, coach)
    if draft is not None:
        _changed(draft, [])


class AthleteHasADraft(DraftProblem):
    """Sending would replace a draft the athlete has (the coach confirms first)."""


def send_to_athlete(db: Session, coach: models.User, athlete: models.User, today: datetime.date, replace: bool) -> int:
    """Copy the coach's draft for `athlete` into the athlete's own draft (their Plan
    tab), to adjust and push themselves. Replaces their draft only with `replace`:
    otherwise AthleteHasADraft when it holds anything. Returns the sessions sent.
    The caller commits."""
    mine = draft_of(db, athlete, coach)
    drop_old_sessions(mine, today)
    if mine is None or not mine.text.strip():
        raise DraftProblem("Your draft for them is empty.")
    if sessions_in(athlete.plan_draft, today) and not replace:
        raise AthleteHasADraft(f"{athlete.username} already has a draft on their Plan tab.")
    replace_own(db, athlete, mine.start_date, mine.weeks, mine.text, today, by="coach")
    mine.sent_at = models.utcnow()
    return sessions_in(mine, today)


def replace_own(
    db: Session, user: models.User, start: datetime.date, weeks: int, text: str, today: datetime.date,
    by: str | None = None,
) -> None:
    """Make this the account's own draft (an import, or what a coach sent: `by`
    "coach"). Its summary goes (it was about the old draft); its LEGACY block
    notes stay. The caller commits."""
    draft = _draft_for(db, user, today)
    draft.start_date, draft.weeks = monday_of(start), weeks
    draft.summary = None
    _changed(draft, text.splitlines(), by)


def sessions_in(draft: models.PlanDraft | None, today: datetime.date) -> int:
    """How many sessions a draft holds now (0 once it has expired)."""
    drop_old_sessions(draft, today)
    if draft is None:
        return 0
    end = draft.start_date + datetime.timedelta(days=draft.weeks * 7 - 1)
    return len(parse_draft(draft.text, draft.start_date, end, today).sessions)


class StaleLine(LookupError):
    """The line number the page sent no longer holds what it showed: the draft changed meanwhile."""
