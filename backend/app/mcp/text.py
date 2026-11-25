"""Compact text for the connector's tools (server.py), where planning/ has no
function for it yet: a draft, a problem with what Claude sent. Everything
else the tools return is built by planning/ (the same text as GET /api/plan/log)."""

from pydantic import ValidationError

from .. import schemas
from ..planning.basics import SPORTS, fmt_day, fmt_minutes
from ..planning.drafts import KEEP_PAST_DAYS


def problems(exc: ValidationError) -> str:
    """'Not saved. planned_duration_minutes: Input should be greater than 0'"""
    parts = [f"{'.'.join(str(step) for step in error['loc'])}: {error['msg']}" for error in exc.errors()]
    return "Not saved. " + "; ".join(parts)


WRITERS = {"claude": "you (Claude)", "coach": "the coach, on the page", "self": "the athlete, on the page"}


def draft_text(draft: schemas.DraftOut, athlete: str | None = None) -> str:
    """The draft the way the Plan tab shows it: where it stands, the lines to fix, the weekly totals, the text.
    `athlete`: a coach's draft for that athlete."""
    if not draft.saved:
        if athlete:
            return f"No draft for {athlete} yet. Write one with save_draft(athlete=\"{athlete}\", ...)."
        return "No draft saved. Write one with save_draft."
    count = f"{draft.existing_planned} planned session{'s' if draft.existing_planned != 1 else ''}"
    span = f"from {fmt_day(draft.replace_from)} to {fmt_day(draft.end_date)}"
    if athlete:
        if draft.sent_at:
            state = f"the coach's draft for {athlete} · sent to their Plan tab {fmt_day(draft.sent_at.date())}"
        elif draft.confirmed_at:
            state = f"the coach's draft for {athlete} · pushed on their calendar"
        else:
            state = f"the coach's draft for {athlete} · not sent yet"
        replaces = f"{athlete} has {count} on their calendar {span}: a push would replace them."
    else:
        state = "on the calendar since it was confirmed" if draft.confirmed_at else "saved, not on the calendar yet"
        replaces = f"Confirming replaces the {count} on the calendar {span}."
    lines = [
        f"DRAFT · {fmt_day(draft.start_date)} to {fmt_day(draft.end_date)} "
        f"({draft.start_date.isoformat()}, {draft.weeks} week{'s' if draft.weeks != 1 else ''}) · {state}",
        f"{draft.sessions_count} session{'s' if draft.sessions_count != 1 else ''} read · "
        f"{len(draft.errors)} line{'s' if len(draft.errors) != 1 else ''} with a problem",
        replaces,
    ]
    if draft.written_by:
        lines.append(f"Last changed by {WRITERS.get(draft.written_by, draft.written_by)}.")
    if draft.summary:
        lines += ["", "SUMMARY (for the reviewer)", draft.summary]
    if draft.past_count:
        lines.append(
            f"{draft.past_count} session{'s' if draft.past_count != 1 else ''} on days already past: kept for reference, "
            f"never put on the calendar, removed once {KEEP_PAST_DAYS} days old."
        )
    if draft.errors:
        lines += ["", "LINES TO FIX (save the draft again with them corrected)"]
        lines += [f"- line {e.line} \"{e.text}\": {e.message}" for e in draft.errors]
    lines += ["", "WEEKS"]
    for week in draft.weeks_preview:
        split = " · ".join(
            f"{sport} {fmt_minutes(week.minutes_by_sport[sport])}" for sport in SPORTS if week.minutes_by_sport.get(sport)
        )
        lines.append(f"- week of {fmt_day(week.start)} · {fmt_minutes(week.total_minutes)}" + (f" ({split})" if split else ""))
    lines += ["", "TEXT", draft.text.rstrip() or "(empty)"]
    return "\n".join(lines)
