import datetime

from conftest import text_response, tool_response


def _planned(client, auth):
    days = client.get("/api/calendar", headers=auth).json()["days"]
    return [p for d in days for p in d["planned"]]


def test_plain_reply_is_saved(client, auth, fake_claude):
    fake = fake_claude(text_response("Nice work!"))
    res = client.post("/api/chat", json={"message": "Ran 10k today"}, headers=auth)
    assert res.status_code == 200
    assert res.json() == {"reply": "Nice work!", "plan_changes": []}

    history = client.get("/api/chat/history", headers=auth).json()["messages"]
    assert [(m["role"], m["content"]) for m in history] == [("user", "Ran 10k today"), ("assistant", "Nice work!")]
    assert fake.calls[0]["messages"] == [{"role": "user", "content": "Ran 10k today"}]


def test_tool_call_creates_updates_deletes(client, auth, fake_claude):
    tomorrow = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()

    fake_claude(
        tool_response([{"action": "create", "date": tomorrow, "activity_type": "running", "planned_duration_minutes": 45}]),
        text_response("Added an easy run."),
    )
    res = client.post("/api/chat", json={"message": "plan a run"}, headers=auth).json()
    assert res["plan_changes"][0]["action"] == "create"
    [row] = _planned(client, auth)
    assert row["date"] == tomorrow and row["source"] == "ai" and row["planned_duration_minutes"] == 45

    fake_claude(tool_response([{"action": "update", "id": row["id"], "planned_duration_minutes": 30}]), text_response("ok"))
    client.post("/api/chat", json={"message": "shorter"}, headers=auth)
    assert _planned(client, auth)[0]["planned_duration_minutes"] == 30

    fake_claude(tool_response([{"action": "delete", "id": row["id"]}]), text_response("ok"))
    client.post("/api/chat", json={"message": "cancel it"}, headers=auth)
    assert _planned(client, auth) == []


def test_tool_cannot_touch_other_users_sessions(client, auth, fake_claude):
    from conftest import login

    tomorrow = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
    fake_claude(tool_response([{"action": "create", "date": tomorrow, "activity_type": "running"}]), text_response("ok"))
    client.post("/api/chat", json={"message": "plan"}, headers=auth)
    [row] = _planned(client, auth)

    other = login(client, email="other@example.com")
    fake_claude(tool_response([{"action": "delete", "id": row["id"]}]), text_response("ok"))
    res = client.post("/api/chat", json={"message": "delete"}, headers=other).json()
    assert res["plan_changes"] == []
    assert len(_planned(client, auth)) == 1


def test_bad_date_from_model_does_not_500(client, auth, fake_claude):
    fake_claude(
        tool_response([{"action": "create", "date": "next tuesday", "activity_type": "running"}]),
        text_response("Sorry, let me fix that."),
    )
    res = client.post("/api/chat", json={"message": "plan"}, headers=auth)
    assert res.status_code == 200
    assert res.json()["plan_changes"] == []


def test_system_prompt_includes_context(client, auth, fake_claude):
    from app import models
    from app.database import SessionLocal

    with SessionLocal() as db:
        db.add(models.Objective(user_id=db.query(models.User).one().id, title="Montreal half",
                                event_date=datetime.date(2027, 4, 20), target_time="1:45:00"))
        db.commit()
    fake = fake_claude(text_response("hi"))
    client.post("/api/chat", json={"message": "hello"}, headers=auth)
    system = fake.calls[0]["system"]
    assert "Montreal half, event date 2027-04-20, target time 1:45:00" in system
    assert [t["name"] for t in fake.calls[0]["tools"]] == ["update_training_plan", "update_objectives"]
    assert datetime.date.today().isoformat() in system


def test_history_window_starts_with_user_turn(client, auth, fake_claude):
    """After enough exchanges the 20-message window must still start on a
    user turn (and alternate), or the Messages API may reject the request."""
    for i in range(11):
        fake = fake_claude(text_response(f"reply {i}"))
        client.post("/api/chat", json={"message": f"msg {i}"}, headers=auth)

    msgs = fake.calls[-1]["messages"]
    assert msgs[0]["role"] == "user"
    assert msgs[-1] == {"role": "user", "content": "msg 10"}
    for a, b in zip(msgs, msgs[1:]):
        assert a["role"] != b["role"]


def test_missing_api_key(client, auth, monkeypatch):
    from app import ai_coach

    monkeypatch.setattr(ai_coach.settings, "anthropic_api_key", "")
    res = client.post("/api/chat", json={"message": "hi"}, headers=auth)
    assert res.status_code == 400
    assert "Settings" in res.json()["detail"]
