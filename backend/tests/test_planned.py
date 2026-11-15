"""Planned sessions (routers/planned.py: add, edit, move, delete), the calendar
that shows them (routers/calendar.py) and the startup column migration."""

import datetime

import pytest
from conftest import make_user
from sqlalchemy import create_engine, inspect, text

TOMORROW = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()


def _day(client, auth, date):
    days = client.get("/api/calendar", params={"start": date, "end": date}, headers=auth).json()["days"]
    return days[0]["planned"]


def test_calendar_span_is_limited(client, auth):
    """One request can't make the server build millions of days."""
    res = client.get("/api/calendar", params={"start": "0001-01-01", "end": "9999-12-31"}, headers=auth)
    assert res.status_code == 422
    year = client.get("/api/calendar", params={"start": "2026-01-01", "end": "2026-12-31"}, headers=auth)
    assert year.status_code == 200 and len(year.json()["days"]) == 365
    # The very last day there is: stepping one day past it used to overflow (500).
    last = client.get("/api/calendar", params={"start": "9999-12-25", "end": "9999-12-31"}, headers=auth)
    assert last.status_code == 200 and len(last.json()["days"]) == 7


def test_an_edit_cannot_blank_a_planned_session(client, auth):
    """null sport (was a 500), null duration or a blank sport are refused; the row is unchanged."""
    row = client.post("/api/planned", json={"date": TOMORROW, "activity_type": "running",
                                            "planned_duration_minutes": 30}, headers=auth).json()
    for body in ({"activity_type": None}, {"activity_type": "   "}, {"planned_duration_minutes": None}, {"date": None}):
        assert client.patch(f"/api/planned/{row['id']}", json=body, headers=auth).status_code == 422, body
    assert client.post("/api/planned", json={"date": TOMORROW, "activity_type": "  ",
                                             "planned_duration_minutes": 30}, headers=auth).status_code == 422
    kept = _day(client, auth, TOMORROW)[0]
    assert kept["activity_type"] == "running" and kept["planned_duration_minutes"] == 30


def test_create_update_delete(client, auth):
    res = client.post("/api/planned", json={"date": TOMORROW, "activity_type": "swimming",
                                            "planned_duration_minutes": 45}, headers=auth)
    assert res.status_code == 201
    row = res.json()
    assert row["source"] == "manual"
    assert _day(client, auth, TOMORROW)[0]["activity_type"] == "swimming"

    res = client.patch(f"/api/planned/{row['id']}", json={"activity_type": "cycling",
                                                         "planned_duration_minutes": 90}, headers=auth)
    assert res.json()["activity_type"] == "cycling" and res.json()["planned_duration_minutes"] == 90

    assert client.delete(f"/api/planned/{row['id']}", headers=auth).status_code == 204
    assert _day(client, auth, TOMORROW) == []


def test_move_to_another_day(client, auth):
    later = (datetime.date.today() + datetime.timedelta(days=3)).isoformat()
    row = client.post("/api/planned", json={"date": TOMORROW, "activity_type": "running",
                                            "planned_duration_minutes": 45, "notes": "tempo"}, headers=auth).json()
    res = client.patch(f"/api/planned/{row['id']}", json={"date": later}, headers=auth)
    assert res.status_code == 200 and res.json()["date"] == later
    assert _day(client, auth, TOMORROW) == []
    moved = _day(client, auth, later)[0]
    assert (moved["activity_type"], moved["planned_duration_minutes"], moved["notes"]) == ("running", 45, "tempo")

    for bad in (None, "not-a-date"):
        assert client.patch(f"/api/planned/{row['id']}", json={"date": bad}, headers=auth).status_code == 422
    assert _day(client, auth, later)[0]["id"] == row["id"]


def test_validation(client, auth):
    for body in [
        {"date": TOMORROW, "activity_type": "running", "planned_duration_minutes": 0},
        {"date": TOMORROW, "activity_type": "running", "planned_duration_minutes": 2000},
        {"date": TOMORROW, "activity_type": "", "planned_duration_minutes": 30},
        {"date": "not-a-date", "activity_type": "running", "planned_duration_minutes": 30},
        {"date": TOMORROW, "activity_type": "running", "planned_duration_minutes": 30, "notes": "x" * 30001},
    ]:
        assert client.post("/api/planned", json=body, headers=auth).status_code == 422


def test_notes_fit_a_long_draft_line(client, auth):
    # A confirmed draft line's details become the notes, and editing the session sends them back.
    body = {"date": TOMORROW, "activity_type": "running", "planned_duration_minutes": 30, "notes": "x" * 30000}
    row = client.post("/api/planned", json=body, headers=auth).json()
    res = client.patch(f"/api/planned/{row['id']}", json={"planned_duration_minutes": 40, "notes": body["notes"]}, headers=auth)
    assert res.status_code == 200
    assert client.patch(f"/api/planned/{row['id']}", json={"notes": "x" * 30001}, headers=auth).status_code == 422


def test_cannot_touch_other_users_sessions(client, auth):
    row = client.post("/api/planned", json={"date": TOMORROW, "activity_type": "running",
                                            "planned_duration_minutes": 30}, headers=auth).json()
    other = make_user(client, "other")
    assert client.patch(f"/api/planned/{row['id']}", json={"planned_duration_minutes": 5}, headers=other).status_code == 404
    assert client.delete(f"/api/planned/{row['id']}", headers=other).status_code == 404
    assert len(_day(client, auth, TOMORROW)) == 1


def test_requires_login(client):
    assert client.post("/api/planned", json={}).status_code == 401


def test_old_tables_get_new_columns(tmp_path, monkeypatch):
    """A database created by an earlier version gains new nullable columns on startup."""
    from app import database

    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE users (id INTEGER PRIMARY KEY, email VARCHAR(255), hashed_password VARCHAR(255))"))
    monkeypatch.setattr(database, "engine", engine)
    database.add_missing_columns()
    database.add_missing_columns()  # idempotent
    cols = {c["name"] for c in inspect(engine).get_columns("users")}
    assert {"is_admin", "token_version", "last_login_at"} <= cols



OLD_PLAN_DRAFTS = """CREATE TABLE plan_drafts (
    id INTEGER NOT NULL, user_id INTEGER NOT NULL, start_date DATE NOT NULL, weeks INTEGER NOT NULL,
    text TEXT NOT NULL, notes TEXT, updated_at DATETIME NOT NULL, confirmed_at DATETIME,
    PRIMARY KEY (id), UNIQUE (user_id), FOREIGN KEY(user_id) REFERENCES users (id))"""  # as v17 created it


def test_plan_drafts_move_to_one_draft_per_user_and_coach(tmp_path):
    """database.migrate_plan_drafts on a database made before coaches: the rows are kept,
    a coach can then have a draft for an athlete, and an athlete still has one draft of their own."""
    from sqlalchemy.exc import IntegrityError

    from app import database

    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE users (id INTEGER PRIMARY KEY, email VARCHAR(255), hashed_password VARCHAR(255))"))
        conn.execute(text("INSERT INTO users (id, email, hashed_password) VALUES (1, 'athlete', ''), (2, 'coach', '')"))
        conn.execute(text(OLD_PLAN_DRAFTS))
        conn.execute(text("INSERT INTO plan_drafts (id, user_id, start_date, weeks, text, notes, updated_at) "
                          "VALUES (7, 1, '2026-11-16', 4, '2026-11-17 | run | 1:00', 'block notes', '2026-11-14 10:00:00')"))

    assert database.migrate_plan_drafts(engine) is True
    assert database.migrate_plan_drafts(engine) is False  # once only
    with engine.begin() as conn:
        rows = conn.execute(text("SELECT id, user_id, coach_id, text, notes, weeks FROM plan_drafts")).all()
        assert rows == [(7, 1, None, "2026-11-17 | run | 1:00", "block notes", 4)]
        conn.execute(text("INSERT INTO plan_drafts (user_id, coach_id, start_date, weeks, text, updated_at) "
                          "VALUES (1, 2, '2026-11-16', 2, '', '2026-11-14 10:00:00')"))  # the coach's draft for them
    for coach_id in ("NULL", "2"):  # a second own draft, a second draft of the same coach
        with pytest.raises(IntegrityError), engine.begin() as conn:
            conn.execute(text(f"INSERT INTO plan_drafts (user_id, coach_id, start_date, weeks, text, updated_at) "
                              f"VALUES (1, {coach_id}, '2026-11-16', 2, '', '2026-11-14 10:00:00')"))
    assert "coach_id" in {c["name"] for c in inspect(engine).get_columns("plan_drafts")}
    assert not inspect(engine).has_table("plan_drafts_old")


def test_a_new_database_needs_no_plan_drafts_migration(client):
    from app import database

    assert database.migrate_plan_drafts() is False
