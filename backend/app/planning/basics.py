"""Days, durations and sports, written the same way in the log, the draft and
the share import. The small shared vocabulary of planning/ (and of
garmin/sync.py, which buckets activities with sport_of): no database, no Garmin.

The page has its own copy of some of these rules (frontend/js/views/calendar.js:
sportCategory and MATCH_TOLERANCE). Change one, change the other."""

import datetime

SPORTS = ("swim", "bike", "run", "strength", "other")  # the buckets weekly totals are split into
MATCH_TOLERANCE = 0.15  # same rule as the calendar (calendar.js): done within 15% of the plan
TRACE_DAYS = 28  # how long a session's second-by-second data is kept (garmin/sync.py purges older)

DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def monday_of(day: datetime.date) -> datetime.date:
    """The Monday of `day`'s week: weeks run Monday to Sunday everywhere."""
    return day - datetime.timedelta(days=day.weekday())


def sport_of(activity_type: str | None) -> str:
    """Bucket a Garmin or planned activity type into swim / bike / run / strength / other.
    Garmin has dozens of types ("trail_running", "virtual_ride", "lap_swimming"...),
    so they are matched by the words they contain. Mirrored by sportCategory() in
    frontend/js/views/calendar.js."""
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
    """A multisport race's legs (swim, transitions, bike, run), once synced.
    Kept in Activity.extras["legs"] by garmin/sync.py (multisport_legs). The same
    as the Activity.legs property, which the calendar API sends (schemas.LegOut)."""
    return (extras or {}).get("legs") or []


def sport_minutes(activity_type: str | None, seconds: float | None, extras: dict | None) -> list[tuple[str, float]]:
    """Minutes per sport: a race counts its swim, bike and run legs (transitions
    as "other"); any other activity counts whole."""
    legs = legs_of(extras)
    if legs:
        return [(leg.get("sport") or "other", (leg.get("duration_seconds") or 0) / 60) for leg in legs]
    return [(sport_of(activity_type), (seconds or 0) / 60)]
