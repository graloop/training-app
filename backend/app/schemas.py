import datetime
from typing import Annotated

from pydantic import AfterValidator, BaseModel, EmailStr, Field


def _as_utc(value: datetime.datetime) -> datetime.datetime:
    # SQLite hands back naive datetimes; they are stored in UTC. Without the
    # offset a browser would read them as local time.
    return value if value.tzinfo else value.replace(tzinfo=datetime.timezone.utc)


UtcDatetime = Annotated[datetime.datetime, AfterValidator(_as_utc)]


# --- Auth & accounts ---

# Lowercase letters, digits and . _ - @ (so older accounts named after their
# Garmin email still fit).
USERNAME_PATTERN = r"^[a-z0-9._@-]{3,64}$"
PASSWORD_MIN, PASSWORD_MAX = 8, 256


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=1, max_length=256)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class MeOut(BaseModel):
    id: int
    username: str
    is_admin: bool
    garmin_connected: bool
    garmin_email: str | None

    @classmethod
    def of(cls, user) -> "MeOut":
        """The logged-in account as the page sees it (a models.User)."""
        return cls(
            id=user.id,
            username=user.username,
            is_admin=bool(user.is_admin),
            garmin_connected=bool(user.garmin_session_encrypted),
            garmin_email=user.garmin_email if user.garmin_session_encrypted else None,
        )


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=PASSWORD_MIN, max_length=PASSWORD_MAX)


class AccountData(BaseModel):
    """What deleting the account would remove."""

    activities: int = 0
    sleep_nights: int = 0
    planned_sessions: int = 0
    objectives: int = 0


class AdminUserOut(BaseModel):
    id: int
    username: str
    is_admin: bool
    has_password: bool
    garmin_connected: bool
    data: AccountData
    created_at: UtcDatetime | None
    last_login_at: UtcDatetime | None


class AdminUserCreate(BaseModel):
    username: str = Field(pattern=USERNAME_PATTERN)
    password: str = Field(min_length=PASSWORD_MIN, max_length=PASSWORD_MAX)
    is_admin: bool = False


class AdminUserUpdate(BaseModel):
    password: str | None = Field(default=None, min_length=PASSWORD_MIN, max_length=PASSWORD_MAX)
    is_admin: bool | None = None


class AdminUsersResponse(BaseModel):
    users: list[AdminUserOut]


class LoginEventOut(BaseModel):
    at: UtcDatetime
    username: str
    success: bool


class ActivityDay(BaseModel):
    date: datetime.date  # in the admin's own timezone
    logins: int
    failed: int


class ActivityTotals(BaseModel):
    accounts: int
    active_accounts: int  # distinct accounts that logged in during the period
    logins: int
    failed: int


class AdminActivity(BaseModel):
    """Connections over the last `days` days, for the admin dashboard."""

    days: list[ActivityDay]  # oldest first, always `days` entries ending today
    totals: ActivityTotals
    recent: list[LoginEventOut]  # newest first


# --- Garmin ---

class GarminConnectRequest(BaseModel):
    garmin_email: EmailStr
    garmin_password: str = Field(min_length=1, max_length=256)


class GarminSyncResponse(BaseModel):
    activities_synced: int
    sleep_records_synced: int
    lookback_days: int
    details_synced: int = 0  # activities whose effort/feel/laps were fetched this time
    days_synced: int = 0  # days whose sleep and recovery data were fetched this time
    more_to_sync: bool = False  # older days or activity details are still waiting: sync again


class SyncJob(BaseModel):
    """A sync running on the server, started with the Sync button."""

    state: str  # idle (none yet) | waiting (for a background run) | running | done | failed
    rounds: int = 0  # batches done so far
    activities_synced: int = 0
    days_synced: int = 0
    details_synced: int = 0
    more_to_sync: bool = False  # still something waiting after the last batch: sync again later
    error: str | None = None
    started_at: UtcDatetime | None = None
    finished_at: UtcDatetime | None = None


class HistoryImportRequest(BaseModel):
    months: int = Field(default=12, ge=1, le=36)


class HistoryImportResponse(BaseModel):
    activities_imported: int
    since: datetime.date


# --- Objectives ---

class ObjectiveOut(BaseModel):
    id: int
    title: str
    event_date: datetime.date | None
    target_time: str | None
    notes: str | None

    class Config:
        from_attributes = True


class ObjectiveCreate(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    event_date: datetime.date | None = None
    target_time: str | None = Field(default=None, max_length=32)
    notes: str | None = Field(default=None, max_length=2000)


class ObjectiveUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    event_date: datetime.date | None = None
    target_time: str | None = Field(default=None, max_length=32)
    notes: str | None = Field(default=None, max_length=2000)


class ObjectivesResponse(BaseModel):
    objectives: list[ObjectiveOut]


# --- Calendar ---

class LegOut(BaseModel):
    """One leg of a multisport race."""

    type: str
    sport: str  # swim | bike | run | other (transitions)
    duration_seconds: float | None = None
    distance_meters: float | None = None
    avg_hr: float | None = None


class ActivityOut(BaseModel):
    id: int
    garmin_activity_id: str
    activity_type: str
    name: str | None
    start_time: datetime.datetime
    duration_seconds: float | None
    distance_meters: float | None
    avg_hr: float | None
    aerobic_training_effect: float | None
    anaerobic_training_effect: float | None
    calories: float | None
    training_load: float | None
    perceived_effort: int | None  # RPE 1-10, answered on the watch
    feel: int | None  # 0 very weak .. 100 very strong
    legs: list[LegOut] = []  # multisport races only

    class Config:
        from_attributes = True


class SleepOut(BaseModel):
    id: int
    date: datetime.date
    total_sleep_seconds: float | None
    deep_sleep_seconds: float | None
    rem_sleep_seconds: float | None
    light_sleep_seconds: float | None
    sleep_score: float | None
    resting_heart_rate: float | None

    class Config:
        from_attributes = True


class PlannedTrainingOut(BaseModel):
    id: int
    date: datetime.date
    activity_type: str
    planned_duration_minutes: float | None
    notes: str | None
    source: str

    class Config:
        from_attributes = True


# A confirmed draft line's details become the session's notes, so they get
# the same room as a whole draft (DraftIn.text).
PLANNED_NOTES_MAX = 30000


class PlannedTrainingCreate(BaseModel):
    date: datetime.date
    activity_type: str = Field(min_length=1, max_length=64)
    planned_duration_minutes: float = Field(gt=0, le=24 * 60)
    notes: str | None = Field(default=None, max_length=PLANNED_NOTES_MAX)


class PlannedTrainingUpdate(BaseModel):
    activity_type: str | None = Field(default=None, min_length=1, max_length=64)
    planned_duration_minutes: float | None = Field(default=None, gt=0, le=24 * 60)
    notes: str | None = Field(default=None, max_length=PLANNED_NOTES_MAX)


class CalendarDay(BaseModel):
    date: datetime.date
    activities: list[ActivityOut] = []
    sleep: SleepOut | None = None
    planned: list[PlannedTrainingOut] = []


class CalendarResponse(BaseModel):
    start: datetime.date
    end: datetime.date
    days: list[CalendarDay]


# --- Plan page ---

MAX_PLAN_WEEKS = 12


class TrainingLog(BaseModel):
    text: str
    start: datetime.date
    end: datetime.date
    weeks: int
    detail: str  # "full" | "summary"


class AthleteNotes(BaseModel):
    """Availability, constraints and anything Garmin doesn't know, in the athlete's words."""

    notes: str = Field(default="", max_length=10000)


class RuleSet(BaseModel):
    """The Plan tab's rules: availability, constraints, rules of thumb, in the athlete's words."""

    rules: str = Field(default="", max_length=20000)


PACE = r"^\d{1,2}:\d{2}$"


class AthleteProfile(BaseModel):
    """What the athlete enters on the Athlete tab: benchmarks and zones Garmin doesn't have
    or gets wrong. Everything is optional."""

    weight_kg: float | None = Field(default=None, gt=20, lt=300)
    height_cm: float | None = Field(default=None, gt=100, lt=250)
    max_hr: int | None = Field(default=None, ge=100, le=230)
    threshold_hr: int | None = Field(default=None, ge=80, le=230)
    resting_hr: int | None = Field(default=None, ge=25, le=120)
    ftp_watts: int | None = Field(default=None, ge=50, le=700)
    ftp_date: datetime.date | None = None
    bike_zones: str | None = Field(default=None, max_length=1000)
    threshold_pace: str | None = Field(default=None, pattern=PACE)  # min:sec per km
    run_zones: str | None = Field(default=None, max_length=1000)
    css_pace: str | None = Field(default=None, pattern=PACE)  # min:sec per 100 m
    swim_zones: str | None = Field(default=None, max_length=1000)
    equipment: str | None = Field(default=None, max_length=2000)
    health: str | None = Field(default=None, max_length=3000)
    other: str | None = Field(default=None, max_length=3000)


class AthleteOut(BaseModel):
    profile: AthleteProfile
    garmin: list[str]  # what Garmin knows, as text lines (read only)


class DraftIn(BaseModel):
    start_date: datetime.date  # any day of the first week to plan
    weeks: int = Field(ge=1, le=MAX_PLAN_WEEKS)
    text: str = Field(default="", max_length=30000)
    notes: str | None = Field(default=None, max_length=20000)  # older block notes: kept unless sent


class DraftSessionOut(BaseModel):
    line: int
    date: datetime.date
    activity_type: str
    sport: str  # swim | bike | run | strength | other
    minutes: float
    details: str | None


class DraftErrorOut(BaseModel):
    line: int
    text: str
    message: str


class DraftWeekOut(BaseModel):
    start: datetime.date
    end: datetime.date
    sessions: list[DraftSessionOut]
    minutes_by_sport: dict[str, float]
    total_minutes: float


class DraftOut(BaseModel):
    saved: bool  # false = nothing stored yet, these are the defaults
    start_date: datetime.date
    end_date: datetime.date
    weeks: int
    text: str
    notes: str
    sessions_count: int
    errors: list[DraftErrorOut]
    weeks_preview: list[DraftWeekOut]
    replace_from: datetime.date  # confirming replaces planned sessions from this day to end_date
    existing_planned: int  # sessions currently on the calendar in that span
    confirmed_at: UtcDatetime | None


class ConfirmResult(BaseModel):
    added: int
    replaced: int
    start_date: datetime.date
    end_date: datetime.date


# --- Import tab: copying someone else's planned sessions ---


class ShareInfo(BaseModel):
    username: str
    share_code: str


class ImportRequest(BaseModel):
    username: str = Field(min_length=1, max_length=255)  # theirs
    share_code: str = Field(min_length=1, max_length=32)  # theirs
    source_start: datetime.date  # any day of their first week
    weeks: int = Field(ge=1, le=MAX_PLAN_WEEKS)
    target_start: datetime.date | None = None  # any day of your first week; default: the same weeks


class ImportPreview(BaseModel):
    from_username: str
    source_start: datetime.date
    source_end: datetime.date
    start_date: datetime.date  # your weeks
    end_date: datetime.date
    weeks: int
    text: str  # as draft lines, what "Import to plan" puts in your draft
    sessions_count: int
    skipped_past: int  # their sessions that land on your days already past
    errors: list[DraftErrorOut] = []
    weeks_preview: list[DraftWeekOut]
    replace_from: datetime.date
    existing_planned: int  # your planned sessions "Import to calendar" replaces
    draft_sessions: int  # sessions in your Plan draft, replaced by "Import to plan"
