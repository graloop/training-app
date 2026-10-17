import datetime

from conftest import make_user
from sqlalchemy import create_engine, inspect, text

TOMORROW = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()


def _day(client, auth, date):
    days = client.get("/api/calendar", params={"start": date, "end": date}, headers=auth).json()["days"]
    return days[0]["planned"]


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

