"""The login Claude goes through to use the connector (app/mcp/oauth.py): the
discovery documents, client registration, the login page, and trading codes
and refresh tokens for tokens. conftest.py turns the connector on for the
account "athlete"."""

import base64
import hashlib
import secrets
from urllib.parse import parse_qs, urlparse

from conftest import ADMIN, USER_PASSWORD, admin_auth, make_user

from app import main, models
from app.database import SessionLocal
from app.mcp import auth as mcp_auth
from app.mcp import oauth

CALLBACK = "https://claude.ai/api/mcp/auth_callback"


def _register(client, uris=(CALLBACK,)):
    return client.post("/oauth/register", json={"redirect_uris": list(uris), "client_name": "Claude"})


def _pkce():
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def _asked(client_id, challenge, **changes):
    return {"client_id": client_id, "redirect_uri": CALLBACK, "response_type": "code",
            "code_challenge": challenge, "code_challenge_method": "S256", "state": "xyz", **changes}


def _log_in(client, client_id, challenge, username="athlete", password=USER_PASSWORD):
    form = {**_asked(client_id, challenge), "username": username, "password": password}
    return client.post("/oauth/authorize", data=form, follow_redirects=False)


def _code(client, client_id, challenge):
    res = _log_in(client, client_id, challenge)
    assert res.status_code == 302, res.text
    answer = parse_qs(urlparse(res.headers["location"]).query)
    assert res.headers["location"].startswith(CALLBACK + "?") and answer["state"] == ["xyz"]
    return answer["code"][0]


def _trade(client, client_id, code, verifier, **changes):
    form = {"grant_type": "authorization_code", "code": code, "redirect_uri": CALLBACK,
            "client_id": client_id, "code_verifier": verifier, **(changes.get("other") or {})}
    return client.post("/oauth/token", data=form)


def connect(client):
    """The whole flow for the account "athlete" (which must exist). Returns (client_id, tokens)."""
    client_id = _register(client).json()["client_id"]
    verifier, challenge = _pkce()
    res = _trade(client, client_id, _code(client, client_id, challenge), verifier)
    assert res.status_code == 200, res.text
    return client_id, res.json()


def _mcp_status(client, tokens):
    return client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
                       headers={"Authorization": f"Bearer {tokens['access_token']}",
                                "Accept": "application/json, text/event-stream"}).status_code


# --- Discovery and registration ----------------------------------------------------


def test_metadata_says_where_to_log_in(client):
    for path in ("/.well-known/oauth-protected-resource", "/.well-known/oauth-protected-resource/mcp"):
        assert client.get(path).json() == {"resource": "http://testserver/mcp",
                                           "authorization_servers": ["http://testserver"]}
    meta = client.get("/.well-known/oauth-authorization-server").json()
    assert meta["issuer"] == "http://testserver"
    assert meta["authorization_endpoint"] == "http://testserver/oauth/authorize"
    assert meta["token_endpoint"] == "http://testserver/oauth/token"
    assert meta["registration_endpoint"] == "http://testserver/oauth/register"
    assert meta["code_challenge_methods_supported"] == ["S256"]
    assert meta["token_endpoint_auth_methods_supported"] == ["none"]
    assert meta["grant_types_supported"] == ["authorization_code", "refresh_token"]


def test_metadata_uses_the_public_address(client, monkeypatch):
    monkeypatch.setattr(mcp_auth.settings, "app_domain", "training.example.com")
    res = client.get("/.well-known/oauth-protected-resource", headers={"Host": "localhost"})
    assert res.json()["resource"] == "https://training.example.com/mcp"


def test_only_claudes_callbacks_can_register(client):
    for uris in (["https://claude.ai/api/mcp/auth_callback"], ["https://claude.com/api/mcp/auth_callback"]):
        res = _register(client, uris)
        assert res.status_code == 201 and res.json()["redirect_uris"] == uris and res.json()["client_id"]
    for uris in (["https://evil.example/callback"], [CALLBACK, "https://evil.example/callback"], [], "nope"):
        assert _register(client, uris).status_code == 400, uris
    assert client.post("/oauth/register", content=b"not json").status_code == 400


def test_registered_clients_are_capped(client, auth, monkeypatch):
    """Registering needs no login: old clients nobody uses make room, a connected one stays."""
    monkeypatch.setattr(oauth, "MAX_CLIENTS", 3)
    connected, _tokens = connect(client)
    for _ in range(6):
        assert _register(client).status_code == 201
    with SessionLocal() as db:
        kept = [c.client_id for c in db.query(models.McpClient)]
    assert len(kept) == 3 and connected in kept


# --- The login page ----------------------------------------------------------------


def test_login_page_lets_the_form_go_back_to_claude(client):
    client_id = _register(client).json()["client_id"]
    res = client.get("/oauth/authorize", params=_asked(client_id, _pkce()[1]))
    assert res.status_code == 200 and 'name="password"' in res.text and 'value="xyz"' in res.text
    # The app's policy with one change: form-action also allows Claude.
    assert res.headers["content-security-policy"] == oauth.AUTHORIZE_CSP
    assert oauth.AUTHORIZE_CSP == main.CONTENT_SECURITY_POLICY.replace(
        "form-action 'self'", "form-action 'self' https://claude.ai https://claude.com")
    assert res.headers["cache-control"] == "no-store"
    # ... and only there.
    assert client.get("/").headers["content-security-policy"] == main.CONTENT_SECURITY_POLICY


def test_login_page_refuses_requests_claude_did_not_make(client):
    client_id = _register(client).json()["client_id"]
    challenge = _pkce()[1]
    for changes in ({"client_id": "unknown"}, {"redirect_uri": "https://evil.example/callback"},
                    {"redirect_uri": "https://claude.com/api/mcp/auth_callback"},  # allowed, but not this client's
                    {"response_type": "token"}, {"code_challenge_method": "plain"}, {"code_challenge": "short"},
                    {"state": "s" * 2000}):
        asked = {**_asked(client_id, challenge), **changes}
        assert client.get("/oauth/authorize", params=asked).status_code == 400, changes
        res = client.post("/oauth/authorize", data={**asked, "username": "athlete", "password": USER_PASSWORD},
                          follow_redirects=False)
        assert res.status_code == 400 and "location" not in res.headers, changes


def test_page_values_are_escaped(client):
    client_id = _register(client).json()["client_id"]
    res = client.get("/oauth/authorize", params=_asked(client_id, _pkce()[1], state='"><script>alert(1)</script>'))
    assert res.status_code == 200 and "<script>alert" not in res.text


# --- Who may connect ------------------------------------------------------------------


def test_full_flow_gives_tokens_that_open_mcp(client, auth):
    _client_id, tokens = connect(client)
    assert tokens["token_type"] == "Bearer" and tokens["expires_in"] == 3600
    assert tokens["access_token"] != tokens["refresh_token"]
    assert _mcp_status(client, tokens) == 200
    with SessionLocal() as db:  # nothing usable is stored
        stored = {t.token_hash for t in db.query(models.McpToken)}
        assert stored == {mcp_auth.token_hash(tokens["access_token"]), mcp_auth.token_hash(tokens["refresh_token"])}
        assert db.query(models.McpAuthCode).one().used_at is not None


def test_login_is_in_the_connection_log(client, auth):
    connect(client)
    with SessionLocal() as db:
        events = db.query(models.LoginEvent).filter(models.LoginEvent.username == "athlete").all()
    assert len(events) == 2 and all(e.success for e in events)  # the page login (fixture) and the connector's


def test_wrong_password_another_account_and_admin_are_refused(client, auth):
    make_user(client, "friend", garmin=False)
    client_id = _register(client).json()["client_id"]
    challenge = _pkce()[1]
    for username, password in (("athlete", "wrong-password"), ("friend", USER_PASSWORD), ADMIN, ("nobody", "x")):
        res = _log_in(client, client_id, challenge, username, password)
        assert res.status_code == 401 and "location" not in res.headers, username
        assert "can&#x27;t use the connector" in res.text
    with SessionLocal() as db:
        assert db.query(models.McpAuthCode).count() == 0


def test_login_page_locks_like_the_normal_login(client, auth):
    client_id = _register(client).json()["client_id"]
    challenge = _pkce()[1]
    for _ in range(5):
        assert _log_in(client, client_id, challenge, password="wrong-password").status_code == 401
    assert _log_in(client, client_id, challenge).status_code == 429  # even with the right password


def test_an_account_that_became_admin_loses_the_connector(client, auth):
    _client_id, tokens = connect(client)
    with SessionLocal() as db:
        db.query(models.User).filter(models.User.username == "athlete").one().is_admin = True
        db.commit()
    assert _mcp_status(client, tokens) == 401


# --- Codes and tokens ---------------------------------------------------------------


def test_wrong_verifier_client_or_callback_is_rejected(client, auth):
    client_id = _register(client).json()["client_id"]
    other_client = _register(client).json()["client_id"]
    for changes in ({"code_verifier": "not-the-verifier"}, {"client_id": other_client},
                    {"redirect_uri": "https://claude.com/api/mcp/auth_callback"}):
        verifier, challenge = _pkce()
        code = _code(client, client_id, challenge)
        res = _trade(client, client_id, code, verifier, other=changes)
        assert res.status_code == 400 and res.json()["error"] == "invalid_grant", changes
        # The failed try used the code up.
        assert _trade(client, client_id, code, verifier).status_code == 400


def test_a_code_works_once_and_not_after_five_minutes(client, auth):
    client_id = _register(client).json()["client_id"]
    verifier, challenge = _pkce()
    code = _code(client, client_id, challenge)
    assert _trade(client, client_id, code, verifier).status_code == 200
    assert _trade(client, client_id, code, verifier).status_code == 400

    verifier, challenge = _pkce()
    code = _code(client, client_id, challenge)
    with SessionLocal() as db:
        row = db.query(models.McpAuthCode).filter(models.McpAuthCode.used_at.is_(None)).one()
        row.expires_at = mcp_auth.expiry(minutes=-1)
        db.commit()
    assert _trade(client, client_id, code, verifier).status_code == 400


def test_refresh_gives_new_tokens_and_the_old_one_stops(client, auth):
    client_id, tokens = connect(client)
    form = {"grant_type": "refresh_token", "refresh_token": tokens["refresh_token"], "client_id": client_id}
    res = client.post("/oauth/token", data=form)
    assert res.status_code == 200
    fresh = res.json()
    assert fresh["refresh_token"] != tokens["refresh_token"] and _mcp_status(client, fresh) == 200
    assert client.post("/oauth/token", data=form).status_code == 400  # the old refresh token
    assert client.post("/oauth/token", data={**form, "refresh_token": fresh["refresh_token"],
                                             "client_id": "someone-else"}).status_code == 400
    assert client.post("/oauth/token", data={**form, "refresh_token": tokens["access_token"]}).status_code == 400
    assert client.post("/oauth/token", data={"grant_type": "password"}).json()["error"] == "unsupported_grant_type"


def test_an_expired_access_token_is_refused(client, auth):
    _client_id, tokens = connect(client)
    with SessionLocal() as db:
        db.query(models.McpToken).filter(models.McpToken.kind == "access").one().expires_at = mcp_auth.expiry(minutes=-1)
        db.commit()
    assert _mcp_status(client, tokens) == 401


def test_a_password_change_disconnects_claude(client, auth):
    client_id, tokens = connect(client)
    res = client.post("/api/auth/password", json={"current_password": USER_PASSWORD,
                                                  "new_password": "another-password-1"}, headers=auth)
    assert res.status_code == 200
    assert _mcp_status(client, tokens) == 401
    form = {"grant_type": "refresh_token", "refresh_token": tokens["refresh_token"], "client_id": client_id}
    assert client.post("/oauth/token", data=form).status_code == 400


def test_deleting_the_account_deletes_its_tokens(client, auth):
    connect(client)
    with SessionLocal() as db:
        user_id = db.query(models.User).filter(models.User.username == "athlete").one().id
    res = client.delete(f"/api/admin/users/{user_id}", params={"confirm": "athlete"}, headers=admin_auth(client))
    assert res.status_code == 204
    with SessionLocal() as db:
        assert db.query(models.McpToken).count() == 0 and db.query(models.McpAuthCode).count() == 0
