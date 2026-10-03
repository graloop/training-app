from types import SimpleNamespace

import anthropic
import httpx
import openai
import pytest
from conftest import text_response

from app import ai_coach, models
from app.database import SessionLocal


def _openai_fake(monkeypatch, reply="OK", error=None):
    seen = {"init": None, "calls": []}

    class FakeOpenAI:
        def __init__(self, **kw):
            seen["init"] = kw

            def create(**k):
                seen["calls"].append(k)
                if error:
                    raise error
                msg = SimpleNamespace(content=reply, tool_calls=None)
                return SimpleNamespace(choices=[SimpleNamespace(message=msg)])

            self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))

    monkeypatch.setattr(ai_coach.openai, "OpenAI", FakeOpenAI)
    return seen


def _anthropic_fake(monkeypatch, reply="OK"):
    seen = {"init": None, "calls": []}

    class FakeAnthropic:
        def __init__(self, **kw):
            seen["init"] = kw
            self.messages = SimpleNamespace(create=lambda **k: seen["calls"].append(k) or text_response(reply))

    monkeypatch.setattr(ai_coach.anthropic, "Anthropic", FakeAnthropic)
    return seen


def _test(client, auth, **body):
    return client.post("/api/ai-settings/test", json=body, headers=auth)


def test_unsaved_form_values_are_tested_and_not_saved(client, auth, monkeypatch):
    seen = _openai_fake(monkeypatch, reply="OK")
    res = _test(client, auth, provider="openai", model="gpt-5-mini", api_key="sk-typed")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["ok"] is True and body["provider"] == "ChatGPT (OpenAI)" and body["model"] == "gpt-5-mini"
    assert body["reply"] == "OK" and body["latency_ms"] >= 0
    assert seen["init"]["api_key"] == "sk-typed"
    assert seen["init"]["timeout"] == ai_coach.TEST_TIMEOUT_SECONDS and seen["init"]["max_retries"] == 0
    assert "tools" not in seen["calls"][0]
    with SessionLocal() as db:
        assert db.query(models.AISettings).count() == 0  # testing never saves


def test_empty_key_field_reuses_the_saved_key(client, auth, monkeypatch):
    client.put("/api/ai-settings", json={"provider": "openai", "api_key": "sk-saved"}, headers=auth)
    seen = _openai_fake(monkeypatch)
    assert _test(client, auth, provider="openai").status_code == 200
    assert seen["init"]["api_key"] == "sk-saved"


def test_saved_key_is_not_sent_to_another_provider(client, auth, monkeypatch):
    client.put("/api/ai-settings", json={"provider": "openai", "api_key": "sk-saved"}, headers=auth)
    _openai_fake(monkeypatch)
    res = _test(client, auth, provider="mistral")
    assert res.status_code == 400 and "No API key for Mistral" in res.json()["detail"]


def test_saved_custom_key_not_sent_to_a_different_url(client, auth, monkeypatch):
    client.put("/api/ai-settings", json={"provider": "custom", "base_url": "https://a.example.com/v1", "model": "m",
                                         "api_key": "iag_live_saved"}, headers=auth)
    seen = _openai_fake(monkeypatch)
    assert _test(client, auth, provider="custom", base_url="https://a.example.com/v1", model="m").status_code == 200
    assert seen["init"] == {"api_key": "iag_live_saved", "base_url": "https://a.example.com/v1",
                            "timeout": ai_coach.TEST_TIMEOUT_SECONDS, "max_retries": 0}
    res = _test(client, auth, provider="custom", base_url="https://evil.example.com/v1", model="m")
    assert res.status_code == 400  # no key travels to an endpoint it wasn't saved for


def test_claude_uses_server_key_and_native_sdk(client, auth, monkeypatch):
    seen = _anthropic_fake(monkeypatch, reply="OK")
    res = _test(client, auth, provider="anthropic")
    assert res.status_code == 200
    assert res.json()["provider"] == "Claude (Anthropic)" and res.json()["model"] == "claude-opus-5-5"
    assert seen["init"]["api_key"] == "sk-ant-test"  # server-wide fallback from conftest
    assert seen["init"]["max_retries"] == 0 and "tools" not in seen["calls"][0]


def test_custom_anthropic_format(client, auth, monkeypatch):
    seen = _anthropic_fake(monkeypatch)
    res = _test(client, auth, provider="custom", base_url="https://proxy.example.com", model="m2",
                api_format="anthropic", api_key="k")
    assert res.status_code == 200 and res.json()["provider"] == "Custom AI (proxy.example.com)"
    assert seen["init"]["base_url"] == "https://proxy.example.com"


def _status_error(cls, code):
    request = httpx.Request("POST", "https://example.invalid")
    return cls("boom", response=httpx.Response(code, request=request), body=None)


@pytest.mark.parametrize("error,status,phrase", [
    (_status_error(openai.AuthenticationError, 401), 400, "rejected the API key"),
    (_status_error(openai.NotFoundError, 404), 400, "doesn't know the model"),
    (openai.APIConnectionError(request=httpx.Request("POST", "https://example.invalid")), 502, "Couldn't reach"),
    (openai.APITimeoutError(request=httpx.Request("POST", "https://example.invalid")), 502, "Couldn't reach"),
])
def test_failures_explain_why(client, auth, monkeypatch, error, status, phrase):
    _openai_fake(monkeypatch, error=error)
    res = _test(client, auth, provider="openai", api_key="sk-x")
    assert res.status_code == status and phrase in res.json()["detail"]


def test_invalid_form_rejected_before_any_call(client, auth, monkeypatch):
    seen = _openai_fake(monkeypatch)
    assert _test(client, auth, provider="custom", base_url="ftp://x", model="m", api_key="k").status_code == 400
    assert _test(client, auth, provider="skynet").status_code == 400
    assert seen["calls"] == []
