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

import pytest
from fastapi.testclient import TestClient

from app import ai_coach, garmin_client
from app.database import Base, engine
from app.main import app


@pytest.fixture(autouse=True)
def fresh_db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
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


def login(client, email="athlete@example.com", password=GARMIN_PASSWORD):
    res = client.post("/api/auth/login", json={"email": email, "password": password})
    assert res.status_code == 200, res.text
    return {"Authorization": f"Bearer {res.json()['access_token']}"}


@pytest.fixture
def auth(client):
    return login(client)


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
