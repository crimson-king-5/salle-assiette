def test_snapshot_has_every_session_in_every_format(client):
    snap = client.get("/snapshot").json()
    assert [s["id"] for s in snap["sessions"]] == ["HA", "BA", "HB", "BB"]
    bas_a = snap["sessions"][1]["formats"]
    assert len(bas_a["complete"]) == 5
    assert [e["slug"] for e in bas_a["express"]] == ["squat", "sdt-roumain"]
    assert bas_a["complete"][0]["rest_seconds"] == 150  # squat in a Force session


def test_snapshot_on_a_fresh_install(client):
    snap = client.get("/snapshot").json()
    assert snap["next_session_id"] == "HA"
    assert snap["last_weights"] == {}
    assert snap["checkin_today"] is None
    assert (snap["week_count"], snap["week_goal"]) == (0, 4)


def test_snapshot_after_a_checkin_and_a_workout(client):
    client.post("/checkin", json={"energy": 5, "sleep": 5, "motivation": 5, "soreness": 1, "minutes": 60})
    workout = client.post("/workouts", json={"session_id": "BA", "format": "express"}).json()
    squat_sets = workout["exercises"][0]["sets"]
    for weight, s in zip([80, 82.5], squat_sets):
        client.patch(f"/workouts/sets/{s['id']}", json={"weight": weight, "reps": 6, "done": True})
    client.post(f"/workouts/{workout['id']}/finish")

    snap = client.get("/snapshot").json()
    assert snap["checkin_today"]["format"] == "complete"
    assert snap["next_session_id"] == "HB"
    assert snap["last_weights"] == {"squat": 82.5}
    assert snap["week_count"] == 1
