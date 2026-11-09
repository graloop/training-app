"""One Garmin activity as text for the training log: the summary line (sport,
time, distance, pace, HR, load, effort) and, in the full log, the detail lines
(zones, power, cadence, weather, intervals, laps). Everything comes from what
Garmin already sent (backend/app/garmin/sync.py stores it).

Laps are stored compact (sync.compact_laps: distance, duration, avg_hr,
avg_power, cadence, kind...). rep_laps() decides which laps are the reps of an
interval session; the log, the one-line summary of older weeks and the session
detail all go through it, so a session never shows different reps in two places."""

from itertools import pairwise

from .. import models
from .basics import fmt_minutes, fmt_seconds, legs_of, sport_of

FEEL_WORDS = {0: "very weak", 25: "weak", 50: "normal", 75: "strong", 100: "very strong"}
_EFFECT_WORDS = {
    "RECOVERY": "recovery", "AEROBIC_BASE": "aerobic base", "TEMPO": "tempo", "LACTATE_THRESHOLD": "threshold",
    "VO2MAX": "VO2max", "ANAEROBIC_CAPACITY": "anaerobic", "SPRINT": "sprint", "SPEED": "speed",
}
_FASTEST_PLAUSIBLE_PACE = 150  # s/km: anything quicker is a GPS glitch
MAX_LAPS_SHOWN = 100  # as many as are stored


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


def is_real_lap(lap: dict, sport: str) -> bool:
    if sport == "bike":  # an indoor trainer may report no distance at all
        return (lap.get("duration") or 0) >= 5
    return (lap.get("distance") or 0) >= 20 and (lap.get("duration") or 0) >= 5  # not a pause or a rest at the wall


def _fmt_bike_lap(lap: dict) -> str:
    metres, seconds = lap.get("distance") or 0, lap["duration"]
    extra = []
    if lap.get("avg_power"):
        extra.append(f"{round(lap['avg_power'])} W" + (f" NP {round(lap['norm_power'])}" if lap.get("norm_power") else ""))
    if metres >= 100:
        extra.append(f"{metres / seconds * 3.6:.1f} km/h")
    if lap.get("avg_hr"):
        extra.append(f"HR {round(lap['avg_hr'])}" + (f" max {round(lap['max_hr'])}" if lap.get("max_hr") else ""))
    if lap.get("cadence"):
        extra.append(f"{round(lap['cadence'])} rpm")
    text = f"{metres / 1000:.2f} km {fmt_seconds(seconds)}" if metres >= 100 else fmt_seconds(seconds)
    return f"{text} ({', '.join(extra)})" if extra else text


def _fmt_lap(lap: dict, sport: str, rep: bool = False) -> str:
    if sport == "bike":
        return _fmt_bike_lap(lap)
    metres, seconds = lap["distance"], lap["duration"]
    distance = f"{metres / 1000:.2f} km" if sport == "run" and metres >= 1000 else f"{round(metres)} m"
    extra = [_fmt_pace(seconds / 60 / (metres / 100)) + " /100 m" if sport == "swim" else _fmt_pace(seconds / 60 / (metres / 1000)) + " /km"]
    if lap.get("avg_hr"):
        extra.append(f"HR {round(lap['avg_hr'])}" + (f" max {round(lap['max_hr'])}" if lap.get("max_hr") else ""))
    if rep and lap.get("cadence"):
        extra.append(f"{round(lap['cadence'])} spm")
    if sport == "swim":
        if lap.get("stroke") and lap["stroke"] not in ("mixed", "freestyle"):
            extra.append(lap["stroke"])
        if lap.get("swolf"):
            extra.append(f"swolf {round(lap['swolf'])}")
    return f"{distance} {fmt_seconds(seconds)} ({', '.join(extra)})"


def _intensity(lap: dict, sport: str) -> float:
    # On a bike, speed mostly says how hilly it was: power is the effort.
    if sport == "bike":
        return lap.get("avg_power") or 0
    return lap["distance"] / lap["duration"]


def _hard_group_floor(values: list[float], seconds: list[float]) -> float:
    """Where the hard laps start: the cut that best separates two groups, each
    lap counting for its time (Otsu's method). One short sprint or GPS glitch
    weighs little, so it can't lift the cut above the real reps.

    Laps are sorted by intensity; each gap between two neighbours is a possible
    cut, scored by time below x time above x (gap between the two groups'
    time-weighted means)^2. The best score's cut, midway between the two laps,
    is returned (-1 when every lap is alike)."""
    laps = sorted(zip(values, seconds, strict=True))
    total_time = sum(seconds)
    total = sum(value * time for value, time in laps)
    best = cut = -1.0
    easy_time = easy = 0.0
    for (value, time), (above, _) in pairwise(laps):
        easy_time += time
        easy += value * time
        if value == above:
            continue
        hard_time = total_time - easy_time
        score = easy_time * hard_time * ((total - easy) / hard_time - easy / easy_time) ** 2
        if score > best:
            best, cut = score, (value + above) / 2
    return cut


def _work_laps(laps: list[dict], sport: str) -> list[dict] | None:
    """The reps of an interval session, or None when the laps are all alike.
    Structured workouts say which laps are work; a lap without a label counts
    if it is as hard as those. With manual laps the reps are the clearly harder
    ones (at least 25% above the easiest)."""
    if any(lap.get("kind") for lap in laps):
        active = [_intensity(lap, sport) for lap in laps if lap.get("kind") == "active"]
        if not active:
            return None
        work = [lap for lap in laps if lap.get("kind") == "active"
                or (not lap.get("kind") and _intensity(lap, sport) >= 0.9 * min(active) > 0)]
        return work if len(work) < len(laps) else None
    if sport not in ("run", "bike") or len(laps) < 4:
        return None
    values = [_intensity(lap, sport) for lap in laps]
    if max(values) <= 0 or max(values) < 1.25 * min(values):
        return None
    floor = _hard_group_floor(values, [lap["duration"] for lap in laps])
    work = [lap for lap, value in zip(laps, values, strict=True) if value > floor]
    return work if len(work) >= 2 else None


def rep_laps(a: models.Activity) -> tuple[list[dict], list[dict] | None]:
    """(the real laps, the reps among them or None). When Garmin counted more
    reps than were found, nothing is called a rep, so none is lost in a total."""
    sport = sport_of(a.activity_type)
    laps = [lap for lap in a.laps or [] if is_real_lap(lap, sport)]
    work = _work_laps(laps, sport) if len(laps) >= 2 else None
    splits = {s.get("splitType"): s for s in (a.raw_json or {}).get("splitSummaries") or []}
    # A structured workout's summary says how many work intervals Garmin counted.
    # Finding fewer means some reps were missed (a rep merged with its recovery,
    # say): then no lap is called a rep rather than a total that silently drops one.
    counted = (splits.get("INTERVAL_ACTIVE") or {}).get("noOfSplits") or 0
    return laps, (None if work and len(work) < counted else work)


def _only_automatic_laps(a: models.Activity, laps: list[dict], sport: str) -> bool:
    """An outdoor ride cut every 5 km by the watch: its laps say nothing."""
    key = a.activity_type.lower()
    if sport != "bike" or "virtual" in key or "indoor" in key or len(laps) < 3 or any(lap.get("kind") for lap in laps):
        return False
    first = laps[0].get("distance") or 0
    return first > 0 and all(abs((lap.get("distance") or 0) - first) <= 0.01 * first for lap in laps[:-1])


def _lap_lines(a: models.Activity, sport: str) -> list[str]:
    all_laps = a.laps or []
    laps, work = rep_laps(a)
    if len(laps) < 2 or _only_automatic_laps(a, laps, sport):
        return []
    if work:
        shown = [_fmt_lap(lap, sport, rep=True) for lap in work[:MAX_LAPS_SHOWN]]
        easy = [lap for lap in laps if lap not in work]
        lines = ["reps: " + " | ".join(shown) + (f" | +{len(work) - len(shown)} more" if len(work) > len(shown) else "")]
        if easy:
            metres, seconds = sum(lap.get("distance") or 0 for lap in easy), sum(lap["duration"] for lap in easy)
            total = (f"{fmt_seconds(seconds)}" if sport == "bike" else
                     f"{metres / 1000:.2f} km in {fmt_seconds(seconds)} ({_fmt_pace(seconds / 60 / (metres / 1000))} /km)")
            lines.append(f"other laps (warm-up, recoveries, cool-down): {len(easy)}, {total}: "
                         + " | ".join(_fmt_lap(lap, sport) for lap in easy[:MAX_LAPS_SHOWN]))
        return lines
    shown = []
    for index, lap in enumerate(all_laps):
        if not is_real_lap(lap, sport):
            continue
        text = _fmt_lap(lap, sport)
        if sport == "swim" and index + 1 < len(all_laps) and not is_real_lap(all_laps[index + 1], sport) and (all_laps[index + 1].get("duration") or 0) >= 5:
            text += f" +{fmt_seconds(all_laps[index + 1]['duration'])} rest"
        shown.append(text)
    more = f" | +{len(shown) - MAX_LAPS_SHOWN} more" if len(shown) > MAX_LAPS_SHOWN else ""
    return ["laps: " + " | ".join(shown[:MAX_LAPS_SHOWN]) + more]


def all_lap_lines(a: models.Activity) -> list[str]:
    """Every lap, numbered, whatever the log would leave out."""
    sport = sport_of(a.activity_type)
    laps, work = rep_laps(a)
    lines = []
    for number, lap in enumerate(laps, start=1):
        kind = "rep" if work and any(lap is w for w in work) else lap.get("kind")
        lines.append(f"{number}. {kind + ' ' if kind else ''}{_fmt_lap(lap, sport, rep=True)}")
    return lines


def rep_summary(a: models.Activity) -> str | None:
    """The reps in one line for older weeks: 'reps: 9 × 1000 m avg 3:58 /km HR 168'."""
    sport = sport_of(a.activity_type)
    _, work = rep_laps(a)
    if not work:
        return None
    seconds = sum(lap["duration"] for lap in work)
    metres = sum(lap.get("distance") or 0 for lap in work)
    sizes = [lap["duration"] if sport == "bike" else lap["distance"] for lap in work]
    if max(sizes) <= 1.05 * min(sizes):
        size = sum(sizes) / len(sizes)
        text = f"{len(work)} × " + (fmt_seconds(size) if sport == "bike" else f"{round(size)} m")
    else:
        text = f"{len(work)}, " + (fmt_seconds(seconds) if sport == "bike" else f"{metres / 1000:.2f} km") + " in all,"
    if sport == "bike":
        watts = sum((lap.get("avg_power") or 0) * lap["duration"] for lap in work) / seconds
        text += f" avg {round(watts)} W" if watts else ""
    elif sport == "swim":
        text += f" avg {_fmt_pace(seconds / 60 / (metres / 100))} /100 m"
    else:
        text += f" avg {_fmt_pace(seconds / 60 / (metres / 1000))} /km"
    with_hr = [lap for lap in work if lap.get("avg_hr")]
    if with_hr:
        text += f" HR {round(sum(lap['avg_hr'] * lap['duration'] for lap in with_hr) / sum(lap['duration'] for lap in with_hr))}"
    return f"reps: {text}"


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
