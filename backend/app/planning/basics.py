"""Days, durations and sports, written the same way in the log, the draft and
the share import."""

import datetime

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
    """'Tue Oct 13'"""
    return f"{DAY_NAMES[day.weekday()]} {MONTH_NAMES[day.month - 1]} {day.day}"


def fmt_minutes(minutes: float | None) -> str:
    """'1:15'"""
    total = round(minutes or 0)
    return f"{total // 60}:{total % 60:02d}"


def fmt_seconds(seconds: float) -> str:
    """'4:30', or '1:02:05' from an hour up."""
    total = round(seconds)
    if total >= 3600:
        return f"{total // 3600}:{total % 3600 // 60:02d}:{total % 60:02d}"
    return f"{total // 60}:{total % 60:02d}"


def legs_of(extras: dict | None) -> list[dict]:
    """A multisport race's legs (swim, transitions, bike, run), once synced."""
    return (extras or {}).get("legs") or []


def sport_minutes(activity_type: str | None, seconds: float | None, extras: dict | None) -> list[tuple[str, float]]:
    """Minutes per sport: a race counts its swim, bike and run legs (transitions
    as "other"); any other activity counts whole."""
    legs = legs_of(extras)
    if legs:
        return [(leg.get("sport") or "other", (leg.get("duration_seconds") or 0) / 60) for leg in legs]
    return [(sport_of(activity_type), (seconds or 0) / 60)]
