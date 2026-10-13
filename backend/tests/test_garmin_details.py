import datetime

import pytest

from app import garmin_client, garmin_details, models
from app.database import SessionLocal

TODAY = datetime.date.today()


# --- Pure conversions ----------------------------------------------------------------


@pytest.mark.parametrize("rpe,feel,expected", [
    (70, 75, (7, 75)), (30, 0, (3, 0)), (100, 100, (10, 100)), (10, 50, (1, 50)),
    (None, None, (None, None)), (0, None, (None, None)), (None, 25, (None, 25)),
])
def test_effort_and_feel(rpe, feel, expected):
    summary = {"summaryDTO": {"directWorkoutRpe": rpe, "directWorkoutFeel": feel}}
    assert garmin_details.effort_and_feel(summary) == expected


def test_effort_and_feel_tolerates_missing_parts():
    assert garmin_details.effort_and_feel({}) == (None, None)
    assert garmin_details.effort_and_feel({"summaryDTO": None}) == (None, None)


def test_compact_laps_keeps_only_what_matters():
    laps = [
        {"distance": 1600.0, "duration": 384.12, "averageHR": 175.0, "maxHR": 186.0, "averagePower": 500.0,
         "startTimeGMT": "x", "lengthDTOs": [1, 2, 3], "strideLength": 120.5},
        {"distance": 0.0, "duration": 20.0, "averageHR": None},
    ]
    assert garmin_details.compact_laps(laps) == [
        {"distance": 1600, "duration": 384.1, "avg_hr": 175, "max_hr": 186, "avg_power": 500},
        {"distance": 0, "duration": 20.0},
    ]
    assert len(garmin_details.compact_laps([{"distance": 100, "duration": 60}] * 500)) == garmin_details.MAX_LAPS_STORED


RAW_PROFILE = {
    "profile": {"userData": {"weight": 70500.0, "height": 178.4, "vo2MaxRunning": 52.0, "vo2MaxCycling": None,
                             "lactateThresholdHeartRate": 170, "lactateThresholdSpeed": 0.3703704}},
    "hr_zones": [
        {"sport": "DEFAULT", "maxHeartRateUsed": 190, "zone1Floor": 95, "zone2Floor": 114, "zone3Floor": 133,
         "zone4Floor": 152, "zone5Floor": 171},
        {"sport": "RUNNING", "maxHeartRateUsed": 192, "zone1Floor": 96, "zone2Floor": 115, "zone3Floor": 134,
         "zone4Floor": 154, "zone5Floor": 173},
    ],
    "records": [
        {"typeId": 1, "value": 27.3, "prStartTimeGmtFormatted": "2025-12-09T10:00:00.0"},   # a glitch type we don't list
        {"typeId": 3, "value": 1200.4, "prStartTimeGmtFormatted": "2024-05-01T08:00:00.0"},  # 5 km in 20:00
        {"typeId": 4, "value": 600.0, "prStartTimeGmtFormatted": "2024-06-01T08:00:00.0"},   # 10 km in 10 min: impossible
        {"typeId": 8, "value": 150000.0, "prStartTimeGmtFormatted": None},
        {"typeId": 10, "value": 250.8, "prStartTimeGmtFormatted": "2023-08-12T08:00:00.0"},
        {"typeId": 99, "value": 5},
    ],
    "predictions": {"time5K": 1210, "time10K": 2520, "timeHalfMarathon": None, "calendarDate": "2026-01-01"},
}


def test_normalize_profile():
    profile = garmin_details.normalize_profile(RAW_PROFILE)
    assert profile["weight_kg"] == 70.5 and profile["height_cm"] == 178
    assert profile["vo2max_running"] == 52 and "vo2max_cycling" not in profile
    assert profile["lactate_threshold_hr"] == 170
    assert profile["threshold_pace_s_per_km"] == 270  # 0.37037 is stored /10: 3.7037 m/s = 4:30 /km
    assert profile["max_hr"] == 192 and profile["hr_zone_floors"] == [96, 115, 134, 154, 173]  # the running zones
    assert profile["records"] == [
        {"label": "5 km", "kind": "time", "value": 1200.4, "date": "2024-05-01"},
        {"label": "longest ride", "kind": "distance", "value": 150000.0, "date": None},
        {"label": "best 20 min power", "kind": "power", "value": 250.8, "date": "2023-08-12"},
    ]
    assert profile["race_predictions"] == {"5 km": 1210, "10 km": 2520}
    assert profile["synced_on"] == TODAY.isoformat()


def test_normalize_profile_with_nothing_or_partial_data():
    assert garmin_details.normalize_profile({"profile": None, "hr_zones": None, "records": None, "predictions": None}) is None
    assert garmin_details.normalize_profile({}) is None
    only_zones = garmin_details.normalize_profile({"hr_zones": [{"sport": "DEFAULT", "maxHeartRateUsed": 185}]})
    assert only_zones["max_hr"] == 185 and "hr_zone_floors" not in only_zones


def test_fetch_profile_survives_missing_calls():
    class PartialApi:  # an account/library where only one call works
        def get_user_profile(self):
            return {"userData": {"weight": 60000.0}}

        def connectapi(self, path):
            raise RuntimeError("403")

    raw = garmin_client.fetch_profile(PartialApi())
    assert raw["profile"] == {"userData": {"weight": 60000.0}}
    assert raw["hr_zones"] is None and raw["records"] is None and raw["predictions"] is None


# --- During a sync -------------------------------------------------------------------


def _activity(aid, kind, days_ago=1, laps=1):
    day = TODAY - datetime.timedelta(days=days_ago)
    return {"activityId": aid, "activityName": f"Act {aid}", "activityType": {"typeKey": kind},
            "startTimeLocal": f"{day} 07:00:00", "duration": 3600.0, "distance": 10000.0, "lapCount": laps}


def _stub(monkeypatch, activities, summaries=None, fail_summary=False):
    calls = {"summary": [], "laps": [], "profile": 0}

    def summary(api, aid):
        calls["summary"].append(aid)
        if fail_summary:
            raise RuntimeError("429 Too Many Requests")
        rpe, feel = (summaries or {}).get(aid, (60, 75))
        return {"summaryDTO": {"directWorkoutRpe": rpe, "directWorkoutFeel": feel}}

    def laps(api, aid):
        calls["laps"].append(aid)
        return [{"distance": 1000.0, "duration": 240.0, "averageHR": 170.0}, {"distance": 400.0, "duration": 150.0}]

    def profile(api):
        calls["profile"] += 1
        return RAW_PROFILE

    monkeypatch.setattr(garmin_client, "get_authenticated_client", lambda s: object())
    monkeypatch.setattr(garmin_client, "fetch_activities", lambda api, a, b: activities)
    monkeypatch.setattr(garmin_client, "fetch_sleep", lambda api, d: None)
    monkeypatch.setattr(garmin_client, "fetch_activity_summary", summary)
    monkeypatch.setattr(garmin_client, "fetch_activity_laps", laps)
    monkeypatch.setattr(garmin_client, "fetch_profile", profile)
    return calls


def _calendar_activities(client, auth):
    days = client.get("/api/calendar", headers=auth).json()["days"]
    return {a["garmin_activity_id"]: a for d in days for a in d["activities"]}


def test_sync_fetches_effort_feel_laps_and_profile_once(client, auth, monkeypatch):
    calls = _stub(monkeypatch, [_activity("1", "track_running", laps=8), _activity("2", "cycling", laps=12),
                                _activity("3", "lap_swimming", laps=20), _activity("4", "running", laps=1)],
                  summaries={"1": (70, 75), "2": (30, 50), "3": (40, 100), "4": (20, 25)})
    res = client.post("/api/garmin/sync", headers=auth).json()
    assert res["activities_synced"] == 4 and res["details_synced"] == 4
    acts = _calendar_activities(client, auth)
    assert (acts["1"]["perceived_effort"], acts["1"]["feel"]) == (7, 75)
    assert (acts["2"]["perceived_effort"], acts["2"]["feel"]) == (3, 50)
    assert sorted(calls["summary"]) == ["1", "2", "3", "4"]
    assert sorted(calls["laps"]) == ["1", "3"]  # run and swim with several laps; not the ride, not a 1-lap run
    with SessionLocal() as db:
        by_id = {a.garmin_activity_id: a for a in db.query(models.Activity)}
        assert by_id["1"].laps == [{"distance": 1000, "duration": 240.0, "avg_hr": 170}, {"distance": 400, "duration": 150.0}]
        assert by_id["2"].laps is None and by_id["4"].laps is None
        user = db.query(models.User).filter_by(username="athlete").one()
        assert user.garmin_profile["max_hr"] == 192

    # A second sync asks Garmin for nothing it already has.
    res = client.post("/api/garmin/sync", headers=auth).json()
    assert res["details_synced"] == 0 and len(calls["summary"]) == 4 and len(calls["laps"]) == 2
    assert calls["profile"] == 2  # the profile is cheap and can change: refreshed each time
    assert _calendar_activities(client, auth)["1"]["perceived_effort"] == 7  # kept when the list is re-synced


def test_effort_answered_later_is_picked_up(client, auth, monkeypatch):
    calls = _stub(monkeypatch, [_activity("1", "running", days_ago=0), _activity("2", "running", days_ago=10)],
                  summaries={"1": (None, None), "2": (None, None)})
    client.post("/api/garmin/sync", headers=auth)
    assert _calendar_activities(client, auth)["1"]["perceived_effort"] is None

    _stub(monkeypatch, [_activity("1", "running", days_ago=0), _activity("2", "running", days_ago=10)],
          summaries={"1": (50, 75), "2": (90, 0)})
    res = client.post("/api/garmin/sync", headers=auth).json()
    acts = _calendar_activities(client, auth)
    assert acts["1"]["perceived_effort"] == 5 and res["details_synced"] == 1  # today's is looked at again
    assert acts["2"]["perceived_effort"] is None  # an old one without an answer is left alone
    assert calls["summary"].count("1") == 1


def test_garmin_refusing_details_does_not_fail_the_sync(client, auth, monkeypatch):
    calls = _stub(monkeypatch, [_activity(str(i), "running") for i in range(1, 9)], fail_summary=True)
    res = client.post("/api/garmin/sync", headers=auth)
    assert res.status_code == 200
    assert res.json()["activities_synced"] == 8 and res.json()["details_synced"] == 0
    assert len(calls["summary"]) == garmin_details.GIVE_UP_AFTER_FAILURES  # stops asking instead of hammering Garmin
    assert len(_calendar_activities(client, auth)) == 8  # the activities themselves are saved
    with SessionLocal() as db:
        assert db.query(models.Activity).filter(models.Activity.details_synced_at.is_not(None)).count() == 0
        assert db.query(models.User).filter_by(username="athlete").one().garmin_profile["max_hr"] == 192  # still saved


def test_a_profile_problem_does_not_lose_the_activity_details(client, auth, monkeypatch):
    _stub(monkeypatch, [_activity("1", "running")])

    def broken(api):
        raise RuntimeError("profile endpoint moved")

    monkeypatch.setattr(garmin_client, "fetch_profile", broken)
    res = client.post("/api/garmin/sync", headers=auth)
    assert res.status_code == 200 and res.json()["details_synced"] == 1
    assert _calendar_activities(client, auth)["1"]["perceived_effort"] == 6


def test_only_a_batch_per_sync(client, auth, monkeypatch):
    monkeypatch.setattr(garmin_details, "MAX_ACTIVITIES_PER_SYNC", 3)
    calls = _stub(monkeypatch, [_activity(str(i), "cycling", days_ago=i) for i in range(1, 8)])
    # enrich_activities reads the limit from its default argument, so call it the way the sync does
    monkeypatch.setattr(garmin_details.enrich_activities, "__defaults__", (3,))
    assert client.post("/api/garmin/sync", headers=auth).json()["details_synced"] == 3
    assert sorted(calls["summary"]) == ["1", "2", "3"]  # newest first
    assert client.post("/api/garmin/sync", headers=auth).json()["details_synced"] == 3
    assert client.post("/api/garmin/sync", headers=auth).json()["details_synced"] == 1
