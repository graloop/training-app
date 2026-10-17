import datetime

import pytest
from conftest import admin_auth, make_user

from app import models, planning
from app.database import SessionLocal

D = datetime.date
TODAY = D.today()
THIS_MONDAY = TODAY - datetime.timedelta(days=TODAY.weekday())
NEXT_MONDAY = THIS_MONDAY + datetime.timedelta(days=7)


def day(n):  # n days after next Monday
    return NEXT_MONDAY + datetime.timedelta(days=n)


def label(d):
    return planning.fmt_day(d)  # e.g. "Tue Oct 13"


# --- Parsing durations and sports -------------------------------------------------


@pytest.mark.parametrize("text,minutes", [
    ("1:15", 75), ("0:50", 50), ("2:00", 120), ("1h15", 75), ("1 h 15", 75), ("1h", 60), ("1.5h", 90),
    ("1,5 h", 90), ("2 hours", 120), ("75 min", 75), ("75", 75), ("45m", 45), ("30 minutes", 30), (" 1:05 ", 65),
])
def test_durations(text, minutes):
    assert planning.parse_duration(text) == minutes


@pytest.mark.parametrize("text", ["", "soon", "1:5", "1:60:00", "-30", "h"])
def test_bad_durations(text):
    assert planning.parse_duration(text) is None


def _parse(text, weeks=4, today=None):
    first = NEXT_MONDAY
    last = first + datetime.timedelta(days=weeks * 7 - 1)
    return planning.parse_draft(text, first, last, today or TODAY)


@pytest.mark.parametrize("sport,activity_type", [
    ("run", "running"), ("Run", "running"), ("Track", "running"), ("brick run", "running"),
    ("swim", "swimming"), ("Swim (club)", "swimming"), ("bike", "cycling"), ("Zwift", "cycling"),
    ("long ride", "cycling"), ("vélo", "cycling"), ("strength", "strength_training"), ("gym", "strength_training"),
    ("yoga", "yoga"), ("Trail Hike", "trail_hike"),
])
def test_sports(sport, activity_type):
    parsed = _parse(f"{day(1).isoformat()} | {sport} | 1:00")
    assert parsed.errors == []
    assert parsed.sessions[0].activity_type == activity_type


# --- Parsing dates ----------------------------------------------------------------


def test_date_formats_all_mean_the_same_day():
    d = day(1)  # a Tuesday
    month, full_month = planning.MONTH_NAMES[d.month - 1], d.strftime("%B")
    forms = [
        d.isoformat(), f"Tue {month} {d.day}", f"Tuesday, {full_month} {d.day}", f"{month} {d.day}",
        f"{d.day} {month}", f"Tue {month} {d.day}, {d.year}", f"tue {month.lower()} {d.day}", f"{month} {d.day}th".replace("1th", "1st") if d.day in (1, 21, 31) else f"{month} {d.day}th",
    ]
    parsed = _parse("\n".join(f"{form} | run | 0:30" for form in forms))
    assert parsed.errors == [], parsed.errors
    assert {s.date for s in parsed.sessions} == {d}


def test_weekday_that_does_not_match_the_date_is_caught():
    d = day(1)  # Tuesday
    parsed = _parse(f"Wed {planning.MONTH_NAMES[d.month - 1]} {d.day} | run | 0:30")
    assert parsed.sessions == []
    assert parsed.errors[0].message == f"{planning.MONTH_NAMES[d.month - 1]} {d.day} is a Tue, not a Wed."


def test_dates_outside_the_planned_weeks_are_errors():
    before, after = NEXT_MONDAY - datetime.timedelta(days=1), day(28)
    parsed = _parse(f"{before.isoformat()} | run | 0:30\n{label(after)} | run | 0:30")
    assert parsed.sessions == []
    assert all("outside the weeks you're planning" in e.message for e in parsed.errors) and len(parsed.errors) == 2


def test_a_date_outside_the_weeks_says_so_whatever_its_weekday():
    # "Mon <date>" written for another block: the weekday is right for this year,
    # so the message must be about the weeks, not a weekday guessed in the wrong year.
    old = NEXT_MONDAY - datetime.timedelta(days=140)
    far_start = NEXT_MONDAY + datetime.timedelta(weeks=20)
    parsed = planning.parse_draft(f"{label(old)} | run | 0:30\n{label(NEXT_MONDAY)} | run | 0:30",
                                  far_start, far_start + datetime.timedelta(days=6), TODAY)
    assert parsed.sessions == [] and len(parsed.errors) == 2
    assert parsed.errors[0].message.startswith(f"{label(old)} is outside the weeks you're planning")
    assert parsed.errors[1].message.startswith(f"{label(NEXT_MONDAY)} is outside the weeks you're planning")


def test_day_number_alone_needs_a_month_only_when_ambiguous():
    d = day(1)
    assert _parse(f"Tue {d.day} | run | 0:30", weeks=1).sessions[0].date == d  # one such day in the week
    parsed = _parse(f"{d.day} | run | 0:30", weeks=12)  # the same day number occurs in 2-3 months
    assert "Add the month" in parsed.errors[0].message


def test_unreadable_dates_and_lines():
    parsed = _parse("tomorrow | run | 0:30\njust some text\nFeb 30 | run | 0:30\n" + f"{day(0).isoformat()} | run | fast")
    messages = [e.message for e in parsed.errors]
    assert "isn't a date I can read" in messages[0]
    assert messages[1].startswith("Use: date | sport | duration | details")
    assert "isn't a real date" in messages[2] or "outside the weeks" in messages[2]
    assert "isn't a duration I can read" in messages[3]
    assert [e.line for e in parsed.errors] == [1, 2, 3, 4]
    assert parsed.errors[0].text == "tomorrow | run | 0:30"


def test_past_days_are_refused():
    monday = THIS_MONDAY
    parsed = planning.parse_draft(
        f"{monday.isoformat()} | run | 0:30\n{(monday + datetime.timedelta(days=3)).isoformat()} | run | 0:30",
        monday, monday + datetime.timedelta(days=6), today=monday + datetime.timedelta(days=2),
    )
    assert [s.date for s in parsed.sessions] == [monday + datetime.timedelta(days=3)]
    assert "already past" in parsed.errors[0].message


def test_comments_rest_days_bullets_and_markdown_tables_are_tolerated():
    text = f"""
# Week 1
- {label(day(0))} | Swim (club) | 1:00
* {label(day(0))} | run | 30 min | easy | keep HR under 145
{label(day(3))} | rest
{label(day(4))} | off | | recovery

| Date | Sport | Duration | Details |
|---|---|---|---|
| {label(day(5))} | Long run | 1:30 | easy Z2 (~15 km) |
"""
    parsed = _parse(text)
    assert parsed.errors == [], parsed.errors
    assert [(s.date, s.activity_type, s.minutes, s.details) for s in parsed.sessions] == [
        (day(0), "swimming", 60, None),
        (day(0), "running", 30, "easy | keep HR under 145"),  # details may contain |
        (day(5), "running", 90, "easy Z2 (~15 km)"),
    ]


def test_sessions_come_back_sorted_by_day_then_line():
    parsed = _parse(f"{label(day(2))} | run | 0:30\n{label(day(0))} | swim | 1:00\n{label(day(0))} | run | 0:30")
    assert [(s.date, s.activity_type) for s in parsed.sessions] == [
        (day(0), "swimming"), (day(0), "running"), (day(2), "running")]


# --- Draft API ---------------------------------------------------------------------


def _save(client, auth, text="", weeks=2, start=None, notes="", **params):
    res = client.put("/api/plan/draft", params=params, headers=auth,
                     json={"start_date": (start or NEXT_MONDAY).isoformat(), "weeks": weeks, "text": text, "notes": notes})
    assert res.status_code == 200, res.text
    return res.json()


def test_default_draft_is_next_week_for_four_weeks(client, auth):
    data = client.get("/api/plan/draft", headers=auth).json()
    assert data["saved"] is False and data["text"] == "" and data["weeks"] == 4
    assert data["start_date"] == NEXT_MONDAY.isoformat()
    assert data["end_date"] == (NEXT_MONDAY + datetime.timedelta(days=27)).isoformat()
    assert len(data["weeks_preview"]) == 4 and data["sessions_count"] == 0


def test_save_returns_a_preview_and_touches_nothing_on_the_calendar(client, auth):
    text = "\n".join([
        f"{label(day(0))} | swim | 1:00 | drills",
        f"{label(day(0))} | run | 0:30 | easy",
        f"{label(day(1))} | run | 1:15 | Track: 2×6×400 m",
        f"{label(day(6))} | bike | 2:40 | Z2",
        f"{label(day(8))} | strength | 45 min",
        "nonsense line",
    ])
    data = _save(client, auth, text, notes="  Goal: 70.3  ")
    assert data["saved"] is True and data["notes"] == "Goal: 70.3" and data["text"] == text
    assert data["sessions_count"] == 5 and [e["line"] for e in data["errors"]] == [6]
    week1, week2 = data["weeks_preview"]
    assert week1["start"] == NEXT_MONDAY.isoformat() and week1["end"] == day(6).isoformat()
    assert week1["minutes_by_sport"] == {"swim": 60, "bike": 160, "run": 105, "strength": 0, "other": 0}
    assert week1["total_minutes"] == 325 and week2["total_minutes"] == 45
    assert [s["sport"] for s in week1["sessions"]] == ["swim", "run", "run", "bike"]
    assert week1["sessions"][2]["details"] == "Track: 2×6×400 m"

    assert client.get("/api/calendar", params={"start": NEXT_MONDAY.isoformat(), "end": day(13).isoformat()},
                      headers=auth).json()["days"][0]["planned"] == []
    again = client.get("/api/plan/draft", headers=auth).json()  # the draft survives a reload
    assert again["text"] == text and again["sessions_count"] == 5


def test_any_day_of_the_week_selects_that_week_and_further_weeks_can_be_planned(client, auth):
    far = NEXT_MONDAY + datetime.timedelta(weeks=9, days=3)  # a Thursday, 10 weeks out
    data = _save(client, auth, f"{label(far)} | run | 1:00", weeks=3, start=far)
    assert data["start_date"] == (NEXT_MONDAY + datetime.timedelta(weeks=9)).isoformat()
    assert data["end_date"] == (NEXT_MONDAY + datetime.timedelta(weeks=12) - datetime.timedelta(days=1)).isoformat()
    assert data["sessions_count"] == 1 and data["errors"] == []
    assert client.put("/api/plan/draft", headers=auth, json={"start_date": far.isoformat(), "weeks": 13}).status_code == 422


def test_changing_the_weeks_re_checks_the_same_text(client, auth):
    text = f"{label(day(8))} | run | 1:00"
    assert _save(client, auth, text, weeks=2)["errors"] == []
    data = _save(client, auth, text, weeks=1)
    assert data["sessions_count"] == 0 and "outside the weeks" in data["errors"][0]["message"]


# --- Confirm -----------------------------------------------------------------------


def _calendar(client, auth, first, last):
    days = client.get("/api/calendar", params={"start": first.isoformat(), "end": last.isoformat()}, headers=auth).json()["days"]
    return {d["date"]: [(p["activity_type"], p["planned_duration_minutes"], p["notes"]) for p in d["planned"]] for d in days}


def _plan(client, auth, date, sport="running", minutes=30):
    res = client.post("/api/planned", headers=auth,
                      json={"date": date.isoformat(), "activity_type": sport, "planned_duration_minutes": minutes})
    assert res.status_code == 201


def test_confirm_puts_the_draft_on_the_calendar_and_replaces_what_was_planned_there(client, auth):
    _plan(client, auth, day(0), "swimming", 90)      # in the planned weeks: replaced
    _plan(client, auth, day(3), "cycling", 60)       # in the planned weeks, on a day the draft leaves empty: removed
    _plan(client, auth, day(14), "running", 40)      # after the planned weeks: kept
    _plan(client, auth, TODAY, "running", 20)        # before the planned weeks: kept

    data = _save(client, auth, f"{label(day(0))} | run | 0:30 | easy\n{label(day(0))} | swim | 1:00\n{label(day(8))} | bike | 2:00")
    assert data["existing_planned"] == 2 and data["replace_from"] == NEXT_MONDAY.isoformat()

    res = client.post("/api/plan/draft/confirm", headers=auth)
    assert res.status_code == 200, res.text
    assert res.json() == {"added": 3, "replaced": 2, "start_date": NEXT_MONDAY.isoformat(), "end_date": day(13).isoformat()}

    cal = _calendar(client, auth, TODAY, day(14))
    assert cal[day(0).isoformat()] == [("running", 30, "easy"), ("swimming", 60, None)]
    assert cal[day(3).isoformat()] == []
    assert cal[day(8).isoformat()] == [("cycling", 120, None)]
    assert cal[day(14).isoformat()] == [("running", 40, None)]
    assert cal[TODAY.isoformat()] == [("running", 20, None)]
    assert client.get("/api/plan/draft", headers=auth).json()["confirmed_at"] is not None


def test_confirming_twice_does_not_duplicate(client, auth):
    _save(client, auth, f"{label(day(1))} | run | 1:00")
    client.post("/api/plan/draft/confirm", headers=auth)
    res = client.post("/api/plan/draft/confirm", headers=auth).json()
    assert (res["added"], res["replaced"]) == (1, 1)
    assert _calendar(client, auth, day(1), day(1))[day(1).isoformat()] == [("running", 60, None)]


def test_confirm_is_refused_with_errors_or_nothing_to_add(client, auth):
    assert client.post("/api/plan/draft/confirm", headers=auth).status_code == 400  # no draft
    _plan(client, auth, day(1))
    _save(client, auth, f"{label(day(1))} | run | 1:00\nbroken")
    res = client.post("/api/plan/draft/confirm", headers=auth)
    assert res.status_code == 400 and res.json()["detail"] == "Fix the 1 line with a problem first"
    _save(client, auth, "# nothing yet")
    assert client.post("/api/plan/draft/confirm", headers=auth).json()["detail"] == "The draft has no sessions"
    assert _calendar(client, auth, day(1), day(1))[day(1).isoformat()] == [("running", 30, None)]  # untouched


def test_planning_the_current_week_keeps_earlier_days(client, auth):
    monday = THIS_MONDAY
    with SessionLocal() as db:
        user = db.query(models.User).filter_by(username="athlete").one()
        for offset in range(7):
            db.add(models.PlannedTraining(user_id=user.id, date=monday + datetime.timedelta(days=offset),
                                          activity_type="running", planned_duration_minutes=30, source="manual"))
        db.commit()
    sunday = monday + datetime.timedelta(days=6)
    data = _save(client, auth, f"{sunday.isoformat()} | bike | 2:00", weeks=1, start=monday)
    assert data["replace_from"] == TODAY.isoformat()
    res = client.post("/api/plan/draft/confirm", headers=auth).json()
    assert res["replaced"] == 7 - TODAY.weekday()  # today through Sunday
    cal = _calendar(client, auth, monday, sunday)
    for offset in range(TODAY.weekday()):
        assert cal[(monday + datetime.timedelta(days=offset)).isoformat()] == [("running", 30, None)]
    expected_sunday = [("cycling", 120, None)]
    assert cal[sunday.isoformat()] == expected_sunday


def test_drafts_are_private_and_admins_have_none(client, auth):
    _save(client, auth, f"{label(day(1))} | run | 1:00", notes="mine")
    other = make_user(client, "other")
    assert client.get("/api/plan/draft", headers=other).json()["saved"] is False
    assert client.post("/api/plan/draft/confirm", headers=other).status_code == 400
    admin = admin_auth(client)
    for method, path in [("get", "/api/plan/draft"), ("get", "/api/plan/log"), ("post", "/api/plan/draft/confirm")]:
        assert getattr(client, method)(path, headers=admin).status_code == 403
    assert client.get("/api/plan/log").status_code == 401


# --- Training log ------------------------------------------------------------------


def test_log_text(client, auth):
    monday = THIS_MONDAY - datetime.timedelta(days=7)  # last week: fully in the past
    with SessionLocal() as db:
        user = db.query(models.User).filter_by(username="athlete").one()
        at = lambda d, h: datetime.datetime.combine(monday + datetime.timedelta(days=d), datetime.time(h))  # noqa: E731
        db.add_all([
            models.Activity(user_id=user.id, garmin_activity_id="1", activity_type="lap_swimming", name="Pool Swim",
                            start_time=at(0, 6), duration_seconds=62 * 60, distance_meters=3000, avg_hr=128, training_load=85.4),
            models.Activity(user_id=user.id, garmin_activity_id="2", activity_type="running", name="Easy Run",
                            start_time=at(0, 7), duration_seconds=30 * 60, distance_meters=5100, avg_hr=141, training_load=40),
            models.Activity(user_id=user.id, garmin_activity_id="3", activity_type="virtual_ride", name="Zwift",
                            start_time=at(2, 18), duration_seconds=41 * 60, distance_meters=20500, avg_hr=118),
            models.Activity(user_id=user.id, garmin_activity_id="4", activity_type="yoga", start_time=at(3, 18),
                            duration_seconds=20 * 60),
            models.SleepRecord(user_id=user.id, date=monday, sleep_score=85, resting_heart_rate=46.4),
            models.SleepRecord(user_id=user.id, date=monday + datetime.timedelta(days=2), sleep_score=71),
            models.PlannedTraining(user_id=user.id, date=monday, activity_type="running", planned_duration_minutes=30,
                                   notes="easy\nHR under 145"),
            models.PlannedTraining(user_id=user.id, date=monday + datetime.timedelta(days=2), activity_type="cycling",
                                   planned_duration_minutes=60),
            models.PlannedTraining(user_id=user.id, date=day(2), activity_type="swimming", planned_duration_minutes=90,
                                   notes="club"),
            models.Objective(user_id=user.id, title="City half marathon",
                             event_date=TODAY + datetime.timedelta(days=600), target_time="1:45:00"),
        ])
        db.commit()

    res = client.get("/api/plan/log", params={"weeks": 2}, headers=auth)
    assert res.status_code == 200
    data = res.json()
    assert data["start"] == monday.isoformat() and data["end"] == TODAY.isoformat() and data["weeks"] == 2
    text = data["text"]
    assert text.startswith("TRAINING LOG · athlete\nToday is ")
    assert f"- City half marathon · {(TODAY + datetime.timedelta(days=600)).isoformat()} (600 days away) · target 1:45:00" in text
    assert (f"WEEK OF {label(monday).upper()} · done 2:33 (swim 1:02 · bike 0:41 · run 0:30 · other 0:20) · planned 1:30"
            " · load 125 · sleep avg 78 · resting HR avg 46") in text
    assert f"{label(monday)} · sleep 85 · RHR 46" in text
    assert "  done  swim 1:02 · 3.0 km · 2:04 /100 m · avg HR 128 · load 85 · effort not synced yet — Pool Swim" in text
    assert "  done  run 0:30 · 5.1 km · 5:53 /km · avg HR 141 · load 40 · effort not synced yet — Easy Run" in text
    assert "NOTE: 4 sessions below still wait for effort, feel, laps and weather from Garmin" in text
    assert "  plan  run 0:30 — easy HR under 145 (done as planned)" in text
    assert "  done  bike 0:41 · 20.5 km · 30.0 km/h · avg HR 118 · effort not synced yet — Zwift" in text
    # 41 min vs 60 planned: the same sport, so done, but not "as planned" (more than 15% off)
    assert "  plan  bike 1:00 (done, 0:41 instead of 1:00)\n" in text
    assert "  done  yoga 0:20 · effort not synced yet" in text
    assert f"{label(monday + datetime.timedelta(days=1))} · no training" in text
    assert f"WEEK OF {label(THIS_MONDAY).upper()} · done 0:00 · planned" in text and "· in progress" in text
    ahead = text[text.index("ALREADY ON THE CALENDAR AFTER TODAY"):]
    assert f"Week of {label(NEXT_MONDAY)} · planned 1:30" in ahead and f"  {label(day(2))} · swim 1:30 — club" in ahead
    # No day after today is listed in the weekly part.
    assert label(TODAY + datetime.timedelta(days=1)) not in text[:text.index("ALREADY ON THE CALENDAR")]


def test_log_for_a_new_account(client, auth):
    text = client.get("/api/plan/log", headers=auth).json()["text"]
    assert "OBJECTIVES\n- none yet" in text and "- nothing planned yet" in text
    assert client.get("/api/plan/log", params={"weeks": 27}, headers=auth).status_code == 422


def test_client_date_is_used_only_when_plausible(client, auth):
    tomorrow = TODAY + datetime.timedelta(days=1)
    assert client.get("/api/plan/log", params={"today": tomorrow.isoformat()}, headers=auth).json()["end"] == tomorrow.isoformat()
    far = TODAY + datetime.timedelta(days=30)
    assert client.get("/api/plan/log", params={"today": far.isoformat()}, headers=auth).json()["end"] == TODAY.isoformat()


_RULES = "RULE SET (the athlete's availability, constraints and rules: the plan must follow them)"


# --- Richer log: details, recovery, athlete profile, notes -------------------------


def _seed_detailed(db):
    user = db.query(models.User).filter_by(username="athlete").one()
    monday = THIS_MONDAY - datetime.timedelta(days=7)
    # All numbers below are invented for the test.
    track_raw = {
        "maxHR": 185.0, "hrTimeInZone_1": 120.0, "hrTimeInZone_2": 900.0, "hrTimeInZone_3": 2100.0, "hrTimeInZone_4": 900.0,
        "hrTimeInZone_5": 780.0, "trainingEffectLabel": "VO2MAX", "differenceBodyBattery": -18, "avgPower": 377.0,
        "averageRunningCadenceInStepsPerMinute": 172.4, "elevationGain": 12.0,
        "fastestSplit_400": 40.0, "fastestSplit_1000": 210.0, "fastestSplit_5000": 1200.0,  # the 400 is a GPS glitch
        "splitSummaries": [
            {"splitType": "INTERVAL_ACTIVE", "noOfSplits": 6, "duration": 1440.0, "distance": 6000.0},
            {"splitType": "INTERVAL_RECOVERY", "noOfSplits": 5, "duration": 900.0, "distance": 2500.0},
            {"splitType": "RWD_RUN", "noOfSplits": 4, "duration": 4700.0, "distance": 15900.0},
        ],
    }
    ride_raw = {"maxHR": 150.0, "hrTimeInZone_1": 2160.0, "hrTimeInZone_2": 300.0, "trainingEffectLabel": "RECOVERY",
                "avgPower": 140.0, "normPower": 152.4, "max20MinPower": 160.0, "averageBikingCadenceInRevPerMinute": 70.0,
                "elevationGain": 300.0}
    db.add_all([
        models.Activity(user_id=user.id, garmin_activity_id="t", activity_type="track_running", name="Track",
                        start_time=datetime.datetime.combine(monday, datetime.time(7)), duration_seconds=4800,
                        distance_meters=16000, avg_hr=150, training_load=200, perceived_effort=7, feel=75, raw_json=track_raw,
                        laps=[{"distance": 4000, "duration": 1380.0, "avg_hr": 140}, {"distance": 1600, "duration": 384.0, "avg_hr": 175},
                              {"distance": 0, "duration": 30.0}, {"distance": 400, "duration": 84.0, "avg_hr": 176}]),
        models.Activity(user_id=user.id, garmin_activity_id="r", activity_type="virtual_ride", name="Zwift",
                        start_time=datetime.datetime.combine(monday, datetime.time(18)), duration_seconds=2400,
                        distance_meters=20000, avg_hr=110, perceived_effort=3, feel=50, raw_json=ride_raw),
        models.Activity(user_id=user.id, garmin_activity_id="s", activity_type="lap_swimming", name="Pool",
                        start_time=datetime.datetime.combine(monday + datetime.timedelta(days=1), datetime.time(6)),
                        duration_seconds=3600, distance_meters=3000, raw_json=None,
                        laps=[{"distance": 400, "duration": 400.0, "avg_hr": 140}, {"distance": 100, "duration": 96.0}]),
        models.SleepRecord(user_id=user.id, date=monday, sleep_score=80, resting_heart_rate=50, total_sleep_seconds=27000,
                           deep_sleep_seconds=5400, rem_sleep_seconds=6000,
                           raw_json={"avgOvernightHrv": 60.0, "hrvStatus": "BALANCED", "bodyBatteryChange": 70}),
        models.Objective(user_id=user.id, title="City half marathon", notes="Main race.\nRun matters most."),
    ])
    user.garmin_profile = {
        "max_hr": 192, "lactate_threshold_hr": 170, "threshold_pace_s_per_km": 270, "vo2max_running": 52, "weight_kg": 70.5,
        "hr_zone_floors": [96, 115, 134, 154, 173], "race_predictions": {"5 km": 1210, "half marathon": 5700},
        "records": [{"label": "5 km", "kind": "time", "value": 1200.4, "date": "2024-05-01"},
                    {"label": "best 20 min power", "kind": "power", "value": 250.8, "date": None},
                    {"label": "longest ride", "kind": "distance", "value": 150000.0, "date": "2023-08-07"}],
        "synced_on": "2026-01-02",
    }
    user.athlete_notes = "Free on weekday mornings.\nPool closed on Saturdays."
    db.commit()
    return monday


def test_full_log_shows_details_recovery_and_the_athlete(client, auth):
    with SessionLocal() as db:
        monday = _seed_detailed(db)
    text = client.get("/api/plan/log", params={"weeks": 2}, headers=auth).json()["text"]

    # what the athlete answered on the watch
    assert "  done  run 1:20 · 16.0 km · 5:00 /km · avg HR 150 · load 200 · effort 7/10, felt strong — Track" in text
    assert "· effort 3/10, felt normal — Zwift" in text
    # heart rate, session type, body battery
    assert "        max HR 185 · HR zones Z1 2 · Z2 15 · Z3 35 · Z4 15 · Z5 13 min · VO2max session · body battery -18" in text
    # run mechanics: the glitched 400 m is dropped, climbs under 20 m too, running power isn't shown
    assert "        cadence 172 spm · fastest 1 km 3:30, 5 km 20:00" in text
    assert "400 m 0:40" not in text and "climb 12" not in text and "377" not in text
    # structured intervals and the laps themselves (the 0 m pause is skipped)
    assert "        intervals: 6 work reps, 6.00 km in 24:00 (4:00 /km), recoveries 15:00 in total" in text
    assert ("        laps: 4.00 km 23:00 (5:45 /km, HR 140) | 1.60 km 6:24 (4:00 /km, HR 175) | 400 m 1:24 (3:30 /km, HR 176)"
            in text)
    # bike: power, cadence, climb
    assert "        power 140 W (normalized 152), best 20 min 160 W · cadence 70 rpm · climb 300 m" in text
    assert "        max HR 150 · HR zones Z1 36 · Z2 5 · Z3 0 · Z4 0 · Z5 0 min · recovery session" in text
    # swim laps as pace per 100 m; an activity with no stored payload still prints
    assert "        laps: 400 m 6:40 (1:40 /100 m, HR 140) | 100 m 1:36 (1:36 /100 m)" in text
    # the night
    assert f"{label(monday)} · sleep 80 (7:30, deep 1:30, REM 1:40) · RHR 50 · HRV 60 balanced · body battery +70 overnight" in text

    athlete = text[text.index("\nATHLETE (from Garmin)\n"):text.index("\nWEEK OF")]
    assert "- max HR 192 · threshold HR 170 · threshold pace 4:30 /km · VO2max (run) 52 · weight 70.5 kg" in athlete
    assert "- HR zones: Z1 96–114 · Z2 115–133 · Z3 134–153 · Z4 154–172 · Z5 173+" in athlete
    assert "- Garmin race predictions: 5 km 20:10 · half marathon 1:35:00" in athlete
    assert "- Personal records: 5 km 20:00 (2024-05) · best 20 min power 251 W · longest ride 150.0 km (2023-08)" in athlete
    assert _RULES + "\nFree on weekday mornings.\nPool closed on Saturdays." in athlete
    assert "- City half marathon · no date · target not set\n  Main race. Run matters most." in text


def test_summary_log_keeps_one_line_per_session(client, auth):
    with SessionLocal() as db:
        monday = _seed_detailed(db)
    full = client.get("/api/plan/log", params={"weeks": 2}, headers=auth).json()
    summary = client.get("/api/plan/log", params={"weeks": 2, "detail": "summary"}, headers=auth).json()
    assert full["detail"] == "full" and summary["detail"] == "summary"
    text = summary["text"]
    assert "effort 7/10, felt strong — Track" in text            # the essentials stay
    assert f"{label(monday)} · sleep 80 · RHR 50\n" in text         # the night in short
    weekly = text[text.index("\nWEEK OF"):]  # the athlete block above keeps its HR zones
    for detail_only in ("HR zones", "laps:", "intervals:", "cadence", "HRV", "normalized"):
        assert detail_only not in weekly
    assert "\nATHLETE (from Garmin)\n" in text and len(text) < len(full["text"])
    assert client.get("/api/plan/log", params={"detail": "everything"}, headers=auth).status_code == 422


def test_log_before_any_details_or_profile(client, auth):
    text = client.get("/api/plan/log", headers=auth).json()["text"]
    assert "ATHLETE (from Garmin)\n- nothing from Garmin yet: press Sync with Garmin" in text
    assert "TRAINING STATUS (Garmin)\n- nothing from Garmin yet: press Sync with Garmin" in text
    assert "no effort, laps or recovery)\n- no earlier activities stored" in text
    assert text.count("· not synced (no Garmin data yet)") == 6 and "no training" not in text  # nothing synced at all
    assert _RULES + "\n- nothing written yet" in text
    assert "(Athlete tab)" in text


def test_rule_set_round_trip_and_privacy(client, auth):
    assert client.get("/api/plan/rules", headers=auth).json() == {"rules": ""}
    res = client.put("/api/plan/rules", json={"rules": "  Exams in December.  "}, headers=auth)
    assert res.json() == {"rules": "Exams in December."}
    assert _RULES + "\nExams in December." in client.get("/api/plan/log", headers=auth).json()["text"]
    assert client.put("/api/plan/rules", json={"rules": "x" * 20001}, headers=auth).status_code == 422
    other = make_user(client, "other")
    assert client.get("/api/plan/rules", headers=other).json() == {"rules": ""}
    assert "Exams" not in client.get("/api/plan/log", headers=other).json()["text"]
    assert client.get("/api/plan/rules", headers=admin_auth(client)).status_code == 403
    client.put("/api/plan/rules", json={"rules": ""}, headers=auth)
    assert "- nothing written yet" in client.get("/api/plan/log", headers=auth).json()["text"]


def test_older_notes_stand_in_for_the_rule_set_until_it_is_saved(client, auth):
    with SessionLocal() as db:
        user = db.query(models.User).filter(models.User.username == "athlete").one()
        user.athlete_notes = "Pool closed Saturdays."
        db.commit()
    _save(client, auth, notes="Easy runs stay easy.")
    assert client.get("/api/plan/rules", headers=auth).json()["rules"] == "Pool closed Saturdays.\n\nEasy runs stay easy."
    # saving the draft later without notes keeps them
    client.put("/api/plan/draft", json={"start_date": NEXT_MONDAY.isoformat(), "weeks": 2, "text": ""}, headers=auth)
    assert "Easy runs stay easy." in client.get("/api/plan/rules", headers=auth).json()["rules"]
    client.put("/api/plan/rules", json={"rules": "Only this."}, headers=auth)
    assert client.get("/api/plan/rules", headers=auth).json()["rules"] == "Only this."


def test_athlete_profile_round_trip_and_log(client, auth):
    empty = client.get("/api/athlete", headers=auth).json()
    assert empty["profile"]["ftp_watts"] is None and empty["garmin"]
    body = {"ftp_watts": 250, "ftp_date": "2026-03-25", "bike_zones": "Z2 138-187 W", "threshold_pace": "4:30",
            "css_pace": "1:45", "equipment": "  Smart trainer  ", "health": "", "weight_kg": 70.5}
    res = client.put("/api/athlete", json=body, headers=auth).json()["profile"]
    assert res["ftp_watts"] == 250 and res["equipment"] == "Smart trainer" and res["health"] is None
    text = client.get("/api/plan/log", headers=auth).json()["text"]
    assert "- Bike: FTP 250 W (tested 2026-03-25) · power zones Z2 138-187 W" in text
    assert "- Run: threshold pace 4:30 /km" in text and "- Swim: CSS 1:45 /100 m" in text
    assert "- Equipment: Smart trainer" in text and "weight 70.5 kg" in text
    assert client.put("/api/athlete", json={"css_pace": "fast"}, headers=auth).status_code == 422
    assert client.put("/api/athlete", json={"ftp_watts": 5}, headers=auth).status_code == 422
    other = make_user(client, "other")
    assert client.get("/api/athlete", headers=other).json()["profile"]["ftp_watts"] is None
    assert client.get("/api/athlete", headers=admin_auth(client)).status_code == 403
    client.put("/api/athlete", json={}, headers=auth)
    assert "FTP" not in client.get("/api/plan/log", headers=auth).json()["text"].split("ATHLETE (from Garmin)")[0]


# --- Phase 3: recovery per day, training status, reps, swim, weather, history ------


def _seed_phase3(db):
    """Last week, fully in the past. All numbers invented."""
    user = db.query(models.User).filter_by(username="athlete").one()
    monday = THIS_MONDAY - datetime.timedelta(days=7)
    at = lambda d, h: datetime.datetime.combine(monday + datetime.timedelta(days=d), datetime.time(h))  # noqa: E731
    synced = datetime.datetime(2026, 1, 1)
    manual_laps = [  # a track session with manual laps: warm-up, 3 fast reps with jogs, cool-down
        {"distance": 3000, "duration": 1080.0, "avg_hr": 135},
        {"distance": 1000, "duration": 220.0, "avg_hr": 172, "max_hr": 180, "cadence": 184},
        {"distance": 400, "duration": 150.0, "avg_hr": 150},
        {"distance": 1000, "duration": 218.0, "avg_hr": 175, "max_hr": 183, "cadence": 185},
        {"distance": 400, "duration": 152.0, "avg_hr": 152},
        {"distance": 1000, "duration": 216.0, "avg_hr": 177, "max_hr": 186},
        {"distance": 2000, "duration": 740.0, "avg_hr": 138},
    ]
    structured_laps = [
        {"distance": 2000, "duration": 720.0, "kind": "warmup"},
        {"distance": 800, "duration": 180.0, "avg_hr": 170, "kind": "active"},
        {"distance": 200, "duration": 90.0, "kind": "recovery"},
        {"distance": 800, "duration": 182.0, "avg_hr": 172, "kind": "active"},
        {"distance": 1000, "duration": 360.0, "kind": "cooldown"},
    ]
    steady_laps = [{"distance": 1000, "duration": 330.0 + i, "avg_hr": 140} for i in range(5)]
    swim_laps = [
        {"distance": 400, "duration": 400.0, "avg_hr": 140, "stroke": "freestyle", "swolf": 34},
        {"distance": 0, "duration": 25.0},
        {"distance": 100, "duration": 110.0, "stroke": "breaststroke", "swolf": 48},
        {"distance": 0, "duration": 3.0},
        {"distance": 200, "duration": 190.0, "stroke": "mixed"},
    ]
    db.add_all([
        models.Activity(user_id=user.id, garmin_activity_id="m", activity_type="track_running", name="Manual laps",
                        start_time=at(0, 7), duration_seconds=2776, distance_meters=8800, laps=manual_laps,
                        aerobic_training_effect=3.4, anaerobic_training_effect=2.1, details_synced_at=synced,
                        raw_json={"trainingEffectLabel": "VO2MAX"}, extras={"sweat_ml": 950, "temp_c": 5, "wind_kmh": 16, "humidity": 70}),
        models.Activity(user_id=user.id, garmin_activity_id="w", activity_type="running", name="Workout",
                        start_time=at(1, 7), duration_seconds=1532, distance_meters=4800, laps=structured_laps,
                        perceived_effort=8, feel=50, details_synced_at=synced),
        models.Activity(user_id=user.id, garmin_activity_id="e", activity_type="running", name="Easy",
                        start_time=at(2, 7), duration_seconds=1660, distance_meters=5000, laps=steady_laps,
                        details_synced_at=synced),
        models.Activity(user_id=user.id, garmin_activity_id="p", activity_type="lap_swimming", name="Pool",
                        start_time=at(3, 6), duration_seconds=900, distance_meters=700, laps=swim_laps, details_synced_at=synced,
                        raw_json={"poolLength": 2500.0, "unitOfPoolLength": {"factor": 100.0}, "activeLengths": 28,
                                  "avgStrokes": 10.84, "averageSwolf": 36.0}),
        models.SleepRecord(user_id=user.id, date=monday, sleep_score=80, resting_heart_rate=50),
        models.DailyMetrics(user_id=user.id, date=monday, readiness=66, readiness_level="moderate", stress_avg=30,
                            body_battery_low=20, body_battery_high=95, acute_load=500, chronic_load=480, load_ratio=1.0,
                            load_ratio_status="optimal", training_status="maintaining", final=True),
        models.DailyMetrics(user_id=user.id, date=monday + datetime.timedelta(days=1), readiness=40, readiness_level="low",
                            stress_avg=50, final=True),
        models.DailyMetrics(user_id=user.id, date=monday + datetime.timedelta(days=6), readiness=80, readiness_level="high",
                            acute_load=600, chronic_load=500, load_ratio=1.2, load_ratio_status="optimal",
                            training_status="productive", vo2max=51.4, final=True,
                            load_focus={"phrase": "aerobic high shortage", "low": 1200, "low_target": [500, 1100],
                                        "high": 300, "high_target": [600, 1200], "anaerobic": 250, "anaerobic_target": [200, 600]}),
        # older history: two weeks with training, a gap week between them
        models.Activity(user_id=user.id, garmin_activity_id="h1", activity_type="cycling", duration_seconds=7200, training_load=120,
                        start_time=datetime.datetime.combine(monday - datetime.timedelta(weeks=20), datetime.time(9))),
        models.Activity(user_id=user.id, garmin_activity_id="h2", activity_type="running", duration_seconds=3600, training_load=80,
                        start_time=datetime.datetime.combine(monday - datetime.timedelta(weeks=20, days=-2), datetime.time(9))),
        models.Activity(user_id=user.id, garmin_activity_id="h3", activity_type="lap_swimming", duration_seconds=1800,
                        start_time=datetime.datetime.combine(monday - datetime.timedelta(weeks=18), datetime.time(9))),
        models.Activity(user_id=user.id, garmin_activity_id="h4", activity_type="running", duration_seconds=600,
                        start_time=datetime.datetime.combine(monday - datetime.timedelta(weeks=60), datetime.time(9))),  # over a year: left out
    ])
    db.commit()
    return monday


def test_reps_are_separated_from_warm_up_and_recoveries(client, auth):
    with SessionLocal() as db:
        _seed_phase3(db)
    text = client.get("/api/plan/log", params={"weeks": 2}, headers=auth).json()["text"]
    # manual laps: the clearly faster ones are the reps, with max HR and cadence
    assert ("        reps: 1.00 km 3:40 (3:40 /km, HR 172 max 180, 184 spm) | 1.00 km 3:38 (3:38 /km, HR 175 max 183, 185 spm)"
            " | 1.00 km 3:36 (3:36 /km, HR 177 max 186)") in text
    assert "        other laps (warm-up, recoveries, cool-down): 4, 5.80 km in 35:22 (6:06 /km)" in text
    # a structured workout says which laps are work
    assert "        reps: 800 m 3:00 (3:45 /km, HR 170) | 800 m 3:02 (3:48 /km, HR 172)" in text
    assert "        other laps (warm-up, recoveries, cool-down): 3, 3.20 km in 19:30 (6:06 /km)" in text
    # an even run is just its laps
    assert "        laps: 1.00 km 5:30 (5:30 /km, HR 140) | 1.00 km 5:31 (5:31 /km, HR 140)" in text
    # effort: answered, skipped on the watch
    assert "· effort 8/10, felt normal — Workout" in text
    assert "· effort not answered — Easy" in text and "NOTE:" not in text


def test_swim_sets_training_effect_and_conditions(client, auth):
    with SessionLocal() as db:
        _seed_phase3(db)
    text = client.get("/api/plan/log", params={"weeks": 2}, headers=auth).json()["text"]
    assert "        pool 25 m · 28 lengths · 10.8 strokes/length · SWOLF 36" in text
    assert ("        laps: 400 m 6:40 (1:40 /100 m, HR 140, swolf 34) +0:25 rest | 100 m 1:50 (1:50 /100 m, breaststroke, swolf 48)"
            " | 200 m 3:10 (1:35 /100 m)") in text  # a 3 s touch at the wall isn't a rest
    assert "        VO2max session (training effect 3.4 aerobic, 2.1 anaerobic)" in text
    assert "        conditions: 5 °C · wind 16 km/h · humidity 70% · estimated sweat loss 0.9 L" in text or \
           "        conditions: 5 °C · wind 16 km/h · humidity 70% · estimated sweat loss 1.0 L" in text


def test_recovery_per_day_and_week_and_garmin_training_status(client, auth):
    with SessionLocal() as db:
        monday = _seed_phase3(db)
    full = client.get("/api/plan/log", params={"weeks": 2}, headers=auth).json()["text"]
    assert f"{label(monday)} · readiness 66 moderate · sleep 80 · RHR 50 · stress 30 · day battery 20–95\n" in full
    assert f"{label(monday + datetime.timedelta(days=1))} · readiness 40 low · stress 50\n" in full
    header = next(line for line in full.splitlines() if line.startswith(f"WEEK OF {label(monday).upper()}"))
    assert header.endswith("· sleep avg 80 · resting HR avg 50 · readiness avg 62")
    assert ("  stress avg 40 · at week end acute load 600 (7 days) vs chronic 500 (4 weeks): ratio 1.2, optimal"
            " · Garmin status productive") in full
    status = full[full.index("TRAINING STATUS (Garmin)"):full.index("EARLIER HISTORY")]
    assert f"- as of {label(monday + datetime.timedelta(days=6))}: productive · acute load 600 (7 days) vs chronic 500 (4 weeks): ratio 1.2, optimal · VO2max 51.4" in status
    assert ("- load by intensity over 4 weeks: low aerobic 1200 (target 500–1100) · high aerobic 300 (target 600–1200)"
            " · anaerobic 250 (target 200–600) → aerobic high shortage") in status

    summary = client.get("/api/plan/log", params={"weeks": 2, "detail": "summary"}, headers=auth).json()["text"]
    assert f"{label(monday)} · readiness 66 · sleep 80 · RHR 50\n" in summary  # the score, nothing more
    assert "stress" not in summary[summary.index("\nWEEK OF"):] and "TRAINING STATUS (Garmin)" in summary


def test_earlier_weeks_give_the_long_term_picture(client, auth):
    with SessionLocal() as db:
        monday = _seed_phase3(db)
    text = client.get("/api/plan/log", params={"weeks": 2}, headers=auth).json()["text"]
    earlier = text[text.index("EARLIER HISTORY"):text.index("\nWEEK OF")].splitlines()
    w20, w18, w60 = (monday - datetime.timedelta(weeks=n) for n in (20, 18, 60))
    name = lambda d: f"Week of {planning.MONTH_NAMES[d.month - 1]} {d.day}, {d.year}"  # noqa: E731
    weeks = [line for line in earlier if line.startswith("Week of")]
    # every week from the first stored one (60 weeks back) up to the detailed part, gaps included
    assert weeks[0] == f"{name(w60)} · 0:10 (run 0:10) · 1 session"
    assert len(weeks) == 60 - 1 + 1
    i = earlier.index(f"{name(w20)} · 3:00 (bike 2:00 · run 1:00) · 2 sessions · load 200")
    # the year before the detailed part lists each session, without "effort not synced"
    assert earlier[i + 1] == f"  {label(w20)} · bike 2:00 · load 120"
    assert earlier[i + 2] == f"  {label(w20 + datetime.timedelta(days=2))} · run 1:00 · load 80"
    assert earlier[i + 3] == f"{name(w20 + datetime.timedelta(weeks=1))} · no training recorded"
    assert f"{name(w18)} · 0:30 (swim 0:30) · 1 session" in earlier
    assert not any("effort" in line for line in earlier[1:])  # (the heading says there is none)
    # 60 weeks back is before the sessions part: totals only
    assert earlier[earlier.index(weeks[0]) + 1].startswith("Week of")


def test_history_older_than_two_years_comes_by_month(client, auth):
    today = datetime.date.today()
    old = today - datetime.timedelta(days=3 * 365)
    with SessionLocal() as db:
        user = db.query(models.User).filter_by(username="athlete").one()
        for i, (kind, hours) in enumerate((("running", 1), ("cycling", 2), ("lap_swimming", 1))):
            db.add(models.Activity(user_id=user.id, garmin_activity_id=f"o{i}", activity_type=kind, duration_seconds=hours * 3600,
                                   training_load=50, start_time=datetime.datetime.combine(old.replace(day=3 + i), datetime.time(8))))
        recent = today - datetime.timedelta(weeks=30)
        db.add(models.Activity(user_id=user.id, garmin_activity_id="r", activity_type="running", duration_seconds=3600,
                               start_time=datetime.datetime.combine(recent, datetime.time(8))))
        db.commit()
    text = client.get("/api/plan/log", params={"weeks": 2}, headers=auth).json()["text"]
    earlier = text[text.index("EARLIER HISTORY"):text.index("\nWEEK OF")].splitlines()
    month = f"Month of {planning.MONTH_NAMES[old.month - 1]} {old.year}"
    assert f"{month} · 4:00 (swim 1:00 · bike 2:00 · run 1:00) · 3 sessions · load 150" in earlier
    assert earlier[1].startswith("By month, before ") and earlier[2].startswith(month)
    months = [line for line in earlier if line.startswith("Month of")]
    assert 12 <= len(months) <= 14  # every month up to two years ago, empty ones included
    assert any(line.startswith("By week, every session listed from") for line in earlier)


def test_days_before_the_first_synced_data_are_not_called_rest_days(client, auth):
    with SessionLocal() as db:
        user = db.query(models.User).filter_by(username="athlete").one()
        first = THIS_MONDAY - datetime.timedelta(days=5)  # the Wednesday of last week
        db.add(models.SleepRecord(user_id=user.id, date=first, sleep_score=75))
        db.commit()
    text = client.get("/api/plan/log", params={"weeks": 4}, headers=auth).json()["text"]
    assert text.count(f"· not synced (this account's Garmin data starts {label(first)})") == 2  # two whole weeks before it
    assert label(first - datetime.timedelta(days=1)) + " " not in text[text.index("\nWEEK OF"):text.index("ALREADY ON")]
    assert f"{label(first)} · sleep 75 · no training" in text
    assert f"{label(first + datetime.timedelta(days=1))} · no training" in text  # after the start: a real rest day


def test_garmin_numbers_with_decimals_do_not_break_the_log(client, auth):
    """Garmin sends whole numbers for body battery, but a 55.0 must not stop the History tab."""
    today = datetime.date.today()
    with SessionLocal() as db:
        user = db.query(models.User).filter(models.User.username == "athlete").one()
        db.add(models.SleepRecord(user_id=user.id, date=today, sleep_score=80, raw_json={"bodyBatteryChange": 55.0}))
        db.add(models.Activity(user_id=user.id, garmin_activity_id="1", activity_type="running", duration_seconds=1800,
                               start_time=datetime.datetime.combine(today, datetime.time(7)),
                               raw_json={"differenceBodyBattery": -12.0}))
        db.commit()
    res = client.get("/api/plan/log", headers=auth)
    assert res.status_code == 200
    assert "body battery +55 overnight" in res.json()["text"] and "body battery -12" in res.json()["text"]
