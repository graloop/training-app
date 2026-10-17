"""Multisport legs, recovery numbers kept off the calendar, the gentler planned-vs-done
matching, and the background sync."""

import datetime
import threading

import pytest
from conftest import admin_auth, make_user

from app import models
from app.garmin import autosync
from app.garmin import client as garmin_client
from app.planning import training_log
from app.database import SessionLocal

TODAY = datetime.date.today()


def _quiet_garmin(monkeypatch, activities=(), summaries=None, fail_ids=()):
    """Garmin answering every call; `summaries` maps activity id -> detail."""
    calls = {"summary": [], "activities": 0}

    def summary(api, aid):
        calls["summary"].append(str(aid))
        if str(aid) in fail_ids:
            raise RuntimeError("429 Too Many Requests")
        return (summaries or {}).get(str(aid), {"summaryDTO": {}})

    def fetch_activities(api, start, end):
        calls["activities"] += 1
        return list(activities)

    monkeypatch.setattr(garmin_client, "get_authenticated_client", lambda s: object())
    monkeypatch.setattr(garmin_client, "fetch_activities", fetch_activities)
    monkeypatch.setattr(garmin_client, "fetch_sleep", lambda api, d: None)
    monkeypatch.setattr(garmin_client, "fetch_training_readiness", lambda api, d: [])
    monkeypatch.setattr(garmin_client, "fetch_training_status", lambda api, d: {})
    monkeypatch.setattr(garmin_client, "fetch_daily_stats", lambda api, d: {})
    monkeypatch.setattr(garmin_client, "fetch_activity_summary", summary)
    monkeypatch.setattr(garmin_client, "fetch_activity_laps", lambda api, aid: [])
    monkeypatch.setattr(garmin_client, "fetch_activity_weather", lambda api, aid: {})
    monkeypatch.setattr(garmin_client, "fetch_profile", lambda api: {})
    return calls


def _listed(aid, kind, day, hours):
    return {"activityId": aid, "activityName": f"Act {aid}", "activityType": {"typeKey": kind},
            "startTimeLocal": f"{day} 07:00:00", "duration": hours * 3600, "distance": 1000.0}


# --- Multisport races ------------------------------------------------------------


def _leg(kind, minutes, km, start, hr=None):
    return {"activityTypeDTO": {"typeKey": kind},
            "summaryDTO": {"duration": minutes * 60.0, "distance": km * 1000, "averageHR": hr, "startTimeGMT": start}}


def _race_summaries(race_id="900"):
    # Garmin may name the legs in any order: they are sorted by start time.
    return {
        race_id: {"metadataDTO": {"childIds": [905, 901, 902, 903, 904]}, "summaryDTO": {}},
        "901": _leg("open_water_swimming", 40, 1.9, "2026-06-01T11:00:00.0", 150),
        "902": _leg("transition_v2", 3, 0, "2026-06-01T11:40:00.0"),
        "903": _leg("road_biking", 160, 90, "2026-06-01T11:43:00.0", 140),
        "904": _leg("transition_v2", 2, 0, "2026-06-01T14:23:00.0"),
        "905": _leg("running", 105, 21.1, "2026-06-01T14:25:00.0", 155),
    }


def test_a_race_gets_its_legs_and_counts_them_per_sport(client, auth, monkeypatch):
    race_day = TODAY - datetime.timedelta(days=2)
    calls = _quiet_garmin(monkeypatch, [_listed("900", "multi_sport", race_day, 5.17)], _race_summaries())
    assert client.post("/api/garmin/sync", headers=auth).status_code == 200

    days = client.get("/api/calendar", headers=auth, params={"start": race_day, "end": race_day}).json()["days"]
    legs = days[0]["activities"][0]["legs"]
    assert [(leg["sport"], leg["duration_seconds"] / 60) for leg in legs] == [
        ("swim", 40), ("other", 3), ("bike", 160), ("other", 2), ("run", 105)]
    assert legs[0] == {"type": "open_water_swimming", "sport": "swim", "duration_seconds": 2400.0,
                       "distance_meters": 1900.0, "avg_hr": 150.0}

    log = client.get("/api/plan/log", headers=auth, params={"weeks": 2, "today": TODAY}).json()["text"]
    assert "legs: swim 0:40 1.9 km avg HR 150 · T1 0:03 · bike 2:40 90.0 km avg HR 140 · T2 0:02 · run 1:45 21.1 km avg HR 155" in log
    assert "(swim 0:40 · bike 2:40 · run 1:45 · other 0:05)" in log  # the week's split, not one "other" block

    asked = len(calls["summary"])
    client.post("/api/garmin/sync", headers=auth)
    assert not set(calls["summary"][asked:]) & {"901", "902", "903", "904", "905"}  # legs are fetched once


def test_old_races_get_their_legs_too(client, auth, monkeypatch):
    """Imported history is older than the details window; races still matter."""
    race_day = TODAY - datetime.timedelta(days=200)
    _quiet_garmin(monkeypatch, [_listed("900", "multi_sport", race_day, 5.17)], _race_summaries())
    client.post("/api/garmin/sync", headers=auth)
    with SessionLocal() as db:
        race = db.query(models.Activity).filter_by(garmin_activity_id="900").one()
        assert len(race.legs) == 5 and race.details_synced_at is None  # legs only, no other details
    log = client.get("/api/plan/log", headers=auth, params={"weeks": 2, "today": TODAY}).json()["text"]
    assert "(swim 0:40 · bike 2:40 · run 1:45 · other 0:05)" in log  # in the earlier weeks' totals


def test_a_race_without_legs_is_not_asked_again(client, auth, monkeypatch):
    calls = _quiet_garmin(monkeypatch, [_listed("900", "multi_sport", TODAY - datetime.timedelta(days=3), 2)],
                          {"900": {"summaryDTO": {}}})
    client.post("/api/garmin/sync", headers=auth)
    client.post("/api/garmin/sync", headers=auth)
    with SessionLocal() as db:
        assert db.query(models.Activity).filter_by(garmin_activity_id="900").one().extras["legs"] == []
    assert calls["summary"].count("900") <= 2  # its details once, its legs once: no endless asking


def test_a_leg_garmin_refuses_is_tried_again_next_sync(client, auth, monkeypatch):
    race_day = TODAY - datetime.timedelta(days=200)
    _quiet_garmin(monkeypatch, [_listed("900", "multi_sport", race_day, 5.17)], _race_summaries(), fail_ids={"903"})
    assert client.post("/api/garmin/sync", headers=auth).status_code == 200  # the sync itself is fine
    with SessionLocal() as db:
        assert "legs" not in (db.query(models.Activity).filter_by(garmin_activity_id="900").one().extras or {})
    _quiet_garmin(monkeypatch, [_listed("900", "multi_sport", race_day, 5.17)], _race_summaries())
    client.post("/api/garmin/sync", headers=auth)
    with SessionLocal() as db:
        assert len(db.query(models.Activity).filter_by(garmin_activity_id="900").one().legs) == 5


# --- Readiness and HRV stay in the history, off the calendar ---------------------------


def test_calendar_days_leave_readiness_and_hrv_to_the_history(client, auth):
    with SessionLocal() as db:
        user = db.query(models.User).filter_by(username="athlete").one()
        db.add(models.DailyMetrics(user_id=user.id, date=TODAY, readiness=72, attempts=1))
        db.add(models.SleepRecord(user_id=user.id, date=TODAY, sleep_score=80,
                                  raw_json={"avgOvernightHrv": 83.4, "hrvStatus": "BALANCED"}))
        db.commit()
    yesterday = TODAY - datetime.timedelta(days=1)
    days = {d["date"]: d for d in client.get("/api/calendar", headers=auth,
                                             params={"start": yesterday, "end": TODAY}).json()["days"]}
    today = days[TODAY.isoformat()]
    assert today["sleep"]["sleep_score"] == 80
    assert "readiness" not in today and "hrv" not in today
    assert "readiness 72" in client.get("/api/plan/log", headers=auth).json()["text"]


# --- Planned vs done ------------------------------------------------------------------------


def _plan(pid, kind, minutes):
    return models.PlannedTraining(id=pid, date=TODAY, activity_type=kind, planned_duration_minutes=minutes)


def _done(aid, kind, minutes):
    return models.Activity(id=aid, garmin_activity_id=str(aid), activity_type=kind,
                           start_time=datetime.datetime.combine(TODAY, datetime.time(7)), duration_seconds=minutes * 60)


def test_same_sport_at_another_duration_still_counts_as_done():
    run = _done(1, "running", 70)
    matched = training_log.matched_plans([_plan(10, "running", 90)], [run])
    assert matched == {10: run}
    assert training_log._fmt_planned(_plan(10, "running", 90), run) == "run 1:30 (done, 1:10 instead of 1:30)"
    assert training_log._fmt_planned(_plan(10, "running", 90), _done(2, "running", 85)) == "run 1:30 (done as planned)"


def test_split_sessions_are_not_added_up():
    """1:03 + 0:29 of swimming against a planned 1:30: the closer one does it, the other stays its own session."""
    long_swim, short_swim = _done(1, "lap_swimming", 63), _done(2, "lap_swimming", 29)
    assert training_log.matched_plans([_plan(10, "swimming", 90)], [short_swim, long_swim]) == {10: long_swim}


def test_as_planned_is_matched_first():
    """Two runs planned: the one done within 15% keeps its match even if listed second."""
    easy, long_ = _done(1, "running", 40), _done(2, "running", 115)
    matched = training_log.matched_plans([_plan(10, "running", 45), _plan(11, "running", 120)], [long_, easy])
    assert matched == {10: easy, 11: long_}


def test_other_sports_and_missing_durations_are_never_matched():
    assert training_log.matched_plans([_plan(10, "running", 60)], [_done(1, "cycling", 60)]) == {}
    assert training_log.matched_plans([_plan(10, "yoga", 60)], [_done(1, "yoga", 60)]) == {}
    assert training_log.matched_plans([_plan(10, "running", 60)], [_done(1, "running", 0)]) == {}


# --- Background sync ----------------------------------------------------------------------


def test_only_connected_regular_accounts_are_synced(client, monkeypatch):
    make_user(client, "connected")
    make_user(client, "not-connected", garmin=False)
    admin_auth(client)
    with SessionLocal() as db:
        ids = {u.username: u.id for u in db.query(models.User)}
    assert autosync.connected_accounts() == [ids["connected"]]


def test_background_sync_brings_in_activities(client, monkeypatch):
    make_user(client, "connected")
    calls = _quiet_garmin(monkeypatch, [_listed("1", "running", TODAY, 1)])
    monkeypatch.setattr(autosync, "PAUSE_BETWEEN_ACCOUNTS_SECONDS", 0)
    assert autosync.sync_all() == 1
    with SessionLocal() as db:
        assert db.query(models.Activity).count() == 1
    # A new account has 90 days of sleep and recovery to catch up: a few rounds...
    assert calls["activities"] == autosync.ROUNDS_PER_ACCOUNT
    # ...after which a run needs only one.
    autosync.sync_all()
    assert calls["activities"] == autosync.ROUNDS_PER_ACCOUNT + 1


def test_one_account_failing_does_not_stop_the_others(client, monkeypatch):
    make_user(client, "expired")
    make_user(client, "fine")
    _quiet_garmin(monkeypatch, [_listed("1", "running", TODAY, 1)])

    def client_for(session):
        if "expired" in session:
            raise garmin_client.GarminAuthError("401 from Garmin")
        return object()

    monkeypatch.setattr(garmin_client, "get_authenticated_client", client_for)
    monkeypatch.setattr(autosync, "PAUSE_BETWEEN_ACCOUNTS_SECONDS", 0)
    assert autosync.sync_all() == 1
    with SessionLocal() as db:
        owners = {a.user.username for a in db.query(models.Activity)}
    assert owners == {"fine"}


def test_an_account_being_synced_is_not_synced_twice_at_once(client, auth, monkeypatch):
    _quiet_garmin(monkeypatch)
    with SessionLocal() as db:
        user_id = db.query(models.User).filter_by(username="athlete").one().id
    lock = autosync.user_lock(user_id)
    lock.acquire()
    try:
        assert autosync.sync_account(user_id) is False  # the background skips it
        monkeypatch.setattr(autosync, "MANUAL_WAIT_SECONDS", 0.1)
        res = client.post("/api/garmin/sync", headers=auth)  # the button waits, then says so
        assert res.status_code == 503 and "already running" in res.json()["detail"]
    finally:
        lock.release()
    assert client.post("/api/garmin/sync", headers=auth).status_code == 200


def test_background_sync_starts_only_when_asked(monkeypatch):
    started = []
    monkeypatch.setattr(autosync, "_started", False)
    monkeypatch.setattr(threading, "Thread", lambda **kw: type("T", (), {"start": lambda self: started.append(kw)})())
    assert autosync.start(0) is False and started == []
    assert autosync.start(4) is True and started[0]["args"] == (4,) and started[0]["daemon"] is True
    assert autosync.start(4) is False  # once per process
    assert len(started) == 1


def test_off_by_default_outside_docker():
    from app.config import Settings

    assert Settings().auto_sync_hours == 0


# --- The Sync button: a job on the server, the page asks how it goes ------------------


def _wait_for_job(client, auth, seconds=10):
    import time

    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        job = client.get("/api/garmin/sync/status", headers=auth).json()
        if job["state"] in ("done", "failed"):
            return job
        time.sleep(0.02)
    raise AssertionError(f"the sync job didn't finish: {job}")


def test_sync_job_runs_on_the_server_until_nothing_waits(client, auth, monkeypatch):
    calls = _quiet_garmin(monkeypatch, [_listed("1", "running", TODAY, 1)])
    assert client.get("/api/garmin/sync/status", headers=auth).json()["state"] == "idle"
    started = client.post("/api/garmin/sync/start", headers=auth)
    assert started.status_code == 200 and started.json()["state"] in ("waiting", "running", "done")
    job = _wait_for_job(client, auth)
    # a new account has 90 days of sleep and recovery: 30 days per batch, so a few batches in one job
    assert job["state"] == "done" and job["rounds"] == calls["activities"] > 1
    assert job["more_to_sync"] is False and job["activities_synced"] == 1
    assert job["days_synced"] >= 91  # today and yesterday are asked again in each batch: they can still change
    assert job["finished_at"] and job["error"] is None
    with SessionLocal() as db:
        assert db.query(models.Activity).count() == 1


def test_starting_again_while_a_job_runs_returns_that_job(client, auth, monkeypatch):
    calls = _quiet_garmin(monkeypatch)
    with SessionLocal() as db:
        user_id = db.query(models.User).filter_by(username="athlete").one().id
    lock = autosync.user_lock(user_id)
    lock.acquire()  # a background run of this account is going on
    try:
        first = client.post("/api/garmin/sync/start", headers=auth).json()
        again = client.post("/api/garmin/sync/start", headers=auth).json()
        assert first["state"] == again["state"] == "waiting" and first["started_at"] == again["started_at"]
    finally:
        lock.release()
    assert _wait_for_job(client, auth)["state"] == "done"
    rounds = calls["activities"]
    client.post("/api/garmin/sync/start", headers=auth)  # once finished, a new press starts a new job
    _wait_for_job(client, auth)
    assert calls["activities"] == rounds + 1  # nothing waits any more: one batch


def test_a_failing_sync_job_says_why(client, auth, monkeypatch):
    _quiet_garmin(monkeypatch)

    def down(api, start, end):
        raise RuntimeError("503 Service Unavailable")

    monkeypatch.setattr(garmin_client, "fetch_activities", down)
    client.post("/api/garmin/sync/start", headers=auth)
    job = _wait_for_job(client, auth)
    assert job["state"] == "failed" and "503 Service Unavailable" in job["error"]


def test_sync_job_needs_a_garmin_connection(client):
    headers = make_user(client, "unlinked", garmin=False)
    res = client.post("/api/garmin/sync/start", headers=headers)
    assert res.status_code == 409  # the page shows "Connect your Garmin account"
    assert client.get("/api/garmin/sync/status", headers=headers).json()["state"] == "idle"
    assert client.post("/api/garmin/sync/start", headers=admin_auth(client)).status_code == 403


def test_each_account_sees_only_its_own_job(client, auth, monkeypatch):
    _quiet_garmin(monkeypatch)
    other = make_user(client, "other")
    client.post("/api/garmin/sync/start", headers=auth)
    _wait_for_job(client, auth)
    assert client.get("/api/garmin/sync/status", headers=other).json()["state"] == "idle"
