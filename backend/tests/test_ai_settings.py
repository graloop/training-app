import datetime
import json
from types import SimpleNamespace

import anthropic
import httpx
import openai
import pytest
from conftest import login, text_response

from app import ai_coach, models
from app.database import SessionLocal


def _put(client, auth, **body):
    res = client.put("/api/ai-settings", json=body, headers=auth)
    assert res.status_code == 200, res.text
    return res.json()


# --- settings API -------------------------------------------------------------


def test_defaults_to_claude_with_server_key(client, auth):
    data = client.get("/api/ai-settings", headers=auth).json()
    assert data["provider"] == "anthropic"
    assert data["has_api_key"] is False
    assert data["using_server_key"] is True  # conftest sets ANTHROPIC_API_KEY
    assert {p["id"] for p in data["providers"]} == {"anthropic", "openai", "gemini", "mistral", "custom"}


def test_save_key_never_returns_it(client, auth):
    data = _put(client, auth, provider="openai", api_key="  sk-proj-secret-1234  ")
    assert data["provider"] == "openai"
    assert data["has_api_key"] is True
    assert data["api_key_hint"] == "1234"
    assert "secret" not in json.dumps(data)
    assert "secret" not in json.dumps(client.get("/api/ai-settings", headers=auth).json())

    with SessionLocal() as db:
        stored = db.query(models.AISettings).one().api_key_encrypted
        assert stored and "secret" not in stored  # encrypted at rest


def test_omitting_key_keeps_it_and_model_can_be_changed(client, auth):
    _put(client, auth, provider="openai", api_key="sk-abcd")
    data = _put(client, auth, provider="openai", model="gpt-5-mini")
    assert data["has_api_key"] is True
    assert data["model"] == "gpt-5-mini" and data["custom_model"] is True
    data = _put(client, auth, provider="openai", model="")
    assert data["model"] == "gpt-5" and data["custom_model"] is False


def test_switching_provider_drops_the_old_key(client, auth):
    _put(client, auth, provider="openai", api_key="sk-abcd")
    data = _put(client, auth, provider="mistral")
    assert data["has_api_key"] is False


def test_clear_key(client, auth):
    _put(client, auth, provider="anthropic", api_key="sk-ant-zzzz")
    assert _put(client, auth, provider="anthropic", clear_api_key=True)["has_api_key"] is False


def test_unknown_provider_rejected(client, auth):
    assert client.put("/api/ai-settings", json={"provider": "skynet"}, headers=auth).status_code == 400


def test_settings_are_per_user(client, auth):
    _put(client, auth, provider="gemini", api_key="AIza-1111")
    other = login(client, email="other@example.com")
    assert client.get("/api/ai-settings", headers=other).json()["provider"] == "anthropic"


# --- chat routing ---------------------------------------------------------------


def test_personal_claude_key_and_model_are_used(client, auth, monkeypatch):
    seen = {}

    class Recorder:
        def __init__(self, **kw):
            seen.update(kw)
            self.messages = SimpleNamespace(create=lambda **k: seen.update(model=k["model"]) or text_response("hey"))

    monkeypatch.setattr(ai_coach.anthropic, "Anthropic", Recorder)
    _put(client, auth, provider="anthropic", api_key="sk-ant-mine", model="claude-sonnet-5-5")
    client.post("/api/chat", json={"message": "hi"}, headers=auth)
    assert seen["api_key"] == "sk-ant-mine"
    assert seen["model"] == "claude-sonnet-5-5"


def test_non_claude_provider_without_key_is_a_clear_error(client, auth):
    _put(client, auth, provider="openai")
    res = client.post("/api/chat", json={"message": "hi"}, headers=auth)
    assert res.status_code == 400
    assert "ChatGPT" in res.json()["detail"]


def _completion(content=None, tool_calls=None):
    message = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def _tool_call(changes, call_id="call_1"):
    fn = SimpleNamespace(name="update_training_plan", arguments=json.dumps({"changes": changes}))
    return SimpleNamespace(id=call_id, function=fn)


@pytest.mark.parametrize("provider,base_url", [
    ("openai", None),
    ("gemini", "https://generativelanguage.googleapis.com/v1beta/openai/"),
    ("mistral", "https://api.mistral.ai/v1"),
])
def test_openai_compatible_tool_loop(client, auth, monkeypatch, provider, base_url):
    tomorrow = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
    responses = [
        _completion(tool_calls=[_tool_call([{"action": "create", "date": tomorrow, "activity_type": "cycling"}])]),
        _completion(content="Added a ride."),
    ]
    calls, init = [], {}

    class FakeOpenAI:
        def __init__(self, **kw):
            init.update(kw)
            create = lambda **k: calls.append({**k, "messages": list(k["messages"])}) or responses.pop(0)  # noqa: E731
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))

    monkeypatch.setattr(ai_coach.openai, "OpenAI", FakeOpenAI)
    _put(client, auth, provider=provider, api_key="key-9999")

    res = client.post("/api/chat", json={"message": "plan a ride"}, headers=auth).json()
    assert res["reply"] == "Added a ride."
    assert res["plan_changes"][0]["activity_type"] == "cycling"
    assert init == {"api_key": "key-9999", "base_url": base_url}

    first, second = calls
    assert first["messages"][0]["role"] == "system"
    assert first["tools"][0]["function"]["name"] == "update_training_plan"
    assert second["messages"][-2]["tool_calls"][0]["id"] == "call_1"
    assert second["messages"][-1]["role"] == "tool"
    assert json.loads(second["messages"][-1]["content"])["applied"][0]["action"] == "create"


def _status_error(sdk, cls, code):
    request = httpx.Request("POST", "https://example.invalid")
    return cls("boom", response=httpx.Response(code, request=request), body=None)


@pytest.mark.parametrize("sdk,provider", [(anthropic, "anthropic"), (openai, "openai")])
@pytest.mark.parametrize("error,status,phrase", [
    ("AuthenticationError", 400, "rejected the API key"),
    ("NotFoundError", 400, "doesn't know the model"),
    ("RateLimitError", 429, "rate limit"),
])
def test_provider_errors_are_readable(client, auth, monkeypatch, sdk, provider, error, status, phrase):
    exc = _status_error(sdk, getattr(sdk, error), status)

    def boom(**_):
        raise exc

    fake = SimpleNamespace(
        messages=SimpleNamespace(create=boom),
        chat=SimpleNamespace(completions=SimpleNamespace(create=boom)),
    )
    monkeypatch.setattr(ai_coach.anthropic, "Anthropic", lambda **_: fake)
    monkeypatch.setattr(ai_coach.openai, "OpenAI", lambda **_: fake)
    _put(client, auth, provider=provider, api_key="key-0000")

    res = client.post("/api/chat", json={"message": "hi"}, headers=auth)
    assert res.status_code == status
    assert phrase in res.json()["detail"]
    # The failed exchange isn't half-saved.
    assert client.get("/api/chat/history", headers=auth).json()["messages"] == []


# --- custom provider ------------------------------------------------------------


def test_custom_requires_url_and_model(client, auth):
    for body in [
        {"provider": "custom", "model": "m"},
        {"provider": "custom", "model": "m", "base_url": "ftp://x"},
        {"provider": "custom", "base_url": "https://ai.example.com/v1"},
    ]:
        assert client.put("/api/ai-settings", json=body, headers=auth).status_code == 400


def test_custom_settings_round_trip(client, auth):
    data = _put(client, auth, provider="custom", base_url=" https://ai.example.com/v1 ", model="coach-7b",
                api_format="anthropic", api_key="iag_live_abcd")
    assert (data["base_url"], data["api_format"], data["model"], data["api_key_hint"]) == (
        "https://ai.example.com/v1", "anthropic", "coach-7b", "abcd")
    # Pointing the same key at a different endpoint drops it.
    data = _put(client, auth, provider="custom", base_url="https://other.example.com", model="coach-7b")
    assert data["has_api_key"] is False
    # Leaving custom clears its endpoint.
    assert _put(client, auth, provider="openai")["base_url"] is None


def test_custom_openai_format_uses_its_url(client, auth, monkeypatch):
    init = {}

    class FakeOpenAI:
        def __init__(self, **kw):
            init.update(kw)
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=lambda **k: _completion(content="yo")))

    monkeypatch.setattr(ai_coach.openai, "OpenAI", FakeOpenAI)
    _put(client, auth, provider="custom", base_url="https://api.intebec.example/ai/v1", model="m1", api_key="iag_live_x")
    assert client.post("/api/chat", json={"message": "hi"}, headers=auth).json()["reply"] == "yo"
    assert init == {"api_key": "iag_live_x", "base_url": "https://api.intebec.example/ai/v1"}


def test_custom_anthropic_format_uses_its_url(client, auth, monkeypatch):
    init = {}

    class FakeAnthropic:
        def __init__(self, **kw):
            init.update(kw)
            self.messages = SimpleNamespace(create=lambda **k: text_response("salut"))

    monkeypatch.setattr(ai_coach.anthropic, "Anthropic", FakeAnthropic)
    _put(client, auth, provider="custom", base_url="https://proxy.example.com", model="m2",
         api_format="anthropic", api_key="k")
    assert client.post("/api/chat", json={"message": "hi"}, headers=auth).json()["reply"] == "salut"
    assert init == {"api_key": "k", "base_url": "https://proxy.example.com"}


def test_custom_errors_name_the_host(client, auth, monkeypatch):
    def boom(**_):
        raise _status_error(openai, openai.AuthenticationError, 401)

    fake = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=boom)))
    monkeypatch.setattr(ai_coach.openai, "OpenAI", lambda **_: fake)
    _put(client, auth, provider="custom", base_url="https://api.intebec.example/ai", model="m", api_key="k")
    detail = client.post("/api/chat", json={"message": "hi"}, headers=auth).json()["detail"]
    assert "api.intebec.example" in detail and "rejected the API key" in detail
