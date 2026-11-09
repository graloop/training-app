"""The only file that talks to Garmin Connect.

It wraps the unofficial `garth` (login, tokens) and `garminconnect` (one method
per Garmin endpoint) libraries, held at garth 0.4.x and garminconnect 0.2.x in
requirements.txt. Both are used with real accounts: logging in, resuming a
stored session, activities, sleep, recovery, effort and feel, laps.

- Logging in: the Garmin password is sent to Garmin once
  (login_and_export_session) and never stored. What comes back is garth's
  session (OAuth tokens) as a string, which routers/garmin.py stores encrypted.
- Every later request resumes that session (get_authenticated_client).
- One fetch_* function per Garmin request, so sync.py never touches the
  libraries and tests can swap any one of them for a fake.

Login and resume problems are raised as GarminAuthError, with a short message
the page can show. Accounts with two-step verification (MFA) are refused with
a clear message rather than left waiting for a code. A fetch_* function that
fails raises whatever the library raised; sync.py decides what a failure means.
"""

import datetime
import logging
from typing import Any

import garth
from garminconnect import Garmin

log = logging.getLogger("trainer.garmin")


class GarminAuthError(Exception):
    """Garmin refused the login, or a stored session can't be resumed: the user
    has to connect Garmin again. The message is short enough to show as is."""


# --- Login and the stored session ------------------------------------------------


def _export_session(client: garth.Client) -> str:
    return client.dumps()


def _import_session(client: garth.Client, session_data: str) -> None:
    client.loads(session_data)


def login_and_export_session(email: str, password: str) -> str:
    """Log in to Garmin Connect with real credentials and return a
    serialized session that can be stored (encrypted) and reused later
    without the password."""
    client = garth.Client()

    def _refuse_mfa():
        # garth's default prompt is input(), which would block the request.
        raise GarminAuthError("Garmin accounts with MFA enabled aren't supported yet.")

    try:
        client.login(email, password, prompt_mfa=_refuse_mfa)
    except GarminAuthError:
        raise
    except Exception as exc:  # noqa: BLE001 - surface as a domain error
        raise GarminAuthError(_readable_login_error(exc)) from exc
    return _export_session(client)


def _readable_login_error(exc: Exception) -> str:
    """garth errors embed the full SSO URL; keep the message short and useful."""
    text = str(exc)
    if "401" in text or "403" in text:
        return "Garmin login failed: Garmin rejected that email or password."
    if "429" in text:
        return "Garmin login failed: too many attempts, Garmin asks to wait a while before trying again."
    text = text.split(" for url:")[0]
    return f"Garmin login failed: {text[:200]}"


def export_session(api: Garmin) -> str | None:
    """The session of a resumed client as it is now (refreshed tokens included),
    or None for a client that isn't a real Garmin one."""
    client = getattr(api, "garth", None)
    return _export_session(client) if isinstance(client, garth.Client) else None


def get_authenticated_client(session_data: str) -> Garmin:
    """Resume a Garmin API client from a previously stored session, without
    needing the password again."""
    garth_client = garth.Client()
    try:
        _import_session(garth_client, session_data)
    except Exception as exc:  # noqa: BLE001
        raise GarminAuthError(f"Stored Garmin session could not be resumed: {exc}") from exc

    api = Garmin()
    api.garth = garth_client
    # Garmin.login() normally sets this; endpoints like get_sleep_data build
    # their URL from it. Fetching the profile also validates the session.
    try:
        api.display_name = garth_client.profile["displayName"]
    except Exception as exc:  # noqa: BLE001
        raise GarminAuthError(f"Stored Garmin session could not be resumed: {exc}") from exc
    return api


# --- One function per Garmin request -------------------------------------------------
# What each answer looks like is read in sync.py (and activity_file.py for files).


def fetch_activities(api: Garmin, start_date: datetime.date, end_date: datetime.date) -> list[dict[str, Any]]:
    """Garmin's activity list between two days, both included: one dict per
    activity (activityId, activityType.typeKey, startTimeLocal, duration,
    distance, averageHR, activityTrainingLoad...), without effort, feel or laps."""
    return api.get_activities_by_date(start_date.isoformat(), end_date.isoformat())


def fetch_sleep(api: Garmin, day: datetime.date) -> dict[str, Any] | None:
    """The night that ended on `day` (dailySleepDTO, restingHeartRate, HRV...),
    or None when Garmin has nothing for it."""
    try:
        return api.get_sleep_data(day.isoformat())
    except Exception as exc:  # noqa: BLE001 - some days simply have no data
        log.debug("No sleep data for %s: %s", day, exc)
        return None


def fetch_activity_summary(api: Garmin, activity_id: str) -> dict[str, Any]:
    """One activity in full; its summaryDTO has the perceived effort and feel
    answered on the watch, which the activity list doesn't include."""
    return api.get_activity(activity_id)


def fetch_activity_laps(api: Garmin, activity_id: str) -> list[dict[str, Any]]:
    """The activity's laps (Garmin's lapDTOs), each with distance, duration,
    heart rate, power, cadence and, in a structured workout, intensityType."""
    return (api.get_activity_splits(activity_id) or {}).get("lapDTOs") or []


def fetch_profile(api: Garmin) -> dict[str, Any]:
    """What Garmin knows about the athlete. Each part is optional: accounts and
    watches differ, and one missing part must not lose the others."""
    parts = {
        "profile": lambda: api.get_user_profile(),
        "hr_zones": lambda: api.connectapi("/biometric-service/heartRateZones"),
        "records": lambda: api.get_personal_record(),
        "predictions": lambda: api.get_race_predictions(),
    }
    result: dict[str, Any] = {}
    for name, call in parts.items():
        try:
            result[name] = call()
        except Exception as exc:  # noqa: BLE001
            log.debug("Garmin profile part %r not available: %s", name, exc)
            result[name] = None
    return result


def fetch_training_readiness(api: Garmin, day: datetime.date) -> list[dict[str, Any]]:
    """Every Training Readiness reading of the day (Garmin recomputes it during
    the day); sync.morning_readiness picks the wake-up one."""
    return api.get_training_readiness(day.isoformat()) or []


def fetch_training_status(api: Garmin, day: datetime.date) -> dict[str, Any]:
    """Training Status as of `day`: status phrase, acute and chronic load, VO2max
    and load by intensity, per device (read by sync.status_fields)."""
    return api.get_training_status(day.isoformat()) or {}


def fetch_daily_stats(api: Garmin, day: datetime.date) -> dict[str, Any]:
    """The day's summary: stress, body battery, steps (read by sync.stats_fields)."""
    return api.get_stats(day.isoformat()) or {}


def fetch_activity_weather(api: Garmin, activity_id: str) -> dict[str, Any]:
    """The weather Garmin recorded for an outdoor activity, in °F and mph
    (converted by sync.weather_fields)."""
    return api.get_activity_weather(activity_id) or {}


def fetch_activity_file(api: Garmin, activity_id: str) -> bytes:
    """The file the watch or Zwift sent to Garmin, zipped (read by activity_file.py)."""
    return api.download_activity(activity_id, dl_fmt=api.ActivityDownloadFormat.ORIGINAL)
