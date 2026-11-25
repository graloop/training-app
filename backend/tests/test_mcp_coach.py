"""A coach's connector (app/mcp/server.py, _target): Claude, logged in as a coach,
reads the athletes linked to that coach with athlete=<username>, keeps notes
about each on the link, and never sees sleep or recovery unless the athlete
shares them (coaching.can_see_health). Every miss answers the same sentence.
conftest: MCP_USERNAMES=coach, MCP_USERNAME=athlete."""

import datetime

import pytest
from conftest import admin_auth, make_user
from test_mcp_oauth import connect
from test_mcp_tools import ACCEPT

from app import models
from app.database import SessionLocal
from app.garmin import autosync
from app.garmin import client as garmin_client
from app.mcp import server as mcp_server

TODAY = datetime.date.today()
RECOVERY_WORDS = ("sleep", "hrv", "readiness", "rhr", "resting", "stress", "battery", "injur")


def _id(username):
    with SessionLocal() as db:
        return db.query(models.User).filter_by(username=username).one().id


def _role(client, username, role):
    res = client.patch(f"/api/admin/users/{_id(username)}", json={"role": role}, headers=admin_auth(client))
    assert res.status_code == 200, res.text


def _make_coach(client, username="coach"):
    headers = make_user(client, username, garmin=False)
    _role(client, username, "coach")
    return headers


def _link(client, coach_headers, athlete="athlete"):
    res = client.post("/api/coach/athletes", json={"username": athlete}, headers=coach_headers)
    assert res.status_code == 201, res.text


def _share(on, coach="coach", athlete="athlete"):
    with SessionLocal() as db:
        link = db.query(models.CoachLink).filter_by(coach_id=_id(coach), athlete_id=_id(athlete)).one()
        link.share_health = on
        db.commit()


def _runner(client, username):
    _client_id, tokens = connect(client, username)

    def run(name, **arguments):
        body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": arguments}}
        res = client.post("/mcp", json=body, headers={**ACCEPT, "Authorization": f"Bearer {tokens['access_token']}"})
        assert res.status_code == 200, res.text
        result = res.json()["result"]
        assert result.get("isError") is not True, result
        return result["content"][0]["text"]

    return run


@pytest.fixture
def coach_web(client, auth):
    """The coach "coach" (page headers), with "athlete" (Garmin connected) as their athlete."""
    headers = _make_coach(client)
    _link(client, headers)
    return headers


@pytest.fixture
def coach_tool(client, coach_web):
    """coach_tool("name", arg=...) runs a tool as the connected coach."""
    return _runner(client, "coach")


@pytest.fixture
def recovery():
    """A night, a day's metrics, a session and profile entries full of recovery data
    for "athlete". Returns the session's id."""
    user_id = _id("athlete")
    with SessionLocal() as db:
        db.add(models.SleepRecord(user_id=user_id, date=TODAY, sleep_score=83, resting_heart_rate=44,
                                  total_sleep_seconds=27000, raw_json={"avgOvernightHrv": 61, "bodyBatteryChange": 55}))
        db.add(models.DailyMetrics(user_id=user_id, date=TODAY, readiness=71, readiness_level="HIGH", stress_avg=25,
                                   body_battery_low=20, body_battery_high=90, acute_load=410, chronic_load=380,
                                   training_status="productive", vo2max=55.0, attempts=1))
        activity = models.Activity(
            user_id=user_id, garmin_activity_id="a1", activity_type="running", name="Morning run",
            start_time=datetime.datetime.combine(TODAY, datetime.time(7)), duration_seconds=3600,
            distance_meters=10000, raw_json={"maxHR": 171, "differenceBodyBattery": -14},
        )
        db.add(activity)
        user = db.get(models.User, user_id)
        user.athlete_profile = {"max_hr": 190, "resting_hr": 45, "health": "Sore Achilles", "equipment": "Road bike"}
        user.rule_set = "Long run on Sundays."
        db.commit()
        return activity.id


def _no_recovery(text):
    lowered = text.lower()
    return [word for word in RECOVERY_WORDS if word in lowered]


# --- Reading an athlete --------------------------------------------------------------


def test_a_coach_reads_a_linked_athlete_without_recovery_data(coach_tool, recovery):
    overview = coach_tool("get_overview", athlete="athlete")
    assert overview.startswith("TRAINING OVERVIEW · athlete")
    assert "Long run on Sundays." in overview and "productive" in overview and "acute load 410" in overview
    assert "doesn't share their recovery data" in overview
    assert _no_recovery(overview) == []

    for detail in ("summary", "full"):
        log = coach_tool("get_training_log", athlete="athlete", detail=detail)
        assert log.startswith("TRAINING LOG · athlete") and "Morning run" in log
        assert _no_recovery(log) == [], detail
    assert "max 190" in coach_tool("get_training_log", athlete="athlete")  # the profile, without health or resting HR

    session = coach_tool("get_session", athlete="athlete", activity_id=recovery)
    assert "max HR 171" in session and _no_recovery(session) == []
    assert f"id {recovery}" in coach_tool("list_sessions", athlete="athlete")
    assert coach_tool("get_rules", athlete="athlete") == "Long run on Sundays."


def test_with_sharing_on_the_coach_gets_recovery_data(coach_tool, recovery):
    _share(True)
    overview = coach_tool("get_overview", athlete="athlete")
    assert "LAST 3 NIGHTS" in overview and "sleep 83" in overview and "readiness 71" in overview
    assert "HRV 61" in overview and "doesn't share" not in overview
    log = coach_tool("get_training_log", athlete="athlete", detail="full")
    assert "body battery -14" in log and "Sore Achilles" in log and "resting 45" in log
    assert "body battery -14" in coach_tool("get_session", athlete="athlete", activity_id=recovery)


def test_every_miss_answers_the_same_sentence(client, coach_tool, recovery):
    make_user(client, "stranger", garmin=False)  # an athlete not linked to the coach
    _make_coach(client, "other-coach")
    for name in ("nobody", "stranger", "admin", "other-coach", "coach"):
        for tool in ("get_overview", "get_training_log", "list_sessions", "get_planned", "get_rules"):
            assert coach_tool(tool, athlete=name) == mcp_server.NOT_YOURS.format(name=name), (tool, name)
    # An athlete's own connector can't name anyone either.
    athlete_tool = _runner(client, "athlete")
    assert athlete_tool("get_overview", athlete="stranger") == mcp_server.NOT_YOURS.format(name="stranger")
    assert "Only a coach account has athletes" in athlete_tool("list_athletes")


def test_another_athletes_session_is_out_of_reach(client, coach_tool, recovery):
    make_user(client, "stranger", garmin=False)
    with SessionLocal() as db:
        other = models.Activity(user_id=_id("stranger"), garmin_activity_id="s1", activity_type="running",
                                start_time=datetime.datetime.combine(TODAY, datetime.time(8)), duration_seconds=600)
        db.add(other)
        db.commit()
        other_id = other.id
    assert coach_tool("get_session", athlete="athlete", activity_id=other_id).startswith(f"No session with id {other_id}")


def test_removing_the_link_cuts_claude_off(client, coach_web, coach_tool, recovery):
    assert coach_tool("get_rules", athlete="athlete") == "Long run on Sundays."
    assert client.delete(f"/api/coach/athletes/{_id('athlete')}", headers=coach_web).status_code == 204
    assert coach_tool("get_rules", athlete="athlete") == mcp_server.NOT_YOURS.format(name="athlete")


def test_a_coach_account_must_name_an_athlete(coach_tool):
    for tool in ("get_overview", "get_training_log", "get_planned", "get_draft", "sync_garmin"):
        assert coach_tool(tool) == mcp_server.COACH_ACCOUNT, tool


def test_tools_that_change_an_athletes_own_things_are_refused(coach_tool, recovery):
    calls = {
        "add_planned": {"date": TODAY.isoformat(), "activity_type": "running", "minutes": 30},
        "update_planned": {"plan_id": 1, "minutes": 30},
        "delete_planned": {"plan_id": 1},
        "confirm_draft": {},
        "set_rules": {"rules": "Nothing"},
        "upsert_objective": {"title": "Race"},
    }
    for name, arguments in calls.items():
        for athlete in ("athlete", ""):
            assert coach_tool(name, athlete=athlete, **arguments) == mcp_server.FOR_ATHLETE_USE_DRAFT, (name, athlete)
    assert "set_athlete_notes" in coach_tool("set_coach_notes", notes="x", athlete="athlete")
    with SessionLocal() as db:
        user = db.get(models.User, _id("athlete"))
        assert user.rule_set == "Long run on Sundays." and user.coach_notes is None
        assert db.query(models.PlannedTraining).count() == 0 and db.query(models.Objective).count() == 0


def test_athlete_tools_still_refuse_athlete_arguments_on_their_own_account(client, auth):
    athlete_tool = _runner(client, "athlete")
    assert athlete_tool("set_rules", rules="x", athlete="athlete") == mcp_server.FOR_ATHLETE_USE_DRAFT
    assert athlete_tool("set_rules", rules="Mine").startswith("Rule set saved")


# --- The coach's own side ----------------------------------------------------------------


def test_list_athletes_and_connection_check(client, coach_tool, recovery):
    assert "Coach of 1 athlete: athlete. Pass athlete=<username>" in coach_tool("connection_check")
    line = coach_tool("list_athletes")
    assert line.startswith("ATHLETES\n- athlete · no dated objective · this week 1:00 done of 0:00 planned")
    assert "Garmin last synced never (stale)" in line and "recovery not shared" in line and "no draft" in line
    _share(True)
    assert "shares sleep and recovery" in coach_tool("list_athletes")


def test_notes_about_an_athlete_live_on_the_link(client, coach_tool):
    with SessionLocal() as db:
        db.get(models.User, _id("athlete")).coach_notes = "The athlete's own Claude notes"
        db.commit()
    assert coach_tool("set_athlete_notes", athlete="athlete", notes="  Responds well to tempo.  ").startswith(
        "Notes about athlete saved (23 of 4000")
    assert "Responds well to tempo." in coach_tool("get_overview", athlete="athlete")
    assert "own Claude notes" not in coach_tool("get_overview", athlete="athlete")
    assert "Not saved: 4001 characters" in coach_tool("set_athlete_notes", athlete="athlete", notes="x" * 4001)
    with SessionLocal() as db:
        assert db.get(models.User, _id("athlete")).coach_notes == "The athlete's own Claude notes"
        assert db.query(models.CoachLink).one().claude_notes == "Responds well to tempo."
    athlete_overview = _runner(client, "athlete")("get_overview")
    assert "Responds well" not in athlete_overview and "own Claude notes" in athlete_overview


def test_a_coach_syncs_an_athlete_at_most_every_30_minutes(client, coach_tool, monkeypatch):
    started = []
    monkeypatch.setattr(garmin_client, "get_authenticated_client", lambda session: "api")
    monkeypatch.setattr(autosync, "start_job", lambda user_id, api: started.append(user_id) or {"state": "waiting"})
    assert coach_tool("sync_garmin", athlete="athlete").startswith("Garmin sync started.")
    assert started == [_id("athlete")]
    assert "synced less than 30 minutes ago" in coach_tool("sync_garmin", athlete="athlete")
    assert started == [_id("athlete")]


def test_a_coach_is_told_when_the_athletes_garmin_needs_reconnecting(client, coach_tool, auth):
    assert client.delete("/api/garmin/connect", headers=auth).status_code == 200
    assert coach_tool("sync_garmin", athlete="athlete") == (
        "Sync not started. athlete's Garmin needs connecting or reconnecting. They do it in their Settings.")
    assert coach_tool("sync_garmin", athlete="athlete").startswith("Sync not started.")  # a failed try isn't counted


# --- The page follows the same switch -------------------------------------------------------


def test_the_coachs_page_shows_sleep_and_health_only_when_shared(client, coach_web, recovery):
    athlete = _id("athlete")
    span = f"start={TODAY.isoformat()}&end={TODAY.isoformat()}"

    def today_sleep():
        days = client.get(f"/api/coach/athletes/{athlete}/calendar?{span}", headers=coach_web).json()["days"]
        return days[0]["sleep"]

    def profile():
        return client.get(f"/api/coach/athletes/{athlete}", headers=coach_web).json()["profile"]

    assert today_sleep() is None and set(profile()) == {"max_hr", "equipment"}
    assert client.get("/api/coach/athletes", headers=coach_web).json()["athletes"][0]["share_health"] is False
    _share(True)
    assert today_sleep()["sleep_score"] == 83 and profile()["health"] == "Sore Achilles"
    assert client.get("/api/coach/athletes", headers=coach_web).json()["athletes"][0]["share_health"] is True


def test_new_link_columns_can_be_added_to_an_existing_database():
    """Nullable: database.add_missing_columns() adds them on the next start."""
    for column in ("share_health", "claude_notes"):
        assert models.CoachLink.__table__.c[column].nullable


# --- Claude writes the coach's draft (phase 3) ------------------------------------------------

NEXT_MONDAY = TODAY - datetime.timedelta(days=TODAY.weekday()) + datetime.timedelta(days=7)


def _plan_text():
    return "\n".join([
        f"{NEXT_MONDAY.isoformat()} | run | 0:45 | Easy run Z2",
        f"{(NEXT_MONDAY + datetime.timedelta(days=2)).isoformat()} | bike | 1:30 | Endurance ride",
    ])


def test_save_draft_for_an_athlete_writes_only_the_coachs_draft(client, auth, coach_tool):
    athlete = _id("athlete")
    client.put("/api/plan/rules", json={"rules": "Mine"}, headers=auth)
    own = client.post("/api/plan/draft/sessions", headers=auth, json={
        "date": NEXT_MONDAY.isoformat(), "activity_type": "swimming", "minutes": 30, "details": "My own swim"})
    assert own.status_code == 200
    with SessionLocal() as db:
        db.add(models.PlannedTraining(user_id=athlete, date=NEXT_MONDAY, activity_type="running",
                                      planned_duration_minutes=60))
        db.commit()

    text = coach_tool("save_draft", athlete="athlete", start_date=NEXT_MONDAY.isoformat(), weeks=2,
                      text=_plan_text(), summary="Base block: 2 h 15 a week, easy.")
    assert "the coach's draft for athlete · not sent yet" in text
    assert "2 sessions read" in text and "athlete has 1 planned session on their calendar" in text
    assert "Last changed by you (Claude)." in text and "Base block: 2 h 15 a week, easy." in text

    with SessionLocal() as db:
        coach_draft = db.query(models.PlanDraft).filter_by(user_id=athlete, coach_id=_id("coach")).one()
        assert (coach_draft.written_by, coach_draft.summary) == ("claude", "Base block: 2 h 15 a week, easy.")
        assert db.query(models.PlannedTraining).filter_by(user_id=athlete).count() == 1  # the calendar is untouched
    mine = client.get("/api/plan/draft", headers=auth).json()  # the athlete's own draft, unchanged
    assert "My own swim" in mine["text"] and "Easy run" not in mine["text"] and mine["summary"] is None
    assert coach_tool("confirm_draft", athlete="athlete") == mcp_server.FOR_ATHLETE_USE_DRAFT
    assert "by Claude, not sent" in coach_tool("list_athletes")


def test_the_coachs_page_edits_show_up_in_get_draft(client, coach_web, coach_tool):
    athlete = _id("athlete")
    coach_tool("save_draft", athlete="athlete", start_date=NEXT_MONDAY.isoformat(), weeks=2, text=_plan_text())
    page = client.get(f"/api/plan/draft?athlete_id={athlete}", headers=coach_web).json()
    assert page["written_by"] == "claude"
    res = client.patch(f"/api/plan/draft/sessions/1?athlete_id={athlete}", headers=coach_web, json={
        "date": NEXT_MONDAY.isoformat(), "activity_type": "running", "minutes": 30, "details": "Shorter, coach's call"})
    assert res.status_code == 200 and res.json()["written_by"] == "coach"
    text = coach_tool("get_draft", athlete="athlete")
    assert "Shorter, coach's call" in text and "Last changed by the coach, on the page." in text
    assert "edited by the coach" in coach_tool("list_athletes")


def test_sending_marks_the_draft_sent_until_the_next_change(client, auth, coach_web, coach_tool):
    athlete = _id("athlete")
    coach_tool("save_draft", athlete="athlete", start_date=NEXT_MONDAY.isoformat(), weeks=2, text=_plan_text(),
               summary="Only for the coach")
    assert client.post(f"/api/plan/draft/send?athlete_id={athlete}", headers=coach_web).status_code == 200
    assert "sent to their Plan tab" in coach_tool("get_draft", athlete="athlete")
    mine = client.get("/api/plan/draft", headers=auth).json()
    assert "Endurance ride" in mine["text"] and mine["summary"] is None and mine["written_by"] == "coach"
    coach_tool("save_draft", athlete="athlete", start_date=NEXT_MONDAY.isoformat(), weeks=2, text=_plan_text())
    assert "not sent yet" in coach_tool("get_draft", athlete="athlete")


def test_a_draft_summary_is_capped(coach_tool):
    answer = coach_tool("save_draft", athlete="athlete", start_date=NEXT_MONDAY.isoformat(), weeks=1, text="",
                        summary="x" * 1501)
    assert answer.startswith("Not saved: the summary has 1501 characters")
    assert coach_tool("get_draft", athlete="athlete").startswith("No draft for athlete yet.")


def test_new_draft_columns_can_be_added_to_an_existing_database():
    for column in ("written_by", "summary", "sent_at"):
        assert models.PlanDraft.__table__.c[column].nullable
