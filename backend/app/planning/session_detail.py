"""One session up close, from its trace (garmin/activity_file.py, kept TRACE_DAYS
days): how each rep went inside for the log, and every lap and sample for the
"One session in detail" part of the History tab."""

from .. import models
from .activity_text import all_lap_lines, detail_lines, fmt_activity, rep_laps
from .basics import TRACE_DAYS, fmt_day, fmt_seconds, sport_of

DRIFT_MIN_SECONDS = 20 * 60
DRIFT_SKIP_SECONDS = 10 * 60  # heart rate is still settling
DRIFT_SKIP_FROM_SECONDS = 30 * 60  # only in a session this long
MIN_REP_SAMPLES = 4


def _mean(values: list) -> float | None:
    present = [v for v in values if v]
    return sum(present) / len(present) if present else None


def _fmt_speed(speed: float | None, sport: str) -> str:
    if not speed:
        return "-"
    if sport == "bike":
        return f"{speed * 3.6:.1f}"
    seconds = (100 if sport == "swim" else 1000) / speed
    return fmt_seconds(seconds) if seconds < 3600 else "-"


def _output(samples: dict, indexes: list[int], sport: str) -> tuple[float | None, str]:
    """(effort, as text) over some samples: watts on a bike with power, else speed."""
    if sport == "bike" and samples.get("power"):
        watts = _mean([samples["power"][i] for i in indexes])
        return watts, f"{round(watts)} W" if watts else "-"
    if samples.get("speed"):
        speed = _mean([samples["speed"][i] for i in indexes])
        return speed, _fmt_speed(speed, sport)
    return None, "-"


def _rep_line(a: models.Activity, trace: models.ActivityTrace, work: list[dict], sport: str) -> list[str]:
    samples = trace.samples
    if not samples.get("hr") or len(trace.laps or []) != len(a.laps or []):
        return []  # the file and Garmin's lap list don't line up: better nothing than the wrong rep
    times = samples["t"]
    parts = []
    for number, lap in enumerate(work, start=1):
        place = next(i for i, candidate in enumerate(a.laps) if candidate is lap)
        begin = trace.laps[place]["start"]
        inside = [i for i, second in enumerate(times) if begin <= second < begin + trace.laps[place]["duration"]]
        beats = [samples["hr"][i] for i in inside if samples["hr"][i]]
        if len(inside) < MIN_REP_SAMPLES or not beats:
            continue
        half = len(inside) // 2
        first, second = _output(samples, inside[:half], sport)[1], _output(samples, inside[half:], sport)[1]
        parts.append(f"{number}) {beats[0]}→{max(beats)}, {first}→{second}")
    if not parts:
        return []
    unit = "power" if sport == "bike" and samples.get("power") else "speed" if sport == "bike" else "pace"
    return [f"within reps (HR start→peak, {unit} first→second half): " + " | ".join(parts)]


def _drift_line(trace: models.ActivityTrace, sport: str) -> list[str]:
    samples = trace.samples
    times = samples["t"]
    if not samples.get("hr") or sport not in ("run", "bike") or times[-1] < DRIFT_MIN_SECONDS:
        return []
    skip = DRIFT_SKIP_SECONDS if times[-1] >= DRIFT_SKIP_FROM_SECONDS else 0
    kept = [i for i, second in enumerate(times) if second >= skip]
    half = len(kept) // 2
    first, second = kept[:half], kept[half:]
    hr_first, hr_second = _mean([samples["hr"][i] for i in first]), _mean([samples["hr"][i] for i in second])
    if not hr_first or not hr_second:
        return []
    (out_first, text_first), (out_second, text_second) = _output(samples, first, sport), _output(samples, second, sport)
    text = f"first vs second half{f' (after {skip // 60} min)' if skip else ''}: HR {round(hr_first)}→{round(hr_second)}"
    if out_first and out_second:
        text += f", {text_first}→{text_second}, HR for the same effort {(hr_second / out_second) / (hr_first / out_first) - 1:+.1%}"
    return [text]


def trace_lines(a: models.Activity, trace: "models.ActivityTrace | None") -> list[str]:
    """What the trace adds to a session in the log: inside each rep, or the drift of a steady session."""
    if trace is None or not (trace.samples or {}).get("t"):
        return []
    sport = sport_of(a.activity_type)
    _, work = rep_laps(a)
    return _rep_line(a, trace, work, sport) if work else _drift_line(trace, sport)


def _sample_lines(samples: dict, sport: str) -> list[str]:
    columns = [("HR", "hr", lambda v: str(v))]
    if samples.get("speed"):
        unit = {"bike": "km/h", "swim": "/100 m"}.get(sport, "/km")
        columns.append((unit, "speed", lambda v: _fmt_speed(v, sport)))
    if samples.get("power"):
        columns.append(("W", "power", lambda v: str(v)))
    if samples.get("cadence"):
        columns.append(("rpm" if sport == "bike" else "spm", "cadence", lambda v: str(v)))
    columns = [column for column in columns if samples.get(column[1])]
    lines = ["time · " + " · ".join(name for name, _, _ in columns)]
    for i, second in enumerate(samples["t"]):
        values = [text(samples[key][i]) if samples[key][i] is not None else "-" for _, key, text in columns]
        lines.append(" ".join([fmt_seconds(second), *values]))
    return lines


def session_text(a: models.Activity, trace: "models.ActivityTrace | None") -> str:
    """Everything stored about one session, as text."""
    sport = sport_of(a.activity_type)
    lines = [f"SESSION · {fmt_day(a.start_time.date())} {a.start_time.year} · {fmt_activity(a)}"]
    lines += detail_lines(a) + trace_lines(a, trace)
    laps = all_lap_lines(a)
    if laps:
        lines += ["", "ALL LAPS"] + laps
    samples = (trace.samples or {}) if trace else {}
    lines += ["", "HEART RATE, PACE, POWER AND CADENCE OVER TIME"]
    if samples.get("t"):
        lines += ["Averages over about 5 seconds; gaps in the time are pauses. No positions are stored."]
        lines += _sample_lines(samples, sport)
    else:
        lines += [f"- not stored: swims, rides and runs have it for {TRACE_DAYS} days, once a sync has fetched the file from Garmin"]
    return "\n".join(lines)
