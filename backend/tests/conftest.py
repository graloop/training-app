"""Shared test setup. The app reads its settings when it is imported, so the
environment is set first: a throwaway SQLite database, fixed secrets and the
first admin (admin / admin-password).

Fixtures:
- fresh_db (every test): empty tables, login lockouts and sync jobs cleared.
- fake_garmin_login (every test): Garmin's login is replaced; GARMIN_PASSWORD
  is accepted for any email, anything else fails like a wrong password.
- client: a TestClient talking to the app directly (no proxy in front); it
  also runs the app's startup (tables, first admin).
- proxied: requests from a private address, as the reverse proxy or tunnel
  sends them, so CF-Connecting-IP / X-Forwarded-Proto headers are believed.
- auth: headers of a regular account "athlete" with Garmin connected.

Helpers: login() returns auth headers, admin_auth() logs the admin in, and
make_user() has the admin create an account, logs it in and connects Garmin.
"""

import os
import tempfile

# Configure the app before it's imported: settings and the engine are read at
# module import time.
_TMP_DIR = tempfile.mkdtemp(prefix="trainer-tests-")
os.environ["DATABASE_URL"] = f"sqlite:///{os.path.join(_TMP_DIR, 'test.db')}"
os.environ["JWT_SECRET"] = "test-secret-" + "x" * 32
os.environ["FERNET_KEY"] = "3m2Gd0b0x8l8ZLp9bXb1lVZ3n7rPq3QmY2V1n2r3c4s="
os.environ["ADMIN_USERNAME"] = "admin"
os.environ["ADMIN_PASSWORD"] = "admin-password"

import pytest
from fastapi.testclient import TestClient

from app import accounts
from app.database import Base, engine
from app.garmin import autosync
from app.garmin import client as garmin_client
from app.main import app


@pytest.fixture(autouse=True)
def fresh_db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    accounts.reset_all_failures()
    autosync.reset_jobs()
    yield


GARMIN_PASSWORD = "garmin-pass"


@pytest.fixture(autouse=True)
def fake_garmin_login(monkeypatch):
    """Garmin SSO stand-in: accepts GARMIN_PASSWORD for any email."""

    def login(email, password):
        if password != GARMIN_PASSWORD:
            raise garmin_client.GarminAuthError("Garmin login failed: 401 Unauthorized")
        return f"session-for-{email}"

    monkeypatch.setattr(garmin_client, "login_and_export_session", login)


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


PROXY_PEER = ("172.18.0.3", 40000)  # the reverse proxy / tunnel container on the stack's network


@pytest.fixture
def proxied(client):
    """Requests arriving the way they do on the internet: from the proxy, with
    the visitor described in headers (CF-Connecting-IP, X-Forwarded-Proto...)."""
    return TestClient(app, client=PROXY_PEER)


ADMIN = ("admin", "admin-password")
USER_PASSWORD = "friend-password"


def login(client, username, password=USER_PASSWORD):
    res = client.post("/api/auth/login", json={"username": username, "password": password})
    assert res.status_code == 200, res.text
    return {"Authorization": f"Bearer {res.json()['access_token']}"}


def admin_auth(client):
    return login(client, *ADMIN)


def make_user(client, username="athlete", password=USER_PASSWORD, garmin=True):
    """Admin creates the account, the friend logs in and (optionally) connects
    Garmin as <username>@example.com. Returns the friend's auth headers."""
    res = client.post("/api/admin/users", json={"username": username, "password": password}, headers=admin_auth(client))
    assert res.status_code == 201, res.text
    headers = login(client, username, password)
    if garmin:
        res = client.post(
            "/api/garmin/connect",
            json={"garmin_email": f"{username}@example.com", "garmin_password": GARMIN_PASSWORD},
            headers=headers,
        )
        assert res.status_code == 200, res.text
    return headers


@pytest.fixture
def auth(client):
    return make_user(client)
