"""The MCP server Claude talks to at /mcp, and every tool it offers.

Tools return compact text written for Claude, never JSON dumps, and work on
the one account auth.py let in. Each is `async` and does its work in a thread
with its own database session (`_run`), so a slow query never holds up the
rest of the app. The work itself is in planning/ and garmin/: the same
functions the page's routers call, so the two can't drift apart.

A tool answers a mistake (unknown id, bad date) with one readable sentence,
never a stack trace. Read tools carry readOnlyHint; every tool that changes
something carries destructiveHint, so Claude asks before running it.

McpApp is what main.py mounts. The SDK's session manager can only be started
once, so each start of the app (and each test) builds a fresh server.
"""

import datetime
import logging
from collections.abc import Callable
from contextlib import asynccontextmanager

import anyio
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import ValidationError
from sqlalchemy.orm import Session
from starlette.responses import JSONResponse
from starlette.types import Receive, Scope, Send

from .. import models, schemas
from ..database import SessionLocal
from ..garmin import autosync
from ..planning import drafts, objectives, planned_sessions
from ..planning.basics import fmt_day
from ..planning.draft_parser import FORMAT_HINT
from ..planning.session_detail import SESSIONS_LISTED_DAYS, session_choices, session_text
from ..planning.training_log import build_log, build_overview, fmt_planned, planned_lines, rule_set_text
from . import text as wording
from .auth import current_user_id

log = logging.getLogger("trainer.mcp")

READ = ToolAnnotations(readOnlyHint=True)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=True)

# Claude condenses its notes instead of adding to them forever.
COACH_NOTES_MAX = 4000

WORKOUT_FORMAT = (
    "Notes format: the workout type first (tempo run, easy ride...), then each interval's time with its "
    "target HR and estimated pace or power. No explanations and no \"should feel like\" commentary."
)


class ToolProblem(Exception):
    """What Claude got wrong, as one sentence it can act on."""


def _today() -> datetime.date:
    return drafts.client_today(None)  # the server's day (TZ)


async def _run(work: Callable[[Session, models.User], str]) -> str:
    """Run `work(db, user)` in a thread with its own database session and the
    connector's account. A problem with what Claude sent comes back as text."""

    def in_thread() -> str:
        with SessionLocal() as db:
            user = db.get(models.User, current_user_id.get())
            try:
                return work(db, user)
            except ToolProblem as problem:
                return str(problem)
            except ValidationError as exc:
                return wording.problems(exc)

    return await anyio.to_thread.run_sync(in_thread)


def _own_plan(db: Session, user: models.User, plan_id: int) -> models.PlannedTraining:
    row = planned_sessions.own(db, user, plan_id)
    if row is None:
        raise ToolProblem(f"No planned session with id {plan_id}. get_planned lists them with their ids.")
    return row


def _planned_line(row: models.PlannedTraining) -> str:
    return planned_lines([row])[0].removeprefix("- ")


def build_server() -> FastMCP:
    """A new server with every tool registered."""
    # FastMCP adds its own handler to the root logger, which would print every
    # line of the app's log twice (logs.py) and one line per request: undo it.
    # The SDK's warnings and errors still reach the console.
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    server = FastMCP(
        "training",
        instructions=(
            "The athlete's training calendar, Garmin history and plan (Training App). "
            "Start with get_overview. Dates are YYYY-MM-DD; durations are minutes."
        ),
        # One answer per request, nothing kept between them: works behind
        # Cloudflare and survives restarts.
        stateless_http=True,
        json_response=True,
        streamable_http_path="/",
        # The SDK's own Host check only knows localhost. main.py already sends
        # anything not on https://APP_DOMAIN away, and every request needs a token.
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )
    root.handlers[:] = handlers
    root.setLevel(level)

    # --- Reading ------------------------------------------------------------------

    @server.tool(annotations=READ)
    async def connection_check() -> str:
        """Check that the connector works: says which Training App account is connected and today's date there."""
        return await _run(
            lambda db, user: f"Connected to the Training App as {user.username}. "
            f"Today is {fmt_day(_today())} ({_today().isoformat()})."
        )

    @server.tool(annotations=READ)
    async def get_overview() -> str:
        """Call this first in every chat about training. Today's date, your coach notes from earlier chats,
        objectives, the rule set the plan must follow, Garmin's training status, the last 3 nights
        (sleep, readiness, resting HR, HRV), the last two weeks one line per session, and the sessions
        planned for the next 14 days with their ids."""
        return await _run(lambda db, user: build_overview(db, user, _today()))

    @server.tool(annotations=READ)
    async def get_training_log(weeks: int = 6, detail: str = "summary") -> str:
        """The training log of the last `weeks` weeks (1 to 26): every session with pace, HR, load and
        effort, sleep, weekly totals, the athlete's profile and zones, a year of earlier history, and
        what is already planned. detail "summary" is one line per session; "full" adds laps, HR zones
        and recovery numbers for the last 4 weeks and is several times longer: ask for it only when
        writing a plan or looking closely at recent weeks."""

        def work(db: Session, user: models.User) -> str:
            if not 1 <= weeks <= 26:
                raise ToolProblem("weeks must be between 1 and 26.")
            if detail not in ("summary", "full"):
                raise ToolProblem('detail must be "summary" or "full".')
            return build_log(db, user, weeks, _today(), detail)[0]

        return await _run(work)

    @server.tool(annotations=READ)
    async def list_sessions(days: int = 30) -> str:
        """The sessions done in the last `days` days (up to 90), newest first, one line each with the id
        get_session takes."""

        def work(db: Session, user: models.User) -> str:
            if not 1 <= days <= SESSIONS_LISTED_DAYS:
                raise ToolProblem(f"days must be between 1 and {SESSIONS_LISTED_DAYS}.")
            lines = [f"id {activity_id} · {label}" for activity_id, label in session_choices(db, user, _today(), days)]
            return "\n".join(lines) or f"No session in the last {days} days."

        return await _run(work)

    @server.tool(annotations=READ)
    async def get_session(activity_id: int) -> str:
        """One done session in full: every lap, and for the last 28 days its heart rate, pace and power
        every 5 seconds. `activity_id` comes from list_sessions."""

        def work(db: Session, user: models.User) -> str:
            activity = db.query(models.Activity).filter_by(id=activity_id, user_id=user.id).first()
            if activity is None:
                raise ToolProblem(f"No session with id {activity_id}. list_sessions gives the ids.")
            return session_text(activity, activity.trace)

        return await _run(work)

    @server.tool(annotations=READ)
    async def get_planned(start: str = "", end: str = "") -> str:
        """The planned sessions from `start` to `end` (YYYY-MM-DD, default today to 14 days ahead), each
        with the id update_planned and delete_planned take."""

        def work(db: Session, user: models.User) -> str:
            try:
                first = datetime.date.fromisoformat(start) if start else _today()
                last = datetime.date.fromisoformat(end) if end else first + datetime.timedelta(days=14)
            except ValueError:
                raise ToolProblem("Dates are written YYYY-MM-DD, like 2026-11-17.") from None
            if not 0 <= (last - first).days <= 366:
                raise ToolProblem("end must be on or after start, at most a year later.")
            rows = drafts.planned_between(db, user, first, last).order_by(
                models.PlannedTraining.date, models.PlannedTraining.id
            )
            lines = planned_lines(rows.all()) or ["- nothing planned"]
            return "\n".join([f"PLANNED · {fmt_day(first)} to {fmt_day(last)}", *lines])

        return await _run(work)

    @server.tool(annotations=READ)
    async def get_objectives() -> str:
        """The target events: title, date, target time and notes, each with the id upsert_objective takes."""

        def work(db: Session, user: models.User) -> str:
            lines = []
            for o in objectives.listed(db, user):
                when = f"{o.event_date.isoformat()} ({(o.event_date - _today()).days} days away)" if o.event_date else "no date"
                lines.append(f"- id {o.id} · {o.title} · {when} · target {o.target_time or 'not set'}")
                if o.notes:
                    lines.append(f"  {' '.join(o.notes.split())}")
            return "\n".join(lines) or "No objectives yet."

        return await _run(work)

    @server.tool(annotations=READ)
    async def get_rules() -> str:
        """The rule set: the athlete's availability, constraints and rules of thumb. Every plan must follow it."""
        return await _run(lambda db, user: rule_set_text(user).strip() or "No rule set written yet.")

    @server.tool(annotations=READ)
    async def get_draft() -> str:
        """The draft plan saved on the Plan tab: its weeks, the lines the app couldn't read and why, the
        weekly totals, and what confirming it would replace on the calendar."""
        return await _run(lambda db, user: wording.draft_text(drafts.draft_out(db, user, user.plan_draft, _today())))

    # --- Changing things ----------------------------------------------------------

    @server.tool(
        annotations=WRITE,
        description="Add one planned session to the calendar. `date` is YYYY-MM-DD, `activity_type` is running, "
        "cycling, swimming, strength_training or another sport, `minutes` its planned duration. "
        "For a whole week or block, use save_draft then confirm_draft instead. " + WORKOUT_FORMAT,
    )
    async def add_planned(date: str, activity_type: str, minutes: float, notes: str = "") -> str:
        def work(db: Session, user: models.User) -> str:
            payload = schemas.PlannedTrainingCreate(
                date=date, activity_type=activity_type, planned_duration_minutes=minutes, notes=notes.strip() or None
            )
            row = planned_sessions.add(db, user, payload, source="mcp")
            db.commit()
            return f"Added: {_planned_line(row)}"

        return await _run(work)

    @server.tool(
        annotations=WRITE,
        description="Change one planned session: only the fields given change. A new `date` (YYYY-MM-DD) moves it; "
        'notes "" clears the notes. `plan_id` comes from get_overview or get_planned. ' + WORKOUT_FORMAT,
    )
    async def update_planned(
        plan_id: int,
        date: str | None = None,
        activity_type: str | None = None,
        minutes: float | None = None,
        notes: str | None = None,
    ) -> str:
        def work(db: Session, user: models.User) -> str:
            row = _own_plan(db, user, plan_id)
            fields = {"date": date, "activity_type": activity_type, "planned_duration_minutes": minutes}
            changes = {name: value for name, value in fields.items() if value is not None}
            if notes is not None:
                changes["notes"] = notes.strip() or None
            if not changes:
                raise ToolProblem("Nothing to change: give a date, activity_type, minutes or notes.")
            planned_sessions.edit(row, schemas.PlannedTrainingUpdate(**changes), source="mcp")
            db.commit()
            return f"Updated: {_planned_line(row)}"

        return await _run(work)

    @server.tool(annotations=WRITE)
    async def delete_planned(plan_id: int) -> str:
        """Remove one planned session from the calendar. It can't be undone. `plan_id` comes from
        get_overview or get_planned. Done sessions (from Garmin) are never touched."""

        def work(db: Session, user: models.User) -> str:
            row = _own_plan(db, user, plan_id)
            line = f"{fmt_day(row.date)} · {fmt_planned(row)}"
            db.delete(row)
            db.commit()
            return f"Deleted: {line}"

        return await _run(work)

    @server.tool(
        annotations=WRITE,
        description="Save a draft plan for `weeks` weeks (1 to 12) starting the week of `start_date` (YYYY-MM-DD). "
        "It replaces the saved draft and never touches the calendar: confirm_draft does that. "
        "`text` has one session per line: date | sport | duration | details. "
        f"{FORMAT_HINT.removeprefix('Use: ')}. Rest days are left out. The answer lists the lines the app "
        "couldn't read and why: fix them and save again. The details become the session's notes. " + WORKOUT_FORMAT,
    )
    async def save_draft(start_date: str, weeks: int, text: str) -> str:
        def work(db: Session, user: models.User) -> str:
            drafts.save(db, user, schemas.DraftIn(start_date=start_date, weeks=weeks, text=text))
            db.commit()
            db.refresh(user)
            return wording.draft_text(drafts.draft_out(db, user, user.plan_draft, _today()))

        return await _run(work)

    @server.tool(annotations=WRITE)
    async def confirm_draft() -> str:
        """Put the saved draft on the calendar. Every planned session from today (or the draft's first
        day, if later) to the draft's last day is replaced by the draft's sessions. Refused while a
        line has a problem. Ask the athlete before calling this."""

        def work(db: Session, user: models.User) -> str:
            try:
                result = drafts.confirm(db, user, _today())
            except drafts.DraftProblem as problem:
                raise ToolProblem(f"Not confirmed. {problem}. get_draft shows the draft.") from None
            db.commit()
            return (
                f"On the calendar: {result.added} sessions from {fmt_day(result.start_date)} to "
                f"{fmt_day(result.end_date)}, replacing {result.replaced} that were planned there."
            )

        return await _run(work)

    @server.tool(annotations=WRITE)
    async def set_rules(rules: str) -> str:
        """Replace the whole rule set (availability, constraints, rules of thumb) with `rules`. Read it
        with get_rules first and send back the full text with your changes: what you leave out is lost."""

        def work(db: Session, user: models.User) -> str:
            user.rule_set = schemas.RuleSet(rules=rules).rules.strip()  # "" = cleared, as on the Plan tab
            db.commit()
            return f"Rule set saved ({len(user.rule_set)} characters)."

        return await _run(work)

    @server.tool(annotations=WRITE)
    async def upsert_objective(
        title: str | None = None,
        objective_id: int | None = None,
        event_date: str | None = None,
        target_time: str | None = None,
        notes: str | None = None,
    ) -> str:
        """Add a target event, or change one when `objective_id` is given (from get_objectives or
        get_overview). A new one needs a `title`. `event_date` is YYYY-MM-DD, `target_time` free text
        like 4:15. When changing, only the fields given change, and "" clears a date, target or notes."""

        def work(db: Session, user: models.User) -> str:
            given = {"title": title, "event_date": event_date, "target_time": target_time, "notes": notes}
            fields = {name: (value.strip() or None) for name, value in given.items() if value is not None}
            if objective_id is None:
                if not fields.get("title"):
                    raise ToolProblem("A new objective needs a title.")
                row = objectives.add(db, user, schemas.ObjectiveCreate(**fields))
                verb = "Added"
            else:
                row = objectives.own(db, user, objective_id)
                if row is None:
                    raise ToolProblem(f"No objective with id {objective_id}. get_objectives lists them.")
                if not fields:
                    raise ToolProblem("Nothing to change: give a title, event_date, target_time or notes.")
                objectives.edit(row, schemas.ObjectiveUpdate(**fields))
                verb = "Updated"
            db.commit()
            when = row.event_date.isoformat() if row.event_date else "no date"
            return f"{verb}: id {row.id} · {row.title} · {when} · target {row.target_time or 'not set'}"

        return await _run(work)

    @server.tool(annotations=WRITE)
    async def set_coach_notes(notes: str) -> str:
        """Replace your coach notes: your memory between chats, shown at the top of get_overview. Keep
        how the athlete felt, what was adjusted and why, and what to watch next. At most 4000
        characters, and this replaces everything: send the whole notes, condensing older entries
        instead of only adding."""

        def work(db: Session, user: models.User) -> str:
            cleaned = notes.strip()
            if len(cleaned) > COACH_NOTES_MAX:
                raise ToolProblem(
                    f"Not saved: {len(cleaned)} characters, the limit is {COACH_NOTES_MAX}. Condense the older notes."
                )
            user.coach_notes = cleaned or None
            db.commit()
            return f"Coach notes saved ({len(cleaned)} of {COACH_NOTES_MAX} characters)."

        return await _run(work)

    @server.tool(annotations=WRITE)
    async def sync_garmin() -> str:
        """Fetch the latest sessions, sleep and recovery from Garmin. It starts in the background and
        answers at once: wait about a minute, then call get_overview again."""

        def work(db: Session, user: models.User) -> str:
            running = autosync.job_status(user.id)
            if running and running["state"] in ("waiting", "running"):
                return "A Garmin sync is already running. Wait about a minute, then read the data again."
            try:
                autosync.start_job(user.id, autosync.api_for(user))
            except autosync.GarminNotReady as problem:
                raise ToolProblem(f"Sync not started. {problem} That is done on the app's page.") from None
            return "Garmin sync started. It takes about a minute; then read the data again."

        return await _run(work)

    return server


class McpApp:
    """The ASGI app behind /mcp (inside auth.BearerAuth). `running()` wraps the
    app's lifetime (main.lifespan); outside it every request gets a 503."""

    def __init__(self) -> None:
        self._manager = None

    @asynccontextmanager
    async def running(self):
        server = build_server()
        server.streamable_http_app()  # creates server.session_manager
        async with server.session_manager.run():
            self._manager = server.session_manager
            try:
                yield
            finally:
                self._manager = None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if self._manager is None:
            await JSONResponse({"detail": "The connector is starting."}, status_code=503)(scope, receive, send)
            return
        await self._manager.handle_request(scope, receive, send)
