import datetime

from conftest import USER_PASSWORD, admin_auth, login, make_user

from app import accounts, models
from app.database import SessionLocal

UTC = datetime.timezone.utc


def _activity(client, headers=None, **params):
    res = client.get("/api/admin/activity", params=params, headers=headers or admin_auth(client))
    assert res.status_code == 200, res.text
    return res.json()


def _add_event(username, when, success=True):
    with SessionLocal() as db:
        user = db.query(models.User).filter_by(username=username).first()
        db.add(models.LoginEvent(user_id=user.id if user else None, username=username, success=success,
                                 created_at=when))
        db.commit()


# --- Admin accounts only manage ---------------------------------------------------


def test_admin_has_no_calendar_plan_objectives_or_garmin(client):
    admin = admin_auth(client)
    for method, path, body in [
        ("get", "/api/calendar", None),
        ("get", "/api/objectives", None),
        ("post", "/api/objectives", {"title": "x"}),
        ("post", "/api/planned", {"date": "2027-01-01", "activity_type": "running", "planned_duration_minutes": 30}),
        ("post", "/api/garmin/sync", None),
        ("post", "/api/garmin/connect", {"garmin_email": "a@example.com", "garmin_password": "x"}),
    ]:
        res = getattr(client, method)(path, headers=admin, **({"json": body} if body else {}))
        assert res.status_code == 403, (path, res.status_code)
        assert "Admin accounts manage users" in res.json()["detail"]
    assert client.get("/api/auth/me", headers=admin).json()["is_admin"] is True  # still logged in


def test_regular_users_keep_the_app_but_not_the_dashboard(client, auth):
    assert client.get("/api/calendar", headers=auth).status_code == 200
    assert client.get("/api/objectives", headers=auth).status_code == 200
    assert client.get("/api/admin/activity", headers=auth).status_code == 403
    assert client.get("/api/admin/users", headers=auth).status_code == 403


def test_granting_admin_swaps_the_calendar_for_the_dashboard(client, auth):
    client.post("/api/objectives", json={"title": "kept"}, headers=auth)
    user_id = client.get("/api/auth/me", headers=auth).json()["id"]
    client.patch(f"/api/admin/users/{user_id}", json={"is_admin": True}, headers=admin_auth(client))
    assert client.get("/api/calendar", headers=auth).status_code == 403
    assert client.get("/api/admin/activity", headers=auth).status_code == 200
    # Taking admin back returns the calendar, with the data untouched.
    client.patch(f"/api/admin/users/{user_id}", json={"is_admin": False}, headers=admin_auth(client))
    assert [o["title"] for o in client.get("/api/objectives", headers=auth).json()["objectives"]] == ["kept"]


# --- Last login ------------------------------------------------------------------------


def test_accounts_list_shows_last_login(client):
    admin = admin_auth(client)
    client.post("/api/admin/users", json={"username": "never", "password": USER_PASSWORD}, headers=admin)
    before = datetime.datetime.now(UTC)
    make_user(client, "friend", garmin=False)

    users = {u["username"]: u for u in client.get("/api/admin/users", headers=admin).json()["users"]}
    assert users["never"]["last_login_at"] is None
    last = datetime.datetime.fromisoformat(users["friend"]["last_login_at"])
    assert last.tzinfo is not None  # carries its offset, so browsers don't read it as local time
    assert before - datetime.timedelta(seconds=5) <= last <= datetime.datetime.now(UTC)
    assert datetime.datetime.fromisoformat(users["admin"]["last_login_at"]).tzinfo is not None
    assert datetime.datetime.fromisoformat(users["friend"]["created_at"]).tzinfo is not None


def test_failed_login_does_not_count_as_last_login(client):
    admin = admin_auth(client)
    client.post("/api/admin/users", json={"username": "friend", "password": USER_PASSWORD}, headers=admin)
    client.post("/api/auth/login", json={"username": "friend", "password": "wrong-password"})
    users = {u["username"]: u for u in client.get("/api/admin/users", headers=admin).json()["users"]}
    assert users["friend"]["last_login_at"] is None


# --- Connections of the last week ------------------------------------------------------


def test_activity_counts_logins_and_failures(client):
    make_user(client, "friend", garmin=False)          # admin login + friend login
    login(client, "friend")                              # friend again
    client.post("/api/auth/login", json={"username": "friend", "password": "wrong-password"})
    client.post("/api/auth/login", json={"username": "hunter2-typed-in-the-wrong-box", "password": "x"})

    data = _activity(client)                             # one more admin login
    assert len(data["days"]) == 7
    assert [d["date"] for d in data["days"]] == sorted(d["date"] for d in data["days"])
    today = data["days"][-1]
    assert today["date"] == datetime.datetime.now(UTC).date().isoformat()
    assert (today["logins"], today["failed"]) == (4, 2)
    assert all((d["logins"], d["failed"]) == (0, 0) for d in data["days"][:-1])
    assert data["totals"] == {"accounts": 2, "active_accounts": 2, "logins": 4, "failed": 2}

    recent = data["recent"]
    assert [e["success"] for e in recent] == [True, False, False, True, True, True]  # newest first
    assert recent[0]["username"] == "admin" and recent[2]["username"] == "friend"
    # A name that isn't an account is never stored: it may be a password.
    assert recent[1]["username"] == accounts.UNKNOWN_ACCOUNT
    assert "hunter2" not in str(data)
    assert all(datetime.datetime.fromisoformat(e["at"]).tzinfo is not None for e in recent)


def test_activity_window_is_the_last_seven_days(client):
    make_user(client, "friend", garmin=False)
    now = datetime.datetime.now(UTC)
    _add_event("friend", now - datetime.timedelta(days=3))
    _add_event("friend", now - datetime.timedelta(days=3), success=False)
    _add_event("friend", now - datetime.timedelta(days=8))   # outside the week

    data = _activity(client)
    by_date = {d["date"]: d for d in data["days"]}
    three_ago = (now - datetime.timedelta(days=3)).date().isoformat()
    assert (by_date[three_ago]["logins"], by_date[three_ago]["failed"]) == (1, 1)
    assert (now - datetime.timedelta(days=8)).date().isoformat() not in by_date
    assert data["totals"]["logins"] == 4  # admin x2, friend x1 today, + the one 3 days ago
    assert len(_activity(client, days=30)["days"]) == 30
    assert client.get("/api/admin/activity", params={"days": 0}, headers=admin_auth(client)).status_code == 422


def test_days_are_bucketed_in_the_admins_timezone(client):
    admin = admin_auth(client)
    today_utc = datetime.datetime.now(UTC).date()
    # 02:30 UTC yesterday = 22:30 two days ago in Montreal (UTC-4, offset 240).
    when = datetime.datetime.combine(today_utc - datetime.timedelta(days=1), datetime.time(2, 30), tzinfo=UTC)
    _add_event("admin", when)

    utc_days = {d["date"]: d["logins"] for d in _activity(client, headers=admin, tz_offset=0)["days"]}
    assert utc_days[(today_utc - datetime.timedelta(days=1)).isoformat()] == 1

    local_today = (datetime.datetime.now(UTC) - datetime.timedelta(minutes=240)).date()
    local = _activity(client, headers=admin, tz_offset=240)["days"]
    assert local[-1]["date"] == local_today.isoformat()
    local_days = {d["date"]: d["logins"] for d in local}
    assert local_days[(today_utc - datetime.timedelta(days=2)).isoformat()] == 1


def test_lockout_attempts_are_not_logged_twice(client):
    admin = admin_auth(client)
    client.post("/api/admin/users", json={"username": "friend", "password": USER_PASSWORD}, headers=admin)
    for _ in range(accounts.MAX_FAILURES + 3):  # the last 3 are refused by the lockout
        client.post("/api/auth/login", json={"username": "friend", "password": "nope"})
    assert _activity(client, headers=admin)["totals"]["failed"] == accounts.MAX_FAILURES


def test_old_connections_are_pruned_and_deleted_with_the_account(client):
    friend = make_user(client, "friend", garmin=False)
    old = datetime.datetime.now(UTC) - datetime.timedelta(days=accounts.LOGIN_EVENTS_KEPT_DAYS + 5)
    _add_event("friend", old)
    login(client, "friend")  # a successful login prunes
    with SessionLocal() as db:
        assert db.query(models.LoginEvent).filter(models.LoginEvent.created_at < old + datetime.timedelta(days=1)).count() == 0

    friend_id = client.get("/api/auth/me", headers=friend).json()["id"]
    assert client.delete(f"/api/admin/users/{friend_id}", params={"confirm": "friend"},
                         headers=admin_auth(client)).status_code == 204
    with SessionLocal() as db:
        assert db.query(models.LoginEvent).filter_by(username="friend").count() == 0


# --- Deleting an account is deliberate --------------------------------------------


def test_accounts_list_says_what_each_account_holds(client, auth):
    import datetime as dt

    client.post("/api/objectives", json={"title": "Race"}, headers=auth)
    for offset in range(3):
        client.post("/api/planned", headers=auth, json={
            "date": (dt.date.today() + dt.timedelta(days=offset)).isoformat(), "activity_type": "running",
            "planned_duration_minutes": 30})
    with SessionLocal() as db:
        user = db.query(models.User).filter_by(username="athlete").one()
        db.add(models.Activity(user_id=user.id, garmin_activity_id="1", activity_type="running",
                               start_time=dt.datetime(2026, 1, 1, 7), duration_seconds=1800))
        db.add(models.SleepRecord(user_id=user.id, date=dt.date(2026, 1, 1), sleep_score=80))
        db.commit()
    users = {u["username"]: u for u in client.get("/api/admin/users", headers=admin_auth(client)).json()["users"]}
    assert users["athlete"]["data"] == {"activities": 1, "sleep_nights": 1, "planned_sessions": 3, "objectives": 1}
    assert users["admin"]["data"] == {"activities": 0, "sleep_nights": 0, "planned_sessions": 0, "objectives": 0}


def test_delete_needs_the_username_typed_back(client, auth):
    admin = admin_auth(client)
    client.post("/api/objectives", json={"title": "kept"}, headers=auth)
    user_id = client.get("/api/auth/me", headers=auth).json()["id"]

    for params in ({}, {"confirm": ""}, {"confirm": "athlet"}, {"confirm": "admin"}):
        res = client.delete(f"/api/admin/users/{user_id}", params=params, headers=admin)
        assert res.status_code == 400 and 'Type the username "athlete"' in res.json()["detail"]
    assert client.get("/api/objectives", headers=auth).status_code == 200  # still there, data intact

    assert client.delete(f"/api/admin/users/{user_id}", params={"confirm": " Athlete "}, headers=admin).status_code == 204
    assert client.get("/api/auth/me", headers=auth).status_code == 401
    with SessionLocal() as db:
        assert db.query(models.Objective).count() == 0


# --- Failed logins can't fill the database -----------------------------------------


def test_attempts_on_made_up_names_are_stored_up_to_a_cap(client):
    admin = admin_auth(client)
    for i in range(accounts.UNKNOWN_ATTEMPTS_KEPT_PER_HOUR + 15):
        assert client.post("/api/auth/login", json={"username": f"guess{i}", "password": "x"}).status_code == 401
    with SessionLocal() as db:
        assert db.query(models.LoginEvent).filter(models.LoginEvent.user_id.is_(None)).count() == accounts.UNKNOWN_ATTEMPTS_KEPT_PER_HOUR
    assert _activity(client, headers=admin)["totals"]["failed"] == accounts.UNKNOWN_ATTEMPTS_KEPT_PER_HOUR
    # Real accounts are still logged (they are bounded by the lockout instead).
    client.post("/api/admin/users", json={"username": "friend", "password": USER_PASSWORD}, headers=admin)
    client.post("/api/auth/login", json={"username": "friend", "password": "wrong-password"})
    login(client, "friend")
    recent = _activity(client, headers=admin)["recent"]
    assert [(e["username"], e["success"]) for e in recent[:2]] == [("friend", True), ("friend", False)]


def test_only_the_last_hour_counts_towards_the_cap(client):
    old = datetime.datetime.now(UTC) - datetime.timedelta(hours=2)
    with SessionLocal() as db:
        for _ in range(accounts.UNKNOWN_ATTEMPTS_KEPT_PER_HOUR):
            db.add(models.LoginEvent(user_id=None, username=accounts.UNKNOWN_ACCOUNT, success=False, created_at=old))
        db.commit()
    client.post("/api/auth/login", json={"username": "someone", "password": "x"})
    with SessionLocal() as db:
        assert db.query(models.LoginEvent).count() == accounts.UNKNOWN_ATTEMPTS_KEPT_PER_HOUR + 1


def test_lockout_memory_forgets_old_names(monkeypatch):
    monkeypatch.setattr(accounts, "MAX_TRACKED_USERNAMES", 10)
    accounts.reset_all_failures()
    clock = [1000.0]
    monkeypatch.setattr(accounts.time, "monotonic", lambda: clock[0])
    for i in range(12):
        accounts.record_failure(f"old{i}")
    clock[0] += accounts.WINDOW_SECONDS + 1
    accounts.record_failure("fresh")
    assert set(accounts._failures) == {"fresh"}
    accounts.reset_all_failures()
