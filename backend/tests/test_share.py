"""Import tab: copying someone else's planned sessions with their name and share code."""

import datetime

from conftest import admin_auth, make_user

from app import accounts, models, sharing
from app.database import SessionLocal

TODAY = datetime.date.today()
MONDAY = TODAY - datetime.timedelta(days=TODAY.weekday())
NEXT_MONDAY = MONDAY + datetime.timedelta(days=7)


def _plan(client, headers, day, sport="running", minutes=60, notes=None):
    res = client.post("/api/planned", headers=headers, json={
        "date": day.isoformat(), "activity_type": sport, "planned_duration_minutes": minutes, "notes": notes})
    assert res.status_code == 201, res.text


def _code(client, headers):
    return client.get("/api/share", headers=headers).json()["share_code"]


def _ask(client, headers, path, code, source=NEXT_MONDAY, weeks=1, target=None, username="marie"):
    body = {"username": username, "share_code": code, "source_start": source.isoformat(), "weeks": weeks}
    if target:
        body["target_start"] = target.isoformat()
    return client.post(f"/api/share/import/{path}", headers=headers, json=body, params={"today": TODAY.isoformat()})


def _planned(username):
    with SessionLocal() as db:
        user = db.query(models.User).filter_by(username=username).one()
        return sorted((p.date, p.activity_type, p.planned_duration_minutes, p.notes, p.source)
                      for p in db.query(models.PlannedTraining).filter_by(user_id=user.id))


# --- The share code ------------------------------------------------------------------


def test_each_account_gets_a_lasting_code(client):
    marie = make_user(client, "marie", garmin=False)
    first = client.get("/api/share", headers=marie).json()
    assert first["username"] == "marie"
    assert len(sharing.normalize_code(first["share_code"])) == sharing.CODE_LENGTH
    assert set(sharing.normalize_code(first["share_code"])) <= set(sharing.CODE_ALPHABET)
    assert _code(client, marie) == first["share_code"]  # same code every time


def test_a_new_code_stops_the_old_one(client):
    marie, paul = make_user(client, "marie", garmin=False), make_user(client, "paul", garmin=False)
    _plan(client, marie, NEXT_MONDAY)
    old = _code(client, marie)
    new = client.post("/api/share/code", headers=marie).json()["share_code"]
    assert new != old
    assert _ask(client, paul, "preview", old).status_code == 404
    assert _ask(client, paul, "preview", new).status_code == 200


def test_name_and_code_must_both_match(client):
    marie, paul = make_user(client, "marie", garmin=False), make_user(client, "paul", garmin=False)
    make_user(client, "lea", garmin=False)
    code = _code(client, marie)
    for username, typed in (("marie", "ABCD-EFGH"), ("lea", code), ("nobody", code)):
        res = _ask(client, paul, "preview", typed, username=username)
        assert res.status_code == 404 and res.json()["detail"] == "No account with that name and share code."
    # Typing is forgiving: case, spaces and the dash don't matter.
    assert _ask(client, paul, "preview", code.lower().replace("-", " "), username=" Marie ").status_code == 200


def test_guessing_codes_is_stopped(client):
    marie, paul = make_user(client, "marie", garmin=False), make_user(client, "paul", garmin=False)
    code = _code(client, marie)
    for _ in range(accounts.MAX_SHARE_FAILURES):
        assert _ask(client, paul, "preview", "WRONG-CODE").status_code == 404
    res = _ask(client, paul, "preview", code)  # even the right one, for a while
    assert res.status_code == 429 and "Too many wrong names or codes" in res.json()["detail"]
    lea = make_user(client, "lea", garmin=False)
    assert _ask(client, lea, "preview", code).status_code == 200  # others aren't affected


def test_admins_neither_share_nor_import(client):
    admin = admin_auth(client)
    assert client.get("/api/share", headers=admin).status_code == 403
    assert _ask(client, admin, "preview", "ABCD-EFGH").status_code == 403
    paul = make_user(client, "paul", garmin=False)
    with SessionLocal() as db:  # even an admin that somehow has a code can't be imported from
        db.query(models.User).filter_by(username="admin").one().share_code = "ABCD-EFGH"
        db.commit()
    assert _ask(client, paul, "preview", "ABCD-EFGH", username="admin").status_code == 404


def test_requires_login(client):
    assert client.get("/api/share").status_code == 401
    assert client.post("/api/share/import/preview", json={}).status_code == 401


# --- What is shared ----------------------------------------------------------------------


def test_only_planned_sessions_are_shared(client):
    marie, paul = make_user(client, "marie", garmin=False), make_user(client, "paul", garmin=False)
    _plan(client, marie, NEXT_MONDAY + datetime.timedelta(days=1), "swimming", 45, "8×100 m | 20 s rest\nsteady")
    _plan(client, marie, NEXT_MONDAY + datetime.timedelta(days=3), "cycling", 90)
    with SessionLocal() as db:  # a done activity and a night of sleep that week stay private
        user = db.query(models.User).filter_by(username="marie").one()
        db.add(models.Activity(user_id=user.id, garmin_activity_id="9", activity_type="running", name="Secret run",
                               start_time=datetime.datetime.combine(NEXT_MONDAY, datetime.time(7)), duration_seconds=3600))
        db.add(models.SleepRecord(user_id=user.id, date=NEXT_MONDAY, sleep_score=55))
        db.commit()
    res = _ask(client, paul, "preview", _code(client, marie))
    assert res.status_code == 200, res.text
    preview = res.json()
    assert preview["from_username"] == "marie" and preview["sessions_count"] == 2 and preview["errors"] == []
    sessions = [s for week in preview["weeks_preview"] for s in week["sessions"]]
    assert [(s["sport"], s["minutes"]) for s in sessions] == [("swim", 45), ("bike", 90)]
    assert sessions[0]["details"] == "8×100 m / 20 s rest steady"  # one line, no column breaks
    assert "Secret run" not in res.text and "55" not in res.text.replace("0:55", "")
    assert preview["text"].splitlines()[0].startswith("# Imported from marie")


def test_weeks_can_be_moved(client):
    """Their block from weeks ago, onto your next weeks: same weekdays."""
    marie, paul = make_user(client, "marie", garmin=False), make_user(client, "paul", garmin=False)
    old_monday = MONDAY - datetime.timedelta(days=21)
    _plan(client, marie, old_monday + datetime.timedelta(days=2), "running", 50, "Tempo")
    _plan(client, marie, old_monday + datetime.timedelta(days=8), "swimming", 40)
    _plan(client, marie, old_monday + datetime.timedelta(days=14), "cycling", 30)  # outside the 2 weeks asked
    res = _ask(client, paul, "preview", _code(client, marie), source=old_monday, weeks=2, target=NEXT_MONDAY).json()
    assert (res["source_start"], res["start_date"]) == (old_monday.isoformat(), NEXT_MONDAY.isoformat())
    sessions = [s for week in res["weeks_preview"] for s in week["sessions"]]
    assert [(s["date"], s["sport"]) for s in sessions] == [
        ((NEXT_MONDAY + datetime.timedelta(days=2)).isoformat(), "run"),
        ((NEXT_MONDAY + datetime.timedelta(days=8)).isoformat(), "swim"),
    ]


def test_days_already_past_are_left_out(client):
    marie, paul = make_user(client, "marie", garmin=False), make_user(client, "paul", garmin=False)
    old_monday = MONDAY - datetime.timedelta(days=7)
    for offset in range(7):
        _plan(client, marie, old_monday + datetime.timedelta(days=offset), "running", 30)
    res = _ask(client, paul, "preview", _code(client, marie), source=old_monday, target=MONDAY).json()
    assert res["skipped_past"] == TODAY.weekday()  # this week's days before today
    assert res["sessions_count"] == 7 - TODAY.weekday() and res["errors"] == []


# --- Import to plan / to calendar --------------------------------------------------------------


def test_import_to_plan_makes_it_the_draft(client):
    marie, paul = make_user(client, "marie", garmin=False), make_user(client, "paul", garmin=False)
    _plan(client, marie, NEXT_MONDAY + datetime.timedelta(days=1), "running", 75, "Track: 6×800 m")
    client.put("/api/plan/draft", headers=paul, params={"today": TODAY.isoformat()}, json={
        "start_date": NEXT_MONDAY.isoformat(), "weeks": 4, "text": "", "notes": "My block notes"})
    res = _ask(client, paul, "plan", _code(client, marie))
    assert res.status_code == 200, res.text
    draft = client.get("/api/plan/draft", headers=paul, params={"today": TODAY.isoformat()}).json()
    assert (draft["start_date"], draft["weeks"], draft["sessions_count"], draft["errors"]) == (NEXT_MONDAY.isoformat(), 1, 1, [])
    assert draft["notes"] == "My block notes"  # kept
    assert "| run | 1:15 | Track: 6×800 m" in draft["text"]
    assert _planned("paul") == []  # the calendar isn't touched
    assert len(_planned("marie")) == 1  # and neither is theirs


def test_import_to_calendar_replaces_like_confirming_a_plan(client):
    marie, paul = make_user(client, "marie", garmin=False), make_user(client, "paul", garmin=False)
    _plan(client, marie, NEXT_MONDAY + datetime.timedelta(days=2), "cycling", 120, "Long ride")
    _plan(client, paul, NEXT_MONDAY + datetime.timedelta(days=4), "running", 30)  # replaced
    _plan(client, paul, NEXT_MONDAY + datetime.timedelta(days=9), "running", 30)  # next week: kept
    preview = _ask(client, paul, "preview", _code(client, marie)).json()
    assert preview["existing_planned"] == 1
    res = _ask(client, paul, "calendar", _code(client, marie))
    assert res.status_code == 200 and (res.json()["added"], res.json()["replaced"]) == (1, 1)
    assert _planned("paul") == [
        (NEXT_MONDAY + datetime.timedelta(days=2), "cycling", 120.0, "Long ride", "import"),
        (NEXT_MONDAY + datetime.timedelta(days=9), "running", 30.0, None, "manual"),
    ]


def test_nothing_to_import(client):
    marie, paul = make_user(client, "marie", garmin=False), make_user(client, "paul", garmin=False)
    code = _code(client, marie)
    preview = _ask(client, paul, "preview", code).json()
    assert preview["sessions_count"] == 0
    for path in ("plan", "calendar"):
        res = _ask(client, paul, path, code)
        assert res.status_code == 400 and res.json()["detail"] == "There are no sessions to import in those weeks."


def test_weeks_are_limited_like_the_plan(client):
    marie, paul = make_user(client, "marie", garmin=False), make_user(client, "paul", garmin=False)
    code = _code(client, marie)
    assert _ask(client, paul, "preview", code, weeks=0).status_code == 422
    assert _ask(client, paul, "preview", code, weeks=13).status_code == 422
    assert _ask(client, paul, "preview", code, weeks=12).status_code == 200
