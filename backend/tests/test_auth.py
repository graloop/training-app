from conftest import ADMIN, GARMIN_PASSWORD, USER_PASSWORD, admin_auth, login, make_user

from app import accounts, models
from app.database import SessionLocal


def test_health(client):
    assert client.get("/api/health").json() == {"status": "ok"}


def test_frontend_is_served(client):
    res = client.get("/")
    assert res.status_code == 200
    assert 'id="auth-username"' in res.text


# --- Admin bootstrap & login ---------------------------------------------------


def test_admin_is_created_from_env(client):
    with SessionLocal() as db:
        admin = db.query(models.User).one()
        assert (admin.username, admin.is_admin) == ("admin", True)
        assert admin.hashed_password.startswith("scrypt$") and "admin-password" not in admin.hashed_password
    me = client.get("/api/auth/me", headers=admin_auth(client)).json()
    assert me["username"] == "admin" and me["is_admin"] is True and me["garmin_connected"] is False


def test_restart_does_not_reset_a_changed_admin_password(client):
    headers = admin_auth(client)
    res = client.post("/api/auth/password", json={"current_password": ADMIN[1], "new_password": "brand-new-pass"},
                      headers=headers)
    assert res.status_code == 200
    accounts.bootstrap_admin()  # what the next startup does
    assert client.post("/api/auth/login", json={"username": "admin", "password": ADMIN[1]}).status_code == 401
    login(client, "admin", "brand-new-pass")


def test_bootstrap_promotes_an_existing_passwordless_account(client, monkeypatch):
    # e.g. an account from the older Garmin-login version of the app
    with SessionLocal() as db:
        db.add(models.User(username="me@example.com", hashed_password=""))
        db.commit()
    settings = accounts.get_settings()
    monkeypatch.setattr(settings, "admin_username", " Me@Example.com ")
    accounts.bootstrap_admin()
    with SessionLocal() as db:
        assert db.query(models.User).filter_by(username="me@example.com").one().is_admin
    login(client, "me@example.com", ADMIN[1])


def test_wrong_password_and_unknown_user_look_the_same(client):
    for username in ("admin", "nobody"):
        res = client.post("/api/auth/login", json={"username": username, "password": "wrong-password"})
        assert res.status_code == 401
        assert res.json()["detail"] == "Wrong username or password"


def test_passwordless_account_cannot_log_in(client):
    with SessionLocal() as db:
        db.add(models.User(username="legacy", hashed_password=""))
        db.commit()
    assert client.post("/api/auth/login", json={"username": "legacy", "password": ""}).status_code == 422
    assert client.post("/api/auth/login", json={"username": "legacy", "password": "x"}).status_code == 401


def test_login_is_locked_after_repeated_failures(client):
    make_user(client, "friend", garmin=False)
    for _ in range(accounts.MAX_FAILURES):
        assert client.post("/api/auth/login", json={"username": "friend", "password": "nope"}).status_code == 401
    res = client.post("/api/auth/login", json={"username": "friend", "password": USER_PASSWORD})
    assert res.status_code == 429  # even the right password, until the window passes
    # The admin resetting the password unlocks the account.
    friend_id = next(u["id"] for u in client.get("/api/admin/users", headers=admin_auth(client)).json()["users"]
                     if u["username"] == "friend")
    client.patch(f"/api/admin/users/{friend_id}", json={"password": "reset-password"}, headers=admin_auth(client))
    login(client, "friend", "reset-password")


def test_usernames_are_case_insensitive(client):
    make_user(client, "friend", garmin=False)
    login(client, "  FRIEND ")


def test_there_is_no_signup(client):
    for path in ["/api/auth/signup", "/api/auth/register"]:
        assert client.post(path, json={"username": "x", "password": "y" * 8}).status_code in (404, 405)


def test_protected_routes_require_token(client):
    for path in ["/api/calendar", "/api/objectives", "/api/auth/me", "/api/admin/users", "/api/admin/activity"]:
        assert client.get(path).status_code == 401
    assert client.get("/api/calendar", headers={"Authorization": "Bearer garbage"}).status_code == 401


def test_tokens_from_the_old_garmin_login_are_rejected(client):
    import jwt

    from app.config import get_settings

    old = jwt.encode({"sub": "athlete@example.com"}, get_settings().jwt_secret, algorithm="HS256")
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {old}"}).status_code == 401


# --- Admin user management --------------------------------------------------------


def test_admin_creates_lists_and_deletes_users(client):
    admin = admin_auth(client)
    res = client.post("/api/admin/users", json={"username": "Marie.T", "password": "marie-password"}, headers=admin)
    assert res.status_code == 422  # usernames are lowercase
    res = client.post("/api/admin/users", json={"username": "marie.t", "password": "marie-password"}, headers=admin)
    assert res.status_code == 201
    marie = res.json()
    assert marie["is_admin"] is False and marie["has_password"] is True and marie["garmin_connected"] is False
    assert client.post("/api/admin/users", json={"username": "marie.t", "password": "other-password"},
                       headers=admin).status_code == 409
    assert client.post("/api/admin/users", json={"username": "short", "password": "1234567"},
                       headers=admin).status_code == 422  # 8+ characters

    names = [u["username"] for u in client.get("/api/admin/users", headers=admin).json()["users"]]
    assert names == ["admin", "marie.t"]
    assert "hashed_password" not in client.get("/api/admin/users", headers=admin).text

    marie_auth = login(client, "marie.t", "marie-password")
    client.post("/api/objectives", json={"title": "Triathlon"}, headers=marie_auth)
    assert client.delete(f"/api/admin/users/{marie['id']}", params={"confirm": "marie.t"}, headers=admin).status_code == 204
    assert client.get("/api/auth/me", headers=marie_auth).status_code == 401  # session dies with the account
    with SessionLocal() as db:
        assert db.query(models.Objective).count() == 0  # and so does the data


def test_friends_cannot_use_admin_endpoints(client):
    friend = make_user(client, "friend", garmin=False)
    assert client.get("/api/admin/users", headers=friend).status_code == 403
    assert client.post("/api/admin/users", json={"username": "sneaky", "password": "sneaky-pass"},
                       headers=friend).status_code == 403
    assert client.patch("/api/admin/users/1", json={"password": "hijacked-pass"}, headers=friend).status_code == 403


def test_admin_reset_logs_the_user_out(client):
    friend = make_user(client, "friend", garmin=False)
    friend_id = client.get("/api/auth/me", headers=friend).json()["id"]
    res = client.patch(f"/api/admin/users/{friend_id}", json={"password": "new-password"}, headers=admin_auth(client))
    assert res.status_code == 200
    assert client.get("/api/auth/me", headers=friend).status_code == 401
    login(client, "friend", "new-password")


def test_admin_can_grant_admin_but_not_demote_or_delete_self(client):
    admin = admin_auth(client)
    admin_id = client.get("/api/auth/me", headers=admin).json()["id"]
    assert client.patch(f"/api/admin/users/{admin_id}", json={"is_admin": False}, headers=admin).status_code == 400
    assert client.delete(f"/api/admin/users/{admin_id}", headers=admin).status_code == 400

    friend = make_user(client, "friend", garmin=False)
    friend_id = client.get("/api/auth/me", headers=friend).json()["id"]
    client.patch(f"/api/admin/users/{friend_id}", json={"is_admin": True}, headers=admin)
    assert client.get("/api/admin/users", headers=friend).status_code == 200


# --- Own password -----------------------------------------------------------------------


def test_change_own_password(client):
    friend = make_user(client, "friend", garmin=False)
    bad = client.post("/api/auth/password", json={"current_password": "wrong", "new_password": "something-new"},
                      headers=friend)
    assert bad.status_code == 400
    res = client.post("/api/auth/password",
                      json={"current_password": USER_PASSWORD, "new_password": "something-new"}, headers=friend)
    assert res.status_code == 200
    assert client.get("/api/auth/me", headers=friend).status_code == 401  # old sessions are logged out
    new = {"Authorization": f"Bearer {res.json()['access_token']}"}
    assert client.get("/api/auth/me", headers=new).status_code == 200  # this one continues
    login(client, "friend", "something-new")


# --- Garmin connect ------------------------------------------------------------------


def test_each_user_connects_their_own_garmin(client):
    friend = make_user(client, "friend", garmin=False)
    assert client.post("/api/garmin/sync", headers=friend).status_code == 409

    res = client.post("/api/garmin/connect", json={"garmin_email": "a@example.com", "garmin_password": "wrong"},
                      headers=friend)
    assert res.status_code == 400  # not 401: a bad Garmin password doesn't log you out of the app
    assert "Garmin login failed" in res.json()["detail"]

    res = client.post("/api/garmin/connect",
                      json={"garmin_email": "Friend@Garmin.com", "garmin_password": GARMIN_PASSWORD}, headers=friend)
    assert res.status_code == 200
    assert res.json()["garmin_connected"] is True and res.json()["garmin_email"] == "friend@garmin.com"
    with SessionLocal() as db:
        user = db.query(models.User).filter_by(username="friend").one()
        assert user.garmin_session_encrypted and "session-for" not in user.garmin_session_encrypted
        assert GARMIN_PASSWORD not in user.garmin_session_encrypted + user.hashed_password

    res = client.delete("/api/garmin/connect", headers=friend)
    assert res.json()["garmin_connected"] is False


def test_users_are_isolated(client, auth):
    client.post("/api/objectives", json={"title": "mine"}, headers=auth)
    other = make_user(client, "other")
    assert client.get("/api/objectives", headers=other).json() == {"objectives": []}


# --- Security headers, no chatbot ---------------------------------------------------


def test_security_headers_and_no_public_api_docs(client):
    res = client.get("/")
    assert res.headers["x-frame-options"] == "DENY"
    assert res.headers["x-content-type-options"] == "nosniff"
    assert client.get("/api/health").headers["cache-control"] == "no-store"
    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 404


def test_there_is_no_built_in_chatbot(client, auth):
    """The app is driven from the page (by hand or with Claude in Chrome): no chat
    or AI-key endpoints, no AI SDKs, nothing about it on the page."""
    from pathlib import Path

    backend = Path(__file__).resolve().parents[1]
    for path in ["/api/chat", "/api/chat/history", "/api/ai-settings", "/api/ai-settings/test", "/api/profile/notes"]:
        assert client.get(path, headers=auth).status_code == 404
    assert client.post("/api/chat", json={"message": "hi"}, headers=auth).status_code in (404, 405)
    assert "ai_chat_enabled" not in client.get("/api/auth/me", headers=auth).json()

    requirements = (backend / "requirements.txt").read_text().lower()
    assert "anthropic" not in requirements and "openai" not in requirements
    page = "".join((backend / "frontend" / name).read_text().lower() for name in ("index.html", "app.js"))
    for word in ("chat", "api key", "anthropic", "openai", "notes for"):
        assert word not in page, word


def test_frontend_is_revalidated_by_browsers(client):
    for path in ["/", "/app.js", "/styles.css"]:
        res = client.get(path)
        assert res.status_code == 200
        assert res.headers["cache-control"] == "no-cache"
    etag = client.get("/app.js").headers["etag"]
    assert client.get("/app.js", headers={"If-None-Match": etag}).status_code == 304  # unchanged = cheap


def test_index_links_versioned_assets(client):
    import os
    import re

    from app import main

    html = client.get("/").text
    for asset in ("app.js", "styles.css"):
        version = int(os.path.getmtime(os.path.join(main._FRONTEND_DIR, asset)))
        assert f'"/{asset}?v={version}"' in html
    assert re.search(r'src="/app\.js\?v=\d+"', client.get("/index.html").text)
    assert client.get("/app.js?v=123").status_code == 200  # the query string is ignored when serving


def test_every_page_shows_the_logo_author_and_version(client):
    from app.version import VERSION

    html = client.get("/").text
    assert 'class="logo" role="img" aria-label="graloop"' in html
    assert f"Created by Julien Tremblay · Version {VERSION}</footer>" in html
    assert "__APP_VERSION__" not in html and "__APP_VERSION__" not in client.get("/index.html").text
