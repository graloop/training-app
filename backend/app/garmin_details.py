"""Per-activity details (perceived effort, feel, laps) and the athlete profile
Garmin keeps (zones, thresholds, records). Fetched during a sync, each activity
only once; nothing here is allowed to fail the sync."""

import datetime
from typing import Any

from sqlalchemy import or_
from sqlalchemy.orm import Session

from . import garmin_client, models
from .planning import sport_of

MAX_ACTIVITIES_PER_SYNC = 40  # a first sync catches up over one or two runs
DETAILS_WINDOW_DAYS = 90  # imported history older than this keeps Garmin's list data only
MAX_DAYS_PER_SYNC = 30  # sleep + recovery: four requests per day
SETTLED_AFTER_DAYS = 2  # today and yesterday can still change (late watch upload)
MAX_DAY_ATTEMPTS = 3  # then stop asking for a day Garmin won't fully answer
MAX_LAPS_STORED = 100
RECHECK_DAYS = 2  # effort/feel can be answered a little after the activity is saved
GIVE_UP_AFTER_FAILURES = 3  # consecutive: Garmin is refusing, try again next sync
MAX_MULTISPORT_PER_SYNC = 3  # races: one request for the race, one per leg
MULTISPORT_TYPE = "multi_sport"


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
        kind = (lap.get("intensityType") or "").lower()
        entry = {
            "distance": round(lap.get("distance") or 0),
            "duration": round(lap.get("duration") or 0, 1),
            "avg_hr": lap.get("averageHR"),
            "max_hr": lap.get("maxHR"),
            "avg_power": lap.get("averagePower"),
            "cadence": lap.get("averageRunCadence"),
            "stroke": (lap.get("swimStroke") or "").lower() or None,
            "swolf": lap.get("averageSWOLF") or None,
            # "interval" is what Garmin calls every manual lap: it says nothing
            "kind": kind if kind in ("active", "recovery", "rest", "warmup", "cooldown") else None,
        }
        out.append({k: (round(v) if isinstance(v, float) and k != "duration" else v) for k, v in entry.items() if v is not None})
    return out


def _wants_laps(activity: models.Activity) -> bool:
    # Run and swim laps are the reps; ride laps are mostly automatic 5 km marks.
    return sport_of(activity.activity_type) in ("run", "swim") and ((activity.raw_json or {}).get("lapCount") or 0) > 1


def weather_fields(weather: dict[str, Any]) -> dict[str, Any]:
    """Garmin reports the weather in Fahrenheit and mph."""
    out = {}
    if isinstance(weather.get("temp"), (int, float)):
        out["temp_c"] = round((weather["temp"] - 32) * 5 / 9)
    if isinstance(weather.get("windSpeed"), (int, float)):
        out["wind_kmh"] = round(weather["windSpeed"] * 1.609)
    if isinstance(weather.get("relativeHumidity"), (int, float)):
        out["humidity"] = round(weather["relativeHumidity"])
    return out


def _extras(api: Any, activity: models.Activity, summary: dict[str, Any]) -> dict[str, Any]:
    extras: dict[str, Any] = {}
    sweat = ((summary or {}).get("summaryDTO") or {}).get("waterEstimated")
    if sweat:
        extras["sweat_ml"] = round(sweat)
    if (activity.raw_json or {}).get("startLatitude"):  # outdoors
        try:
            extras.update(weather_fields(garmin_client.fetch_activity_weather(api, activity.garmin_activity_id)))
        except Exception:  # noqa: BLE001 - weather is a nice-to-have
            pass
    return extras


def _needs_details():
    # Never fetched, or fetched before weather / sweat / the richer laps existed.
    return or_(models.Activity.details_synced_at.is_(None), models.Activity.extras.is_(None))


def pending_details(db: Session, user: models.User) -> int:
    window = datetime.datetime.now() - datetime.timedelta(days=DETAILS_WINDOW_DAYS)
    return (
        db.query(models.Activity)
        .filter(models.Activity.user_id == user.id, _needs_details(), models.Activity.start_time >= window)
        .count()
    )


def enrich_activities(db: Session, api: Any, user: models.User, limit: int = MAX_ACTIVITIES_PER_SYNC) -> int:
    """Fetch details for activities that don't have them yet (newest first), and
    look again at the last couple of days if the effort wasn't answered yet."""
    recent = datetime.datetime.now() - datetime.timedelta(days=RECHECK_DAYS)
    window = datetime.datetime.now() - datetime.timedelta(days=DETAILS_WINDOW_DAYS)
    base = db.query(models.Activity).filter(models.Activity.user_id == user.id)
    pending = (
        base.filter(_needs_details(), models.Activity.start_time >= window)
        .order_by(models.Activity.start_time.desc())
        .limit(limit)
        .all()
    )
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
            first_time = activity.extras is None  # also true for details fetched by an older version
            if (activity.laps is None or first_time) and _wants_laps(activity):
                activity.laps = compact_laps(garmin_client.fetch_activity_laps(api, activity.garmin_activity_id))
            if first_time:
                activity.extras = _extras(api, activity, summary)
        except Exception:  # noqa: BLE001 - leave it for the next sync
            failures += 1
            if failures >= GIVE_UP_AFTER_FAILURES:
                break
            continue
        failures = 0
        activity.details_synced_at = models.utcnow()
        done += 1
    return done


# --- Multisport races ----------------------------------------------------------
# Garmin's list has a triathlon as one "multi_sport" activity. Its legs (swim,
# transitions, bike, run) are separate activities that only the race's own
# detail names, so they are fetched once and kept with the race.


def _leg(summary: dict[str, Any]) -> dict[str, Any]:
    dto = summary.get("summaryDTO") or {}
    kind = (summary.get("activityTypeDTO") or {}).get("typeKey") or "other"
    return {
        "type": kind,
        "sport": sport_of(kind),
        "duration_seconds": dto.get("duration") or dto.get("elapsedDuration"),
        "distance_meters": dto.get("distance"),
        "avg_hr": dto.get("averageHR"),
        "start": dto.get("startTimeGMT") or dto.get("startTimeLocal"),
    }


def multisport_legs(db: Session, api: Any, user: models.User, limit: int = MAX_MULTISPORT_PER_SYNC) -> int:
    """Fetch the legs of multisport activities that don't have them yet, at
    any age (races are few and matter). Returns how many races got their legs."""
    races = [
        a for a in db.query(models.Activity).filter(
            models.Activity.user_id == user.id, models.Activity.activity_type == MULTISPORT_TYPE)
        if "legs" not in (a.extras or {})
    ]
    done = 0
    for race in sorted(races, key=lambda a: a.start_time, reverse=True)[:limit]:
        try:
            summary = garmin_client.fetch_activity_summary(api, race.garmin_activity_id)
            child_ids = (summary.get("metadataDTO") or {}).get("childIds") or summary.get("childIds") or []
            legs = [_leg(garmin_client.fetch_activity_summary(api, str(cid))) for cid in child_ids]
        except Exception:  # noqa: BLE001 - asked again next sync
            continue
        legs.sort(key=lambda leg: leg["start"] or "")
        race.extras = {**(race.extras or {}), "legs": legs}  # [] = Garmin names no legs: don't ask again
        done += 1
    return done


# --- One sync ---------------------------------------------------------------------


def sync_user(db: Session, api: Any, user: models.User, today: datetime.date, lookback_days: int) -> dict[str, Any]:
    """One batch for one account (the fields of schemas.GarminSyncResponse): activities, then missing days, activity
    details, multisport legs and the profile. Only the activity list can fail
    it (the exception is raised); every later step is saved on its own, and
    what Garmin doesn't answer now is asked again next time."""
    start = today - datetime.timedelta(days=lookback_days)
    raw_activities = garmin_client.fetch_activities(api, start, today)
    activities_synced = save_activities(db, user, raw_activities)
    db.commit()

    days_synced = sleep_synced = days_remaining = details_synced = 0
    try:
        days_synced, sleep_synced, days_remaining = sync_days(db, api, user, start, today)
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()
    try:
        details_synced = enrich_activities(db, api, user)
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()
        details_synced = 0
    for step in (multisport_legs, refresh_profile):
        try:
            step(db, api, user)
            db.commit()
        except Exception:  # noqa: BLE001
            db.rollback()

    return {
        "activities_synced": activities_synced,
        "sleep_records_synced": sleep_synced,
        "lookback_days": lookback_days,
        "details_synced": details_synced,
        "days_synced": days_synced,
        "more_to_sync": days_remaining > 0 or pending_details(db, user) > 0,
    }


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


# --- Daily recovery and load ---------------------------------------------------


def _words(code: str | None) -> str | None:
    """'WELL_RECOVERED' -> 'well recovered'; 'MAINTAINING_4' -> 'maintaining'."""
    if not code:
        return None
    parts = [p for p in str(code).split("_") if not p.isdigit()]
    return " ".join(parts).lower() or None


def morning_readiness(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """Garmin recomputes readiness through the day; the wake-up reading is the
    one that says how ready the athlete started the day."""
    if not entries:
        return {}
    entry = next((e for e in entries if "WAKEUP" in (e.get("inputContext") or "")), None)
    if entry is None:  # no wake-up reading (watch not worn overnight): take the earliest
        entry = min(entries, key=lambda e: e.get("timestampLocal") or e.get("timestamp") or "")
    return {
        "readiness": entry.get("score"),
        "readiness_level": _words(entry.get("level")),
        "readiness_feedback": _words(entry.get("feedbackShort")),
        "recovery_minutes": entry.get("recoveryTime"),
    }


def _primary(by_device: dict[str, Any] | None) -> dict[str, Any]:
    devices = list((by_device or {}).values())
    return next((d for d in devices if d.get("primaryTrainingDevice")), devices[0] if devices else {})


def status_fields(status: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    latest = _primary((status.get("mostRecentTrainingStatus") or {}).get("latestTrainingStatusData"))
    if latest:
        out["training_status"] = _words(latest.get("trainingStatusFeedbackPhrase"))
        load = latest.get("acuteTrainingLoadDTO") or {}
        out["acute_load"] = load.get("dailyTrainingLoadAcute")
        out["chronic_load"] = load.get("dailyTrainingLoadChronic")
        out["load_ratio"] = load.get("dailyAcuteChronicWorkloadRatio")
        out["load_ratio_status"] = _words(load.get("acwrStatus"))
    vo2 = (status.get("mostRecentVO2Max") or {}).get("generic") or {}
    if vo2.get("vo2MaxPreciseValue") or vo2.get("vo2MaxValue"):
        out["vo2max"] = vo2.get("vo2MaxPreciseValue") or vo2.get("vo2MaxValue")
    balance = _primary((status.get("mostRecentTrainingLoadBalance") or {}).get("metricsTrainingLoadBalanceDTOMap"))
    if balance.get("monthlyLoadAerobicLow") is not None:
        focus = {"phrase": _words(balance.get("trainingBalanceFeedbackPhrase"))}
        for key, field in (("low", "AerobicLow"), ("high", "AerobicHigh"), ("anaerobic", "Anaerobic")):
            focus[key] = round(balance.get(f"monthlyLoad{field}") or 0)
            low, high = balance.get(f"monthlyLoad{field}TargetMin"), balance.get(f"monthlyLoad{field}TargetMax")
            if low is not None and high is not None:
                focus[f"{key}_target"] = [round(low), round(high)]
        out["load_focus"] = focus
    return out


def stats_fields(stats: dict[str, Any]) -> dict[str, Any]:
    return {
        "stress_avg": stats.get("averageStressLevel") if (stats.get("averageStressLevel") or -1) >= 0 else None,
        "body_battery_high": stats.get("bodyBatteryHighestValue"),
        "body_battery_low": stats.get("bodyBatteryLowestValue"),
        "steps": stats.get("totalSteps"),
    }


def save_activities(db: Session, user: models.User, raw_activities: list[dict[str, Any]]) -> int:
    """Insert or update activities from Garmin's list. Details fetched earlier are kept."""
    existing = {
        a.garmin_activity_id: a for a in db.query(models.Activity).filter(models.Activity.user_id == user.id)
    }
    saved = 0
    for raw in raw_activities:
        if raw.get("activityId") is None:
            continue
        garmin_id = str(raw["activityId"])
        start_raw = raw.get("startTimeLocal") or raw.get("startTimeGMT")
        try:
            start_time = datetime.datetime.fromisoformat(start_raw) if start_raw else None
        except ValueError:
            start_time = None
        if start_time is None:
            continue
        fields = dict(
            activity_type=(raw.get("activityType") or {}).get("typeKey", "unknown"),
            name=raw.get("activityName"),
            start_time=start_time,
            duration_seconds=raw.get("duration"),
            distance_meters=raw.get("distance"),
            avg_hr=raw.get("averageHR"),
            aerobic_training_effect=raw.get("aerobicTrainingEffect"),
            anaerobic_training_effect=raw.get("anaerobicTrainingEffect"),
            calories=raw.get("calories"),
            training_load=raw.get("activityTrainingLoad"),
            raw_json=raw,
        )
        row = existing.get(garmin_id)
        if row is None:
            row = models.Activity(user_id=user.id, garmin_activity_id=garmin_id)
            db.add(row)
            existing[garmin_id] = row
        for key, value in fields.items():
            setattr(row, key, value)
        saved += 1
    return saved


def save_sleep(db: Session, user: models.User, day: datetime.date, raw_sleep: dict[str, Any] | None,
               existing: models.SleepRecord | None) -> bool:
    dto = (raw_sleep or {}).get("dailySleepDTO") or {}
    if dto.get("sleepTimeSeconds") is None:
        return False
    overall = (dto.get("sleepScores") or {}).get("overall") or {}
    row = existing or models.SleepRecord(user_id=user.id, date=day)
    if existing is None:
        db.add(row)
    row.total_sleep_seconds = dto.get("sleepTimeSeconds")
    row.deep_sleep_seconds = dto.get("deepSleepSeconds")
    row.rem_sleep_seconds = dto.get("remSleepSeconds")
    row.light_sleep_seconds = dto.get("lightSleepSeconds")
    row.sleep_score = overall.get("value") if isinstance(overall, dict) else None
    row.resting_heart_rate = (raw_sleep or {}).get("restingHeartRate")
    row.raw_json = raw_sleep
    return True


def sync_days(db: Session, api: Any, user: models.User, start: datetime.date, today: datetime.date,
              limit: int = MAX_DAYS_PER_SYNC) -> tuple[int, int, int]:
    """Per day, newest first: last night's sleep, the morning Training Readiness,
    Training Status with acute vs chronic load, and stress / body battery. Only
    days that aren't settled yet are requested, at most `limit` per sync.
    Returns (days fetched, nights of sleep saved, days still to fetch)."""
    metrics = {
        m.date: m
        for m in db.query(models.DailyMetrics).filter(models.DailyMetrics.user_id == user.id, models.DailyMetrics.date >= start)
    }
    nights = {
        s.date: s
        for s in db.query(models.SleepRecord).filter(models.SleepRecord.user_id == user.id, models.SleepRecord.date >= start)
    }
    wanted = [today - datetime.timedelta(days=i) for i in range((today - start).days + 1)]
    wanted = [day for day in wanted if day not in metrics or not metrics[day].final]

    days_done = sleep_saved = failures = handled = 0
    for day in wanted[:limit]:
        handled += 1
        got_sleep = save_sleep(db, user, day, garmin_client.fetch_sleep(api, day), nights.get(day))
        fields: dict[str, Any] = {}
        answered = refused = 0
        for fetch, convert in (
            (garmin_client.fetch_training_readiness, morning_readiness),
            (garmin_client.fetch_training_status, status_fields),
            (garmin_client.fetch_daily_stats, stats_fields),
        ):
            try:
                fields.update(convert(fetch(api, day)))
                answered += 1
            except Exception:  # noqa: BLE001
                refused += 1
        if not got_sleep and not answered:
            failures += 1
            if failures >= GIVE_UP_AFTER_FAILURES:
                break  # Garmin is refusing everything: leave the rest for the next sync
        else:
            failures = 0
        sleep_saved += got_sleep
        row = metrics.get(day)
        if row is None:
            row = models.DailyMetrics(user_id=user.id, date=day, attempts=0)
            db.add(row)
            metrics[day] = row
        for key, value in fields.items():
            setattr(row, key, value)
        row.attempts = (row.attempts or 0) + 1
        settled = day <= today - datetime.timedelta(days=SETTLED_AFTER_DAYS)
        row.final = settled and (refused == 0 or row.attempts >= MAX_DAY_ATTEMPTS)
        row.fetched_at = models.utcnow()
        days_done += 1
    remaining = sum(1 for day in wanted[handled:] if day < today - datetime.timedelta(days=SETTLED_AFTER_DAYS - 1))
    return days_done, sleep_saved, remaining
