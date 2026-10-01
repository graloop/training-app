import dataclasses
import datetime
import json
from urllib.parse import urlparse

import anthropic
import openai
from sqlalchemy.orm import Session

from . import models
from .ai_providers import DEFAULT_PROVIDER, PROVIDERS, Provider
from .config import get_settings
from .schemas import PlanChange
from .security import decrypt_text

settings = get_settings()

MAX_HISTORY_MESSAGES = 20
MAX_TOOL_ROUNDS = 4
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


UPDATE_OBJECTIVES_TOOL = {
    "name": "update_objectives",
    "description": (
        "Create, update, or delete the athlete's objectives (target events) shown in the "
        "app's Objectives panel. Use it when the athlete tells you about a race or goal, or "
        "changes one — e.g. 'Montreal half marathon on 2027-04-20, aiming for 1:45:00'."
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
                        "id": {"type": "integer", "description": "objective id; required for update and delete"},
                        "title": {"type": "string", "description": "e.g. 'Montreal half marathon'; required for create"},
                        "event_date": {"type": "string", "description": "YYYY-MM-DD"},
                        "target_time": {
                            "type": "string",
                            "description": "finish time aimed for, as H:MM:SS (e.g. 1:45:00), or a short phrase like 'finish'",
                        },
                    },
                    "required": ["action"],
                },
            }
        },
        "required": ["changes"],
    },
}

TOOLS = [UPDATE_PLAN_TOOL, UPDATE_OBJECTIVES_TOOL]


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
    objectives = (
        db.query(models.Objective)
        .filter(models.Objective.user_id == user.id)
        .order_by(models.Objective.event_date.is_(None), models.Objective.event_date)
        .all()
    )

    def fmt_objective(o: models.Objective) -> str:
        return f"- id={o.id} {o.title}, event date {o.event_date or 'unknown'}, target time {o.target_time or 'not set'}"

    objectives_block = "\n".join(fmt_objective(o) for o in objectives) or "(none yet)"
    if user.objectives:  # free-text goals from before objectives were structured
        objectives_block += f"\nEarlier free-text notes from the athlete: {user.objectives}"

    return f"""You are an experienced, encouraging endurance training coach helping a real \
athlete through a chat interface embedded in their training calendar app.

Today's date is {today.isoformat()}.

Athlete's objectives (id is needed to update or delete one):
{objectives_block}

Completed activities, last {CONTEXT_LOOKBACK_DAYS} days (from Garmin):
{activities_block}

Sleep, last {CONTEXT_LOOKBACK_DAYS} days (from Garmin):
{sleep_block}

Planned/upcoming sessions currently on the calendar (id is needed to update or delete a row):
{planned_block}

Discuss how training is going, and when it's warranted, adjust the upcoming plan using the \
update_training_plan tool — don't just describe changes in prose, actually make them. When the \
athlete mentions a target event (title, date, goal time), record it with update_objectives. Keep \
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
    rows = list(reversed(rows))
    # The window can cut a user/assistant pair in half; the API expects the
    # conversation to open with a user turn.
    while rows and rows[0].role != "user":
        rows.pop(0)
    return rows


def _apply_plan_change(db: Session, user: models.User, change: dict) -> PlanChange | None:
    action = change.get("action")
    if change.get("date"):
        try:
            datetime.date.fromisoformat(change["date"])
        except (TypeError, ValueError):
            return None

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


class CoachError(Exception):
    """A chat failure worth showing to the user as-is."""

    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.status_code = status_code


def _resolve_provider(user: models.User) -> tuple[Provider, str, str]:
    """(provider, model, api_key) for this user: their own key from the
    settings menu, else the server-wide Anthropic key if one is configured."""
    row = user.ai_settings
    provider = PROVIDERS.get(row.provider if row else DEFAULT_PROVIDER, PROVIDERS[DEFAULT_PROVIDER])
    if provider.custom:
        if not (row.base_url and row.model):
            raise CoachError("The custom AI needs an API URL and a model. Set them in ⚙️ Settings.", 400)
        provider = dataclasses.replace(
            provider,
            label=f"Custom AI ({urlparse(row.base_url).netloc})",
            base_url=row.base_url,
            openai_compatible=row.api_format != "anthropic",
        )

    if row and row.api_key_encrypted:
        try:
            api_key = decrypt_text(row.api_key_encrypted)
        except ValueError as exc:
            raise CoachError("Your saved API key can't be read anymore. Enter it again in Settings.", 400) from exc
        return provider, (row.model or provider.default_model), api_key

    if provider.id == "anthropic" and settings.anthropic_api_key:
        return provider, (row.model if row and row.model else settings.anthropic_model), settings.anthropic_api_key

    raise CoachError(f"No API key for {provider.label}. Add yours in ⚙️ Settings (top right).", 400)


def _run_anthropic(
    provider: Provider, api_key: str, model: str, system_prompt: str, history: list[dict], run_tool
) -> str | None:
    client = anthropic.Anthropic(api_key=api_key, base_url=provider.base_url)
    messages = list(history)

    for _ in range(MAX_TOOL_ROUNDS):
        response = client.messages.create(
            model=model,
            max_tokens=16000,  # current models think before answering; 1024 would truncate
            system=system_prompt,
            tools=TOOLS,
            messages=messages,
        )

        if response.stop_reason == "refusal":
            return "Sorry, I can't help with that one."
        if response.stop_reason != "tool_use":
            return "".join(block.text for block in response.content if block.type == "text")

        # Append the full content (thinking blocks included), not just the text.
        messages.append({"role": "assistant", "content": response.content})

        tool_results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            result = run_tool(block.name, block.input)
            if result is not None:
                tool_results.append({"type": "tool_result", "tool_use_id": block.id, "content": json.dumps(result)})
            else:
                tool_results.append(
                    {"type": "tool_result", "tool_use_id": block.id, "content": "unknown tool", "is_error": True}
                )
        messages.append({"role": "user", "content": tool_results})

    return None


_OPENAI_TOOLS = [
    {
        "type": "function",
        "function": {"name": tool["name"], "description": tool["description"], "parameters": tool["input_schema"]},
    }
    for tool in TOOLS
]


def _run_openai_compatible(
    provider: Provider, api_key: str, model: str, system_prompt: str, history: list[dict], run_tool
) -> str | None:
    client = openai.OpenAI(api_key=api_key, base_url=provider.base_url)
    messages = [{"role": "system", "content": system_prompt}, *history]

    for _ in range(MAX_TOOL_ROUNDS):
        response = client.chat.completions.create(model=model, messages=messages, tools=_OPENAI_TOOLS)
        message = response.choices[0].message
        if not message.tool_calls:
            return message.content or ""

        messages.append(
            {
                "role": "assistant",
                "content": message.content,
                "tool_calls": [
                    {"id": tc.id, "type": "function", "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                    for tc in message.tool_calls
                ],
            }
        )
        for tc in message.tool_calls:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                content = json.dumps({"error": "arguments were not valid JSON"})
            else:
                result = run_tool(tc.function.name, args)
                content = json.dumps(result if result is not None else {"error": "unknown tool"})
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": content})

    return None


def _call_provider(provider: Provider, model: str, run):
    """Run a provider call, turning SDK errors into messages the user can act on."""
    sdk = openai if provider.openai_compatible else anthropic
    try:
        return run()
    except sdk.AuthenticationError as exc:
        raise CoachError(f"{provider.label} rejected the API key. Check it in ⚙️ Settings.", 400) from exc
    except sdk.NotFoundError as exc:
        raise CoachError(f"{provider.label} doesn't know the model '{model}'. Check it in ⚙️ Settings.", 400) from exc
    except sdk.RateLimitError as exc:
        raise CoachError(f"{provider.label} rate limit or quota reached. Try again later.", 429) from exc
    except sdk.APIStatusError as exc:
        raise CoachError(f"{provider.label} error ({exc.status_code}): {exc.message}") from exc
    except sdk.APIConnectionError as exc:
        raise CoachError(f"Couldn't reach {provider.label}. Try again in a moment.") from exc


def _apply_objective_change(db: Session, user: models.User, change: dict) -> dict | None:
    action = change.get("action")
    event_date = None
    if change.get("event_date"):
        try:
            event_date = datetime.date.fromisoformat(change["event_date"])
        except (TypeError, ValueError):
            return None

    if action == "create":
        title = (change.get("title") or "").strip()
        if not title:
            return None
        row = models.Objective(
            user_id=user.id, title=title[:255], event_date=event_date,
            target_time=(change.get("target_time") or "").strip()[:32] or None,
        )
        db.add(row)
        db.flush()
        return {"action": "create", "id": row.id}

    if action in ("update", "delete") and change.get("id") is not None:
        row = (
            db.query(models.Objective)
            .filter(models.Objective.id == change["id"], models.Objective.user_id == user.id)
            .first()
        )
        if row is None:
            return None
        if action == "delete":
            db.delete(row)
            db.flush()
            return {"action": "delete", "id": change["id"]}
        if (change.get("title") or "").strip():
            row.title = change["title"].strip()[:255]
        if event_date is not None:
            row.event_date = event_date
        if "target_time" in change:
            row.target_time = (change.get("target_time") or "").strip()[:32] or None
        db.flush()
        return {"action": "update", "id": row.id}

    return None


def handle_chat_message(db: Session, user: models.User, message_text: str) -> tuple[str, list[PlanChange]]:
    provider, model, api_key = _resolve_provider(user)

    user_msg = models.ChatMessage(user_id=user.id, role="user", content=message_text)
    db.add(user_msg)
    db.flush()

    history = [{"role": m.role, "content": m.content} for m in _load_recent_messages(db, user)]
    system_prompt = _build_system_prompt(db, user)
    plan_changes: list[PlanChange] = []

    objective_changes = 0

    def run_tool(name: str, args: dict) -> dict | None:
        nonlocal objective_changes
        changes = args.get("changes", []) if isinstance(args, dict) else []
        applied = []
        if name == UPDATE_PLAN_TOOL["name"]:
            for change in changes:
                result = _apply_plan_change(db, user, change)
                if result is not None:
                    plan_changes.append(result)
                    applied.append(result.model_dump(mode="json"))
        elif name == UPDATE_OBJECTIVES_TOOL["name"]:
            for change in changes:
                result = _apply_objective_change(db, user, change)
                if result is not None:
                    objective_changes += 1
                    applied.append(result)
        else:
            return None
        return {"applied": applied}

    if provider.openai_compatible:
        run = lambda: _run_openai_compatible(provider, api_key, model, system_prompt, history, run_tool)  # noqa: E731
    else:
        run = lambda: _run_anthropic(provider, api_key, model, system_prompt, history, run_tool)  # noqa: E731
    reply_text = _call_provider(provider, model, run)

    if not (reply_text or "").strip():
        # Empty reply, or the model kept calling tools without ever finishing.
        reply_text = "Done — I've updated things for you." if (plan_changes or objective_changes) else "Got it."

    db.add(models.ChatMessage(user_id=user.id, role="assistant", content=reply_text))
    db.commit()
    return reply_text, plan_changes
