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
    return datetime.datetime.now(datetime.UTC)


class User(Base):
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
    athlete_notes: Mapped[str | None] = mapped_column(Text, nullable=True)  # older "About you" text, read until rule_set is saved
    # What the athlete enters on the Athlete tab: FTP, zones, CSS, equipment... (schemas.AthleteProfile).
    athlete_profile: Mapped[dict | None] = mapped_column(JSON(none_as_null=True), nullable=True)
    # The Plan tab's rule set: availability, constraints and rules of thumb the plan must follow.
    # It replaces the older "About you" and block notes (see planning/training_log.py, rule_set_text).
    rule_set: Mapped[str | None] = mapped_column(Text, nullable=True)
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


class Activity(Base):
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

    @property
    def legs(self) -> list[dict]:
        """A multisport race's legs (swim, transitions, bike, run), once synced."""
        return (self.extras or {}).get("legs") or []


class SleepRecord(Base):
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
