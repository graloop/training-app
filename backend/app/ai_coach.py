import datetime
import json

import anthropic
from sqlalchemy.orm import Session

from . import models
from .config import get_settings
from .schemas import PlanChange

settings = get_settings()

MAX_HISTORY_MESSAGES = 20
CONTEXT_LOOKBACK_DAYS = 14
CONTEXT_LOOKAHEAD_DAYS = 14

UPDATE_PLAN_TOOL = {
    "name": "update_training_plan",
    "description": (
        "Create, update, or delete sessions on the user's upcoming training plan. "
        "Call this whenever you decide the plan should actually change based on the "
        "conversation — don't just describe the change in words, apply it."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "changes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "enum": ["create", "update", "delete"]},
                        "id": {
                            "type": "integer",
                            "description": "planned_training id; required for update and delete",
                        },
                        "date": {
                            "type": "string",
                            "description": "YYYY-MM-DD; required for create",
                        },
                        "activity_type": {
                            "type": "string",
                            "description": "e.g. running, swimming, cycling, rest",
                        },
                        "planned_duration_minutes": {"type": "number"},
                        "notes": {"type": "string"},
                    },
                    "required": ["action"],
                },
            }
        },
        "required": ["changes"],
    },
}


def _build_system_prompt(db: Session, user: models.User) -> str:
    today = datetime.date.today()
    lookback = today - datetime.timedelta(days=CONTEXT_LOOKBACK_DAYS)
    lookahead = today + datetime.timedelta(days=CONTEXT_LOOKAHEAD_DAYS)

    recent_activities = (
        db.query(models.Activity)
        .filter(models.Activity.user_id == user.id, models.Activity.start_time >= lookback)
        .order_by(models.Activity.start_time)
        .all()
    )
    recent_sleep = (
        db.query(models.SleepRecord)
        .filter(models.SleepRecord.user_id == user.id, models.SleepRecord.date >= lookback)
        .order_by(models.SleepRecord.date)
        .all()
    )
    upcoming_planned = (
        db.query(models.PlannedTraining)
        .filter(
            models.PlannedTraining.user_id == user.id,
            models.PlannedTraining.date >= lookback,
            models.PlannedTraining.date <= lookahead,
        )
        .order_by(models.PlannedTraining.date)
        .all()
    )

    def fmt_activity(a: models.Activity) -> str:
        mins = round(a.duration_seconds / 60) if a.duration_seconds else None
        return (
            f"- {a.start_time.date()} {a.activity_type}: {mins} min, "
            f"avg HR {a.avg_hr}, aerobic effect {a.aerobic_training_effect}, "
            f"anaerobic effect {a.anaerobic_training_effect}"
        )

    def fmt_sleep(s: models.SleepRecord) -> str:
        hours = round(s.total_sleep_seconds / 3600, 1) if s.total_sleep_seconds else None
        return f"- {s.date}: {hours} h sleep, score {s.sleep_score}"

    def fmt_planned(p: models.PlannedTraining) -> str:
        return (
            f"- id={p.id} {p.date} {p.activity_type}, "
            f"{p.planned_duration_minutes} min planned, source={p.source}, notes={p.notes or ''}"
        )

    activities_block = "\n".join(fmt_activity(a) for a in recent_activities) or "(none)"
    sleep_block = "\n".join(fmt_sleep(s) for s in recent_sleep) or "(none)"
    planned_block = "\n".join(fmt_planned(p) for p in upcoming_planned) or "(none)"
    objectives = user.objectives or "(not set)"

    return f"""You are an experienced, encouraging endurance training coach helping a real \
athlete through a chat interface embedded in their training calendar app.

Today's date is {today.isoformat()}.

Athlete's stated objectives:
{objectives}

Completed activities, last {CONTEXT_LOOKBACK_DAYS} days (from Garmin):
{activities_block}

Sleep, last {CONTEXT_LOOKBACK_DAYS} days (from Garmin):
{sleep_block}

Planned/upcoming sessions currently on the calendar (id is needed to update or delete a row):
{planned_block}

Discuss how training is going, and when it's warranted, adjust the upcoming plan using the \
update_training_plan tool — don't just describe changes in prose, actually make them. Keep \
replies conversational and concise. Only change sessions in the near future (today onward); \
never edit or delete past sessions."""


def _load_recent_messages(db: Session, user: models.User) -> list[models.ChatMessage]:
    rows = (
        db.query(models.ChatMessage)
        .filter(models.ChatMessage.user_id == user.id)
        .order_by(models.ChatMessage.created_at.desc())
        .limit(MAX_HISTORY_MESSAGES)
        .all()
    )
    return list(reversed(rows))


def _apply_plan_change(db: Session, user: models.User, change: dict) -> PlanChange | None:
    action = change.get("action")

    if action == "create":
        if not change.get("date") or not change.get("activity_type"):
            return None
        row = models.PlannedTraining(
            user_id=user.id,
            date=datetime.date.fromisoformat(change["date"]),
            activity_type=change["activity_type"],
            planned_duration_minutes=change.get("planned_duration_minutes"),
            notes=change.get("notes"),
            source="ai",
        )
        db.add(row)
        db.flush()
        return PlanChange(action="create", id=row.id, date=row.date, activity_type=row.activity_type,
                           planned_duration_minutes=row.planned_duration_minutes, notes=row.notes)

    if action in ("update", "delete"):
        plan_id = change.get("id")
        if plan_id is None:
            return None
        row = (
            db.query(models.PlannedTraining)
            .filter(models.PlannedTraining.id == plan_id, models.PlannedTraining.user_id == user.id)
            .first()
        )
        if row is None:
            return None

        if action == "delete":
            db.delete(row)
            db.flush()
            return PlanChange(action="delete", id=plan_id)

        if change.get("date"):
            row.date = datetime.date.fromisoformat(change["date"])
        if change.get("activity_type"):
            row.activity_type = change["activity_type"]
        if "planned_duration_minutes" in change:
            row.planned_duration_minutes = change["planned_duration_minutes"]
        if "notes" in change:
            row.notes = change["notes"]
        row.source = "ai"
        db.flush()
        return PlanChange(action="update", id=row.id, date=row.date, activity_type=row.activity_type,
                           planned_duration_minutes=row.planned_duration_minutes, notes=row.notes)

    return None


def handle_chat_message(db: Session, user: models.User, message_text: str) -> tuple[str, list[PlanChange]]:
    if not settings.anthropic_api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not configured on the server.")

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    user_msg = models.ChatMessage(user_id=user.id, role="user", content=message_text)
    db.add(user_msg)
    db.flush()

    history = _load_recent_messages(db, user)
    messages: list[dict] = [{"role": m.role, "content": m.content} for m in history]

    system_prompt = _build_system_prompt(db, user)
    plan_changes: list[PlanChange] = []

    for _ in range(4):  # bound the tool-use loop
        response = client.messages.create(
            model=settings.anthropic_model,
            max_tokens=1024,
            system=system_prompt,
            tools=[UPDATE_PLAN_TOOL],
            messages=messages,
        )

        if response.stop_reason != "tool_use":
            reply_text = "".join(block.text for block in response.content if block.type == "text")
            if not reply_text.strip():
                reply_text = "Done — I've updated your plan." if plan_changes else "Got it."
            assistant_msg = models.ChatMessage(user_id=user.id, role="assistant", content=reply_text)
            db.add(assistant_msg)
            db.commit()
            return reply_text, plan_changes

        messages.append({"role": "assistant", "content": response.content})

        tool_results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            if block.name == "update_training_plan":
                changes = block.input.get("changes", [])
                applied = []
                for change in changes:
                    result = _apply_plan_change(db, user, change)
                    if result is not None:
                        plan_changes.append(result)
                        applied.append(result.model_dump(mode="json"))
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps({"applied": applied}),
                    }
                )
            else:
                tool_results.append(
                    {"type": "tool_result", "tool_use_id": block.id, "content": "unknown tool", "is_error": True}
                )

        messages.append({"role": "user", "content": tool_results})

    # Fallback if the model kept calling tools without ever finishing.
    db.commit()
    fallback_text = "I've updated your plan."
    assistant_msg = models.ChatMessage(user_id=user.id, role="assistant", content=fallback_text)
    db.add(assistant_msg)
    db.commit()
    return fallback_text, plan_changes
