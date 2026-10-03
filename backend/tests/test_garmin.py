import datetime

from app import garmin_client


# Bound at import, before conftest's autouse fixture swaps in the fake.
real_login_and_export_session = garmin_client.login_and_export_session


def test_expired_garmin_session_sends_user_back_to_login(client, auth, monkeypatch):
    def expired(session):
        raise garmin_client.GarminAuthError("401 from Garmin")

    monkeypatch.setattr(garmin_client, "get_authenticated_client", expired)
    res = client.post("/api/garmin/sync", headers=auth)
    assert res.status_code == 401  # the frontend treats this as "log in again"
    assert "log in again" in res.json()["detail"]


def test_sync_stores_and_dedupes(client, auth, monkeypatch):
    today = datetime.date.today()
    yesterday = today - datetime.timedelta(days=1)

    seen_sessions = []
    monkeypatch.setattr(garmin_client, "get_authenticated_client", lambda s: seen_sessions.append(s) or object())
    monkeypatch.setattr(
        garmin_client,
        "fetch_activities",
        lambda api, start, end: [
            {
                "activityId": 111,
                "activityName": "Morning Run",
                "activityType": {"typeKey": "running"},
                "startTimeLocal": f"{yesterday} 07:00:00",
                "duration": 3600.0,
                "distance": 10000.0,
                "averageHR": 150.0,
                "aerobicTrainingEffect": 3.2,
                "anaerobicTrainingEffect": 1.1,
                "calories": 700,
                "activityTrainingLoad": 112.6,
            },
            {"activityId": 222, "activityType": {"typeKey": "lap_swimming"}},  # no start time -> skipped
        ],
    )
    monkeypatch.setattr(
        garmin_client,
        "fetch_sleep",
        lambda api, day: (
            {"dailySleepDTO": {"sleepTimeSeconds": 28800, "sleepScores": {"overall": {"value": 82}}},
             "restingHeartRate": 47}
            if day == yesterday
            else None
        ),
    )

    for _ in range(2):  # second sync must update in place, not duplicate
        res = client.post("/api/garmin/sync", headers=auth)
        assert res.status_code == 200, res.text
        assert res.json()["activities_synced"] == 1
        assert res.json()["sleep_records_synced"] == 1

    assert seen_sessions == ["session-for-athlete@example.com"] * 2  # session from login, decrypted for use

    days = {d["date"]: d for d in client.get("/api/calendar", headers=auth).json()["days"]}
    day = days[yesterday.isoformat()]
    assert len(day["activities"]) == 1
    assert day["activities"][0]["activity_type"] == "running"
    assert day["sleep"]["sleep_score"] == 82
    assert day["sleep"]["resting_heart_rate"] == 47
    assert day["activities"][0]["training_load"] == 112.6


# --- garmin_client against fake garth objects --------------------------------


class FakeGarth:
    def __init__(self, *a, **kw):
        self.loaded = None
        self.profile = {"displayName": "abc-123", "fullName": "Test Athlete"}

    def loads(self, s):
        self.loaded = s


def test_resumed_client_has_display_name(monkeypatch):
    """garminconnect builds the sleep URL from display_name; without it every
    sleep fetch hits .../dailySleepData/None and silently returns nothing."""
    monkeypatch.setattr(garmin_client.garth, "Client", FakeGarth)
    api = garmin_client.get_authenticated_client("blob")
    assert api.garth.loaded == "blob"
    assert api.display_name == "abc-123"


def test_mfa_does_not_block_on_stdin(monkeypatch):
    class MfaGarth(FakeGarth):
        def login(self, email, password, prompt_mfa=None):
            assert prompt_mfa is not None, "garth would fall back to input() and hang the request"
            prompt_mfa()

    monkeypatch.setattr(garmin_client.garth, "Client", MfaGarth)
    try:
        real_login_and_export_session("me@garmin.com", "pw")
    except garmin_client.GarminAuthError as exc:
        assert "MFA" in str(exc)
    else:
        raise AssertionError("expected GarminAuthError")


def _stub_sync(monkeypatch, fail=False):
    def activities(api, start, end):
        if fail:
            raise RuntimeError("Garmin is down")
        return []

    monkeypatch.setattr(garmin_client, "get_authenticated_client", lambda s: object())
    monkeypatch.setattr(garmin_client, "fetch_activities", activities)
    monkeypatch.setattr(garmin_client, "fetch_sleep", lambda api, day: None)


def _seed_chat(client, auth, fake_claude):
    from conftest import text_response

    fake_claude(text_response("hello"))
    client.post("/api/chat", json={"message": "hi"}, headers=auth)
    assert len(client.get("/api/chat/history", headers=auth).json()["messages"]) == 2


def test_sync_clears_the_chat(client, auth, monkeypatch, fake_claude):
    _seed_chat(client, auth, fake_claude)
    _stub_sync(monkeypatch)
    assert client.post("/api/garmin/sync", headers=auth).status_code == 200
    assert client.get("/api/chat/history", headers=auth).json()["messages"] == []


def test_failed_sync_keeps_the_chat(client, auth, monkeypatch, fake_claude):
    _seed_chat(client, auth, fake_claude)
    _stub_sync(monkeypatch, fail=True)
    assert client.post("/api/garmin/sync", headers=auth).status_code == 502
    assert len(client.get("/api/chat/history", headers=auth).json()["messages"]) == 2


def test_sync_only_clears_own_chat(client, auth, monkeypatch, fake_claude):
    from conftest import login

    other = login(client, email="other@example.com")
    from conftest import text_response

    fake_claude(text_response("yo"))
    client.post("/api/chat", json={"message": "mine"}, headers=other)
    _stub_sync(monkeypatch)
    client.post("/api/garmin/sync", headers=auth)
    assert len(client.get("/api/chat/history", headers=other).json()["messages"]) == 2
