"""An activity's original file, as Garmin keeps it, turned into what is stored:
heart rate, speed, power and cadence every few seconds, and when each lap began.
Positions are never read, and the file itself is not kept: Garmin still has it."""

import io
import zipfile
from typing import Any

import fitdecode

SAMPLE_SECONDS = 5
MAX_FILE_BYTES = 64 * 1024 * 1024  # a 24-hour ride is far less: anything bigger isn't a real file


class UnreadableFile(ValueError):
    """Not a FIT file we can read (some uploads keep a GPX or TCX as the original)."""


def _fit_bytes(data: bytes) -> bytes:
    """Garmin sends the original zipped, with the FIT file inside."""
    if data[8:12] == b".FIT":
        return data
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            for member in archive.infolist():
                if member.filename.lower().endswith(".fit"):
                    if member.file_size > MAX_FILE_BYTES:
                        raise UnreadableFile(f"{member.file_size} bytes unzipped is too big")
                    return archive.read(member)
    except zipfile.BadZipFile as exc:
        raise UnreadableFile("neither a zip nor a FIT file") from exc
    raise UnreadableFile("no FIT file in the zip")


def _round_series(values: list[float | None], digits: int) -> list[float | None] | None:
    """The series, rounded; None when the file has no such data."""
    if all(v is None for v in values):
        return None
    return [None if v is None else (round(v, digits) if digits else round(v)) for v in values]


def read_trace(data: bytes, running: bool = False) -> dict[str, Any]:
    """{"samples": {"t": [seconds since the start], "hr", "speed" (m/s), "power",
    "cadence"}, "laps": [{"start", "duration"} in seconds]}. A series the file
    doesn't have is left out. `running`: a FIT file counts one foot's steps."""
    buckets: dict[int, dict[str, list[float]]] = {}
    raw_laps = []
    start = None
    try:
        with fitdecode.FitReader(io.BytesIO(_fit_bytes(data))) as reader:
            for frame in reader:
                if not isinstance(frame, fitdecode.FitDataMessage):
                    continue
                if frame.name == "lap":
                    raw_laps.append((frame.get_value("start_time", fallback=None), frame.get_value("total_elapsed_time", fallback=None)))
                if frame.name != "record":
                    continue
                stamp = frame.get_value("timestamp", fallback=None)
                if stamp is None:
                    continue
                start = start or stamp
                bucket = buckets.setdefault(int((stamp - start).total_seconds()) // SAMPLE_SECONDS, {})
                speed = frame.get_value("enhanced_speed", fallback=None)
                values = {"hr": frame.get_value("heart_rate", fallback=None),
                          "speed": frame.get_value("speed", fallback=None) if speed is None else speed,
                          "power": frame.get_value("power", fallback=None),
                          "cadence": frame.get_value("cadence", fallback=None)}
                for key, value in values.items():
                    if isinstance(value, (int, float)):
                        bucket.setdefault(key, []).append(value)
    except fitdecode.FitError as exc:
        raise UnreadableFile(str(exc)) from exc

    order = sorted(buckets)
    samples: dict[str, list] = {"t": [i * SAMPLE_SECONDS for i in order]}
    for key, digits in (("hr", 0), ("speed", 2), ("power", 0), ("cadence", 0)):
        factor = 2 if key == "cadence" and running else 1
        series = _round_series([sum(buckets[i][key]) / len(buckets[i][key]) * factor if key in buckets[i] else None
                                for i in order], digits)
        if series:
            samples[key] = series
    laps = [{"start": round((began - start).total_seconds()), "duration": round(float(elapsed), 1)}
            for began, elapsed in raw_laps if start is not None and began is not None and elapsed is not None]
    return {"samples": samples, "laps": laps}
