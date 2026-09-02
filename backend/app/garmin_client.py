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
    try:
        client.login(email, password)
    except Exception as exc:  # noqa: BLE001 - surface as a domain error
        raise GarminAuthError(f"Garmin login failed: {exc}") from exc
    return _export_session(client)


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
    return api


def fetch_activities(api: Garmin, start_date: datetime.date, end_date: datetime.date) -> list[dict[str, Any]]:
    return api.get_activities_by_date(start_date.isoformat(), end_date.isoformat())


def fetch_sleep(api: Garmin, day: datetime.date) -> dict[str, Any] | None:
    try:
        return api.get_sleep_data(day.isoformat())
    except Exception:  # noqa: BLE001 - some days simply have no data
        return None
