"""What the logs say when something goes wrong: the first place to look
(docs/TROUBLESHOOTING.md)."""

import logging

from conftest import login
from fastapi.testclient import TestClient

from app import accounts
from app.garmin import client as garmin_client
from app.main import app
from app.version import VERSION


def test_startup_says_the_version_and_the_database(caplog):
    with caplog.at_level(logging.INFO, logger="trainer"), TestClient(app):
        pass
    assert f"Training App version {VERSION} starting" in caplog.text
    assert "database sqlite:" in caplog.text


def test_a_locked_username_is_logged(client, caplog):
    with caplog.at_level(logging.WARNING, logger="trainer"):
        for _ in range(accounts.MAX_FAILURES):
            client.post("/api/auth/login", json={"username": "admin", "password": "wrong"})
    assert "Too many failed logins: username 'admin' blocked" in caplog.text
    assert "wrong" not in caplog.text  # never the password


def test_a_failed_garmin_sync_is_logged_with_its_cause(client, auth, monkeypatch, caplog):
    def refused(api, start, end):
        raise RuntimeError("Garmin answered 503")

    monkeypatch.setattr(garmin_client, "get_authenticated_client", lambda session: object())
    monkeypatch.setattr(garmin_client, "fetch_activities", refused)
    with caplog.at_level(logging.WARNING, logger="trainer"):
        assert client.post("/api/garmin/sync", headers=auth).status_code == 502
    assert "Garmin sync failed for 'athlete'" in caplog.text
    assert "Garmin answered 503" in caplog.text  # the traceback, for whoever reads the logs


def test_a_failed_garmin_login_is_logged_without_the_password(client, caplog):
    headers = login(client, "admin", "admin-password")
    client.post("/api/admin/users", json={"username": "friend", "password": "friend-password"}, headers=headers)
    friend = login(client, "friend", "friend-password")
    with caplog.at_level(logging.WARNING, logger="trainer"):
        res = client.post("/api/garmin/connect", json={"garmin_email": "f@example.com", "garmin_password": "nope-secret"},
                          headers=friend)
    assert res.status_code == 400
    assert "Garmin connect failed for 'friend'" in caplog.text
    assert "nope-secret" not in caplog.text
