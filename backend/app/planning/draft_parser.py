"""Reads a draft plan: one session per line as date | sport | duration | details.
Each line becomes a session, or an error saying what to fix on that line. The
Plan tab and the share import both use it; nothing here touches the database."""

import datetime
import re
from dataclasses import dataclass, field

from .basics import DAY_NAMES, MONTH_NAMES, fmt_day

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

        def error(message: str, number: int = number, raw: str = raw) -> None:
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
