"""Coaches and their athletes: roles, links, and what a coach may see of an athlete.

Roles (set by the admin, set_role): athlete (every new account), coach, admin.
Only athletes train: own calendar, Garmin, objectives and plan. A coach plans
for their athletes and has none of that (deps.get_current_member refuses them);
an admin manages accounts. So only an athlete can be coached. Changing an
athlete's role keeps their training data, out of sight, should they come back.

A link (models.CoachLink) is made by the coach with the athlete's username,
with no approval (add_athlete); the athlete sees their coaches in Settings and
either side can remove it. Names that aren't athletes are counted
(accounts.record_coach_failure), so usernames can't be guessed this way.

What a coach sees of an athlete (athlete_profile, calendar_days in
routers/coach.py, and the training log mcp/server.py gives the coach's
Claude): planned and done sessions, objectives, the benchmarks and zones they
entered, Garmin's zones, records, training status and load, the rule set.
Sleep, recovery (readiness, HRV, resting HR, stress, body battery) and the
profile keys in HIDDEN_FROM_COACH only when the athlete turns on sharing for
that coach (CoachLink.share_health, can_see_health): off by default.

Every route or tool about one athlete goes through athlete_for: no link, no
athlete (404, or one sentence for Claude). Used by routers/admin.py (roles),
routers/coach.py, routers/coaches.py, routers/plan.py (planning for an
athlete), mcp/server.py (a coach's connector) and avatars.py.
"""

import datetime

from sqlalchemy.orm import Session

from . import accounts, avatars, models, schemas
from .planning import drafts, objectives
from .planning.basics import monday_of
from .planning.training_log import garmin_profile_lines, rule_set_text

# The athlete's own profile keys a coach sees only with sharing on (training_log.PRIVATE_PROFILE_KEYS).
HIDDEN_FROM_COACH = ("health", "resting_hr")


class CoachingProblem(ValueError):
    """Why that can't be done, as a sentence. `status` is the HTTP status to answer."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


# --- Roles --------------------------------------------------------------------------


def role_of(user: models.User) -> str:
    if user.is_admin:
        return "admin"
    return "coach" if user.is_coach else "athlete"


def set_role(db: Session, user: models.User, role: str) -> None:
    """Make the account an athlete, a coach or an admin. Links that no longer make
    sense go: a coach who stops coaching loses their athletes, and anyone who
    stops being an athlete loses their coaches. The caller commits."""
    user.is_admin = role == "admin"
    user.is_coach = role == "coach"
    if role != "coach":
        db.query(models.CoachLink).filter(models.CoachLink.coach_id == user.id).delete()
        db.query(models.PlanDraft).filter(models.PlanDraft.coach_id == user.id).delete()
    if role != "athlete":  # only athletes are coached
        db.query(models.CoachLink).filter(models.CoachLink.athlete_id == user.id).delete()
        db.query(models.PlanDraft).filter(
            models.PlanDraft.user_id == user.id, models.PlanDraft.coach_id.is_not(None)
        ).delete()


# --- Links --------------------------------------------------------------------------


def athletes_of(db: Session, coach: models.User) -> list[models.User]:
    return (
        db.query(models.User)
        .join(models.CoachLink, models.CoachLink.athlete_id == models.User.id)
        .filter(models.CoachLink.coach_id == coach.id)
        .order_by(models.User.username)
        .all()
    )


def coaches_of(db: Session, athlete: models.User) -> list[tuple[models.CoachLink, models.User]]:
    return (
        db.query(models.CoachLink, models.User)
        .join(models.User, models.CoachLink.coach_id == models.User.id)
        .filter(models.CoachLink.athlete_id == athlete.id)
        .order_by(models.User.username)
        .all()
    )


def is_coach_of(db: Session, coach: models.User, athlete: models.User) -> bool:
    return (
        db.query(models.CoachLink).filter_by(coach_id=coach.id, athlete_id=athlete.id).first() is not None
    )


def link_of(db: Session, coach: models.User, athlete: models.User) -> models.CoachLink | None:
    return db.query(models.CoachLink).filter_by(coach_id=coach.id, athlete_id=athlete.id).first()


def can_see_health(db: Session, coach: models.User, athlete: models.User) -> bool:
    """Whether the athlete shares sleep and recovery with this coach: the one place that decides."""
    link = link_of(db, coach, athlete)
    return bool(link and link.share_health)


def athlete_named(db: Session, coach: models.User, username: str) -> models.User | None:
    """athlete_for, by username (what Claude passes): None for every kind of miss."""
    found = db.query(models.User).filter(models.User.username == accounts.normalize_username(username)).first()
    return athlete_for(db, coach, found.id) if found else None


def athlete_for(db: Session, coach: models.User, athlete_id: int) -> models.User | None:
    """The athlete with that id, if `coach` coaches them (and they aren't an admin)."""
    athlete = db.get(models.User, athlete_id)
    if athlete is None or athlete.is_admin or athlete.is_coach or not coach.is_coach or not is_coach_of(db, coach, athlete):
        return None
    return athlete


def add_athlete(db: Session, coach: models.User, username: str) -> models.User:
    """Link the athlete with that username to `coach`, straight away. Unknown names,
    admins and coaches (yourself included) are refused the same way, and counted.
    The caller commits."""
    wait = accounts.seconds_coach_blocked(coach.id)
    if wait:
        raise CoachingProblem(f"Too many names that aren't athletes. Try again in {max(1, wait // 60)} min.", 429)
    name = accounts.normalize_username(username)
    athlete = db.query(models.User).filter(models.User.username == name).first()
    if athlete is None or athlete.is_admin or athlete.is_coach:
        accounts.record_coach_failure(coach.id)
        raise CoachingProblem(f'No athlete is called "{name}".', 404)
    if is_coach_of(db, coach, athlete):
        raise CoachingProblem(f"{athlete.username} is already one of your athletes.", 409)
    db.add(models.CoachLink(coach_id=coach.id, athlete_id=athlete.id))
    return athlete


def remove_athlete(db: Session, coach: models.User, athlete_id: int) -> bool:
    """The coach lets an athlete go; their draft for them goes too. The caller commits."""
    removed = db.query(models.CoachLink).filter_by(coach_id=coach.id, athlete_id=athlete_id).delete()
    db.query(models.PlanDraft).filter_by(coach_id=coach.id, user_id=athlete_id).delete()
    return bool(removed)


def remove_coach(db: Session, athlete: models.User, link_id: int) -> bool:
    """The athlete removes one of their coaches (and that coach's draft for them). The caller commits."""
    link = db.query(models.CoachLink).filter_by(id=link_id, athlete_id=athlete.id).first()
    if link is None:
        return False
    db.query(models.PlanDraft).filter_by(coach_id=link.coach_id, user_id=athlete.id).delete()
    db.delete(link)
    return True


# --- What a coach sees ----------------------------------------------------------------


def _week_minutes(db: Session, athlete: models.User, today: datetime.date) -> tuple[float, float]:
    """(planned, done) minutes of this Monday-to-Sunday week."""
    start = monday_of(today)
    end = start + datetime.timedelta(days=6)
    planned = (
        db.query(models.PlannedTraining.planned_duration_minutes)
        .filter(models.PlannedTraining.user_id == athlete.id, models.PlannedTraining.date.between(start, end))
        .all()
    )
    done = (
        db.query(models.Activity.duration_seconds)
        .filter(
            models.Activity.user_id == athlete.id,
            models.Activity.start_time >= datetime.datetime.combine(start, datetime.time.min),
            models.Activity.start_time <= datetime.datetime.combine(end, datetime.time.max),
        )
        .all()
    )
    return sum(m or 0 for (m,) in planned), sum(s or 0 for (s,) in done) / 60


STALE_SYNC_DAYS = 3  # older than this, a plan would be built on stale data


def garmin_state(athlete: models.User, now: datetime.datetime | None = None) -> str:
    """"ok", "stale" (no sync for STALE_SYNC_DAYS days, often an expired Garmin
    session) or "not connected"."""
    if not athlete.garmin_session_encrypted:
        return "not connected"
    synced = athlete.garmin_synced_at
    if synced is None:
        return "stale"
    if synced.tzinfo is None:
        synced = synced.replace(tzinfo=datetime.UTC)
    return "stale" if (now or models.utcnow()) - synced > datetime.timedelta(days=STALE_SYNC_DAYS) else "ok"


def athlete_summary(
    db: Session, athlete: models.User, today: datetime.date, coach: models.User | None = None
) -> schemas.CoachAthleteOut:
    """One line of the coach's list: the next dated objective, this week's hours, the
    last sync and whether it's stale, and, for `coach`, whether recovery is shared
    and where their draft for this athlete stands."""
    upcoming = [o for o in objectives.listed(db, athlete) if o.event_date and o.event_date >= today]
    main = f"{upcoming[0].title} · {upcoming[0].event_date.isoformat()}" if upcoming else None
    planned, done = _week_minutes(db, athlete, today)
    return schemas.CoachAthleteOut(
        id=athlete.id,
        username=athlete.username,
        avatar_url=avatars.url(athlete),
        main_objective=main,
        planned_minutes=planned,
        done_minutes=done,
        garmin_synced_at=athlete.garmin_synced_at,
        garmin_state=garmin_state(athlete),
        share_health=bool(coach and can_see_health(db, coach, athlete)),
        draft=_draft_state(db, athlete, coach, today) if coach else None,
    )


def _draft_state(
    db: Session, athlete: models.User, coach: models.User, today: datetime.date
) -> schemas.CoachDraftState | None:
    draft = drafts.draft_of(db, athlete, coach)
    if draft is None or not draft.text.strip():
        return None
    return schemas.CoachDraftState(
        written_by=draft.written_by, updated_at=draft.updated_at, sent_at=draft.sent_at,
        sessions=drafts.sessions_in(draft, today),
    )


def athlete_profile(db: Session, athlete: models.User, shared: bool = False) -> schemas.CoachAthleteProfile:
    """What the coach's Athlete tab shows; `shared` (can_see_health) adds the hidden profile keys."""
    profile = {k: v for k, v in (athlete.athlete_profile or {}).items() if shared or k not in HIDDEN_FROM_COACH}
    return schemas.CoachAthleteProfile(
        id=athlete.id,
        username=athlete.username,
        objectives=objectives.listed(db, athlete),
        profile=profile,
        garmin=garmin_profile_lines(athlete),
        rules=rule_set_text(athlete),
    )
