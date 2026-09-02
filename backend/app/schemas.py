import datetime
from typing import Any, Literal

from pydantic import BaseModel, EmailStr, Field


# --- Auth ---

class SignupRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


# --- Garmin ---

class GarminConnectRequest(BaseModel):
    garmin_email: EmailStr
    garmin_password: str


class GarminSyncResponse(BaseModel):
    activities_synced: int
    sleep_records_synced: int
    lookback_days: int


# --- Objectives ---

class ObjectivesRequest(BaseModel):
    text: str


class ObjectivesResponse(BaseModel):
    text: str | None


# --- Calendar ---

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


class CalendarDay(BaseModel):
    date: datetime.date
    activities: list[ActivityOut] = []
    sleep: SleepOut | None = None
    planned: list[PlannedTrainingOut] = []


class CalendarResponse(BaseModel):
    start: datetime.date
    end: datetime.date
    days: list[CalendarDay]


# --- Chat ---

class ChatRequest(BaseModel):
    message: str


class PlanChange(BaseModel):
    action: Literal["create", "update", "delete"]
    id: int | None = None
    date: datetime.date | None = None
    activity_type: str | None = None
    planned_duration_minutes: float | None = None
    notes: str | None = None


class ChatMessageOut(BaseModel):
    id: int
    role: str
    content: str
    created_at: datetime.datetime

    class Config:
        from_attributes = True


class ChatResponse(BaseModel):
    reply: str
    plan_changes: list[PlanChange] = []


class ChatHistoryResponse(BaseModel):
    messages: list[ChatMessageOut]
