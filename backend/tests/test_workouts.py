def start(client, session_id="BA", format="complete"):
    response = client.post("/workouts", json={"session_id": session_id, "format": format})
    assert response.status_code == 201
    return response.json()


def validate(client, set_id, weight, reps):
    return client.patch(f"/workouts/sets/{set_id}", json={"weight": weight, "reps": reps, "done": True})


def test_start_creates_empty_sets_from_the_session(client):
    workout = start(client, "BA")
    assert workout["name"] == "Bas A"
    squat = workout["exercises"][0]
    assert squat["slug"] == "squat"
    assert squat["target_reps"] == "5-6"
    assert len(squat["sets"]) == 4
    assert squat["sets"][0] == {"id": squat["sets"][0]["id"], "weight": None, "reps": None, "done": False}


def test_start_respects_the_format(client):
    workout = start(client, "BA", "express")
    assert [e["slug"] for e in workout["exercises"]] == ["squat", "sdt-roumain"]
    assert [len(e["sets"]) for e in workout["exercises"]] == [3, 3]


def test_rest_timer_depends_on_exercise_type(client):
    rests = {e["slug"]: e["rest_seconds"] for e in start(client, "BA")["exercises"]}
    assert rests["squat"] == 150         # Force session, compound
    assert rests["leg-curl"] == 60       # isolation


def test_only_one_workout_at_a_time(client):
    start(client)
    assert client.post("/workouts", json={"session_id": "HA", "format": "complete"}).status_code == 409


def test_current_workout_can_be_resumed(client):
    assert client.get("/workouts/current").status_code == 404
    started = start(client)
    assert client.get("/workouts/current").json()["id"] == started["id"]


def test_validate_and_unvalidate_a_set(client):
    set_id = start(client)["exercises"][0]["sets"][0]["id"]
    assert validate(client, set_id, 80, 6).json()["done"] is True
    undone = client.patch(f"/workouts/sets/{set_id}", json={"weight": 80, "reps": 6, "done": False})
    assert undone.json()["done"] is False


def test_finish_returns_the_recap(client):
    workout = start(client, "BA", "express")
    squat_sets = workout["exercises"][0]["sets"]
    validate(client, squat_sets[0]["id"], 80, 6)
    validate(client, squat_sets[1]["id"], 80, 5)

    summary = client.post(f"/workouts/{workout['id']}/finish").json()
    assert summary["sets_done"] == 2
    assert summary["tonnage"] == 80 * 6 + 80 * 5
    assert summary["week_count"] == 1
    assert client.get("/workouts/current").status_code == 404


def test_last_weight_prefills_the_next_workout(client):
    first = start(client, "BA", "express")
    validate(client, first["exercises"][0]["sets"][0]["id"], 82.5, 6)
    client.post(f"/workouts/{first['id']}/finish")

    second = start(client, "BA", "express")
    assert second["exercises"][0]["last_weight"] == 82.5


def test_rotation_moves_to_next_session_after_a_workout(client):
    assert client.get("/sessions/next").json() == {"id": "HA"}
    workout = start(client, "HA")
    client.post(f"/workouts/{workout['id']}/finish")
    assert client.get("/sessions/next").json() == {"id": "BA"}


def test_abandon_deletes_the_workout(client):
    workout = start(client)
    assert client.delete(f"/workouts/{workout['id']}").status_code == 204
    assert client.get("/workouts/current").status_code == 404
