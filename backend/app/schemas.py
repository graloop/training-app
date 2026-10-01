import datetime
from typing import Any, Literal

from pydantic import BaseModel, EmailStr, Field


# --- Auth ---

class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


# --- Garmin ---

class GarminSyncResponse(BaseModel):
    activities_synced: int
    sleep_records_synced: int
    lookback_days: int


# --- Objectives ---

class ObjectiveOut(BaseModel):
    id: int
    title: str
    event_date: datetime.date | None
    target_time: str | None

    class Config:
        from_attributes = True


class ObjectiveUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    event_date: datetime.date | None = None
    target_time: str | None = Field(default=None, max_length=32)


class ObjectivesResponse(BaseModel):
    objectives: list[ObjectiveOut]


# --- AI settings ---

class AIProviderOut(BaseModel):
    id: str
    label: str
    default_model: str
    key_url: str
    custom: bool


class AISettingsOut(BaseModel):
    provider: str
    model: str  # effective model (the provider default when none was chosen)
    custom_model: bool
    has_api_key: bool
    api_key_hint: str | None  # last 4 characters, never the key itself
    using_server_key: bool  # no personal key, falling back to the server's Anthropic key
    base_url: str | None  # custom provider only
    api_format: str | None  # custom provider only: "openai" | "anthropic"
    providers: list[AIProviderOut]


class AISettingsUpdate(BaseModel):
    provider: str
    model: str | None = None  # empty/None = provider default
    api_key: str | None = None  # None = keep the stored key
    clear_api_key: bool = False
    base_url: str | None = None  # required for "custom"
    api_format: Literal["openai", "anthropic"] | None = None  # "custom" only, default "openai"


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


class PlannedTrainingCreate(BaseModel):
    date: datetime.date
    activity_type: str = Field(min_length=1, max_length=64)
    planned_duration_minutes: float = Field(gt=0, le=24 * 60)
    notes: str | None = None


class PlannedTrainingUpdate(BaseModel):
    activity_type: str | None = Field(default=None, min_length=1, max_length=64)
    planned_duration_minutes: float | None = Field(default=None, gt=0, le=24 * 60)
    notes: str | None = None


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
