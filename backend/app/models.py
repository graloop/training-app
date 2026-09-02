import datetime

from sqlalchemy import (
    JSON,
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
    return datetime.datetime.now(datetime.timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)

    garmin_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Encrypted (Fernet) blob of the garth session tokens. Never store the raw
    # Garmin password.
    garmin_session_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)

    objectives: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    activities: Mapped[list["Activity"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    sleep_records: Mapped[list["SleepRecord"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    planned_sessions: Mapped[list["PlannedTraining"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    chat_messages: Mapped[list["ChatMessage"]] = relationship(back_populates="user", cascade="all, delete-orphan")


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
    raw_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    user: Mapped["User"] = relationship(back_populates="activities")


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
    source: Mapped[str] = mapped_column(String(16), default="manual")  # "manual" | "ai"

    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    user: Mapped["User"] = relationship(back_populates="planned_sessions")


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)

    role: Mapped[str] = mapped_column(String(16), nullable=False)  # "user" | "assistant"
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)

    user: Mapped["User"] = relationship(back_populates="chat_messages")
