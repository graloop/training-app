"""One Garmin activity as text for the training log: the summary line (sport,
time, distance, pace, HR, load, effort) and, in the full log, the detail lines
(zones, power, cadence, weather, intervals, laps). Everything comes from what
Garmin already sent (backend/app/garmin/sync.py stores it)."""

from .. import models
from .basics import fmt_minutes, fmt_seconds, legs_of, sport_of

FEEL_WORDS = {0: "very weak", 25: "weak", 50: "normal", 75: "strong", 100: "very strong"}
_EFFECT_WORDS = {
    "RECOVERY": "recovery", "AEROBIC_BASE": "aerobic base", "TEMPO": "tempo", "LACTATE_THRESHOLD": "threshold",
    "VO2MAX": "VO2max", "ANAEROBIC_CAPACITY": "anaerobic", "SPRINT": "sprint", "SPEED": "speed",
}
_FASTEST_PLAUSIBLE_PACE = 150  # s/km: anything quicker is a GPS glitch
MAX_LAPS_SHOWN = 40


def _fmt_pace(minutes_per_unit: float) -> str:
    seconds = round(minutes_per_unit * 60)
    return f"{seconds // 60}:{seconds % 60:02d}"


def _fmt_legs(legs: list[dict]) -> str:
    """'swim 0:42 1.9 km · T1 0:04 · bike 2:40 90.0 km · T2 0:02 · run 1:45 21.1 km'"""
    parts, transitions = [], 0
    for leg in legs:
        minutes = (leg.get("duration_seconds") or 0) / 60
        if leg.get("sport") == "other" and "transition" in (leg.get("type") or ""):
            transitions += 1
            parts.append(f"T{transitions} {fmt_minutes(minutes)}")
            continue
        text = f"{leg.get('sport') if leg.get('sport') != 'other' else leg.get('type')} {fmt_minutes(minutes)}"
        if (leg.get("distance_meters") or 0) >= 100:
            text += f" {leg['distance_meters'] / 1000:.1f} km"
        if leg.get("avg_hr"):
            text += f" avg HR {round(leg['avg_hr'])}"
        parts.append(text)
    return " · ".join(parts)


def _fmt_effort(a: models.Activity) -> str:
    """What the athlete answered on the watch: 'effort 7/10, felt strong'."""
    parts = []
    if a.perceived_effort:
        parts.append(f"effort {a.perceived_effort}/10")
    if a.feel is not None:
        parts.append(f"felt {FEEL_WORDS.get(a.feel, f'{a.feel}/100')}")
    if parts:
        return ", ".join(parts)
    # Nothing to show: a skipped prompt on the watch, or details not fetched yet.
    return "effort not answered" if a.details_synced_at else "effort not synced yet"


def fmt_activity(a: models.Activity, effort: bool = True) -> str:
    """'run 0:45 · 8.2 km · 5:29 /km · avg HR 142 · load 85 · effort 4/10, felt strong — Easy run'"""
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
    felt = _fmt_effort(a) if effort else None
    if felt:
        parts.append(felt)
    text = " · ".join(parts)
    text = f"{text} — {a.name}" if a.name else text
    legs = legs_of(a.extras)
    return f"{text} · legs: {_fmt_legs(legs)}" if legs else text


# --- Laps ---------------------------------------------------------------------


def _is_real_lap(lap: dict) -> bool:
    return (lap.get("distance") or 0) >= 20 and (lap.get("duration") or 0) >= 5  # not a pause or a rest at the wall


def _fmt_lap(lap: dict, sport: str, rep: bool = False) -> str:
    metres, seconds = lap["distance"], lap["duration"]
    distance = f"{metres / 1000:.2f} km" if sport == "run" and metres >= 1000 else f"{round(metres)} m"
    extra = [_fmt_pace(seconds / 60 / (metres / 100)) + " /100 m" if sport == "swim" else _fmt_pace(seconds / 60 / (metres / 1000)) + " /km"]
    if lap.get("avg_hr"):
        extra.append(f"HR {round(lap['avg_hr'])}" + (f" max {round(lap['max_hr'])}" if rep and lap.get("max_hr") else ""))
    if rep and lap.get("cadence"):
        extra.append(f"{round(lap['cadence'])} spm")
    if sport == "swim":
        if lap.get("stroke") and lap["stroke"] not in ("mixed", "freestyle"):
            extra.append(lap["stroke"])
        if lap.get("swolf"):
            extra.append(f"swolf {round(lap['swolf'])}")
    return f"{distance} {fmt_seconds(seconds)} ({', '.join(extra)})"


def _work_laps(laps: list[dict], sport: str) -> list[dict] | None:
    """The reps of an interval session, or None when the laps are all alike.
    Structured workouts say which laps are work; with manual laps the reps are
    the clearly faster ones (at least 25% faster than the slowest)."""
    if any(lap.get("kind") for lap in laps):
        work = [lap for lap in laps if lap.get("kind") == "active"]
        return work if 0 < len(work) < len(laps) else None
    if sport != "run" or len(laps) < 4:
        return None
    speeds = [lap["distance"] / lap["duration"] for lap in laps]
    if max(speeds) < 1.25 * min(speeds):
        return None
    cut = (max(speeds) + min(speeds)) / 2
    return [lap for lap, speed in zip(laps, speeds) if speed > cut]


def _lap_lines(a: models.Activity, sport: str) -> list[str]:
    all_laps = a.laps or []
    laps = [lap for lap in all_laps if _is_real_lap(lap)]
    if len(laps) < 2:
        return []
    work = _work_laps(laps, sport)
    if work:
        shown = [_fmt_lap(lap, sport, rep=True) for lap in work[:MAX_LAPS_SHOWN]]
        easy = [lap for lap in laps if lap not in work]
        metres, seconds = sum(lap["distance"] for lap in easy), sum(lap["duration"] for lap in easy)
        lines = ["reps: " + " | ".join(shown) + (f" | +{len(work) - len(shown)} more" if len(work) > len(shown) else "")]
        if easy and metres:
            lines.append(f"other laps (warm-up, recoveries, cool-down): {len(easy)}, {metres / 1000:.2f} km in "
                         f"{fmt_seconds(seconds)} ({_fmt_pace(seconds / 60 / (metres / 1000))} /km)")
        return lines
    shown = []
    for index, lap in enumerate(all_laps):
        if not _is_real_lap(lap):
            continue
        text = _fmt_lap(lap, sport)
        if sport == "swim" and index + 1 < len(all_laps) and not _is_real_lap(all_laps[index + 1]) and (all_laps[index + 1].get("duration") or 0) >= 5:
            text += f" +{fmt_seconds(all_laps[index + 1]['duration'])} rest"
        shown.append(text)
    more = f" | +{len(shown) - MAX_LAPS_SHOWN} more" if len(shown) > MAX_LAPS_SHOWN else ""
    return ["laps: " + " | ".join(shown[:MAX_LAPS_SHOWN]) + more]


# --- Detail lines (full log only) ----------------------------------------------


def detail_lines(a: models.Activity) -> list[str]:
    """Extra lines for the full log, all from what Garmin already sent."""
    raw = a.raw_json or {}
    sport = sport_of(a.activity_type)
    lines = []

    body = []
    if raw.get("maxHR"):
        body.append(f"max HR {round(raw['maxHR'])}")
    zones = [raw.get(f"hrTimeInZone_{i}") or 0 for i in range(1, 6)]
    if sum(zones) >= 60:
        body.append("HR zones " + " · ".join(f"Z{i} {round(s / 60)}" for i, s in enumerate(zones, start=1)) + " min")
    label = raw.get("trainingEffectLabel")
    if label and label != "UNKNOWN":
        effect = f"{_EFFECT_WORDS.get(label, label.lower().replace('_', ' '))} session"
        if a.aerobic_training_effect is not None:
            effect += f" (training effect {a.aerobic_training_effect:.1f} aerobic"
            effect += f", {a.anaerobic_training_effect:.1f} anaerobic)" if a.anaerobic_training_effect is not None else ")"
        body.append(effect)
    if raw.get("differenceBodyBattery"):
        body.append(f"body battery {round(raw['differenceBodyBattery']):+d}")
    if body:
        lines.append(" · ".join(body))

    mechanics = []
    if sport == "bike" and raw.get("avgPower"):
        power = f"power {round(raw['avgPower'])} W"
        if raw.get("normPower"):
            power += f" (normalized {round(raw['normPower'])})"
        if raw.get("max20MinPower"):
            power += f", best 20 min {round(raw['max20MinPower'])} W"
        mechanics.append(power)
    cadence = raw.get("averageRunningCadenceInStepsPerMinute") if sport == "run" else raw.get("averageBikingCadenceInRevPerMinute") if sport == "bike" else None
    if cadence:
        mechanics.append(f"cadence {round(cadence)} {'spm' if sport == 'run' else 'rpm'}")
    if (raw.get("elevationGain") or 0) >= 20:
        mechanics.append(f"climb {round(raw['elevationGain'])} m")
    if sport == "run":
        best = []
        for metres, name in ((400, "400 m"), (1000, "1 km"), (5000, "5 km"), (10000, "10 km")):
            seconds = raw.get(f"fastestSplit_{metres}")
            if seconds and seconds / (metres / 1000) >= _FASTEST_PLAUSIBLE_PACE:
                best.append(f"{name} {fmt_seconds(seconds)}")
        if best:
            mechanics.append("fastest " + ", ".join(best))
    if sport == "swim":
        factor = (raw.get("unitOfPoolLength") or {}).get("factor") or 100
        if raw.get("poolLength"):
            mechanics.append(f"pool {raw['poolLength'] / factor:g} m")
        if raw.get("activeLengths"):
            mechanics.append(f"{round(raw['activeLengths'])} lengths")
        if raw.get("avgStrokes"):
            mechanics.append(f"{raw['avgStrokes']:.1f} strokes/length")
        if raw.get("averageSwolf"):
            mechanics.append(f"SWOLF {round(raw['averageSwolf'])}")
    if mechanics:
        lines.append(" · ".join(mechanics))

    extras = a.extras or {}
    conditions = []
    if extras.get("temp_c") is not None:
        conditions.append(f"{extras['temp_c']} °C")
    if extras.get("wind_kmh"):
        conditions.append(f"wind {extras['wind_kmh']} km/h")
    if extras.get("humidity"):
        conditions.append(f"humidity {extras['humidity']}%")
    if extras.get("sweat_ml"):
        conditions.append(f"estimated sweat loss {extras['sweat_ml'] / 1000:.1f} L")
    if conditions:
        lines.append("conditions: " + " · ".join(conditions))

    splits = {s.get("splitType"): s for s in raw.get("splitSummaries") or []}
    work = splits.get("INTERVAL_ACTIVE")
    if work and (work.get("noOfSplits") or 0) > 1 and work.get("distance") and work.get("duration"):
        text = (f"intervals: {work['noOfSplits']} work reps, {work['distance'] / 1000:.2f} km in {fmt_seconds(work['duration'])}"
                f" ({_fmt_pace(work['duration'] / 60 / (work['distance'] / 1000))} /km)")
        rest = splits.get("INTERVAL_RECOVERY")
        if rest and rest.get("duration"):
            text += f", recoveries {fmt_seconds(rest['duration'])} in total"
        lines.append(text)

    return lines + _lap_lines(a, sport)
