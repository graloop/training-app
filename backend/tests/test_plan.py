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
    assert "  done  swim 1:02 · 3.0 km · 2:04 /100 m · avg HR 128 · load 85 — Pool Swim" in text
    assert "  done  run 0:30 · 5.1 km · 5:53 /km · avg HR 141 · load 40 — Easy Run" in text
    assert "  plan  run 0:30 — easy HR under 145 (done as planned)" in text
    assert "  done  bike 0:41 · 20.5 km · 30.0 km/h · avg HR 118 — Zwift" in text
    assert "  plan  bike 1:00\n" in text  # 41 min vs 60 planned: not within 15%, so not marked done
    assert "  done  yoga 0:20" in text
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
