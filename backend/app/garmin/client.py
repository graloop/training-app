"""
Thin wrapper around the unofficial `garminconnect` / `garth` libraries.

IMPORTANT — NOT TESTED AGAINST A REAL GARMIN ACCOUNT:
This module has not been exercised against a live Garmin Connect login. The
`garth`/`garminconnect` libraries change their session-serialization API
across versions (some expose `Client.dumps()`/`Client.loads()` for in-memory
JSON strings, older ones only support `Client.dump(dir)`/`Client.load(dir)`
against a filesystem directory). `_export_session`/`_import_session` below
try the string-based API first and fall back to the directory-based one, but
this needs a real login to confirm it actually round-trips a resumable
session. Garmin's login can also require MFA/2FA for some accounts, which
this code does not handle — a login that requires MFA will raise and surface
as a generic "Garmin login failed" error.
"""

import datetime
import json
import os
import tempfile
from typing import Any

import garth
from garminconnect import Garmin


class GarminAuthError(Exception):
    pass


def _export_session(client: garth.Client) -> str:
    try:
        return client.dumps()
    except AttributeError:
        with tempfile.TemporaryDirectory() as tmp_dir:
            client.dump(tmp_dir)
            data: dict[str, str] = {}
            for fname in os.listdir(tmp_dir):
                with open(os.path.join(tmp_dir, fname), "r", encoding="utf-8") as f:
                    data[fname] = f.read()
            return json.dumps(data)


def _import_session(client: garth.Client, session_data: str) -> None:
    try:
        client.loads(session_data)
        return
    except AttributeError:
        pass

    data = json.loads(session_data)
    with tempfile.TemporaryDirectory() as tmp_dir:
        for fname, content in data.items():
            with open(os.path.join(tmp_dir, fname), "w", encoding="utf-8") as f:
                f.write(content)
        client.load(tmp_dir)


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


def fetch_activities(api: Garmin, start_date: datetime.date, end_date: datetime.date) -> list[dict[str, Any]]:
    return api.get_activities_by_date(start_date.isoformat(), end_date.isoformat())


def fetch_sleep(api: Garmin, day: datetime.date) -> dict[str, Any] | None:
    try:
        return api.get_sleep_data(day.isoformat())
    except Exception:  # noqa: BLE001 - some days simply have no data
        return None


def fetch_activity_summary(api: Garmin, activity_id: str) -> dict[str, Any]:
    """One activity in full; its summaryDTO has the perceived effort and feel
    answered on the watch, which the activity list doesn't include."""
    return api.get_activity(activity_id)


def fetch_activity_laps(api: Garmin, activity_id: str) -> list[dict[str, Any]]:
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
        except Exception:  # noqa: BLE001
            result[name] = None
    return result


def fetch_training_readiness(api: Garmin, day: datetime.date) -> list[dict[str, Any]]:
    return api.get_training_readiness(day.isoformat()) or []


def fetch_training_status(api: Garmin, day: datetime.date) -> dict[str, Any]:
    return api.get_training_status(day.isoformat()) or {}


def fetch_daily_stats(api: Garmin, day: datetime.date) -> dict[str, Any]:
    return api.get_stats(day.isoformat()) or {}


def fetch_activity_weather(api: Garmin, activity_id: str) -> dict[str, Any]:
    return api.get_activity_weather(activity_id) or {}
