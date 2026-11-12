"""Compact text for the connector's tools (server.py), where planning/ has no
function for it yet: a draft, a problem with what Claude sent. Everything
else the tools return is built by planning/ (the same text as the History tab)."""

from pydantic import ValidationError

from .. import schemas
from ..planning.basics import SPORTS, fmt_day, fmt_minutes


def problems(exc: ValidationError) -> str:
    """'Not saved. planned_duration_minutes: Input should be greater than 0'"""
    parts = [f"{'.'.join(str(step) for step in error['loc'])}: {error['msg']}" for error in exc.errors()]
    return "Not saved. " + "; ".join(parts)


def draft_text(draft: schemas.DraftOut) -> str:
    """The draft the way the Plan tab shows it: where it stands, the lines to fix, the weekly totals, the text."""
    if not draft.saved:
        return "No draft saved. Write one with save_draft."
    state = "on the calendar since it was confirmed" if draft.confirmed_at else "saved, not on the calendar yet"
    lines = [
        f"DRAFT · {fmt_day(draft.start_date)} to {fmt_day(draft.end_date)} "
        f"({draft.start_date.isoformat()}, {draft.weeks} week{'s' if draft.weeks != 1 else ''}) · {state}",
        f"{draft.sessions_count} session{'s' if draft.sessions_count != 1 else ''} read · "
        f"{len(draft.errors)} line{'s' if len(draft.errors) != 1 else ''} with a problem",
        f"Confirming replaces the {draft.existing_planned} planned session{'s' if draft.existing_planned != 1 else ''} "
        f"on the calendar from {fmt_day(draft.replace_from)} to {fmt_day(draft.end_date)}.",
    ]
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
