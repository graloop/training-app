"""The database tables. Everything a user owns hangs off User and is deleted
with it (cascade), which is how the admin's "delete account" removes all data.

    User ─┬─ Activity ── ActivityTrace   done sessions, from Garmin (+ 5-second data, 28 days)
          ├─ SleepRecord                one row per night, from Garmin
          ├─ DailyMetrics               one row per day: readiness, training status, stress
          ├─ PlannedTraining            sessions on the calendar, typed or from a confirmed plan
          ├─ PlanDraft                  the Plan tab's draft (at most one)
          ├─ Objective                  target events
          ├─ LoginEvent                 the connection log (admin dashboard)
          └─ McpAuthCode, McpToken      Claude's connector: login codes and tokens (app/mcp/)

    McpClient                           a Claude app registered as a connector (no owner)

Every new column must be nullable: that is all database.add_missing_columns()
can add to an existing database. What the API sends is in schemas.py.
"""

import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def utcnow() -> datetime.datetime:
    """Timestamps are stored in UTC (SQLite gives them back without the offset: see schemas.UtcDatetime)."""
    return datetime.datetime.now(datetime.UTC)


class User(Base):
    """An account, created by an admin (there is no sign-up). An admin account
    only manages the others: it has no calendar or Garmin connection."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Login name chosen by the admin. Stored in the historical "email" column
    # (accounts used to be keyed by Garmin email), so existing databases keep
    # working without a migration.
    username: Mapped[str] = mapped_column("email", String(255), unique=True, index=True, nullable=False)
    # scrypt hash (see security.hash_password); "" = no password, can't log in.
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    is_admin: Mapped[bool | None] = mapped_column(Boolean, nullable=True, default=False)
    # Bumped on password change/reset: every token issued before stops working.
    token_version: Mapped[int | None] = mapped_column(Integer, nullable=True, default=0)

    garmin_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Encrypted (Fernet) blob of the garth session tokens. Never store the raw
    # Garmin password.
    garmin_session_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Zones, thresholds, records and race predictions as Garmin has them,
    # refreshed on each sync (see garmin/sync.py, normalize_profile).
    garmin_profile: Mapped[dict | None] = mapped_column(JSON(none_as_null=True), nullable=True)
    # The athlete's own words: availability, constraints, what Garmin doesn't know.
    # LEGACY ("About you", before v14, 2026-10-17): read by training_log.rule_set_text until rule_set is saved.
    athlete_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    # What the athlete enters on the Athlete tab: FTP, zones, CSS, equipment... (schemas.AthleteProfile).
    athlete_profile: Mapped[dict | None] = mapped_column(JSON(none_as_null=True), nullable=True)
    # The Plan tab's rule set: availability, constraints and rules of thumb the plan must follow.
    # It replaces the older "About you" and block notes (see planning/training_log.py, rule_set_text).
    rule_set: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Claude's memory between chats (mcp/server.py, set_coach_notes): how the athlete felt,
    # what was adjusted and why, what to watch. Only written through the connector.
    coach_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    # With their username, lets someone else copy this account's planned sessions
    # (Import tab). Created on first use; a new one stops the old one working.
    share_code: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)

    # Set on every successful login; shown to the admin next to the account.
    last_login_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    activities: Mapped[list["Activity"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    sleep_records: Mapped[list["SleepRecord"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    planned_sessions: Mapped[list["PlannedTraining"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    login_events: Mapped[list["LoginEvent"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    objective_items: Mapped[list["Objective"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    plan_draft: Mapped["PlanDraft | None"] = relationship(back_populates="user", cascade="all, delete-orphan")
    daily_metrics: Mapped[list["DailyMetrics"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    mcp_auth_codes: Mapped[list["McpAuthCode"]] = relationship(cascade="all, delete-orphan")
    mcp_tokens: Mapped[list["McpToken"]] = relationship(cascade="all, delete-orphan")


class Activity(Base):
    """A done session, as Garmin's activity list gives it (garmin/sync.py,
    save_activities), completed later by the per-activity details: effort,
    feel, laps, weather (enrich_activities). Times are the athlete's local time."""

    __tablename__ = "activities"
    __table_args__ = (UniqueConstraint("user_id", "garmin_activity_id", name="uq_activity_user_garmin_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)

    garmin_activity_id: Mapped[str] = mapped_column(String(64), nullable=False)
    activity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    start_time: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    distance_meters: Mapped[float | None] = mapped_column(Float, nullable=True)
    avg_hr: Mapped[float | None] = mapped_column(Float, nullable=True)
    aerobic_training_effect: Mapped[float | None] = mapped_column(Float, nullable=True)
    anaerobic_training_effect: Mapped[float | None] = mapped_column(Float, nullable=True)
    calories: Mapped[float | None] = mapped_column(Float, nullable=True)
    training_load: Mapped[float | None] = mapped_column(Float, nullable=True)  # Garmin "activityTrainingLoad"
    # Answered on the watch after the activity; fetched per activity (see garmin/sync.py).
    perceived_effort: Mapped[int | None] = mapped_column(Integer, nullable=True)  # RPE 1-10
    feel: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 0 very weak .. 100 very strong
    # none_as_null: "nothing yet" is a real SQL NULL, so it can be queried for.
    laps: Mapped[list | None] = mapped_column(JSON(none_as_null=True), nullable=True)  # compact per-lap distance/time/HR
    extras: Mapped[dict | None] = mapped_column(JSON(none_as_null=True), nullable=True)  # sweat_ml, temp_c, wind_kmh, humidity
    details_synced_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    raw_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    user: Mapped["User"] = relationship(back_populates="activities")
    trace: Mapped["ActivityTrace | None"] = relationship(back_populates="activity", cascade="all, delete-orphan", uselist=False)

    @property
    def legs(self) -> list[dict]:
        """A multisport race's legs (swim, transitions, bike, run), once synced."""
        return (self.extras or {}).get("legs") or []


class ActivityTrace(Base):
    """What a recent session's original file says second by second. Its own
    table: activities are often loaded whole, and these are deleted after
    TRACE_DAYS days (garmin/sync.py)."""

    __tablename__ = "activity_traces"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    activity_id: Mapped[int] = mapped_column(ForeignKey("activities.id"), nullable=False, unique=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # failed downloads or unreadable files
    fetched_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)  # None: not got yet
    samples: Mapped[dict | None] = mapped_column(JSON(none_as_null=True), nullable=True)  # t, hr, speed, power, cadence
    laps: Mapped[list | None] = mapped_column(JSON(none_as_null=True), nullable=True)  # when each lap began: start, duration

    activity: Mapped["Activity"] = relationship(back_populates="trace")


class SleepRecord(Base):
    """One night, dated by the morning it ends. raw_json keeps only the few parts
    of Garmin's answer that are read (garmin/sync.py, SLEEP_KEPT)."""

    __tablename__ = "sleep_records"
    __table_args__ = (UniqueConstraint("user_id", "date", name="uq_sleep_user_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)

    date: Mapped[datetime.date] = mapped_column(nullable=False, index=True)
    total_sleep_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    deep_sleep_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    rem_sleep_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    light_sleep_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    sleep_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    resting_heart_rate: Mapped[float | None] = mapped_column(Float, nullable=True)  # bpm, from the sleep payload
    raw_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    user: Mapped["User"] = relationship(back_populates="sleep_records")


class PlannedTraining(Base):
    """A session on the calendar that is still to do. `source` says where it came
    from: "manual" (the calendar's dialog), "plan" (a confirmed draft) or "import"."""

    __tablename__ = "planned_training"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)

    date: Mapped[datetime.date] = mapped_column(nullable=False, index=True)
    activity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    planned_duration_minutes: Mapped[float | None] = mapped_column(Float, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String(16), default="manual")  # older rows may say "ai"

    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    user: Mapped["User"] = relationship(back_populates="planned_sessions")


class Objective(Base):
    """A target event (title, date, target time), added and edited on the page."""

    __tablename__ = "objectives"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)

    title: Mapped[str] = mapped_column(String(255), nullable=False)
    event_date: Mapped[datetime.date | None] = mapped_column(nullable=True)
    target_time: Mapped[str | None] = mapped_column(String(32), nullable=True)  # e.g. "1:45:00"
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)  # priority, what matters most

    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    user: Mapped["User"] = relationship(back_populates="objective_items")


class LoginEvent(Base):
    """One login attempt, for the admin dashboard. Failed attempts on names
    that aren't accounts are stored without the typed name (it may be a
    password typed in the wrong box)."""

    __tablename__ = "login_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    username: Mapped[str] = mapped_column(String(255), nullable=False)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)

    user: Mapped["User | None"] = relationship(back_populates="login_events")


class PlanDraft(Base):
    """The Plan page's working draft: the weeks being planned, the sessions as
    typed (one per line) and the block notes. Nothing here is on the calendar
    until the user confirms."""

    __tablename__ = "plan_drafts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, unique=True)

    start_date: Mapped[datetime.date] = mapped_column(nullable=False)  # a Monday
    weeks: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    confirmed_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped["User"] = relationship(back_populates="plan_draft")


class DailyMetrics(Base):
    """Garmin's daily recovery and load picture: the morning Training Readiness,
    Training Status with acute vs chronic load, stress and body battery."""

    __tablename__ = "daily_metrics"
    __table_args__ = (UniqueConstraint("user_id", "date", name="uq_daily_metrics_user_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    date: Mapped[datetime.date] = mapped_column(nullable=False, index=True)

    readiness: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 0-100, the wake-up reading
    readiness_level: Mapped[str | None] = mapped_column(String(32), nullable=True)  # "high", "moderate", ...
    readiness_feedback: Mapped[str | None] = mapped_column(String(64), nullable=True)  # "well recovered"
    recovery_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)

    training_status: Mapped[str | None] = mapped_column(String(32), nullable=True)  # "maintaining", "productive", ...
    acute_load: Mapped[float | None] = mapped_column(Float, nullable=True)  # last 7 days
    chronic_load: Mapped[float | None] = mapped_column(Float, nullable=True)  # last 4 weeks
    load_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    load_ratio_status: Mapped[str | None] = mapped_column(String(32), nullable=True)  # "optimal", "low", "high"
    vo2max: Mapped[float | None] = mapped_column(Float, nullable=True)
    # 4-week load by intensity against Garmin's targets: {"phrase", "low", "low_target": [min, max], ...}
    load_focus: Mapped[dict | None] = mapped_column(JSON(none_as_null=True), nullable=True)

    stress_avg: Mapped[int | None] = mapped_column(Integer, nullable=True)
    body_battery_high: Mapped[int | None] = mapped_column(Integer, nullable=True)
    body_battery_low: Mapped[int | None] = mapped_column(Integer, nullable=True)
    steps: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # False while the day could still change (today, yesterday) or Garmin
    # refused part of it: fetched again next sync, a few times at most.
    final: Mapped[bool | None] = mapped_column(Boolean, nullable=True, default=False)
    attempts: Mapped[int | None] = mapped_column(Integer, nullable=True, default=0)
    fetched_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    user: Mapped["User"] = relationship(back_populates="daily_metrics")


# --- Claude's connector (app/mcp/oauth.py) ---------------------------------------
# Codes and tokens are random strings handed out once; only their SHA-256 is
# stored, so a copy of the database can't be used to connect.


class McpClient(Base):
    """A Claude app (web, desktop, phone) that registered itself as a connector."""

    __tablename__ = "mcp_clients"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_id: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    redirect_uris: Mapped[list] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class McpAuthCode(Base):
    """The code a successful login hands to Claude, traded once for tokens within minutes."""

    __tablename__ = "mcp_auth_codes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    client_id: Mapped[str] = mapped_column(String(64), nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    redirect_uri: Mapped[str] = mapped_column(String(255), nullable=False)
    code_challenge: Mapped[str] = mapped_column(String(128), nullable=False)  # PKCE, S256
    expires_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class McpToken(Base):
    """An access token (sent with every /mcp request, 1 hour) or the refresh
    token that gets the next one (MCP_TOKEN_DAYS, replaced at each use)."""

    __tablename__ = "mcp_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # access | refresh
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    client_id: Mapped[str] = mapped_column(String(64), nullable=False)
    # User.token_version when it was issued: a password change stops the token working.
    token_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    expires_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
