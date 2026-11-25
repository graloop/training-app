"""Coaches and their athletes (coaching.py, routers/coach.py, routers/coaches.py,
the ?athlete_id= of routers/plan.py): roles set by the admin, adding an athlete
by username with no approval, what a coach sees (no sleep, no health notes),
planning for an athlete in the coach's own draft, pushing it on their calendar
or sending it to their Plan tab, and removing the link from either side."""

import datetime

from conftest import admin_auth, login, make_user

from app import accounts, models
from app.database import SessionLocal

TODAY = datetime.date.today()
NEXT_MONDAY = TODAY - datetime.timedelta(days=TODAY.weekday()) + datetime.timedelta(days=7)


def day(n):  # n days after next Monday
    return NEXT_MONDAY + datetime.timedelta(days=n)


def _id(username):
    with SessionLocal() as db:
        return db.query(models.User).filter_by(username=username).one().id


def _role(client, username, role):
    res = client.patch(f"/api/admin/users/{_id(username)}", json={"role": role}, headers=admin_auth(client))
    assert res.status_code == 200, res.text
    return res.json()


def _coach(client, username="coach"):
    headers = make_user(client, username, garmin=False)
    _role(client, username, "coach")
    return headers


def _link(client, coach, athlete="athlete"):
    res = client.post("/api/coach/athletes", json={"username": athlete}, headers=coach)
    assert res.status_code == 201, res.text
    return res.json()


def _session(date, sport="running", minutes=60, details=None):
    return {"date": date.isoformat(), "activity_type": sport, "minutes": minutes, "details": details}


# --- Roles ---------------------------------------------------------------------------


def test_new_accounts_are_athletes_and_the_admin_sets_roles(client):
    make_user(client, "friend", garmin=False)
    users = {u["username"]: u for u in client.get("/api/admin/users", headers=admin_auth(client)).json()["users"]}
    assert users["friend"]["role"] == "athlete" and users["admin"]["role"] == "admin"
    assert _role(client, "friend", "coach")["role"] == "coach"
    me = client.get("/api/auth/me", headers=login(client, "friend"))
    assert me.json()["is_coach"] is True and me.json()["is_admin"] is False
    assert _role(client, "friend", "admin")["role"] == "admin"
    assert _role(client, "friend", "athlete")["role"] == "athlete"
    res = client.patch(f"/api/admin/users/{_id('admin')}", json={"role": "coach"}, headers=admin_auth(client))
    assert res.status_code == 400  # not your own admin rights
    bad = client.patch(f"/api/admin/users/{_id('friend')}", json={"role": "king"}, headers=admin_auth(client))
    assert bad.status_code == 422


def test_a_coach_has_no_training_of_their_own(client, auth):
    """No calendar, Garmin, objectives, profile, plan or Import: a coach plans for athletes only."""
    coach = _coach(client)
    for method, path in (("get", "/api/calendar"), ("post", "/api/garmin/sync/start"), ("get", "/api/objectives"),
                         ("get", "/api/athlete"), ("get", "/api/plan/draft"), ("get", "/api/plan/rules"),
                         ("get", "/api/share"), ("get", "/api/coaches")):
        res = getattr(client, method)(path, headers=coach)
        assert res.status_code == 403 and "Coach accounts" in res.json()["detail"], path
    assert client.post("/api/planned", json={"date": day(1).isoformat(), "activity_type": "running",
                                             "planned_duration_minutes": 30}, headers=coach).status_code == 403
    assert client.get("/api/auth/me", headers=coach).status_code == 200  # Settings still work


def test_athletes_and_admins_reach_no_coach_route(client, auth):
    assert client.get("/api/coach/athletes", headers=auth).status_code == 403
    assert client.get("/api/coach/athletes", headers=admin_auth(client)).status_code == 403


def test_a_coach_cannot_be_coached(client, auth):
    coach = _coach(client)
    _coach(client, "other-coach")
    res = client.post("/api/coach/athletes", json={"username": "other-coach"}, headers=coach)
    assert res.status_code == 404
    _link(client, coach)
    _role(client, "athlete", "coach")  # an athlete who becomes a coach loses their coaches
    assert client.get("/api/coach/athletes", headers=coach).json()["athletes"] == []


def test_the_background_sync_skips_coaches(client, auth):
    from app.garmin import autosync

    athlete = _id("athlete")
    assert athlete in autosync.connected_accounts()
    _role(client, "athlete", "coach")  # the Garmin link stays stored, unused
    assert athlete not in autosync.connected_accounts()


def test_a_coach_plans_edits_and_deletes_on_the_athletes_calendar(client, auth):
    coach = _coach(client)
    _link(client, coach)
    athlete = _id("athlete")
    base = f"/api/coach/athletes/{athlete}/planned"
    res = client.post(base, json={"date": day(1).isoformat(), "activity_type": "running",
                                  "planned_duration_minutes": 45, "notes": "Easy"}, headers=coach)
    assert res.status_code == 201 and (res.json()["source"], res.json()["planned_by"]) == ("coach", "coach")
    plan_id = res.json()["id"]
    edited = client.patch(f"{base}/{plan_id}", json={"date": day(2).isoformat(), "planned_duration_minutes": 60},
                          headers=coach).json()
    assert (edited["date"], edited["planned_duration_minutes"], edited["planned_by"]) == (day(2).isoformat(), 60, "coach")
    days = client.get("/api/calendar", params={"start": day(2).isoformat(), "end": day(2).isoformat()},
                      headers=auth).json()["days"]
    assert days[0]["planned"][0]["planned_by"] == "coach"  # the athlete sees who planned it

    # the athlete's own sessions can be changed too; someone else's can't
    mine = client.post("/api/planned", json={"date": day(3).isoformat(), "activity_type": "swimming",
                                             "planned_duration_minutes": 30}, headers=auth).json()
    assert client.patch(f"{base}/{mine['id']}", json={"planned_duration_minutes": 40}, headers=coach).json()["planned_by"] == "coach"
    friend = make_user(client, "friend", garmin=False)
    theirs = client.post("/api/planned", json={"date": day(3).isoformat(), "activity_type": "swimming",
                                               "planned_duration_minutes": 30}, headers=friend).json()
    assert client.patch(f"{base}/{theirs['id']}", json={"planned_duration_minutes": 5}, headers=coach).status_code == 404
    assert client.post(f"/api/coach/athletes/{_id('friend')}/planned", json={"date": day(1).isoformat(),
                       "activity_type": "running", "planned_duration_minutes": 30}, headers=coach).status_code == 404

    assert client.delete(f"{base}/{plan_id}", headers=coach).status_code == 204
    assert client.delete(f"{base}/{plan_id}", headers=coach).status_code == 404


# --- Links ---------------------------------------------------------------------------


def test_a_coach_adds_an_athlete_by_username_straight_away(client, auth):
    coach = _coach(client)
    added = _link(client, coach, " Athlete ")
    assert added["username"] == "athlete" and added["planned_minutes"] == 0
    assert [a["username"] for a in client.get("/api/coach/athletes", headers=coach).json()["athletes"]] == ["athlete"]
    coaches = client.get("/api/coaches", headers=auth).json()["coaches"]
    assert [c["username"] for c in coaches] == ["coach"]
    assert client.post("/api/coach/athletes", json={"username": "athlete"}, headers=coach).status_code == 409


def test_unknown_names_admins_and_yourself_are_refused_alike(client, auth):
    coach = _coach(client)
    for name in ("nobody", "admin", "coach"):
        res = client.post("/api/coach/athletes", json={"username": name}, headers=coach)
        assert res.status_code == 404 and res.json()["detail"] == f'No athlete is called "{name}".'


def test_guessing_names_is_limited(client, auth):
    coach = _coach(client)
    for i in range(accounts.MAX_COACH_FAILURES):
        assert client.post("/api/coach/athletes", json={"username": f"guess{i}"}, headers=coach).status_code == 404
    assert client.post("/api/coach/athletes", json={"username": "athlete"}, headers=coach).status_code == 429


def test_either_side_removes_the_link(client, auth):
    coach = _coach(client)
    _link(client, coach)
    assert client.delete(f"/api/coach/athletes/{_id('athlete')}", headers=coach).status_code == 204
    assert client.get("/api/coaches", headers=auth).json()["coaches"] == []
    assert client.delete(f"/api/coach/athletes/{_id('athlete')}", headers=coach).status_code == 404

    _link(client, coach)
    link_id = client.get("/api/coaches", headers=auth).json()["coaches"][0]["link_id"]
    friend = make_user(client, "friend", garmin=False)
    assert client.delete(f"/api/coaches/{link_id}", headers=friend).status_code == 404  # not their coach
    assert client.delete(f"/api/coaches/{link_id}", headers=auth).status_code == 204
    assert client.get("/api/coach/athletes", headers=coach).json()["athletes"] == []
    assert client.get(f"/api/coach/athletes/{_id('athlete')}", headers=coach).status_code == 404


def test_without_a_link_a_coach_sees_nothing(client, auth):
    coach = _coach(client)
    athlete = _id("athlete")
    for path in (f"/api/coach/athletes/{athlete}", f"/api/coach/athletes/{athlete}/calendar?start={day(0)}&end={day(6)}",
                 f"/api/plan/draft?athlete_id={athlete}"):
        assert client.get(path, headers=coach).status_code == 404, path
    assert client.post(f"/api/plan/draft/sessions?athlete_id={athlete}", json=_session(day(1)),
                       headers=coach).status_code == 404
    friend = make_user(client, "friend", garmin=False)  # not a coach at all
    assert client.get(f"/api/plan/draft?athlete_id={athlete}", headers=friend).status_code == 403


def test_leaving_the_coach_role_ends_the_links(client, auth):
    coach = _coach(client)
    _link(client, coach)
    _role(client, "coach", "athlete")
    assert client.get("/api/coaches", headers=auth).json()["coaches"] == []
    _role(client, "coach", "coach")
    assert client.get("/api/coach/athletes", headers=coach).json()["athletes"] == []
    _link(client, coach)
    _role(client, "athlete", "admin")  # an admin has no calendar: nobody coaches them
    assert client.get("/api/coach/athletes", headers=coach).json()["athletes"] == []


def test_deleting_a_coach_or_an_athlete_deletes_the_links_and_drafts(client, auth):
    coach = _coach(client)
    _link(client, coach)
    assert client.post(f"/api/plan/draft/sessions?athlete_id={_id('athlete')}", json=_session(day(1)),
                       headers=coach).status_code == 200
    res = client.delete(f"/api/admin/users/{_id('coach')}", params={"confirm": "coach"}, headers=admin_auth(client))
    assert res.status_code == 204
    with SessionLocal() as db:
        assert db.query(models.CoachLink).count() == 0 and db.query(models.PlanDraft).count() == 0


# --- What a coach sees ------------------------------------------------------------------


def test_a_coach_sees_the_profile_without_health_notes(client, auth):
    coach = _coach(client)
    _link(client, coach)
    assert client.put("/api/athlete", json={"ftp_watts": 250, "health": "Knee surgery in 2024"}, headers=auth).status_code == 200
    assert client.put("/api/plan/rules", json={"rules": "Long run on Sunday."}, headers=auth).status_code == 200
    client.post("/api/objectives", json={"title": "City 10k", "event_date": day(60).isoformat()}, headers=auth)
    profile = client.get(f"/api/coach/athletes/{_id('athlete')}", headers=coach).json()
    assert profile["profile"] == {"ftp_watts": 250} and profile["rules"] == "Long run on Sunday."
    assert [o["title"] for o in profile["objectives"]] == ["City 10k"]
    listed = client.get("/api/coach/athletes", headers=coach).json()["athletes"][0]
    assert listed["main_objective"] == f"City 10k · {day(60).isoformat()}"


def test_a_coachs_calendar_view_has_no_sleep(client, auth):
    coach = _coach(client)
    _link(client, coach)
    with SessionLocal() as db:
        db.add(models.SleepRecord(user_id=_id("athlete"), date=TODAY, sleep_score=80, resting_heart_rate=44))
        db.add(models.PlannedTraining(user_id=_id("athlete"), date=TODAY, activity_type="running",
                                      planned_duration_minutes=45))
        db.commit()
    days = client.get(f"/api/coach/athletes/{_id('athlete')}/calendar",
                      params={"start": TODAY.isoformat(), "end": TODAY.isoformat()}, headers=coach).json()["days"]
    assert days[0]["sleep"] is None and days[0]["planned"][0]["planned_duration_minutes"] == 45
    assert client.get("/api/calendar", params={"start": TODAY.isoformat(), "end": TODAY.isoformat()},
                      headers=auth).json()["days"][0]["sleep"]["sleep_score"] == 80  # the athlete still sees it
    listed = client.get("/api/coach/athletes", headers=coach).json()["athletes"][0]
    assert listed["planned_minutes"] == 45


def test_coach_and_athlete_see_each_others_photo(client, auth):
    coach = _coach(client)
    photo = b"\xff\xd8\xff\xe0" + b"\x00" * 100
    url = client.put("/api/auth/avatar", content=photo, headers={**auth, "Content-Type": "image/jpeg"}).json()["avatar_url"]
    coach_url = client.put("/api/auth/avatar", content=photo,
                           headers={**coach, "Content-Type": "image/jpeg"}).json()["avatar_url"]
    assert client.get(url, headers=coach).status_code == 404
    _link(client, coach)
    assert client.get(url, headers=coach).status_code == 200
    assert client.get(coach_url, headers=auth).status_code == 200
    assert client.get("/api/coach/athletes", headers=coach).json()["athletes"][0]["avatar_url"] == url


# --- Planning for an athlete --------------------------------------------------------------


def test_the_coachs_draft_stays_theirs_until_sent(client, auth):
    coach = _coach(client)
    _link(client, coach)
    athlete = _id("athlete")
    draft = client.post(f"/api/plan/draft/sessions?athlete_id={athlete}", json=_session(day(1), details="Tempo"),
                        headers=coach).json()
    assert draft["sessions_count"] == 1
    assert client.get("/api/plan/draft", headers=auth).json()["sessions_count"] == 0  # not on the athlete's Plan tab

    edited = client.patch(f"/api/plan/draft/sessions/1?athlete_id={athlete}", json=_session(day(2), minutes=50),
                          headers=coach).json()
    assert edited["text"] == f"{day(2).isoformat()} | run | 0:50"
    assert client.delete(f"/api/plan/draft?athlete_id={athlete}", headers=coach).json()["text"] == ""


def test_a_coach_pushes_on_the_athletes_calendar(client, auth):
    coach = _coach(client)
    _link(client, coach)
    athlete = _id("athlete")
    with SessionLocal() as db:
        db.add(models.PlannedTraining(user_id=athlete, date=day(1), activity_type="swimming", planned_duration_minutes=30))
        db.commit()
    client.post(f"/api/plan/draft/sessions?athlete_id={athlete}", json=_session(day(1), details="Track"), headers=coach)
    draft = client.get(f"/api/plan/draft?athlete_id={athlete}", headers=coach).json()
    assert draft["existing_planned"] == 1  # what pushing would replace on the athlete's calendar
    res = client.post(f"/api/plan/draft/confirm?athlete_id={athlete}", headers=coach)
    assert res.status_code == 200 and res.json()["added"] == 1 and res.json()["replaced"] == 1
    planned = client.get("/api/calendar", params={"start": day(1).isoformat(), "end": day(1).isoformat()},
                         headers=auth).json()["days"][0]["planned"]
    assert [(p["activity_type"], p["notes"], p["source"], p["planned_by"]) for p in planned] == [
        ("running", "Track", "coach", "coach")]


def test_a_coach_sends_the_draft_to_the_athletes_plan_tab(client, auth):
    coach = _coach(client)
    _link(client, coach)
    athlete = _id("athlete")
    send = f"/api/plan/draft/send?athlete_id={athlete}"
    assert client.post(send, headers=coach).status_code == 400  # nothing to send
    client.post(f"/api/plan/draft/sessions?athlete_id={athlete}", json=_session(day(1)), headers=coach)
    client.post(f"/api/plan/draft/sessions?athlete_id={athlete}", json=_session(day(3), sport="cycling"), headers=coach)
    assert client.post(send, headers=coach).json() == {"sessions": 2}
    mine = client.get("/api/plan/draft", headers=auth).json()
    assert mine["sessions_count"] == 2  # theirs to adjust and push

    client.post(f"/api/plan/draft/sessions?athlete_id={athlete}", json=_session(day(4)), headers=coach)
    again = client.post(send, headers=coach)
    assert again.status_code == 409 and "already has a draft" in again.json()["detail"]
    assert client.post(f"{send}&replace=true", headers=coach).json() == {"sessions": 3}
    assert client.get("/api/plan/draft", headers=auth).json()["sessions_count"] == 3
    assert client.post("/api/plan/draft/confirm", headers=auth).json()["added"] == 3  # the athlete pushes it


def test_a_coachs_draft_drops_old_sessions_like_any_other(client, auth):
    coach = _coach(client)
    _link(client, coach)
    athlete = _id("athlete")
    old = TODAY - datetime.timedelta(days=30)
    with SessionLocal() as db:
        db.add(models.PlanDraft(user_id=athlete, coach_id=_id("coach"), start_date=old - datetime.timedelta(days=old.weekday()),
                                weeks=8, text=f"{old.isoformat()} | run | 0:30\n{day(1).isoformat()} | run | 0:40"))
        db.commit()
    draft = client.get(f"/api/plan/draft?athlete_id={athlete}", headers=coach).json()
    assert draft["text"] == f"{day(1).isoformat()} | run | 0:40" and draft["sessions_count"] == 1


def test_the_athletes_own_draft_and_the_connector_are_unaffected(client, auth):
    """The athlete's draft (coach_id NULL) is the one the Plan tab and Claude's connector use."""
    coach = _coach(client)
    _link(client, coach)
    client.post("/api/plan/draft/sessions", json=_session(day(2), sport="swimming"), headers=auth)
    client.post(f"/api/plan/draft/sessions?athlete_id={_id('athlete')}", json=_session(day(1)), headers=coach)
    with SessionLocal() as db:
        user = db.query(models.User).filter_by(username="athlete").one()
        assert user.plan_draft.coach_id is None and "swim" in user.plan_draft.text


def test_a_session_the_athlete_edits_is_no_longer_the_coachs(client, auth):
    coach = _coach(client)
    _link(client, coach)
    client.post(f"/api/plan/draft/sessions?athlete_id={_id('athlete')}", json=_session(day(1)), headers=coach)
    client.post(f"/api/plan/draft/confirm?athlete_id={_id('athlete')}", headers=coach)
    span = {"start": day(1).isoformat(), "end": day(1).isoformat()}
    planned = client.get("/api/calendar", params=span, headers=auth).json()["days"][0]["planned"][0]
    assert planned["planned_by"] == "coach"
    client.patch(f"/api/planned/{planned['id']}", json={"planned_duration_minutes": 30}, headers=auth)
    edited = client.get("/api/calendar", params=span, headers=auth).json()["days"][0]["planned"][0]
    assert (edited["source"], edited["planned_by"]) == ("manual", None)


# --- Sent drafts: "Sent ..." for the coach, "From {coach}" for the athlete -------------------


def test_sending_marks_both_drafts_until_the_next_change(client, auth):
    coach = _coach(client)
    _link(client, coach)
    athlete = _id("athlete")
    mine = f"/api/plan/draft?athlete_id={athlete}"
    client.post(f"/api/plan/draft/sessions?athlete_id={athlete}", json=_session(day(1)), headers=coach)
    assert client.get("/api/coach/athletes", headers=coach).json()["athletes"][0]["draft"]["written_by"] == "coach"
    assert client.post(f"/api/plan/draft/send?athlete_id={athlete}", headers=coach).status_code == 200

    sent = client.get(mine, headers=coach).json()
    assert sent["sent_at"] is not None
    listed = client.get("/api/coach/athletes", headers=coach).json()["athletes"][0]["draft"]
    assert listed["sent_at"] is not None and listed["sessions"] == 1
    own = client.get("/api/plan/draft", headers=auth).json()
    assert own["sent_by"] == "coach" and own["sent_at"] is not None

    # The athlete adjusting it keeps "From coach"; the coach changing theirs clears "Sent".
    client.post("/api/plan/draft/sessions", json=_session(day(2), "cycling"), headers=auth)
    assert client.get("/api/plan/draft", headers=auth).json()["sent_by"] == "coach"
    client.post(f"/api/plan/draft/sessions?athlete_id={athlete}", json=_session(day(3)), headers=coach)
    assert client.get(mine, headers=coach).json()["sent_at"] is None

    # Pushing (or clearing) it is the athlete's: "From coach" goes.
    assert client.post("/api/plan/draft/confirm", headers=auth).status_code == 200
    own = client.get("/api/plan/draft", headers=auth).json()
    assert (own["sent_by"], own["sent_at"]) == (None, None)


def test_clearing_a_received_draft_forgets_who_sent_it(client, auth):
    coach = _coach(client)
    _link(client, coach)
    athlete = _id("athlete")
    client.post(f"/api/plan/draft/sessions?athlete_id={athlete}", json=_session(day(1)), headers=coach)
    client.post(f"/api/plan/draft/send?athlete_id={athlete}", headers=coach)
    assert client.delete("/api/plan/draft", headers=auth).json()["sent_by"] is None


def test_the_coach_list_has_no_draft_until_one_holds_something(client, auth):
    coach = _coach(client)
    _link(client, coach)
    assert client.get("/api/coach/athletes", headers=coach).json()["athletes"][0]["draft"] is None


def test_sent_by_column_can_be_added_to_an_existing_database():
    assert models.PlanDraft.__table__.c.sent_by.nullable


# --- The athlete's sharing switch --------------------------------------------------------


def test_only_the_athlete_turns_sharing_on_and_off(client, auth):
    coach = _coach(client)
    _link(client, coach)
    link_id = client.get("/api/coaches", headers=auth).json()["coaches"][0]["link_id"]
    assert client.get("/api/coaches", headers=auth).json()["coaches"][0]["share_health"] is False
    # The coach can't flip it, nor can another athlete.
    assert client.patch(f"/api/coaches/{link_id}", json={"share_health": True}, headers=coach).status_code == 403
    other = make_user(client, "friend", garmin=False)
    assert client.patch(f"/api/coaches/{link_id}", json={"share_health": True}, headers=other).status_code == 404
    assert client.get("/api/coach/athletes", headers=coach).json()["athletes"][0]["share_health"] is False

    res = client.patch(f"/api/coaches/{link_id}", json={"share_health": True}, headers=auth)
    assert res.status_code == 200 and res.json()["share_health"] is True
    assert client.get("/api/coach/athletes", headers=coach).json()["athletes"][0]["share_health"] is True
    assert client.patch(f"/api/coaches/{link_id}", json={"share_health": False}, headers=auth).json()["share_health"] is False
    assert client.get("/api/coach/athletes", headers=coach).json()["athletes"][0]["share_health"] is False
