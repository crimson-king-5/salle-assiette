"""/snapshot: progress stats and tips, computed from real (synced) workouts."""

import datetime as dt
import uuid

NOW = dt.datetime.now().replace(microsecond=0)
TODAY = NOW.date()
THIS_MONDAY = TODAY - dt.timedelta(days=TODAY.weekday())
LAST_MONDAY = THIS_MONDAY - dt.timedelta(days=7)


def put_workout(client, exercises, day=TODAY, name="Bas A", fmt="complete", minutes=45, finished=None):
    finished = finished or dt.datetime.combine(day, dt.time(18, 0))
    doc = {
        "session_id": "BA", "name": name, "focus": "Force", "format": fmt, "date": day.isoformat(),
        "started_at": (finished - dt.timedelta(minutes=minutes)).isoformat(),
        "finished_at": finished.isoformat(), "exercises": exercises,
    }
    r = client.put(f"/workouts/{uuid.uuid4()}", json=doc)
    assert r.status_code == 201
    return r.json()["id"]


def ex(slug, sets, target="5-6"):
    return {"slug": slug, "target_reps": target,
            "sets": [{"weight": w, "reps": r, "done": d} for w, r, d in sets]}


def progress(client):
    return client.get("/snapshot").json()["progress"]


def test_empty_progress(client):
    p = progress(client)
    assert p["total_workouts"] == 0 and p["weeks_held"] == 0
    assert p["cardio_minutes_week"] == 0 and p["cardio_week_target_min"] == 150
    assert p["records"] == [] and p["recent_workouts"] == []
    snap = client.get("/snapshot").json()
    assert snap["weights"] == []
    # every library exercise has a tip, all "never done"
    assert len(snap["tips"]) == 22
    assert set(snap["tips"].values()) == {"Prends une charge où il te reste 2 à 3 reps en réserve"}


def test_total_workouts_ignores_unfinished_ones(client):
    put_workout(client, [ex("squat", [(100, 5, True)])])
    client.post("/workouts", json={"session_id": "HA", "format": "complete"})  # started, not finished
    assert progress(client)["total_workouts"] == 1


def test_weeks_held_through_the_api(client):
    # goal 4 → 3 workouts a week. Last week has 3, the current week has none yet: streak = 1.
    for i in range(3):
        put_workout(client, [ex("squat", [(100, 5, True)])], day=LAST_MONDAY + dt.timedelta(days=i))
    assert progress(client)["weeks_held"] == 1
    client.put("/profile", json={"sessions_goal": 7})  # needs 6 now
    assert progress(client)["weeks_held"] == 0


def test_cardio_minutes_this_week_only_and_excluded_from_records_and_tonnage(client):
    client.post("/exercises", json={"name": "Tapis", "muscle_group": "Cardio", "equipment": "Machine",
                                    "cardio_unit": "km/h"})
    put_workout(client, [ex("tapis", [(10, 20, True), (10, 10, True), (10, 15, False)], "20")])
    put_workout(client, [ex("tapis", [(None, 40, True)], "20")], day=LAST_MONDAY)  # last week
    p = progress(client)
    assert p["cardio_minutes_week"] == 30
    assert p["records"] == []                       # cardio never makes a weight record
    recent = p["recent_workouts"][0]
    assert recent["tonnage"] == 0 and recent["sets_done"] == 2
    snap = client.get("/snapshot").json()
    assert snap["tips"]["tapis"] == "La dernière fois : 40 min. Ajoute 2 à 5 min ou monte un peu le niveau" \
        or snap["tips"]["tapis"].startswith("La dernière fois : ")


def test_records_best_weight_then_reps_with_date(client):
    first = LAST_MONDAY
    put_workout(client, [ex("squat", [(100, 5, True), (110, 3, True)]),
                         ex("rowing-barre", [(60, 8, True)])], day=first)
    put_workout(client, [ex("squat", [(110, 4, True), (120, 1, False)])], day=TODAY)
    records = {r["slug"]: r for r in progress(client)["records"]}
    assert (records["squat"]["weight"], records["squat"]["reps"]) == (110, 4)
    assert records["squat"]["date"] == TODAY.isoformat()
    assert records["squat"]["name"] == "Squat"
    assert (records["rowing-barre"]["weight"], records["rowing-barre"]["date"]) == (60, first.isoformat())
    assert [r["name"] for r in progress(client)["records"]] == ["Rowing barre", "Squat"]


def test_recent_workouts_are_the_last_six_finished_newest_first(client):
    for i in range(8):
        day = TODAY - dt.timedelta(days=7 - i)
        put_workout(client, [ex("squat", [(100, 5, True), (100, 5, True), (100, 5, False)])],
                    day=day, name=f"S{i}", fmt="minimale", minutes=40 + i)
    recent = progress(client)["recent_workouts"]
    assert [r["name"] for r in recent] == ["S7", "S6", "S5", "S4", "S3", "S2"]
    first = recent[0]
    assert (first["sets_done"], first["tonnage"], first["format"]) == (2, 1000, "minimale")
    assert first["duration_min"] == 47 and first["date"] == TODAY.isoformat()
    assert isinstance(first["id"], int)


def test_tips_follow_the_last_finished_workout_with_the_exercise(client):
    put_workout(client, [ex("squat", [(100, 5, True), (100, 5, True)])], day=LAST_MONDAY)
    assert client.get("/snapshot").json()["tips"]["squat"] == "Garde 100 kg et vise une répétition de plus par série"
    put_workout(client, [ex("squat", [(100, 6, True), (100, 6, True)])], day=TODAY)  # top of 5-6
    tips = client.get("/snapshot").json()["tips"]
    assert tips["squat"] == "Tout était au haut de la fourchette : passe à 105 kg"  # lower body: +5
    # an exercise never trained keeps the starter tip
    assert tips["curl-halteres"].startswith("Prends une charge")


def test_tip_uses_the_isolation_step_and_skips_workouts_without_validated_sets(client):
    put_workout(client, [ex("curl-halteres", [(14, 12, True)], "10-12")], day=LAST_MONDAY)
    put_workout(client, [ex("curl-halteres", [(14, 3, False)], "10-12")], day=TODAY)  # skipped it
    assert client.get("/snapshot").json()["tips"]["curl-halteres"].endswith("passe à 15 kg")


def test_snapshot_exposes_cardio_flags_on_session_exercises(client):
    client.post("/exercises", json={"name": "Tapis", "muscle_group": "Cardio", "equipment": "Machine",
                                    "cardio_unit": "km/h"})
    tapis = client.get("/exercises", params={"q": "tapis"}).json()[0]["slug"]
    sid = client.post("/sessions", json={"name": "Cardio", "focus": "Libre"}).json()["id"]
    client.put(f"/sessions/{sid}", json={"name": "Cardio", "focus": "Libre",
                                         "exercises": [{"slug": tapis, "sets": 1, "reps": "20"}]})
    snap = client.get("/snapshot").json()
    session = next(s for s in snap["sessions"] if s["id"] == sid)
    assert session["formats"]["complete"][0]["is_cardio"] is True
    assert session["formats"]["complete"][0]["cardio_unit"] == "km/h"
    assert snap["sessions"][0]["formats"]["complete"][0]["is_cardio"] is False
