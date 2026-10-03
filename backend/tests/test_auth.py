from conftest import login

from app import models
from app.database import SessionLocal


def test_health(client):
    assert client.get("/api/health").json() == {"status": "ok"}


def test_frontend_is_served(client):
    res = client.get("/")
    assert res.status_code == 200
    assert "Log in with Garmin Connect" in res.text


def test_first_garmin_login_creates_account_with_session(client):
    login(client)
    with SessionLocal() as db:
        user = db.query(models.User).one()
        assert user.email == user.garmin_email == "athlete@example.com"
        # Stored encrypted, never in plaintext; the password isn't stored at all.
        assert user.garmin_session_encrypted
        assert "session-for" not in user.garmin_session_encrypted
        assert "garmin-pass" not in (user.hashed_password + user.garmin_session_encrypted)


def test_repeat_login_reuses_account_and_refreshes_session(client):
    login(client)
    with SessionLocal() as db:
        user = db.query(models.User).one()
        db.add(models.Objective(user_id=user.id, title="keep me"))
        db.commit()
        old_session = user.garmin_session_encrypted

    second = login(client, email="Athlete@Example.com")  # case-insensitive, same account
    assert [o["title"] for o in client.get("/api/objectives", headers=second).json()["objectives"]] == ["keep me"]
    with SessionLocal() as db:
        assert db.query(models.User).count() == 1
        assert db.query(models.User).one().garmin_session_encrypted != old_session


def test_wrong_garmin_password(client):
    res = client.post("/api/auth/login", json={"email": "athlete@example.com", "password": "wrong"})
    assert res.status_code == 401
    assert "Garmin login failed" in res.json()["detail"]
    with SessionLocal() as db:
        assert db.query(models.User).count() == 0


def test_signup_and_connect_are_gone(client, auth):
    assert client.post("/api/auth/signup", json={"email": "a@example.com", "password": "x" * 8}).status_code in (404, 405)
    res = client.post("/api/garmin/connect", json={"garmin_email": "a@example.com", "garmin_password": "x"}, headers=auth)
    assert res.status_code in (404, 405)


def test_protected_routes_require_token(client):
    for path in ["/api/calendar", "/api/objectives", "/api/chat/history"]:
        assert client.get(path).status_code == 401
    assert client.get("/api/calendar", headers={"Authorization": "Bearer garbage"}).status_code == 401


def test_users_are_isolated(client, auth):
    with SessionLocal() as db:
        db.add(models.Objective(user_id=db.query(models.User).one().id, title="mine"))
        db.commit()
    other = login(client, email="other@example.com")
    assert client.get("/api/objectives", headers=other).json() == {"objectives": []}


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
