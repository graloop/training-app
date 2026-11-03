"""Reps never go missing, older weeks shrink, and a session's second-by-second
data comes from its file, without positions, and goes away after 28 days."""

import datetime
import io
import struct
import zipfile

import pytest
from conftest import make_user

from app import models
from app.database import SessionLocal
from app.garmin import activity_file
from app.garmin import client as garmin_client
from app.garmin import sync as garmin_sync
from app.planning import activity_text
from app.planning.basics import monday_of
from app.planning.session_detail import session_text, trace_lines
from app.planning.training_log import build_log

TODAY = datetime.date.today()
AT = datetime.datetime.combine(TODAY - datetime.timedelta(days=1), datetime.time(7))


# --- A tiny FIT file, by the book ---------------------------------------------------

FIT_EPOCH = datetime.datetime(1989, 12, 31, tzinfo=datetime.UTC)
START = int((datetime.datetime(2026, 10, 1, 7, tzinfo=datetime.UTC) - FIT_EPOCH).total_seconds())
_CRC_TABLE = (0x0000, 0xCC01, 0xD801, 0x1400, 0xF001, 0x3C00, 0x2800, 0xE401,
              0xA001, 0x6C00, 0x7800, 0xB401, 0x5000, 0x9C01, 0x8801, 0x4400)


def _crc(data: bytes) -> int:
    crc = 0
    for byte in data:
        for nibble in (byte & 0xF, byte >> 4):
            tmp = _CRC_TABLE[crc & 0xF]
            crc = ((crc >> 4) & 0x0FFF) ^ tmp ^ _CRC_TABLE[nibble]
    return crc


def _definition(local: int, message: int, fields: list[tuple[int, int, int]]) -> bytes:
    return struct.pack("<BBBHB", 0x40 | local, 0, 0, message, len(fields)) + b"".join(struct.pack("BBB", *f) for f in fields)


def make_fit(seconds: int = 60, power: bool = True) -> bytes:
    """One lap of `seconds` seconds, a record each second, with a position to be ignored."""
    record = [(253, 4, 0x86), (0, 4, 0x85), (1, 4, 0x85), (3, 1, 0x02), (73, 4, 0x86), (7, 2, 0x84), (4, 1, 0x02)]
    lap = [(253, 4, 0x86), (2, 4, 0x86), (7, 4, 0x86)]
    body = _definition(0, 20, record)
    for i in range(seconds):
        body += struct.pack("<BIiiBIHB", 0, START + i, 515000000, -880000000, 120 + i // 5, 3000 + i, 200 if power else 0xFFFF, 85)
    body += _definition(1, 19, lap) + struct.pack("<BIII", 1, START + seconds, START, seconds * 1000)
    head = struct.pack("<BBHI4s", 14, 0x20, 2132, len(body), b".FIT")
    head += struct.pack("<H", _crc(head))
    return head + body + struct.pack("<H", _crc(head + body))


def zipped(data: bytes) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        archive.writestr("123_ACTIVITY.fit", data)
    return out.getvalue()


# --- Reading a file -----------------------------------------------------------------


def test_a_file_becomes_samples_without_positions():
    read = activity_file.read_trace(zipped(make_fit(60)), running=True)
    samples = read["samples"]
    assert samples["t"] == list(range(0, 60, 5))
    assert samples["hr"][0] == 120 and samples["speed"][0] == 3.0 and samples["power"][0] == 200
    assert samples["cadence"][0] == 170  # a FIT file counts one foot
    assert read["laps"] == [{"start": 0, "duration": 60.0}]
    assert not {"position_lat", "position_long", "lat", "long"} & set(samples)


def test_a_series_the_file_lacks_is_left_out():
    assert "power" not in activity_file.read_trace(make_fit(30, power=False))["samples"]


@pytest.mark.parametrize("data", [b"", b"not a file", zipped(b"not a fit file"), make_fit()[:60]])
def test_a_bad_file_says_so(data):
    with pytest.raises(activity_file.UnreadableFile):
        activity_file.read_trace(data)


def test_a_zip_with_no_fit_file_says_so():
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        archive.writestr("ride.gpx", "<gpx/>")
    with pytest.raises(activity_file.UnreadableFile):
        activity_file.read_trace(out.getvalue())


# --- Reps -----------------------------------------------------------------------------


def _lap(metres, pace, **more):
    return {"distance": metres, "duration": round(metres / 1000 * pace, 1), **more}


FADING = (220, 222, 225, 228, 232, 236, 240, 245, 250)  # 3:40 to 4:10 /km


def _activity(laps, kind="track_running", raw=None, **more):
    return models.Activity(activity_type=kind, laps=laps, raw_json=raw or {}, start_time=AT, **more)


def _thousands(activity):
    _, work = activity_text.rep_laps(activity)
    return [lap for lap in work if lap["distance"] == 1000]


def test_one_fast_strider_does_not_hide_the_slower_reps():
    laps = [_lap(2000, 360), _lap(200, 170)]
    for pace in FADING:
        laps += [_lap(1000, pace), _lap(200, 390)]
    assert len(_thousands(_activity([*laps, _lap(1500, 370)]))) == 9


def test_reps_garmin_did_not_label_still_count():
    laps = [_lap(2000, 360, kind="warmup")]
    for i, pace in enumerate(FADING):
        laps += [_lap(1000, pace, kind="active") if i < 6 else _lap(1000, pace), _lap(200, 390, kind="recovery")]
    assert len(_thousands(_activity(laps))) == 9


def test_a_slow_lap_without_a_label_is_not_a_rep():
    laps = [_lap(1000, 240, kind="active"), _lap(400, 400, kind="recovery"), _lap(1000, 241, kind="active"), _lap(2000, 380)]
    assert len(activity_text.rep_laps(_activity(laps))[1]) == 2


def test_when_garmin_counted_more_reps_every_lap_is_shown():
    laps = [_lap(1000, 220), _lap(200, 400)] * 3 + [_lap(1000, 221)]
    raw = {"splitSummaries": [{"splitType": "INTERVAL_ACTIVE", "noOfSplits": 9}]}
    a = _activity(laps, raw=raw)
    assert activity_text.rep_laps(a)[1] is None
    assert sum("1.00 km" in part for line in activity_text.detail_lines(a) for part in line.split("|")) == 4


def test_every_lap_of_an_interval_session_is_written_with_its_heart_rate():
    laps = [_lap(2000, 360, avg_hr=130, max_hr=140), _lap(1000, 220, avg_hr=170, max_hr=178), _lap(200, 400, avg_hr=150, max_hr=160),
            _lap(1000, 222, avg_hr=172, max_hr=180), _lap(1500, 370, avg_hr=135, max_hr=142)]
    text = "\n".join(activity_text.detail_lines(_activity(laps)))
    assert "1.00 km 3:40 (3:40 /km, HR 170 max 178)" in text
    for easy in ("2.00 km 12:00 (6:00 /km, HR 130 max 140)", "200 m 1:20 (6:40 /km, HR 150 max 160)", "1.50 km 9:15 (6:10 /km, HR 135 max 142)"):
        assert easy in text


def _zwift_laps():
    laps = [{"distance": 4000, "duration": 600.0, "avg_power": 150, "avg_hr": 120}]
    for i in range(5):
        laps += [{"distance": 5000, "duration": 480.0, "avg_power": 280 - i, "norm_power": 282, "avg_hr": 160 + i, "max_hr": 170, "cadence": 92},
                 {"distance": 1500, "duration": 240.0, "avg_power": 130, "avg_hr": 135}]
    return laps


def test_a_ride_shows_watts_and_speed_never_a_pace():
    text = "\n".join(activity_text.detail_lines(_activity(_zwift_laps(), kind="virtual_ride")))
    assert "reps: 5.00 km 8:00 (280 W NP 282, 37.5 km/h, HR 160 max 170, 92 rpm)" in text
    assert "other laps (warm-up, recoveries, cool-down): 6, 30:00: 4.00 km 10:00 (150 W, 24.0 km/h, HR 120)" in text
    assert "/km" not in text.replace("km/h", "")


def test_a_trainer_without_distance_still_shows_its_laps():
    laps = [{"distance": 0, "duration": 300.0, "avg_power": p, "avg_hr": 140} for p in (150, 300, 150, 300, 150)]
    text = "\n".join(activity_text.detail_lines(_activity(laps, kind="indoor_cycling")))
    assert "reps: 5:00 (300 W, HR 140) | 5:00 (300 W, HR 140)" in text


def test_an_outdoor_ride_cut_every_5_km_stays_on_one_line():
    laps = [{"distance": 5000, "duration": 560.0 + i * 9, "avg_power": 180 + i} for i in range(6)] + [{"distance": 2100, "duration": 250.0}]
    assert activity_text.detail_lines(_activity(laps, kind="road_biking")) == []
    assert activity_text.detail_lines(_activity(laps, kind="virtual_ride")) != []  # Zwift's laps can be the workout


@pytest.mark.parametrize("laps,kind,expected", [
    ([_lap(1000, 240, avg_hr=170), _lap(200, 400), _lap(1000, 240, avg_hr=170), _lap(200, 400), _lap(1000, 240, avg_hr=170)],
     "running", "reps: 3 × 1000 m avg 4:00 /km HR 170"),
    ([_lap(1000, 240), _lap(200, 400), _lap(400, 230), _lap(200, 400), _lap(600, 235)], "running", "reps: 3, 2.00 km in all, avg 3:56 /km"),
    (_zwift_laps(), "virtual_ride", "reps: 5 × 8:00 avg 278 W HR 162"),
])
def test_rep_summary(laps, kind, expected):
    assert activity_text.rep_summary(_activity(laps, kind=kind)) == expected


def test_a_steady_session_has_no_rep_summary():
    assert activity_text.rep_summary(_activity([_lap(1000, 330) for _ in range(5)])) is None


# --- During a sync --------------------------------------------------------------------


def _raw(aid, kind, days_ago=1):
    day = TODAY - datetime.timedelta(days=days_ago)
    return {"activityId": aid, "activityType": {"typeKey": kind}, "startTimeLocal": f"{day} 07:00:00", "duration": 3600.0}


def _stub(monkeypatch, activities, file=None):
    asked = []

    def fetch(api, aid):
        asked.append(aid)
        return file(aid) if file else zipped(make_fit())

    monkeypatch.setattr(garmin_client, "get_authenticated_client", lambda s: object())
    monkeypatch.setattr(garmin_client, "fetch_activities", lambda api, a, b: activities)
    monkeypatch.setattr(garmin_client, "fetch_sleep", lambda api, d: None)
    monkeypatch.setattr(garmin_client, "fetch_activity_summary", lambda api, aid: {})
    monkeypatch.setattr(garmin_client, "fetch_activity_file", fetch)
    return asked


@pytest.fixture
def auth(client):
    return make_user(client, "athlete")


def test_recent_sessions_get_a_trace_once(client, auth, monkeypatch):
    asked = _stub(monkeypatch, [_raw("run", "running"), _raw("ride", "virtual_ride", 27), _raw("swim", "lap_swimming"),
                                _raw("gym", "strength_training"), _raw("old", "running", 29)])
    client.post("/api/garmin/sync", headers=auth)
    assert sorted(asked) == ["ride", "run", "swim"]  # not strength, not older than 28 days
    with SessionLocal() as db:
        assert db.query(models.ActivityTrace).count() == 3
        assert db.query(models.ActivityTrace).first().samples["hr"]
    client.post("/api/garmin/sync", headers=auth)
    assert len(asked) == 3


def test_only_a_batch_of_files_per_sync(client, auth, monkeypatch):
    asked = _stub(monkeypatch, [_raw(str(i), "running", i % 27) for i in range(garmin_sync.MAX_TRACES_PER_SYNC + 5)])
    client.post("/api/garmin/sync", headers=auth)
    assert len(asked) == garmin_sync.MAX_TRACES_PER_SYNC
    client.post("/api/garmin/sync", headers=auth)
    assert len(asked) == garmin_sync.MAX_TRACES_PER_SYNC + 5


def test_a_file_that_fails_does_not_fail_the_sync_or_block_the_others(client, auth, monkeypatch):
    def file(aid):
        if aid == "bad":
            raise RuntimeError("404")
        return b"not a file" if aid == "gpx" else zipped(make_fit())

    asked = _stub(monkeypatch, [_raw("bad", "running"), _raw("gpx", "running"), _raw("good", "running", 2)], file)
    assert client.post("/api/garmin/sync", headers=auth).status_code == 200
    with SessionLocal() as db:
        stored = {a.garmin_activity_id: a.trace for a in db.query(models.Activity)}
        assert stored["good"].samples and stored["bad"].attempts == 1 and stored["gpx"].attempts == 1
    for _ in range(garmin_sync.MAX_TRACE_ATTEMPTS + 2):
        client.post("/api/garmin/sync", headers=auth)
    assert asked.count("bad") == garmin_sync.MAX_TRACE_ATTEMPTS and asked.count("good") == 1  # then it stops asking


def test_garmin_refusing_every_file_stops_early(client, auth, monkeypatch):
    def file(aid):
        raise RuntimeError("429 Too Many Requests")

    asked = _stub(monkeypatch, [_raw(str(i), "running") for i in range(8)], file)
    assert client.post("/api/garmin/sync", headers=auth).status_code == 200
    assert len(asked) == garmin_sync.GIVE_UP_AFTER_FAILURES


def test_traces_go_after_28_days_and_the_session_and_its_laps_stay(client, auth):
    with SessionLocal() as db:
        user = db.query(models.User).filter_by(username="athlete").one()
        for name, days in (("old", 29), ("new", 27)):
            day = datetime.datetime.combine(TODAY - datetime.timedelta(days=days), datetime.time(7))
            activity = models.Activity(user_id=user.id, garmin_activity_id=name, activity_type="running", start_time=day, laps=[_lap(1000, 240)])
            activity.trace = models.ActivityTrace(attempts=0, fetched_at=day, samples={"t": [0]}, laps=[])
            db.add(activity)
        db.commit()
        assert garmin_sync.purge_old_traces(db, TODAY) == 1
        assert [t.activity.garmin_activity_id for t in db.query(models.ActivityTrace)] == ["new"]
        assert db.query(models.Activity).filter_by(garmin_activity_id="old").one().laps


def test_deleting_an_account_deletes_its_traces(client, auth):
    with SessionLocal() as db:
        user = db.query(models.User).filter_by(username="athlete").one()
        activity = models.Activity(user_id=user.id, garmin_activity_id="1", activity_type="running", start_time=AT)
        activity.trace = models.ActivityTrace(attempts=0, samples={"t": [0]})
        db.add(activity)
        db.commit()
        db.delete(user)
        db.commit()
        assert db.query(models.ActivityTrace).count() == 0


# --- In the log, and one session on the page -----------------------------------------


def _session_with_trace(user_id):
    """2 × a 2-minute rep with a minute between, heart rate climbing inside each."""
    laps = [{"distance": 500, "duration": 120.0, "avg_hr": 170, "kind": "active"}, {"distance": 100, "duration": 60.0, "kind": "recovery"},
            {"distance": 500, "duration": 120.0, "avg_hr": 172, "kind": "active"}]
    times = list(range(0, 300, 5))
    activity = models.Activity(user_id=user_id, garmin_activity_id="t", activity_type="track_running", name="Reps",
                               start_time=AT, duration_seconds=300, distance_meters=1100, laps=laps)
    activity.trace = models.ActivityTrace(
        attempts=0, fetched_at=AT, laps=[{"start": 0, "duration": 120.0}, {"start": 120, "duration": 60.0}, {"start": 180, "duration": 120.0}],
        samples={"t": times, "hr": [150 + i // 4 for i in range(len(times))], "speed": [4.0 if i < 12 else 4.2 for i in range(len(times))]})
    return activity


def test_the_log_says_what_happened_inside_each_rep(client, auth):
    with SessionLocal() as db:
        user = db.query(models.User).filter_by(username="athlete").one()
        db.add(_session_with_trace(user.id))
        db.commit()
    text = client.get("/api/plan/log", params={"weeks": 2}, headers=auth).json()["text"]
    line = next(line for line in text.splitlines() if "within reps" in line)
    assert line.strip().startswith("within reps (HR start→peak, pace first→second half): 1) 150→155, 4:10→3:58 | 2) 159→164, 3:58→3:58")
    assert "One session in detail" in text
    assert "second half" not in client.get("/api/plan/log", params={"weeks": 2, "detail": "summary"}, headers=auth).json()["text"]


def test_a_steady_session_shows_its_drift():
    times = list(range(0, 3600, 5))
    a = _activity(None, kind="running")
    trace = models.ActivityTrace(samples={"t": times, "hr": [140 if s < 2100 else 150 for s in times], "speed": [3.0] * len(times)}, laps=[])
    assert trace_lines(a, trace) == ["first vs second half (after 10 min): HR 140→150, 5:33→5:33, HR for the same effort +7.1%"]


def test_laps_that_do_not_line_up_with_the_file_say_nothing():
    a = _session_with_trace(1)
    a.trace.laps = a.trace.laps[:2]
    assert trace_lines(a, a.trace) == []


def _seed_weeks(user_id):
    rows = []
    for weeks_ago in (0, 3, 4, 8):
        day = monday_of(TODAY) - datetime.timedelta(weeks=weeks_ago)
        laps = [_lap(1000, 240, avg_hr=170), _lap(200, 400, avg_hr=140)] * 3
        rows.append(models.Activity(user_id=user_id, garmin_activity_id=str(weeks_ago), activity_type="running", name=f"W{weeks_ago}",
                                    start_time=datetime.datetime.combine(day, datetime.time(0)), duration_seconds=1000, distance_meters=3600,
                                    laps=laps, raw_json={"maxHR": 180.0}, details_synced_at=AT, extras={}))
    return rows


def test_full_detail_for_4_weeks_then_a_line_with_the_reps(client, auth):
    with SessionLocal() as db:
        user = db.query(models.User).filter_by(username="athlete").one()
        db.add_all(_seed_weeks(user.id))
        db.commit()
        text, _ = build_log(db, user, 12, TODAY)
        summary, _ = build_log(db, user, 12, TODAY, "summary")
    sections = text.split("WEEK OF ")
    section = {f"W{n}": next(s for s in sections if f"— W{n}" in s) for n in (0, 3, 4, 8)}
    for recent in ("W0", "W3"):
        assert "reps: 1.00 km 4:00 (4:00 /km, HR 170)" in section[recent] and "max HR 180" in section[recent]
    for older in ("W4", "W8"):
        assert "reps: 3 × 1000 m avg 4:00 /km HR 170" in section[older] and "max HR 180" not in section[older]
    assert "reps:" not in summary
    assert len(summary) < len(text)


def test_one_session_in_detail(client, auth):
    with SessionLocal() as db:
        user = db.query(models.User).filter_by(username="athlete").one()
        activity = _session_with_trace(user.id)
        db.add(activity)
        db.commit()
        activity_id = activity.id
    listed = client.get("/api/plan/sessions", headers=auth).json()
    assert [s["id"] for s in listed] == [activity_id] and "Reps" in listed[0]["label"]
    text = client.get(f"/api/plan/sessions/{activity_id}", headers=auth).json()["text"]
    assert text.startswith("SESSION · ")
    assert "ALL LAPS\n1. rep 500 m 2:00 (4:00 /km, HR 170)\n2. recovery 100 m 1:00 (10:00 /km)\n3. rep 500 m 2:00" in text
    assert "time · HR · /km\n0:00 150 4:10\n0:05 150 4:10\n" in text


def test_only_your_own_session_can_be_opened(client, auth):
    with SessionLocal() as db:
        user = db.query(models.User).filter_by(username="athlete").one()
        activity = _session_with_trace(user.id)
        db.add(activity)
        db.commit()
        activity_id = activity.id
    other = make_user(client, "other")
    assert client.get(f"/api/plan/sessions/{activity_id}", headers=other).status_code == 404
    assert client.get("/api/plan/sessions", headers=other).json() == []
    assert client.get(f"/api/plan/sessions/{activity_id}").status_code == 401


def test_an_older_session_keeps_its_laps_and_says_the_trace_is_gone():
    text = session_text(_activity([_lap(1000, 240), _lap(1000, 241)], name="Old", duration_seconds=500), None)
    assert "ALL LAPS\n1. 1.00 km 4:00 (4:00 /km)\n2. 1.00 km 4:01 (4:01 /km)" in text
    assert "- not stored:" in text
