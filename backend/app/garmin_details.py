"""Per-activity details (perceived effort, feel, laps) and the athlete profile
Garmin keeps (zones, thresholds, records). Fetched during a sync, each activity
only once; nothing here is allowed to fail the sync."""

import datetime
from typing import Any

from sqlalchemy.orm import Session

from . import garmin_client, models
from .planning import sport_of

MAX_ACTIVITIES_PER_SYNC = 40  # a first sync catches up over one or two runs
MAX_LAPS_STORED = 100
RECHECK_DAYS = 2  # effort/feel can be answered a little after the activity is saved
GIVE_UP_AFTER_FAILURES = 3  # consecutive: Garmin is refusing, try again next sync


def effort_and_feel(summary: dict[str, Any]) -> tuple[int | None, int | None]:
    """(RPE 1-10, feel 0-100) from an activity's summaryDTO. Garmin stores the
    effort as 10..100 and the feel as 0/25/50/75/100."""
    dto = (summary or {}).get("summaryDTO") or {}
    rpe, feel = dto.get("directWorkoutRpe"), dto.get("directWorkoutFeel")
    effort = min(10, max(1, round(rpe / 10))) if isinstance(rpe, (int, float)) and rpe > 0 else None
    return effort, (int(feel) if isinstance(feel, (int, float)) else None)


def compact_laps(laps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep what a plan is judged on: distance, time, heart rate, power."""
    out = []
    for lap in laps[:MAX_LAPS_STORED]:
        entry = {
            "distance": round(lap.get("distance") or 0),
            "duration": round(lap.get("duration") or 0, 1),
            "avg_hr": lap.get("averageHR"),
            "max_hr": lap.get("maxHR"),
            "avg_power": lap.get("averagePower"),
        }
        out.append({k: (round(v) if isinstance(v, float) and k != "duration" else v) for k, v in entry.items() if v is not None})
    return out


def _wants_laps(activity: models.Activity) -> bool:
    # Run and swim laps are the reps; ride laps are mostly automatic 5 km marks.
    return sport_of(activity.activity_type) in ("run", "swim") and ((activity.raw_json or {}).get("lapCount") or 0) > 1


def enrich_activities(db: Session, api: Any, user: models.User, limit: int = MAX_ACTIVITIES_PER_SYNC) -> int:
    """Fetch details for activities that don't have them yet (newest first), and
    look again at the last couple of days if the effort wasn't answered yet."""
    recent = datetime.datetime.now() - datetime.timedelta(days=RECHECK_DAYS)
    base = db.query(models.Activity).filter(models.Activity.user_id == user.id)
    pending = base.filter(models.Activity.details_synced_at.is_(None)).order_by(models.Activity.start_time.desc()).limit(limit).all()
    recheck = (
        base.filter(
            models.Activity.details_synced_at.is_not(None),
            models.Activity.perceived_effort.is_(None),
            models.Activity.start_time >= recent,
        )
        .order_by(models.Activity.start_time.desc())
        .limit(10)
        .all()
    )

    done = failures = 0
    for activity in pending + recheck:
        try:
            summary = garmin_client.fetch_activity_summary(api, activity.garmin_activity_id)
            activity.perceived_effort, activity.feel = effort_and_feel(summary)
            if activity.laps is None and _wants_laps(activity):
                activity.laps = compact_laps(garmin_client.fetch_activity_laps(api, activity.garmin_activity_id))
        except Exception:  # noqa: BLE001 - leave it for the next sync
            failures += 1
            if failures >= GIVE_UP_AFTER_FAILURES:
                break
            continue
        failures = 0
        activity.details_synced_at = models.utcnow()
        done += 1
    return done


# --- Athlete profile ----------------------------------------------------------

# Garmin's personal-record type ids -> (label, kind). Distances are in metres.
_RECORD_TYPES = {
    3: ("5 km", "time", 5000),
    4: ("10 km", "time", 10000),
    5: ("half marathon", "time", 21097),
    6: ("marathon", "time", 42195),
    7: ("longest run", "distance", None),
    8: ("longest ride", "distance", None),
    10: ("best 20 min power", "power", None),
    17: ("longest swim", "distance", None),
}
_FASTEST_PLAUSIBLE_PACE = 150  # s/km; faster than 2:30 /km is a GPS glitch, not a record


def normalize_profile(raw: dict[str, Any]) -> dict[str, Any] | None:
    """The athlete as Garmin knows them, in plain units. None when Garmin gave nothing."""
    out: dict[str, Any] = {}
    data = ((raw.get("profile") or {}).get("userData") or {}) if isinstance(raw.get("profile"), dict) else {}
    if data.get("weight"):
        out["weight_kg"] = round(data["weight"] / 1000, 1)
    if data.get("height"):
        out["height_cm"] = round(data["height"])
    for key, field in (("vo2max_running", "vo2MaxRunning"), ("vo2max_cycling", "vo2MaxCycling"),
                       ("lactate_threshold_hr", "lactateThresholdHeartRate"), ("ftp_watts", "functionalThresholdPower")):
        if data.get(field):
            out[key] = round(data[field])
    speed = data.get("lactateThresholdSpeed")
    if speed:
        metres_per_second = speed * 10 if speed < 1 else speed  # Garmin stores it divided by 10
        out["threshold_pace_s_per_km"] = round(1000 / metres_per_second)

    zones = raw.get("hr_zones") if isinstance(raw.get("hr_zones"), list) else []
    chosen = next((z for z in zones if z.get("sport") == "RUNNING"), None) or next(iter(zones), None)
    if chosen:
        if chosen.get("maxHeartRateUsed"):
            out["max_hr"] = chosen["maxHeartRateUsed"]
        floors = [chosen.get(f"zone{i}Floor") for i in range(1, 6)]
        if all(floors):
            out["hr_zone_floors"] = floors

    records = []
    for record in raw.get("records") if isinstance(raw.get("records"), list) else []:
        known = _RECORD_TYPES.get(record.get("typeId"))
        value = record.get("value")
        if not known or not value:
            continue
        label, kind, metres = known
        if kind == "time" and value / (metres / 1000) < _FASTEST_PLAUSIBLE_PACE:
            continue
        records.append({"label": label, "kind": kind, "value": round(value, 1),
                        "date": (record.get("prStartTimeGmtFormatted") or "")[:10] or None})
    if records:
        out["records"] = records

    predictions = raw.get("predictions") if isinstance(raw.get("predictions"), dict) else {}
    predicted = {label: predictions.get(key) for label, key in (("5 km", "time5K"), ("10 km", "time10K"),
                 ("half marathon", "timeHalfMarathon"), ("marathon", "timeMarathon")) if predictions.get(key)}
    if predicted:
        out["race_predictions"] = predicted

    if not out:
        return None
    out["synced_on"] = datetime.date.today().isoformat()
    return out


def refresh_profile(db: Session, api: Any, user: models.User) -> bool:
    profile = normalize_profile(garmin_client.fetch_profile(api))
    if profile is None:
        return False  # keep whatever was stored before
    user.garmin_profile = profile
    return True
