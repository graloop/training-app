"""Share codes, and turning someone's planned sessions into draft lines (Import tab).

Only planned sessions are ever shared: date, sport, duration and details.
Garmin activities, sleep and health data stay private.
"""

import datetime
import hmac
import re
import secrets

from sqlalchemy.orm import Session

from . import models, planning

# No 0/O, 1/I/L: the code is read out loud or typed from a message.
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 8  # about 40 bits, and wrong guesses are limited per account

_DRAFT_SPORT = {"swimming": "swim", "cycling": "bike", "running": "run", "strength_training": "strength"}


def normalize_code(text: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", text.upper())


def _random_code() -> str:
    raw = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
    return f"{raw[:4]}-{raw[4:]}"


def new_code(db: Session, user: models.User) -> str:
    """Give the account a fresh code; the previous one stops working."""
    while True:
        code = _random_code()
        if not db.query(models.User).filter(models.User.share_code == code).first():
            break
    user.share_code = code
    db.commit()
    return code


def ensure_code(db: Session, user: models.User) -> str:
    return user.share_code or new_code(db, user)


def find_sharer(db: Session, username: str, code: str) -> models.User | None:
    """The regular account with that username and share code, or None."""
    user = db.query(models.User).filter(models.User.username == username.strip().lower()).first()
    if user is None or user.is_admin or not user.share_code:
        return None
    if not hmac.compare_digest(normalize_code(user.share_code), normalize_code(code)):
        return None
    return user


def _cell(text: str | None) -> str:
    # A draft line is split on "|" and ends at a line break.
    return " ".join((text or "").replace("|", "/").split())


def draft_lines(
    db: Session, sharer: models.User, source_start: datetime.date, weeks: int,
    target_start: datetime.date, today: datetime.date,
) -> tuple[str, int]:
    """Their planned sessions from `source_start` for `weeks` weeks, moved to the
    same weekdays of the weeks starting `target_start`, as Plan draft lines.
    Sessions landing before `today` are left out (and counted)."""
    source_end = source_start + datetime.timedelta(days=weeks * 7 - 1)
    shift = target_start - source_start
    planned = (
        db.query(models.PlannedTraining)
        .filter(
            models.PlannedTraining.user_id == sharer.id,
            models.PlannedTraining.date >= source_start,
            models.PlannedTraining.date <= source_end,
            models.PlannedTraining.planned_duration_minutes > 0,
        )
        .order_by(models.PlannedTraining.date, models.PlannedTraining.id)
        .all()
    )
    lines = [
        f"# Imported from {sharer.username}: their weeks of {planning.fmt_day(source_start)}"
        f" to {planning.fmt_day(source_end)}"
    ]
    skipped = 0
    for p in planned:
        day = p.date + shift
        if day < today:
            skipped += 1
            continue
        sport = _DRAFT_SPORT.get(p.activity_type, _cell(p.activity_type))
        line = f"{planning.fmt_day(day)} | {sport} | {planning.fmt_minutes(p.planned_duration_minutes)}"
        details = _cell(p.notes)
        lines.append(f"{line} | {details}" if details else line)
    return "\n".join(lines), skipped
