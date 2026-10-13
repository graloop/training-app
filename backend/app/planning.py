"""The Plan page: a text training log to plan from, and a draft plan written
one session per line that only reaches the calendar when confirmed."""

import datetime
import re
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from . import models

SPORTS = ("swim", "bike", "run", "strength", "other")
MATCH_TOLERANCE = 0.15  # same rule as the calendar: done within 15% of the plan

DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def monday_of(day: datetime.date) -> datetime.date:
    return day - datetime.timedelta(days=day.weekday())


def sport_of(activity_type: str | None) -> str:
    """Bucket a Garmin or planned activity type into swim / bike / run / strength / other."""
    key = (activity_type or "").lower()
    if "swim" in key:
        return "swim"
    if "run" in key:
        return "run"
    if any(word in key for word in ("cycl", "bik", "ride", "zwift")):
        return "bike"
    if "strength" in key:
        return "strength"
    return "other"


def fmt_day(day: datetime.date) -> str:
    return f"{DAY_NAMES[day.weekday()]} {MONTH_NAMES[day.month - 1]} {day.day}"


def fmt_minutes(minutes: float | None) -> str:
    total = round(minutes or 0)
    return f"{total // 60}:{total % 60:02d}"


# --- Training log -------------------------------------------------------------


def _fmt_pace(minutes_per_unit: float) -> str:
    seconds = round(minutes_per_unit * 60)
    return f"{seconds // 60}:{seconds % 60:02d}"


def _fmt_activity(a: models.Activity) -> str:
    sport = sport_of(a.activity_type)
    minutes = (a.duration_seconds or 0) / 60
    parts = [f"{sport if sport != 'other' else a.activity_type} {fmt_minutes(minutes)}"]
    km = (a.distance_meters or 0) / 1000
    if km >= 0.1:
        parts.append(f"{km:.1f} km")
        if minutes > 0:
            if sport == "run":
                parts.append(f"{_fmt_pace(minutes / km)} /km")
            elif sport == "swim":
                parts.append(f"{_fmt_pace(minutes / (km * 10))} /100 m")
            elif sport == "bike":
                parts.append(f"{km / (minutes / 60):.1f} km/h")
    if a.avg_hr:
        parts.append(f"avg HR {round(a.avg_hr)}")
    if a.training_load is not None:
        parts.append(f"load {round(a.training_load)}")
    text = " · ".join(parts)
    return f"{text} — {a.name}" if a.name else text


def _fmt_planned(p: models.PlannedTraining, done: bool = False) -> str:
    sport = sport_of(p.activity_type)
    text = f"{sport if sport != 'other' else p.activity_type} {fmt_minutes(p.planned_duration_minutes)}"
    if p.notes:
        text += f" — {' '.join(p.notes.split())}"
    return text + (" (done as planned)" if done else "")


def _matched_plans(planned: list[models.PlannedTraining], activities: list[models.Activity]) -> set[int]:
    """Ids of planned sessions that were done: same sport, time within 15%."""
    left = list(activities)
    matched = set()
    for p in planned:
        plan_min = p.planned_duration_minutes or 0
        if plan_min <= 0 or sport_of(p.activity_type) == "other":
            continue
        best = None
        for a in left:
            if sport_of(a.activity_type) != sport_of(p.activity_type) or not a.duration_seconds:
                continue
            diff = abs(a.duration_seconds / 60 - plan_min) / plan_min
            if diff <= MATCH_TOLERANCE and (best is None or diff < best[0]):
                best = (diff, a)
        if best:
            matched.add(p.id)
            left.remove(best[1])
    return matched


def _split(minutes_by_sport: dict[str, float]) -> str:
    return " · ".join(f"{s} {fmt_minutes(m)}" for s, m in minutes_by_sport.items() if m > 0)


def build_log(db: Session, user: models.User, weeks: int, today: datetime.date) -> tuple[str, datetime.date]:
    """Plain-text log of the last `weeks` weeks (this week included) plus what
    is already planned ahead. Returns (text, first day covered)."""
    start = monday_of(today) - datetime.timedelta(weeks=weeks - 1)
    week_end = monday_of(today) + datetime.timedelta(days=6)

    activities = (
        db.query(models.Activity)
        .filter(
            models.Activity.user_id == user.id,
            models.Activity.start_time >= datetime.datetime.combine(start, datetime.time.min),
            models.Activity.start_time <= datetime.datetime.combine(today, datetime.time.max),
        )
        .order_by(models.Activity.start_time)
        .all()
    )
    sleep = {
        s.date: s
        for s in db.query(models.SleepRecord).filter(
            models.SleepRecord.user_id == user.id, models.SleepRecord.date >= start, models.SleepRecord.date <= today
        )
    }
    planned = (
        db.query(models.PlannedTraining)
        .filter(models.PlannedTraining.user_id == user.id, models.PlannedTraining.date >= start)
        .order_by(models.PlannedTraining.date, models.PlannedTraining.id)
        .all()
    )
    objectives = (
        db.query(models.Objective)
        .filter(models.Objective.user_id == user.id)
        .order_by(models.Objective.event_date.is_(None), models.Objective.event_date)
        .all()
    )

    acts_by_day: dict[datetime.date, list[models.Activity]] = {}
    for a in activities:
        acts_by_day.setdefault(a.start_time.date(), []).append(a)
    plans_by_day: dict[datetime.date, list[models.PlannedTraining]] = {}
    for p in planned:
        plans_by_day.setdefault(p.date, []).append(p)

    lines = [
        f"TRAINING LOG · {user.username}",
        f"Today is {DAY_NAMES[today.weekday()]} {MONTH_NAMES[today.month - 1]} {today.day}, {today.year}. "
        f"Covers {fmt_day(start)} to {fmt_day(today)} ({weeks} week{'s' if weeks != 1 else ''}), from Garmin.",
        "Times are h:mm. \"plan\" lines are what was planned for that day.",
        "",
        "OBJECTIVES",
    ]
    if objectives:
        for o in objectives:
            when = f"{o.event_date.isoformat()} ({(o.event_date - today).days} days away)" if o.event_date else "no date"
            lines.append(f"- {o.title} · {when} · target {o.target_time or 'not set'}")
    else:
        lines.append("- none yet")

    week_start = start
    while week_start <= today:
        days = [week_start + datetime.timedelta(days=i) for i in range(7)]
        done = {s: 0.0 for s in SPORTS}
        planned_min = 0.0
        load = 0.0
        scores, rhrs = [], []
        for day in days:
            for a in acts_by_day.get(day, []):
                done[sport_of(a.activity_type)] += (a.duration_seconds or 0) / 60
                load += a.training_load or 0
            planned_min += sum(p.planned_duration_minutes or 0 for p in plans_by_day.get(day, []))
            night = sleep.get(day)
            if night and night.sleep_score is not None:
                scores.append(night.sleep_score)
            if night and night.resting_heart_rate is not None:
                rhrs.append(night.resting_heart_rate)

        header = f"WEEK OF {fmt_day(week_start).upper()} · done {fmt_minutes(sum(done.values()))}"
        if sum(done.values()) > 0:
            header += f" ({_split(done)})"
        header += f" · planned {fmt_minutes(planned_min)}"
        if load:
            header += f" · load {round(load)}"
        if scores:
            header += f" · sleep avg {round(sum(scores) / len(scores))}"
        if rhrs:
            header += f" · resting HR avg {round(sum(rhrs) / len(rhrs))}"
        if week_start == monday_of(today):
            header += " · in progress"
        lines += ["", header]

        for day in days:
            if day > today:
                break
            day_acts = acts_by_day.get(day, [])
            day_plans = plans_by_day.get(day, [])
            label = fmt_day(day)
            night = sleep.get(day)
            if night and night.sleep_score is not None:
                label += f" · sleep {round(night.sleep_score)}"
            if night and night.resting_heart_rate is not None:
                label += f" · RHR {round(night.resting_heart_rate)}"
            if not day_acts and not day_plans:
                lines.append(f"{label} · no training")
                continue
            lines.append(label)
            matched = _matched_plans(day_plans, day_acts)
            lines += [f"  done  {_fmt_activity(a)}" for a in day_acts]
            lines += [f"  plan  {_fmt_planned(p, p.id in matched)}" for p in day_plans]
        week_start += datetime.timedelta(days=7)

    ahead = [p for p in planned if p.date > today]
    lines += ["", "ALREADY ON THE CALENDAR AFTER TODAY"]
    if ahead:
        current = None
        for p in ahead:
            if monday_of(p.date) != current:
                current = monday_of(p.date)
                week_total = sum(q.planned_duration_minutes or 0 for q in ahead if monday_of(q.date) == current)
                lines.append(f"Week of {fmt_day(current)} · planned {fmt_minutes(week_total)}")
            lines.append(f"  {fmt_day(p.date)} · {_fmt_planned(p)}")
    else:
        lines.append("- nothing planned yet")
    if week_end > today and not any(p.date <= week_end for p in ahead):
        lines.append(f"(Nothing planned for the rest of this week, through {fmt_day(week_end)}.)")

    return "\n".join(lines), start


# --- Draft plan ---------------------------------------------------------------

FORMAT_HINT = "Use: date | sport | duration | details   e.g.  Tue Oct 13 | run | 1:15 | Track: 2×6×400 m"

_WEEKDAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
_MONTHS = {name.lower(): i + 1 for i, name in enumerate(MONTH_NAMES)}
_SPORT_WORDS = [
    # activity_type stored on the calendar, words that select it
    ("swimming", ("swim", "natation", "pool")),
    ("cycling", ("bike", "biking", "cycl", "ride", "zwift", "velo", "vélo")),
    ("running", ("run", "jog", "track", "course")),
    ("strength_training", ("strength", "gym", "weights", "muscu")),
]
_REST_WORDS = ("rest", "off", "repos")


@dataclass
class DraftSession:
    line: int
    date: datetime.date
    activity_type: str
    minutes: float
    details: str | None


@dataclass
class DraftError:
    line: int
    text: str
    message: str


@dataclass
class ParsedDraft:
    sessions: list[DraftSession] = field(default_factory=list)
    errors: list[DraftError] = field(default_factory=list)


def parse_duration(text: str) -> float | None:
    """Minutes from '1:15', '1h15', '1 h', '1.5h', '75 min' or '75'."""
    value = text.strip().lower().replace(",", ".")
    if m := re.fullmatch(r"(\d{1,2}):(\d{2})", value):
        return int(m[1]) * 60 + int(m[2])
    if m := re.fullmatch(r"(\d+(?:\.\d+)?)\s*h(?:ours?|rs?)?\s*(?:(\d{1,2})\s*(?:m|min|mins|minutes)?)?", value):
        return float(m[1]) * 60 + int(m[2] or 0)
    if m := re.fullmatch(r"(\d+(?:\.\d+)?)\s*(?:m|min|mins|minutes)?", value):
        return float(m[1])
    return None


def _parse_date(text: str, first: datetime.date, last: datetime.date) -> datetime.date | str:
    """A date inside [first, last], or a message saying what's wrong."""
    value = text.strip()
    span = f"{fmt_day(first)} to {fmt_day(last)}"
    try:
        day = datetime.date.fromisoformat(value)
    except ValueError:
        day = None
    weekday = month = number = year = None
    if day is None:
        for token in re.split(r"[\s,./]+", value.lower()):
            if not token:
                continue
            token = re.sub(r"^(\d{1,2})(st|nd|rd|th)$", r"\1", token)
            if token.isdigit():
                if len(token) == 4:
                    year = int(token)
                elif number is None and 1 <= int(token) <= 31:
                    number = int(token)
                else:
                    return f'"{value}" isn\'t a date I can read. Write it like "Tue Oct 13" or 2026-10-13.'
            elif token[:3] in _WEEKDAYS:
                weekday = _WEEKDAYS[token[:3]]
            elif token[:3] in _MONTHS:
                month = _MONTHS[token[:3]]
            else:
                return f'"{value}" isn\'t a date I can read. Write it like "Tue Oct 13" or 2026-10-13.'
        if number is None:
            return f'"{value}" has no day of the month. Write it like "Tue Oct 13" or 2026-10-13.'
        candidates = [
            first + datetime.timedelta(days=i)
            for i in range((last - first).days + 1)
            if (first + datetime.timedelta(days=i)).day == number
        ]
        candidates = [d for d in candidates if (month is None or d.month == month) and (year is None or d.year == year)]
        if weekday is not None and len(candidates) > 1:
            candidates = [d for d in candidates if d.weekday() == weekday] or candidates
        if len(candidates) > 1:
            return f'"{value}" could be several days in {span}. Add the month.'
        if not candidates:
            if month is None:
                return f'There is no day {number} in the weeks you\'re planning ({span}).'
            # Not in the planned weeks. Find which date was meant (the year isn't
            # written) so the message names it, rather than guessing a weekday.
            years = [year] if year else [first.year - 1, first.year, last.year, last.year + 1]
            options = []
            for y in dict.fromkeys(years):
                try:
                    options.append(datetime.date(y, month, number))
                except ValueError:
                    pass
            if not options:
                return f'"{value}" isn\'t a real date.'
            matching = [d for d in options if weekday is None or d.weekday() == weekday] or options
            meant = min(matching, key=lambda d: abs((d - first).days))
            return f"{fmt_day(meant)} is outside the weeks you're planning ({span})."
        day = candidates[0]
    if weekday is not None and day.weekday() != weekday:
        return f"{MONTH_NAMES[day.month - 1]} {day.day} is a {DAY_NAMES[day.weekday()]}, not a {DAY_NAMES[weekday]}."
    if not first <= day <= last:
        return f"{fmt_day(day)} is outside the weeks you're planning ({span})."
    return day


def _parse_sport(text: str) -> str | None:
    """Calendar activity type, or None for a rest day."""
    value = " ".join(text.strip().lower().split())
    if any(value == word or value.startswith(word + " ") for word in _REST_WORDS):
        return None
    for activity_type, words in _SPORT_WORDS:
        if any(word in value for word in words):
            return activity_type
    return value.replace(" ", "_")[:64]


def parse_draft(text: str, first: datetime.date, last: datetime.date, today: datetime.date) -> ParsedDraft:
    """One session per line: date | sport | duration | details. Blank lines,
    # comments, rest days and markdown-table decoration are skipped."""
    result = ParsedDraft()
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        line = re.sub(r"^[-*•]\s+", "", line)
        if line.startswith("|"):
            line = line.strip("|").strip()
        if not line or line.startswith("#") or set(line) <= set("-|: "):
            continue
        cells = [c.strip() for c in line.split("|", 3)]
        if cells[0].lower() in ("date", "day"):  # a table header row
            continue

        def error(message: str) -> None:
            result.errors.append(DraftError(line=number, text=raw.strip(), message=message))

        if len(cells) < 2 or not cells[1]:
            error(FORMAT_HINT)
            continue
        activity_type = _parse_sport(cells[1])
        if activity_type is None:
            continue  # rest day
        if len(cells) < 3:
            error(FORMAT_HINT)
            continue
        day = _parse_date(cells[0], first, last)
        if isinstance(day, str):
            error(day)
            continue
        if day < today:
            error(f"{fmt_day(day)} is already past. Leave it out: the plan only adds sessions from today on.")
            continue
        minutes = parse_duration(cells[2])
        if minutes is None:
            error(f'"{cells[2]}" isn\'t a duration I can read. Write it like 1:15 or 45 min.')
            continue
        if not 0 < minutes <= 24 * 60:
            error("The duration has to be more than 0 and at most 24 h.")
            continue
        details = cells[3] if len(cells) > 3 and cells[3] else None
        result.sessions.append(
            DraftSession(line=number, date=day, activity_type=activity_type, minutes=minutes, details=details)
        )
    result.sessions.sort(key=lambda s: (s.date, s.line))
    return result
