from conftest import make_user


def _objectives(client, auth):
    return client.get("/api/objectives", headers=auth).json()["objectives"]


def _add(client, auth, **body):
    res = client.post("/api/objectives", json=body, headers=auth)
    assert res.status_code == 201, res.text
    return res.json()


def test_create_objective(client, auth):
    obj = _add(client, auth, title="  City triathlon ", event_date="2027-06-20", target_time=" 5:30:00 ")
    assert (obj["title"], obj["event_date"], obj["target_time"]) == ("City triathlon", "2027-06-20",
                                                                     "5:30:00")
    assert client.post("/api/objectives", json={"title": "   "}, headers=auth).status_code == 422
    assert client.post("/api/objectives", json={}, headers=auth).status_code == 422
    assert client.post("/api/objectives", json={"title": "x", "event_date": "spring"}, headers=auth).status_code == 422
    assert [o["title"] for o in _objectives(client, auth)] == ["City triathlon"]


def test_date_and_target_time_are_optional(client, auth):
    obj = _add(client, auth, title="10k")
    assert obj["event_date"] is None and obj["target_time"] is None


def test_edit_and_delete(client, auth):
    obj = _add(client, auth, title="Half", event_date="2027-04-20", target_time="1:50:00")

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


def test_sorted_by_event_date_undated_last(client, auth):
    _add(client, auth, title="someday")
    _add(client, auth, title="later", event_date="2027-09-01")
    _add(client, auth, title="sooner", event_date="2027-05-01")
    assert [o["title"] for o in _objectives(client, auth)] == ["sooner", "later", "someday"]


def test_other_users_objectives_are_off_limits(client, auth):
    obj = _add(client, auth, title="mine")
    other = make_user(client, "other")
    assert client.patch(f"/api/objectives/{obj['id']}", json={"title": "x"}, headers=other).status_code == 404
    assert client.delete(f"/api/objectives/{obj['id']}", headers=other).status_code == 404
    assert len(_objectives(client, auth)) == 1


def test_objective_notes(client, auth):
    obj = _add(client, auth, title="Spring race", notes="  Main race of the year.  ")
    assert obj["notes"] == "Main race of the year."
    assert _add(client, auth, title="Tune-up")["notes"] is None
    res = client.patch(f"/api/objectives/{obj['id']}", json={"notes": "B race"}, headers=auth)
    assert res.json()["notes"] == "B race" and res.json()["title"] == "Spring race"
    assert client.patch(f"/api/objectives/{obj['id']}", json={"notes": ""}, headers=auth).json()["notes"] is None
    assert client.patch(f"/api/objectives/{obj['id']}", json={"notes": "x" * 2001}, headers=auth).status_code == 422
