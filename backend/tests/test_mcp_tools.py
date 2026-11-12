"""Claude's connector end to end (app/mcp/auth.py, app/mcp/server.py): /mcp
refuses requests without a valid token in the way Claude expects, and with
one it lists and runs the tools as the connected account. Every tool is
called through /mcp, the way Claude calls it: its text and what it changed in
the database are checked, and that it can't reach another account's rows."""

import datetime
import json

import pytest
from conftest import GARMIN_PASSWORD, make_user
from test_mcp_oauth import connect

from app import models
from app.database import SessionLocal
from app.garmin import autosync
from app.garmin import client as garmin_client
from app.mcp import auth as mcp_auth
from app.mcp import server as mcp_server
from app.planning import basics

ACCEPT = {"Accept": "application/json, text/event-stream"}
TODAY = datetime.date.today()
NEXT_MONDAY = TODAY - datetime.timedelta(days=TODAY.weekday()) + datetime.timedelta(days=7)


def day(n):  # n days after today
    return TODAY + datetime.timedelta(days=n)


def _call(client, tokens, method, params=None, path="/mcp"):
    body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
    res = client.post(path, json=body, headers={**ACCEPT, "Authorization": f"Bearer {tokens['access_token']}"})
    assert res.status_code == 200, res.text
    return res.json()["result"]


@pytest.fixture
def tool(client, auth):
    """tool("name", arg=...) runs a tool as the connected account "athlete" and returns its text."""
    _client_id, tokens = connect(client)

    def run(name, **arguments):
        result = _call(client, tokens, "tools/call", {"name": name, "arguments": arguments})
        assert result.get("isError") is not True, result
        return result["content"][0]["text"]

    return run


def _user_id(username):
    with SessionLocal() as db:
        return db.query(models.User).filter(models.User.username == username).one().id


def _planned(username="athlete"):
    with SessionLocal() as db:
        rows = db.query(models.PlannedTraining).filter_by(user_id=_user_id(username)).order_by(models.PlannedTraining.date)
        return [(p.date, p.activity_type, p.planned_duration_minutes, p.notes, p.source) for p in rows]


def _add(row):
    with SessionLocal() as db:
        db.add(row)
        db.commit()
        return row.id


@pytest.fixture
def friend(client):
    """Another account with one planned session, one objective and one done session: (plan id, objective id, activity id)."""
    make_user(client, "friend", garmin=False)
    user_id = _user_id("friend")
    return (
        _add(models.PlannedTraining(user_id=user_id, date=day(1), activity_type="running", planned_duration_minutes=40)),
        _add(models.Objective(user_id=user_id, title="Friend's race")),
        _add(models.Activity(user_id=user_id, garmin_activity_id="f1", activity_type="running", name="Friend run",
                             start_time=datetime.datetime.combine(TODAY, datetime.time(7)), duration_seconds=1800)),
    )


# --- /mcp itself -------------------------------------------------------------------


def test_without_a_token_mcp_points_to_the_login(client):
    for headers in ({}, {"Authorization": "Bearer made-up"}, {"Authorization": "Basic abc"}):
        for path in ("/mcp", "/mcp/"):
            res = client.post(path, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                              headers={**ACCEPT, **headers}, follow_redirects=False)
            assert res.status_code == 401, (path, headers)
            assert res.headers["www-authenticate"] == (
                'Bearer resource_metadata="http://testserver/.well-known/oauth-protected-resource"')
            assert res.headers["cache-control"] == "no-store"
    assert client.get("/mcp").status_code == 401


def test_a_page_session_token_does_not_open_mcp(client, auth):
    """The connector only takes its own tokens, not the page's login."""
    res = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, headers={**ACCEPT, **auth})
    assert res.status_code == 401


def test_probes_are_answered_without_a_token(client):
    assert client.options("/mcp").status_code == 204
    assert client.head("/mcp").status_code == 200


def test_tools_are_listed_and_run_as_the_connected_account(client, auth):
    _client_id, tokens = connect(client)
    init = _call(client, tokens, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                                "clientInfo": {"name": "test", "version": "1"}})
    assert init["serverInfo"]["name"] == "training"

    tools = {tool["name"]: tool for tool in _call(client, tokens, "tools/list", path="/mcp/")["tools"]}
    assert set(tools) == {
        "connection_check", "get_overview", "get_training_log", "list_sessions", "get_session", "get_planned",
        "get_objectives", "get_rules", "get_draft", "add_planned", "update_planned", "delete_planned", "save_draft",
        "confirm_draft", "set_rules", "upsert_objective", "set_coach_notes", "sync_garmin",
    }
    for name in ("add_planned", "update_planned", "save_draft"):
        assert "No explanations" in tools[name]["description"], name  # the workout text format

    result = _call(client, tokens, "tools/call", {"name": "connection_check", "arguments": {}})
    assert "Connected to the Training App as athlete." in result["content"][0]["text"]
    assert mcp_auth.current_user_id.get() is None  # nothing left behind after the request


def test_every_tool_says_what_it_does_and_whether_it_changes_data():
    """Claude picks tools from their descriptions, and asks before running one that isn't read-only."""
    tools = mcp_server.build_server()._tool_manager.list_tools()
    assert len(tools) == 18
    for tool in tools:
        assert tool.description and len(tool.description) > 40, tool.name
        hints = json.loads(tool.annotations.model_dump_json()) if tool.annotations else {}
        reads = tool.name.startswith(("get_", "list_")) or tool.name == "connection_check"
        assert hints.get("readOnlyHint") is reads, tool.name
        assert (hints.get("destructiveHint") is True) is not reads, tool.name


# --- Reading -----------------------------------------------------------------------


def test_overview_has_what_a_chat_starts_from(tool, friend):
    user_id = _user_id("athlete")
    plan_id = _add(models.PlannedTraining(user_id=user_id, date=day(2), activity_type="running",
                                          planned_duration_minutes=75, notes="Tempo run\n3×10 min HR 160"))
    _add(models.PlannedTraining(user_id=user_id, date=day(30), activity_type="cycling", planned_duration_minutes=60))
    objective_id = _add(models.Objective(user_id=user_id, title="City 10k", event_date=day(60), target_time="42:00"))
    _add(models.Activity(user_id=user_id, garmin_activity_id="a1", activity_type="running", name="Morning run",
                         start_time=datetime.datetime.combine(day(-1), datetime.time(7)), duration_seconds=3000,
                         distance_meters=10000))
    _add(models.SleepRecord(user_id=user_id, date=TODAY, sleep_score=82, resting_heart_rate=44))
    tool("set_rules", rules="Long run on Sunday.")
    tool("set_coach_notes", notes="Felt tired on Tuesday.")

    text = tool("get_overview")
    assert f"Today is {basics.DAY_NAMES[TODAY.weekday()]}" in text and TODAY.isoformat() in text
    assert "COACH NOTES" in text and "Felt tired on Tuesday." in text
    assert f"id {objective_id} · City 10k · {day(60).isoformat()} (60 days away) · target 42:00" in text
    assert "Long run on Sunday." in text
    assert f"- {basics.fmt_day(TODAY)} · sleep 82" in text and "RHR 44" in text  # the last nights
    assert "Morning run" in text and "done  run 0:50" in text
    assert f"- id {plan_id} · {basics.fmt_day(day(2))} ({day(2).isoformat()}) · run 1:15 — Tempo run 3×10 min HR 160" in text
    assert "bike 1:00" not in text  # planned 30 days ahead: past the 14 days shown
    assert "Friend" not in text and "EARLIER HISTORY" not in text
    assert len(text) < 3000  # stays short


def test_overview_of_an_empty_account(tool):
    text = tool("get_overview")
    assert "- none yet" in text and "- nothing planned" in text and "- nothing written yet" in text


def test_training_log_is_the_history_tabs_text(client, auth, tool):
    page = client.get("/api/plan/log", params={"weeks": 3, "detail": "summary"}, headers=auth).json()["text"]
    assert tool("get_training_log", weeks=3) == page
    assert tool("get_training_log", weeks=3, detail="full") != page
    assert tool("get_training_log", weeks=40) == "weeks must be between 1 and 26."
    assert tool("get_training_log", detail="everything") == 'detail must be "summary" or "full".'


def test_sessions_are_listed_and_opened_only_for_the_account(tool, friend):
    user_id = _user_id("athlete")
    recent = _add(models.Activity(user_id=user_id, garmin_activity_id="a1", activity_type="running", name="Track",
                                  start_time=datetime.datetime.combine(day(-3), datetime.time(18)), duration_seconds=3600,
                                  laps=[{"distance": 1000, "duration": 240}]))
    _add(models.Activity(user_id=user_id, garmin_activity_id="a2", activity_type="cycling", name="Old ride",
                         start_time=datetime.datetime.combine(day(-45), datetime.time(9)), duration_seconds=7200))

    assert tool("list_sessions").splitlines() == [f"id {recent} · {basics.fmt_day(day(-3))} · run 1:00 — Track"]
    assert "Old ride" in tool("list_sessions", days=90) and "Friend" not in tool("list_sessions", days=90)
    assert tool("list_sessions", days=400) == "days must be between 1 and 90."

    assert "Track" in tool("get_session", activity_id=recent)
    _plan, _objective, friends_activity = friend
    assert tool("get_session", activity_id=friends_activity) == (
        f"No session with id {friends_activity}. list_sessions gives the ids.")


def test_planned_objectives_and_rules_can_be_read(tool, friend):
    user_id = _user_id("athlete")
    near = _add(models.PlannedTraining(user_id=user_id, date=day(3), activity_type="swimming", planned_duration_minutes=60))
    far = _add(models.PlannedTraining(user_id=user_id, date=day(40), activity_type="running", planned_duration_minutes=30))
    assert tool("get_planned").splitlines() == [
        f"PLANNED · {basics.fmt_day(TODAY)} to {basics.fmt_day(day(14))}",
        f"- id {near} · {basics.fmt_day(day(3))} ({day(3).isoformat()}) · swim 1:00",
    ]
    assert f"- id {far} ·" in tool("get_planned", start=day(30).isoformat(), end=day(50).isoformat())
    assert "- nothing planned" in tool("get_planned", start=day(100).isoformat())
    assert tool("get_planned", start="next week") == "Dates are written YYYY-MM-DD, like 2026-11-17."
    assert "on or after start" in tool("get_planned", start=day(5).isoformat(), end=day(1).isoformat())

    assert tool("get_objectives") == "No objectives yet."
    assert tool("get_rules") == "No rule set written yet."


# --- Planned sessions ---------------------------------------------------------------


def test_add_update_and_delete_a_planned_session(tool):
    added = tool("add_planned", date=day(1).isoformat(), activity_type="running", minutes=45,
                 notes="Easy run\n45 min HR under 145")
    assert added.startswith("Added: id ") and "run 0:45 — Easy run 45 min HR under 145" in added
    assert _planned() == [(day(1), "running", 45, "Easy run\n45 min HR under 145", "mcp")]
    plan_id = int(added.split()[2])

    moved = tool("update_planned", plan_id=plan_id, date=day(2).isoformat(), minutes=50)
    assert moved.startswith(f"Updated: id {plan_id} · {basics.fmt_day(day(2))}") and "run 0:50" in moved
    assert _planned() == [(day(2), "running", 50, "Easy run\n45 min HR under 145", "mcp")]
    tool("update_planned", plan_id=plan_id, notes="")
    assert _planned() == [(day(2), "running", 50, None, "mcp")]

    assert tool("delete_planned", plan_id=plan_id) == f"Deleted: {basics.fmt_day(day(2))} · run 0:50"
    assert _planned() == []


def test_bad_planned_input_is_answered_in_words(tool):
    assert tool("add_planned", date="tomorrow", activity_type="running", minutes=45).startswith("Not saved. date:")
    assert "planned_duration_minutes" in tool("add_planned", date=day(1).isoformat(), activity_type="running", minutes=0)
    assert tool("add_planned", date=day(1).isoformat(), activity_type="  ", minutes=30).startswith("Not saved.")
    assert _planned() == []
    added = tool("add_planned", date=day(1).isoformat(), activity_type="running", minutes=45)
    plan_id = int(added.split()[2])
    assert tool("update_planned", plan_id=plan_id).startswith("Nothing to change")
    assert tool("update_planned", plan_id=plan_id, minutes=-5).startswith("Not saved.")
    assert _planned() == [(day(1), "running", 45, None, "mcp")]


def test_another_accounts_rows_are_out_of_reach(tool, friend):
    plan_id, objective_id, _activity = friend
    missing = f"No planned session with id {plan_id}. get_planned lists them with their ids."
    assert tool("update_planned", plan_id=plan_id, minutes=5) == missing
    assert tool("delete_planned", plan_id=plan_id) == missing
    assert tool("upsert_objective", objective_id=objective_id, title="Mine now") == (
        f"No objective with id {objective_id}. get_objectives lists them.")
    assert _planned("friend") == [(day(1), "running", 40, None, "manual")]
    with SessionLocal() as db:
        assert db.get(models.Objective, objective_id).title == "Friend's race"
    assert tool("get_objectives") == "No objectives yet." and "- nothing planned" in tool("get_planned")


# --- Drafts --------------------------------------------------------------------------


def test_a_draft_is_saved_fixed_and_confirmed(client, auth, tool):
    assert tool("get_draft") == "No draft saved. Write one with save_draft."
    assert tool("confirm_draft") == "Not confirmed. Save a draft first. get_draft shows the draft."

    monday, tuesday = NEXT_MONDAY, NEXT_MONDAY + datetime.timedelta(days=1)
    lines = f"{monday.isoformat()} | run | 1:00 | Easy run\n{tuesday.isoformat()} | bike | soon | Easy ride"
    saved = tool("save_draft", start_date=monday.isoformat(), weeks=1, text=lines)
    assert "1 session read · 1 line with a problem" in saved and "LINES TO FIX" in saved and "- line 2" in saved
    assert "Fix the 1 line with a problem first" in tool("confirm_draft")
    assert _planned() == []

    _add(models.PlannedTraining(user_id=_user_id("athlete"), date=monday, activity_type="swimming",
                                        planned_duration_minutes=30))
    saved = tool("save_draft", start_date=monday.isoformat(), weeks=1, text=lines.replace("soon", "1:30"))
    assert "2 sessions read · 0 lines with a problem" in saved and "LINES TO FIX" not in saved
    assert "Confirming replaces the 1 planned session" in saved
    assert f"- week of {basics.fmt_day(monday)} · 2:30 (bike 1:30 · run 1:00)" in saved
    assert tool("get_draft") == saved
    assert client.get("/api/plan/draft", headers=auth).json()["sessions_count"] == 2  # the Plan tab sees the same draft

    done = tool("confirm_draft")
    assert done == (f"On the calendar: 2 sessions from {basics.fmt_day(monday)} to "
                    f"{basics.fmt_day(monday + datetime.timedelta(days=6))}, replacing 1 that were planned there.")
    assert _planned() == [(monday, "running", 60, "Easy run", "plan"), (tuesday, "cycling", 90, "Easy ride", "plan")]
    assert "on the calendar since it was confirmed" in tool("get_draft")
    assert tool("save_draft", start_date=monday.isoformat(), weeks=99, text="").startswith("Not saved. weeks:")


# --- Rules, objectives, coach notes, sync -------------------------------------------


def test_rules_are_replaced(client, auth, tool):
    assert tool("set_rules", rules="  Rest on Friday.  ") == "Rule set saved (15 characters)."
    assert tool("get_rules") == "Rest on Friday."
    assert client.get("/api/plan/rules", headers=auth).json()["rules"] == "Rest on Friday."
    assert tool("set_rules", rules="x" * 20001).startswith("Not saved. rules:")
    assert tool("get_rules") == "Rest on Friday."


def test_an_objective_is_added_then_changed(tool):
    assert tool("upsert_objective", notes="no title") == "A new objective needs a title."
    added = tool("upsert_objective", title=" Half marathon ", event_date=day(90).isoformat(), target_time="1:35")
    objective_id = int(added.split()[2])
    assert added == f"Added: id {objective_id} · Half marathon · {day(90).isoformat()} · target 1:35"

    changed = tool("upsert_objective", objective_id=objective_id, target_time="1:32", notes="Flat course")
    assert changed == f"Updated: id {objective_id} · Half marathon · {day(90).isoformat()} · target 1:32"
    listed = tool("get_objectives").splitlines()
    assert listed == [f"- id {objective_id} · Half marathon · {day(90).isoformat()} (90 days away) · target 1:32",
                      "  Flat course"]

    assert tool("upsert_objective", objective_id=objective_id, event_date="") .endswith("· no date · target 1:32")
    assert tool("upsert_objective", objective_id=objective_id, title="  ").startswith("Not saved. title:")
    assert tool("upsert_objective", objective_id=objective_id).startswith("Nothing to change")
    assert tool("upsert_objective", title="Bad date", event_date="june").startswith("Not saved. event_date:")


def test_coach_notes_are_replaced_and_capped(tool):
    assert tool("set_coach_notes", notes=" Knee sore after the long run. ") == "Coach notes saved (29 of 4000 characters)."
    assert tool("set_coach_notes", notes="x" * 4001) == (
        "Not saved: 4001 characters, the limit is 4000. Condense the older notes.")
    assert "Knee sore after the long run." in tool("get_overview")
    tool("set_coach_notes", notes="Knee fine again.")
    overview = tool("get_overview")
    assert "Knee fine again." in overview and "Knee sore" not in overview
    tool("set_coach_notes", notes="")
    with SessionLocal() as db:
        assert db.query(models.User).filter(models.User.username == "athlete").one().coach_notes is None


def test_coach_notes_column_is_added_to_an_existing_database():
    """A nullable column: database.add_missing_columns() can add it on the next start."""
    assert models.User.__table__.c.coach_notes.nullable


def test_sync_starts_in_the_background_and_answers_at_once(tool, monkeypatch):
    started = []
    monkeypatch.setattr(garmin_client, "get_authenticated_client", lambda session: "api")
    monkeypatch.setattr(autosync, "start_job", lambda user_id, api: started.append((user_id, api)) or {"state": "waiting"})
    assert tool("sync_garmin") == "Garmin sync started. It takes about a minute; then read the data again."
    assert started == [(_user_id("athlete"), "api")]

    monkeypatch.setattr(autosync, "job_status", lambda user_id: {"state": "running"})
    assert tool("sync_garmin").startswith("A Garmin sync is already running.")
    assert len(started) == 1


def test_sync_says_when_garmin_is_not_connected(client, tool, auth):
    assert client.delete("/api/garmin/connect", headers=auth).status_code == 200
    assert tool("sync_garmin") == (
        "Sync not started. Connect your Garmin account first. That is done on the app's page.")
    assert GARMIN_PASSWORD  # (the fixture connected Garmin with it before the disconnect above)
