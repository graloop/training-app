from conftest import login, text_response, tool_response

OBJ = "update_objectives"


def _objectives(client, auth):
    return client.get("/api/objectives", headers=auth).json()["objectives"]


def test_coach_creates_objective(client, auth, fake_claude):
    fake_claude(
        tool_response([{"action": "create", "title": "Montreal half marathon", "event_date": "2027-04-20",
                        "target_time": "1:45:00"}], name=OBJ),
        text_response("Locked in!"),
    )
    res = client.post("/api/chat", json={"message": "My race is the Montreal half on April 20, aiming 1:45"},
                      headers=auth)
    assert res.json()["reply"] == "Locked in!"
    [obj] = _objectives(client, auth)
    assert (obj["title"], obj["event_date"], obj["target_time"]) == ("Montreal half marathon", "2027-04-20", "1:45:00")


def test_coach_updates_and_deletes(client, auth, fake_claude):
    fake_claude(tool_response([{"action": "create", "title": "10k"}], name=OBJ), text_response("ok"))
    client.post("/api/chat", json={"message": "a"}, headers=auth)
    [obj] = _objectives(client, auth)
    assert obj["event_date"] is None and obj["target_time"] is None  # coach may not know them yet

    fake_claude(tool_response([{"action": "update", "id": obj["id"], "target_time": "45:00"}], name=OBJ),
                text_response("ok"))
    client.post("/api/chat", json={"message": "b"}, headers=auth)
    assert _objectives(client, auth)[0]["target_time"] == "45:00"

    fake_claude(tool_response([{"action": "delete", "id": obj["id"]}], name=OBJ), text_response("ok"))
    client.post("/api/chat", json={"message": "c"}, headers=auth)
    assert _objectives(client, auth) == []


def test_coach_bad_input_is_ignored(client, auth, fake_claude):
    fake_claude(tool_response([{"action": "create", "title": ""},
                               {"action": "create", "title": "x", "event_date": "spring"}], name=OBJ),
                text_response("ok"))
    assert client.post("/api/chat", json={"message": "a"}, headers=auth).status_code == 200
    assert _objectives(client, auth) == []


def test_user_edits_and_deletes(client, auth, fake_claude):
    fake_claude(tool_response([{"action": "create", "title": "Half", "event_date": "2027-04-20",
                                "target_time": "1:50:00"}], name=OBJ), text_response("ok"))
    client.post("/api/chat", json={"message": "a"}, headers=auth)
    [obj] = _objectives(client, auth)

    res = client.patch(f"/api/objectives/{obj['id']}", json={"title": " Montreal half ", "event_date": "2027-04-27",
                                                            "target_time": "1:45:00"}, headers=auth)
    assert res.status_code == 200
    assert (res.json()["title"], res.json()["event_date"], res.json()["target_time"]) == (
        "Montreal half", "2027-04-27", "1:45:00")

    res = client.patch(f"/api/objectives/{obj['id']}", json={"event_date": None, "target_time": ""}, headers=auth)
    assert res.json()["event_date"] is None and res.json()["target_time"] is None
    assert client.patch(f"/api/objectives/{obj['id']}", json={"title": ""}, headers=auth).status_code == 422

    assert client.delete(f"/api/objectives/{obj['id']}", headers=auth).status_code == 204
    assert _objectives(client, auth) == []


def test_sorted_by_event_date_undated_last(client, auth, fake_claude):
    fake_claude(tool_response([
        {"action": "create", "title": "someday"},
        {"action": "create", "title": "later", "event_date": "2027-09-01"},
        {"action": "create", "title": "sooner", "event_date": "2027-05-01"},
    ], name=OBJ), text_response("ok"))
    client.post("/api/chat", json={"message": "a"}, headers=auth)
    assert [o["title"] for o in _objectives(client, auth)] == ["sooner", "later", "someday"]


def test_other_users_objectives_are_off_limits(client, auth, fake_claude):
    fake_claude(tool_response([{"action": "create", "title": "mine"}], name=OBJ), text_response("ok"))
    client.post("/api/chat", json={"message": "a"}, headers=auth)
    [obj] = _objectives(client, auth)
    other = login(client, email="other@example.com")
    assert client.patch(f"/api/objectives/{obj['id']}", json={"title": "x"}, headers=other).status_code == 404
    assert client.delete(f"/api/objectives/{obj['id']}", headers=other).status_code == 404
    fake_claude(tool_response([{"action": "delete", "id": obj["id"]}], name=OBJ), text_response("ok"))
    client.post("/api/chat", json={"message": "b"}, headers=other)
    assert len(_objectives(client, auth)) == 1
