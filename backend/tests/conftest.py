import os
import tempfile
from types import SimpleNamespace

# Configure the app before it's imported: settings, the engine and the
# Anthropic key are all read at module import time.
_TMP_DIR = tempfile.mkdtemp(prefix="trainer-tests-")
os.environ["DATABASE_URL"] = f"sqlite:///{os.path.join(_TMP_DIR, 'test.db')}"
os.environ["JWT_SECRET"] = "test-secret-" + "x" * 32
os.environ["FERNET_KEY"] = "3m2Gd0b0x8l8ZLp9bXb1lVZ3n7rPq3QmY2V1n2r3c4s="
os.environ["ANTHROPIC_API_KEY"] = "sk-ant-test"
os.environ["ADMIN_USERNAME"] = "admin"
os.environ["ADMIN_PASSWORD"] = "admin-password"
# The chat/AI tests exercise the optional coach chat; test_accounts checks it's off by default.
os.environ["AI_CHAT_ENABLED"] = "true"

import pytest
from fastapi.testclient import TestClient

from app import accounts, ai_coach, garmin_client
from app.database import Base, engine
from app.main import app


@pytest.fixture(autouse=True)
def fresh_db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    accounts.reset_all_failures()
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


# --- Fake Anthropic client ---------------------------------------------------


def text_response(text):
    return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=text)])


def tool_response(changes, tool_id="toolu_1", name="update_training_plan"):
    block = SimpleNamespace(type="tool_use", id=tool_id, name=name, input={"changes": changes})
    return SimpleNamespace(stop_reason="tool_use", content=[block])


class FakeAnthropic:
    """Replays scripted responses and records every messages.create call."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        # Snapshot the messages list: the coach mutates it between calls.
        self.calls.append({**kwargs, "messages": list(kwargs["messages"])})
        return self.responses.pop(0)


@pytest.fixture
def fake_claude(monkeypatch):
    def install(*responses):
        fake = FakeAnthropic(responses)
        monkeypatch.setattr(ai_coach.anthropic, "Anthropic", lambda **_: fake)
        return fake

    return install
