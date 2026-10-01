import datetime

from conftest import login
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


def test_validation(client, auth):
    for body in [
        {"date": TOMORROW, "activity_type": "running", "planned_duration_minutes": 0},
        {"date": TOMORROW, "activity_type": "running", "planned_duration_minutes": 2000},
        {"date": TOMORROW, "activity_type": "", "planned_duration_minutes": 30},
        {"date": "not-a-date", "activity_type": "running", "planned_duration_minutes": 30},
    ]:
        assert client.post("/api/planned", json=body, headers=auth).status_code == 422


def test_cannot_touch_other_users_sessions(client, auth):
    row = client.post("/api/planned", json={"date": TOMORROW, "activity_type": "running",
                                            "planned_duration_minutes": 30}, headers=auth).json()
    other = login(client, email="other@example.com")
    assert client.patch(f"/api/planned/{row['id']}", json={"planned_duration_minutes": 5}, headers=other).status_code == 404
    assert client.delete(f"/api/planned/{row['id']}", headers=other).status_code == 404
    assert len(_day(client, auth, TOMORROW)) == 1


def test_requires_login(client):
    assert client.post("/api/planned", json={}).status_code == 401


def test_old_ai_settings_table_gets_new_columns(tmp_path, monkeypatch):
    """A database created before the custom-provider columns existed is upgraded on startup."""
    from app import database

    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE ai_settings (id INTEGER PRIMARY KEY, user_id INTEGER, provider VARCHAR(32), "
                          "model VARCHAR(128), api_key_encrypted TEXT, updated_at DATETIME)"))
    monkeypatch.setattr(database, "engine", engine)
    database.add_missing_columns()
    database.add_missing_columns()  # idempotent
    cols = {c["name"] for c in inspect(engine).get_columns("ai_settings")}
    assert {"base_url", "api_format"} <= cols
