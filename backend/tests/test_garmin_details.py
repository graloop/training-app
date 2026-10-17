import datetime

import pytest

from app import models
from app.database import SessionLocal
from app.garmin import client as garmin_client
from app.garmin import sync as garmin_sync

TODAY = datetime.date.today()


# --- Pure conversions ----------------------------------------------------------------


@pytest.mark.parametrize("rpe,feel,expected", [
    (70, 75, (7, 75)), (30, 0, (3, 0)), (100, 100, (10, 100)), (10, 50, (1, 50)),
    (None, None, (None, None)), (0, None, (None, None)), (None, 25, (None, 25)),
])
def test_effort_and_feel(rpe, feel, expected):
    summary = {"summaryDTO": {"directWorkoutRpe": rpe, "directWorkoutFeel": feel}}
    assert garmin_sync.effort_and_feel(summary) == expected


def test_effort_and_feel_tolerates_missing_parts():
    assert garmin_sync.effort_and_feel({}) == (None, None)
    assert garmin_sync.effort_and_feel({"summaryDTO": None}) == (None, None)


def test_compact_laps_keeps_only_what_matters():
    laps = [
        {"distance": 1600.0, "duration": 384.12, "averageHR": 175.0, "maxHR": 186.0, "averagePower": 500.0,
         "startTimeGMT": "x", "lengthDTOs": [1, 2, 3], "strideLength": 120.5},
        {"distance": 0.0, "duration": 20.0, "averageHR": None},
    ]
    assert garmin_sync.compact_laps(laps) == [
        {"distance": 1600, "duration": 384.1, "avg_hr": 175, "max_hr": 186, "avg_power": 500},
        {"distance": 0, "duration": 20.0},
    ]
    assert len(garmin_sync.compact_laps([{"distance": 100, "duration": 60}] * 500)) == garmin_sync.MAX_LAPS_STORED


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
    profile = garmin_sync.normalize_profile(RAW_PROFILE)
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
    assert garmin_sync.normalize_profile({"profile": None, "hr_zones": None, "records": None, "predictions": None}) is None
    assert garmin_sync.normalize_profile({}) is None
    only_zones = garmin_sync.normalize_profile({"hr_zones": [{"sport": "DEFAULT", "maxHeartRateUsed": 185}]})
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
    assert calls["profile"] == 1  # four requests to Garmin: refreshed once a day, not on every sync
    assert _calendar_activities(client, auth)["1"]["perceived_effort"] == 7  # kept when the list is re-synced


def test_the_profile_is_refreshed_again_the_next_day(client, auth, monkeypatch):
    calls = _stub(monkeypatch, [])
    client.post("/api/garmin/sync", headers=auth)
    with SessionLocal() as db:
        user = db.query(models.User).filter_by(username="athlete").one()
        user.garmin_profile = {**user.garmin_profile, "synced_on": (TODAY - datetime.timedelta(days=1)).isoformat()}
        db.commit()
    client.post("/api/garmin/sync", headers=auth)
    assert calls["profile"] == 2


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
    assert len(calls["summary"]) == garmin_sync.GIVE_UP_AFTER_FAILURES  # stops asking instead of hammering Garmin
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
    monkeypatch.setattr(garmin_sync, "MAX_ACTIVITIES_PER_SYNC", 3)
    calls = _stub(monkeypatch, [_activity(str(i), "cycling", days_ago=i) for i in range(1, 8)])
    # enrich_activities reads the limit from its default argument, so call it the way the sync does
    monkeypatch.setattr(garmin_sync.enrich_activities, "__defaults__", (3,))
    assert client.post("/api/garmin/sync", headers=auth).json()["details_synced"] == 3
    assert sorted(calls["summary"]) == ["1", "2", "3"]  # newest first
    assert client.post("/api/garmin/sync", headers=auth).json()["details_synced"] == 3
    assert client.post("/api/garmin/sync", headers=auth).json()["details_synced"] == 1


# --- Daily recovery and load: conversions (all numbers invented) ------------------


def test_morning_readiness_is_the_wake_up_reading():
    entries = [
        {"inputContext": "UPDATE_REALTIME_VARIABLES", "score": 80, "level": "HIGH", "feedbackShort": "RESTED_AND_READY",
         "recoveryTime": 500, "timestampLocal": "2026-01-05T21:00:00"},
        {"inputContext": "AFTER_POST_EXERCISE_RESET", "score": 40, "level": "LOW", "feedbackShort": "LISTEN_TO_YOUR_BODY",
         "recoveryTime": 1500, "timestampLocal": "2026-01-05T08:00:00"},
        {"inputContext": "AFTER_WAKEUP_RESET", "score": 66, "level": "MODERATE", "feedbackShort": "WELL_RECOVERED",
         "recoveryTime": 120, "timestampLocal": "2026-01-05T06:00:00"},
    ]
    assert garmin_sync.morning_readiness(entries) == {
        "readiness": 66, "readiness_level": "moderate", "readiness_feedback": "well recovered", "recovery_minutes": 120}
    # No wake-up reading (watch not worn overnight): the earliest one of the day.
    assert garmin_sync.morning_readiness(entries[:2])["readiness"] == 40
    assert garmin_sync.morning_readiness([]) == {}


STATUS = {
    "mostRecentTrainingStatus": {"latestTrainingStatusData": {
        "111": {"trainingStatusFeedbackPhrase": "UNPRODUCTIVE_2", "primaryTrainingDevice": False,
                "acuteTrainingLoadDTO": {"dailyTrainingLoadAcute": 1}},
        "222": {"trainingStatusFeedbackPhrase": "PRODUCTIVE_3", "primaryTrainingDevice": True,
                "acuteTrainingLoadDTO": {"dailyTrainingLoadAcute": 600, "dailyTrainingLoadChronic": 500,
                                         "dailyAcuteChronicWorkloadRatio": 1.2, "acwrStatus": "OPTIMAL"}},
    }},
    "mostRecentVO2Max": {"generic": {"vo2MaxPreciseValue": 51.4, "vo2MaxValue": 51.0}},
    "mostRecentTrainingLoadBalance": {"metricsTrainingLoadBalanceDTOMap": {"222": {
        "primaryTrainingDevice": True, "trainingBalanceFeedbackPhrase": "AEROBIC_HIGH_SHORTAGE",
        "monthlyLoadAerobicLow": 1200.4, "monthlyLoadAerobicLowTargetMin": 500, "monthlyLoadAerobicLowTargetMax": 1100,
        "monthlyLoadAerobicHigh": 300.2, "monthlyLoadAerobicHighTargetMin": 600, "monthlyLoadAerobicHighTargetMax": 1200,
        "monthlyLoadAnaerobic": 250.0, "monthlyLoadAnaerobicTargetMin": 200, "monthlyLoadAnaerobicTargetMax": 600}}},
}


def test_status_fields_use_the_primary_device():
    assert garmin_sync.status_fields(STATUS) == {
        "training_status": "productive", "acute_load": 600, "chronic_load": 500, "load_ratio": 1.2,
        "load_ratio_status": "optimal", "vo2max": 51.4,
        "load_focus": {"phrase": "aerobic high shortage", "low": 1200, "low_target": [500, 1100], "high": 300,
                       "high_target": [600, 1200], "anaerobic": 250, "anaerobic_target": [200, 600]},
    }
    assert garmin_sync.status_fields({}) == {}
    assert garmin_sync.status_fields({"mostRecentTrainingStatus": None, "mostRecentVO2Max": None}) == {}


def test_stats_and_weather_fields():
    assert garmin_sync.stats_fields({"averageStressLevel": 30, "bodyBatteryHighestValue": 95,
                                        "bodyBatteryLowestValue": 20, "totalSteps": 9000}) == {
        "stress_avg": 30, "body_battery_high": 95, "body_battery_low": 20, "steps": 9000}
    assert garmin_sync.stats_fields({"averageStressLevel": -1})["stress_avg"] is None  # Garmin's "no data"
    assert garmin_sync.weather_fields({"temp": 50, "windSpeed": 10, "relativeHumidity": 60.4}) == {
        "temp_c": 10, "wind_kmh": 16, "humidity": 60}
    assert garmin_sync.weather_fields({"temp": None}) == {}


def test_compact_laps_keep_kind_stroke_and_cadence():
    laps = [
        {"distance": 400.0, "duration": 90.0, "intensityType": "ACTIVE", "averageRunCadence": 181.6},
        {"distance": 200.0, "duration": 80.0, "intensityType": "RECOVERY"},
        {"distance": 1000.0, "duration": 300.0, "intensityType": "INTERVAL"},  # a manual lap: says nothing
        {"distance": 100.0, "duration": 95.0, "swimStroke": "BREASTSTROKE", "averageSWOLF": 41.0},
        {"distance": 0.0, "duration": 20.0, "averageSWOLF": 0.0},
    ]
    assert garmin_sync.compact_laps(laps) == [
        {"distance": 400, "duration": 90.0, "cadence": 182, "kind": "active"},
        {"distance": 200, "duration": 80.0, "kind": "recovery"},
        {"distance": 1000, "duration": 300.0},
        {"distance": 100, "duration": 95.0, "stroke": "breaststroke", "swolf": 41},
        {"distance": 0, "duration": 20.0},
    ]


# --- Daily recovery and load: during a sync ---------------------------------------


def _stub_days(monkeypatch, lookback=5, sleep_days=None, refuse=()):
    """Garmin answering the per-day calls. `refuse` names the calls that raise."""
    from app.routers import garmin as garmin_router

    calls = {"sleep": [], "readiness": [], "status": [], "stats": [], "weather": []}
    monkeypatch.setattr(garmin_router.settings, "garmin_sync_lookback_days", lookback)
    monkeypatch.setattr(garmin_client, "get_authenticated_client", lambda s: object())
    monkeypatch.setattr(garmin_client, "fetch_activities", lambda api, a, b: [])
    monkeypatch.setattr(garmin_client, "fetch_profile", lambda api: {})

    def sleep(api, day):
        calls["sleep"].append(day)
        if sleep_days is not None and day not in sleep_days:
            return None
        return {"dailySleepDTO": {"sleepTimeSeconds": 27000, "sleepScores": {"overall": {"value": 80}}}, "restingHeartRate": 50}

    def make(name, payload):
        def call(api, day):
            calls[name].append(day)
            if name in refuse:
                raise RuntimeError("429")
            return payload
        return call

    monkeypatch.setattr(garmin_client, "fetch_sleep", sleep)
    monkeypatch.setattr(garmin_client, "fetch_training_readiness", make("readiness", [
        {"inputContext": "AFTER_WAKEUP_RESET", "score": 66, "level": "MODERATE", "feedbackShort": "WELL_RECOVERED", "recoveryTime": 120}]))
    monkeypatch.setattr(garmin_client, "fetch_training_status", make("status", STATUS))
    monkeypatch.setattr(garmin_client, "fetch_daily_stats", make("stats", {"averageStressLevel": 30, "bodyBatteryHighestValue": 95,
                                                                            "bodyBatteryLowestValue": 20, "totalSteps": 9000}))
    return calls


def _days_ago(n):
    return TODAY - datetime.timedelta(days=n)


def test_sync_stores_sleep_and_recovery_for_each_day(client, auth, monkeypatch):
    calls = _stub_days(monkeypatch, lookback=5)
    res = client.post("/api/garmin/sync", headers=auth).json()
    assert res["days_synced"] == 6 and res["sleep_records_synced"] == 6 and res["more_to_sync"] is False
    assert calls["sleep"] == [_days_ago(i) for i in range(6)]  # newest first
    with SessionLocal() as db:
        rows = {m.date: m for m in db.query(models.DailyMetrics)}
        day = rows[_days_ago(3)]
        assert (day.readiness, day.readiness_level, day.training_status) == (66, "moderate", "productive")
        assert (day.acute_load, day.chronic_load, day.load_ratio, day.stress_avg) == (600, 500, 1.2, 30)
        assert (day.body_battery_low, day.body_battery_high, day.vo2max) == (20, 95, 51.4)
        assert day.load_focus["phrase"] == "aerobic high shortage"
        assert [rows[_days_ago(i)].final for i in range(6)] == [False, False, True, True, True, True]
        assert db.query(models.SleepRecord).count() == 6


def test_later_syncs_only_ask_for_today_and_yesterday(client, auth, monkeypatch):
    _stub_days(monkeypatch, lookback=5)
    client.post("/api/garmin/sync", headers=auth)
    calls = _stub_days(monkeypatch, lookback=5)
    res = client.post("/api/garmin/sync", headers=auth).json()
    for name in ("sleep", "readiness", "status", "stats"):
        assert calls[name] == [TODAY, _days_ago(1)], name  # these two can still change
    assert res["days_synced"] == 2 and res["more_to_sync"] is False
    with SessionLocal() as db:
        assert db.query(models.DailyMetrics).count() == 6 and db.query(models.SleepRecord).count() == 6  # updated, not duplicated


def test_a_long_backlog_is_fetched_over_several_syncs(client, auth, monkeypatch):
    monkeypatch.setattr(garmin_sync.sync_days, "__defaults__", (4,))  # 4 days per sync
    calls = _stub_days(monkeypatch, lookback=9)
    first = client.post("/api/garmin/sync", headers=auth).json()
    assert first["days_synced"] == 4 and first["more_to_sync"] is True
    assert calls["sleep"] == [_days_ago(i) for i in range(4)]
    second = client.post("/api/garmin/sync", headers=auth).json()
    assert second["more_to_sync"] is True
    assert calls["sleep"][4:] == [TODAY, _days_ago(1), _days_ago(4), _days_ago(5)]  # the open days, then older ones
    third = client.post("/api/garmin/sync", headers=auth).json()
    fourth = client.post("/api/garmin/sync", headers=auth).json()
    assert third["more_to_sync"] is True and fourth["more_to_sync"] is False
    with SessionLocal() as db:
        assert db.query(models.DailyMetrics).count() == 10


def test_a_part_garmin_refuses_is_retried_a_few_times_then_dropped(client, auth, monkeypatch):
    calls = _stub_days(monkeypatch, lookback=3, refuse=("readiness",))
    assert client.post("/api/garmin/sync", headers=auth).status_code == 200
    with SessionLocal() as db:
        old = db.query(models.DailyMetrics).filter_by(date=_days_ago(3)).one()
        assert old.readiness is None and old.stress_avg == 30 and old.final is False  # kept what did answer
    client.post("/api/garmin/sync", headers=auth)
    client.post("/api/garmin/sync", headers=auth)
    with SessionLocal() as db:
        assert db.query(models.DailyMetrics).filter_by(date=_days_ago(3)).one().final is True  # gave up after 3 tries
    asked = len(calls["readiness"])
    client.post("/api/garmin/sync", headers=auth)
    assert calls["readiness"][asked:] == [TODAY, _days_ago(1)]  # the old day is left alone now


def test_days_without_sleep_data_are_not_asked_again(client, auth, monkeypatch):
    calls = _stub_days(monkeypatch, lookback=4, sleep_days={_days_ago(1)})
    res = client.post("/api/garmin/sync", headers=auth).json()
    assert res["sleep_records_synced"] == 1 and res["days_synced"] == 5
    client.post("/api/garmin/sync", headers=auth)
    assert calls["sleep"][5:] == [TODAY, _days_ago(1)]  # the empty nights of 2-4 days ago are settled


def test_garmin_down_for_the_day_calls_stops_early_and_keeps_the_sync(client, auth, monkeypatch):
    calls = _stub_days(monkeypatch, lookback=20, sleep_days=set(), refuse=("readiness", "status", "stats"))
    res = client.post("/api/garmin/sync", headers=auth)
    assert res.status_code == 200 and res.json()["more_to_sync"] is True
    assert len(calls["sleep"]) == garmin_sync.GIVE_UP_AFTER_FAILURES


def test_sweat_and_weather_for_outdoor_sessions(client, auth, monkeypatch):
    _stub(monkeypatch, [{**_activity("1", "running"), "startLatitude": 46.0}, _activity("2", "lap_swimming")])
    monkeypatch.setattr(garmin_client, "fetch_activity_summary",
                        lambda api, aid: {"summaryDTO": {"directWorkoutRpe": 50, "directWorkoutFeel": 50, "waterEstimated": 800.0}})
    weather_calls = []
    monkeypatch.setattr(garmin_client, "fetch_activity_weather",
                        lambda api, aid: weather_calls.append(aid) or {"temp": 41, "windSpeed": 5, "relativeHumidity": 70})
    client.post("/api/garmin/sync", headers=auth)
    with SessionLocal() as db:
        by_id = {a.garmin_activity_id: a for a in db.query(models.Activity)}
        assert by_id["1"].extras == {"sweat_ml": 800, "temp_c": 5, "wind_kmh": 8, "humidity": 70}
        assert by_id["2"].extras == {"sweat_ml": 800}  # indoors: no weather asked
    assert weather_calls == ["1"]


# --- Importing older activities ----------------------------------------------------


def test_import_history(client, auth, monkeypatch):
    from app.routers import garmin as garmin_router

    monkeypatch.setattr(garmin_router.settings, "garmin_sync_lookback_days", 90)
    asked = []
    old = [_activity("old1", "running", days_ago=200), _activity("old2", "cycling", days_ago=120)]

    def activities(api, start, end):
        asked.append((start, end))
        return old

    monkeypatch.setattr(garmin_client, "get_authenticated_client", lambda s: object())
    monkeypatch.setattr(garmin_client, "fetch_activities", activities)
    summaries = []
    monkeypatch.setattr(garmin_client, "fetch_activity_summary", lambda api, aid: summaries.append(aid) or {})

    res = client.post("/api/garmin/import-history", json={"months": 12}, headers=auth)
    assert res.status_code == 200 and res.json()["activities_imported"] == 2
    since, until = asked[0]
    assert until == TODAY - datetime.timedelta(days=90)  # stops where the regular sync begins
    assert 360 <= (TODAY - since).days <= 370 and res.json()["since"] == since.isoformat()
    assert summaries == []  # one list, no per-activity requests
    with SessionLocal() as db:
        assert db.query(models.Activity).count() == 2
    assert client.post("/api/garmin/import-history", json={"months": 12}, headers=auth).json()["activities_imported"] == 2
    with SessionLocal() as db:
        assert db.query(models.Activity).count() == 2  # importing twice doesn't duplicate

    # Old history isn't queued for effort/feel/laps: that would be hundreds of requests.
    monkeypatch.setattr(garmin_client, "fetch_activities", lambda api, a, b: [])
    monkeypatch.setattr(garmin_client, "fetch_sleep", lambda api, d: None)
    res = client.post("/api/garmin/sync", headers=auth).json()
    assert summaries == [] and res["details_synced"] == 0

    assert client.post("/api/garmin/import-history", json={"months": 2}, headers=auth).json()["activities_imported"] == 0
    assert client.post("/api/garmin/import-history", json={"months": 99}, headers=auth).status_code == 422


def test_import_history_needs_garmin_and_a_regular_account(client, monkeypatch):
    from conftest import admin_auth, make_user

    friend = make_user(client, "friend", garmin=False)
    assert client.post("/api/garmin/import-history", json={"months": 6}, headers=friend).status_code == 409
    assert client.post("/api/garmin/import-history", json={"months": 6}, headers=admin_auth(client)).status_code == 403


def test_details_fetched_by_an_older_version_are_refreshed_once(client, auth, monkeypatch):
    calls = _stub(monkeypatch, [_activity("1", "running", laps=5)])
    client.post("/api/garmin/sync", headers=auth)
    with SessionLocal() as db:  # as an older version left it: effort and old-style laps, no extras
        a = db.query(models.Activity).one()
        a.extras = None
        a.laps = [{"distance": 1000, "duration": 240.0}]
        db.commit()
    with SessionLocal() as db:
        assert db.query(models.Activity).filter(models.Activity.extras.is_(None)).count() == 1  # a real NULL, findable
    before = (len(calls["summary"]), len(calls["laps"]))
    res = client.post("/api/garmin/sync", headers=auth).json()
    assert res["details_synced"] == 1
    assert (len(calls["summary"]), len(calls["laps"])) == (before[0] + 1, before[1] + 1)
    with SessionLocal() as db:
        a = db.query(models.Activity).one()
        assert a.extras == {} and len(a.laps) == 2  # indoors-less stub: no weather, laps re-read in the new format
    res = client.post("/api/garmin/sync", headers=auth).json()
    assert res["details_synced"] == 0 and len(calls["summary"]) == before[0] + 1  # and never again after that


# --- Keeping what is read, and Garmin's refreshed session ---------------------------


FULL_NIGHT = {
    "dailySleepDTO": {"sleepTimeSeconds": 27000, "sleepScores": {"overall": {"value": 80}}},
    "restingHeartRate": 48, "avgOvernightHrv": 61.0, "hrvStatus": "BALANCED", "bodyBatteryChange": 55,
    "sleepMovement": [{"activityLevel": 0.1}] * 2000, "sleepHeartRate": [{"value": 50}] * 500,
}


def test_only_the_parts_of_a_night_that_are_read_are_stored(client, auth, monkeypatch):
    _stub_days(monkeypatch, lookback=0)
    monkeypatch.setattr(garmin_client, "fetch_sleep", lambda api, day: FULL_NIGHT)
    client.post("/api/garmin/sync", headers=auth)
    with SessionLocal() as db:
        night = db.query(models.SleepRecord).one()
        assert set(night.raw_json) == set(garmin_sync.SLEEP_KEPT)
        assert night.sleep_score == 80 and night.resting_heart_rate == 48
    text = client.get("/api/plan/log", headers=auth).json()["text"]
    assert "HRV 61 balanced · body battery +55 overnight" in text


def test_nights_stored_whole_by_an_older_version_are_trimmed(client, auth):
    with SessionLocal() as db:
        user = db.query(models.User).filter_by(username="athlete").one()
        db.add(models.SleepRecord(user_id=user.id, date=TODAY, sleep_score=80, raw_json=FULL_NIGHT))
        db.add(models.SleepRecord(user_id=user.id, date=_days_ago(1), sleep_score=70, raw_json={"avgOvernightHrv": 50}))
        db.commit()
        assert garmin_sync.trim_stored_sleep(db) == 1  # the small one is left alone
        assert garmin_sync.trim_stored_sleep(db) == 0
        night = db.query(models.SleepRecord).filter_by(date=TODAY).one()
        assert night.raw_json["avgOvernightHrv"] == 61.0 and "sleepMovement" not in night.raw_json


def test_a_refreshed_garmin_session_is_kept(client, auth, monkeypatch):
    from app.security import decrypt_text

    _stub(monkeypatch, [])
    monkeypatch.setattr(garmin_client, "export_session", lambda api: "refreshed-session")
    client.post("/api/garmin/sync", headers=auth)
    with SessionLocal() as db:
        user = db.query(models.User).filter_by(username="athlete").one()
        assert decrypt_text(user.garmin_session_encrypted) == "refreshed-session"
        assert garmin_sync.save_session(db, object(), user) is False  # unchanged: nothing written


def test_a_client_that_cannot_export_keeps_the_stored_session(client, auth, monkeypatch):
    from app.security import decrypt_text

    _stub(monkeypatch, [])
    assert garmin_client.export_session(object()) is None
    client.post("/api/garmin/sync", headers=auth)
    with SessionLocal() as db:
        user = db.query(models.User).filter_by(username="athlete").one()
        assert decrypt_text(user.garmin_session_encrypted) == "session-for-athlete@example.com"
