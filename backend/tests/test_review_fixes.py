"""Fixes after review: CSRF, import robustness, exercise editing, stable ids, one weight a day."""

import copy
import datetime as dt
import json
import time
import uuid

import pytest

from app.algorithms import guess_exercise
from app.models import (
    Exercise, ProteinEntry, SessionTemplate, WeightEntry, Workout, WorkoutTombstone,
)

NOW = dt.datetime.now().replace(microsecond=0)
TODAY = NOW.date()
OWN = "https://desktop-m7c8gnc.tail696942.ts.net"
JSON_HEADERS = {"content-type": "application/json"}


def post_import(client, data, headers=None, **params):
    return client.post("/import/prototype", content=json.dumps(data), params=params,
                       headers=headers or JSON_HEADERS)


def log(date="2026-09-20", started=1758360000000, ex=None, **extra):
    doc = {"session": "HA", "name": "Haut A", "focus": "Force", "fmt": "complete", "date": date,
           "ex": ex or [{"n": "Squat", "r": "5-6", "sets": [{"w": "100", "r": "5"}]}]}
    if started is not None:
        doc["startedAt"] = started
    doc.update(extra)
    return doc


# --- 1. CSRF ---

EVIL_IMPORT = {"program": {"HA": {"name": "PIRATE", "focus": "Libre", "ex": []}},
               "goals": {"sessions": 1}}


def test_cross_origin_text_plain_import_is_refused(client, db):
    r = client.post("/import/prototype", content=json.dumps(EVIL_IMPORT),
                    headers={"origin": "https://evil.example", "content-type": "text/plain"})
    assert r.status_code == 403
    assert db.get(SessionTemplate, "HA").name == "Haut A"
    assert client.get("/profile").json()["sessions_goal"] == 4


def test_cross_origin_json_is_refused_too(client, db):
    r = post_import(client, EVIL_IMPORT, {"origin": "https://evil.example", **JSON_HEADERS})
    assert r.status_code == 403 and r.json()["detail"] == "Origine non autorisée"
    assert db.get(SessionTemplate, "HA").name == "Haut A"


def test_origin_null_is_refused(client):
    assert client.put("/profile", json={"sessions_goal": 2}, headers={"origin": "null"}).status_code == 403


def test_import_requires_application_json(client, db):
    for content_type in ("text/plain", "application/x-www-form-urlencoded", "multipart/form-data"):
        r = client.post("/import/prototype", content=json.dumps(EVIL_IMPORT),
                        headers={"content-type": content_type})
        assert r.status_code == 415
    assert client.post("/import/prototype", content=json.dumps(EVIL_IMPORT)).status_code == 415
    assert db.get(SessionTemplate, "HA").name == "Haut A"


@pytest.mark.parametrize("origin", [OWN, "http://localhost:8001", "http://127.0.0.1:8001"])
def test_same_origin_json_import_works(client, db, origin):
    r = post_import(client, {"program": {"HA": {"name": "Renommé", "focus": "Force", "ex": []}}},
                    {"origin": origin, **JSON_HEADERS})
    assert r.status_code == 200
    assert db.get(SessionTemplate, "HA").name == "Renommé"


def test_foreign_origin_is_blocked_on_every_write_method_but_not_on_reads(client):
    evil = {"origin": "https://evil.example"}
    assert client.get("/snapshot", headers=evil).status_code == 200
    assert client.put(f"/weights/{uuid.uuid4()}", headers=evil, json={}).status_code == 403
    assert client.delete(f"/weights/{uuid.uuid4()}", headers=evil).status_code == 403
    assert client.post("/sessions", headers=evil, json={"name": "A", "focus": "Libre"}).status_code == 403
    assert client.patch("/workouts/sets/1", headers=evil, json={"done": True}).status_code == 403


TEXT_PLAIN_WRITES = [
    ("POST", "/checkin", {"energy": 5, "sleep": 5, "motivation": 5, "soreness": 1, "minutes": 60}),
    ("PUT", f"/checkins/{uuid.uuid4()}", {"energy": 5, "sleep": 5, "motivation": 5, "soreness": 1,
                                         "minutes": 60, "date": "2026-10-01", "created_at": "2026-10-01T08:00:00"}),
    ("POST", "/workouts", {"session_id": "HA", "format": "complete"}),
    ("PUT", f"/workouts/{uuid.uuid4()}", {"session_id": "HA", "name": "x", "focus": "Libre",
                                          "format": "complete", "date": "2026-10-01",
                                          "started_at": "2026-10-01T08:00:00",
                                          "finished_at": "2026-10-01T09:00:00", "exercises": []}),
    ("PUT", f"/protein/{uuid.uuid4()}", {"date": "2026-10-01", "label": "x", "grams": 10,
                                         "created_at": "2026-10-01T08:00:00"}),
    ("PUT", f"/weights/{uuid.uuid4()}", {"date": "2026-10-01", "kg": 80, "created_at": "2026-10-01T08:00:00"}),
    ("PUT", "/profile", {"sessions_goal": 2}),
    ("POST", "/exercises", {"name": "Pirate", "muscle_group": "Dos", "equipment": "Machine"}),
    ("POST", "/sessions", {"name": "Pirate", "focus": "Libre"}),
    ("PUT", "/sessions/HA", {"name": "Pirate", "focus": "Libre", "exercises": []}),
    ("PUT", "/rotation", {"order": ["BB", "BA", "HB", "HA"]}),
]


@pytest.mark.parametrize("method,url,body", TEXT_PLAIN_WRITES)
def test_text_plain_bodies_never_modify_data(client, db, method, url, body):
    r = client.request(method, url, content=json.dumps(body), headers={"content-type": "text/plain"})
    assert r.status_code in (415, 422)
    assert db.query(Workout).count() == 0 and db.query(WeightEntry).count() == 0
    assert db.query(ProteinEntry).count() == 0 and db.query(Exercise).count() == 22
    assert [t.id for t in db.query(SessionTemplate).order_by(SessionTemplate.position)] == ["HA", "BA", "HB", "BB"]
    assert db.get(SessionTemplate, "HA").name == "Haut A"
    assert client.get("/profile").json()["sessions_goal"] == 4


# --- 2. import DoS ---

def test_import_of_thousands_of_unknown_names_is_refused_fast(client, db):
    data = {"logs": [log(date="2026-09-20", ex=[
        {"n": f"Exercice inconnu {i}", "r": "10", "sets": [{"w": "10", "r": "10"}]} for i in range(30)])
        for _ in range(100)]}
    data["logs"] = [dict(l, ex=[dict(e, n=f"{e['n']}-{k}") for e in l["ex"]]) for k, l in enumerate(data["logs"])]
    start = time.time()
    r = post_import(client, data)
    assert r.status_code == 422 and "Trop d'exercices inconnus" in r.json()["detail"]
    assert time.time() - start < 5
    assert db.query(Exercise).count() == 22 and db.query(Workout).count() == 0


def test_import_cap_is_200_new_exercises(client, db):
    # a log holds at most 30 exercises, so spread them over several logs
    def many(count):
        logs = []
        for start in range(0, count, 20):
            logs.append(log(started=1758360000000 + start,
                            ex=[{"n": f"Machine {i}", "r": "10", "sets": []}
                                for i in range(start, min(count, start + 20))]))
        return {"logs": logs}
    assert post_import(client, many(201)).status_code == 422
    assert db.query(Exercise).count() == 22
    r = post_import(client, many(200))
    assert r.status_code == 200 and len(r.json()["exercises_created"]) == 200


def test_import_error_leaves_nothing_behind(client, db):
    data = {"program": {"HA": {"name": "X", "focus": "Libre", "ex": [{"n": "Truc", "s": 3, "r": "10"}]}},
            "logs": [log(ex=[{"n": "  ", "r": "", "sets": []}])]}
    assert post_import(client, data).status_code == 422
    assert db.get(SessionTemplate, "HA").name == "Haut A" and db.query(Exercise).count() == 22


# --- dry run ---

def test_dry_run_returns_the_counts_and_writes_nothing(client, db):
    data = {"program": {"ZZ": {"name": "Nouvelle", "focus": "Libre", "ex": [{"n": "Truc", "s": 3, "r": "10"}]}},
            "goals": {"sessions": 6}, "logs": [log()], "weights": [{"date": "2026-09-20", "kg": 88}],
            "protein": {"2026-09-20": [["Poulet", 30]]}}
    preview = post_import(client, data, dry_run="true").json()
    assert preview["dry_run"] is True
    assert (preview["sessions_created"], preview["workouts_created"], preview["weights_created"],
            preview["protein_created"], preview["goals_updated"]) == (1, 1, 1, 1, True)
    assert db.query(Workout).count() == 0 and db.query(WeightEntry).count() == 0
    assert db.get(SessionTemplate, "ZZ") is None and db.query(Exercise).count() == 22
    assert client.get("/profile").json()["sessions_goal"] == 4
    real = post_import(client, data).json()
    assert real["dry_run"] is False
    assert {k: v for k, v in real.items() if k != "dry_run"} == {k: v for k, v in preview.items() if k != "dry_run"}


# --- 3. guessing, needs_review, editing ---

def test_guess_exercise_keywords():
    tapis = guess_exercise("Tapis de course")
    assert (tapis["is_cardio"], tapis["cardio_unit"], tapis["muscle_group"]) == (True, "km/h", "Cardio")
    for name, unit in [("Vélo", "niveau"), ("Elliptique", "niveau"), ("Rameur", "m"),
                       ("Corde à sauter", "tours"), ("Natation", "m"), ("Stepper", "niveau"),
                       ("Assault bike", "niveau"), ("Marche inclinée", "km/h")]:
        assert guess_exercise(name)["cardio_unit"] == unit, name
    assert guess_exercise("Fentes marchées")["is_cardio"] is False       # "marchees" is not "marche"
    assert guess_exercise("Rowing barre")["is_cardio"] is False
    assert guess_exercise("Extension triceps corde")["muscle_group"] == "Triceps"
    curl = guess_exercise("Curl barre EZ")
    assert (curl["muscle_group"], curl["is_isolation"], curl["is_lower"]) == ("Biceps", True, False)
    leg = guess_exercise("Leg extension unilatéral")
    assert (leg["muscle_group"], leg["is_isolation"], leg["is_lower"], leg["equipment"]) == (
        "Jambes", True, True, "Machine")
    assert guess_exercise("Mollets à la presse")["is_lower"] is True
    assert guess_exercise("Élévations latérales poulie")["muscle_group"] == "Épaules"


def test_imported_cardio_is_not_a_weight_record_and_unknown_ones_need_review(client, db):
    data = {"logs": [log(ex=[
        {"n": "Tapis de course", "r": "20", "sets": [{"w": "10", "r": "20"}, {"w": "10", "r": "10"}]},
        {"n": "Machine bizarre", "r": "10", "sets": [{"w": "50", "r": "10"}]},
        {"n": "Squat", "r": "5", "sets": [{"w": "100", "r": "5"}]},
    ])]}
    out = post_import(client, data).json()
    assert out["exercises_created"] == ["Tapis de course", "Machine bizarre"]
    review = {e["name"]: e for e in out["exercises_to_review"]}
    assert review["Tapis de course"]["is_cardio"] is True and review["Tapis de course"]["muscle_group"] == "Cardio"
    assert db.get(Exercise, "tapis-de-course").needs_review is True
    assert db.get(Exercise, "squat").needs_review is False
    p = client.get("/snapshot").json()["progress"]
    assert {r["slug"] for r in p["records"]} == {"machine-bizarre", "squat"}   # no cardio record
    assert p["recent_workouts"][0]["tonnage"] == 50 * 10 + 100 * 5
    assert client.get("/snapshot").json()["tips"]["tapis-de-course"].startswith("La dernière fois : 30 min")


def test_described_custom_exercises_are_not_flagged(client, db):
    data = {"logs": [log(ex=[{"n": "Ma machine", "r": "10", "sets": []}])],
            "custom": {"Ma machine": {"g": "Dos", "eq": "Machine", "iso": 1}}}
    out = post_import(client, data).json()
    assert out["exercises_created"] == ["Ma machine"] and out["exercises_to_review"] == []
    assert db.get(Exercise, "ma-machine").needs_review is False


def test_prototype_rename_pec_deck(client, db):
    data = {"logs": [log(ex=[{"n": "Pec deck", "r": "12", "sets": []}]),
                     log(started=1758360000001, ex=[{"n": "Pec deck (butterfly)", "r": "12", "sets": []}])]}
    out = post_import(client, data).json()
    assert out["exercises_created"] == ["Pec deck (butterfly)"]
    assert db.query(Exercise).filter(Exercise.is_custom).count() == 1
    # once the library has it, the old name resolves to it
    again = post_import(client, {"logs": [log(started=5, ex=[{"n": "pec deck", "r": "12", "sets": []}])]}).json()
    assert again["exercises_created"] == []


def test_parenthesis_free_name_matches_the_library(client, db):
    out = post_import(client, {"logs": [log(ex=[{"n": "Développé militaire (barre)", "r": "8", "sets": []},
                                                {"n": "lat pulldown", "r": "8", "sets": []}])]}).json()
    assert out["exercises_created"] == []


def test_editing_a_custom_exercise_fixes_stats_retroactively(client, db):
    post_import(client, {"logs": [log(ex=[{"n": "Machine bizarre", "r": "20",
                                           "sets": [{"w": "10", "r": "20"}, {"w": "10", "r": "10"}]}])]})
    snap = client.get("/snapshot").json()
    assert [r["slug"] for r in snap["progress"]["records"]] == ["machine-bizarre"]
    assert snap["progress"]["cardio_minutes_week"] == 0 and snap["progress"]["recent_workouts"][0]["tonnage"] > 0
    assert snap["tips"]["machine-bizarre"] == "Garde 10 kg et vise une répétition de plus par série"

    r = client.put("/exercises/machine-bizarre", json={
        "name": "Vélo de salle", "muscle_group": "Cardio", "equipment": "Machine", "cardio_unit": "niveau"})
    assert r.status_code == 200
    body = r.json()
    assert (body["name"], body["is_cardio"], body["needs_review"], body["slug"]) == (
        "Vélo de salle", True, False, "machine-bizarre")
    snap = client.get("/snapshot").json()
    assert snap["progress"]["records"] == []
    assert snap["progress"]["recent_workouts"][0]["tonnage"] == 0
    assert snap["tips"]["machine-bizarre"] == "La dernière fois : 30 min. Ajoute 2 à 5 min ou monte un peu le niveau"
    this_week = post_import(client, {"logs": [log(date=TODAY.isoformat(), started=None, ex=[
        {"n": "Vélo de salle", "r": "10", "sets": [{"r": "25"}]}])]})
    assert this_week.status_code == 200
    assert client.get("/snapshot").json()["progress"]["cardio_minutes_week"] == 25


def test_put_exercise_rules(client):
    assert client.put("/exercises/squat", json={"name": "X", "muscle_group": "Dos", "equipment": "Machine"}
                      ).status_code == 403
    assert client.put("/exercises/inconnu", json={"name": "X", "muscle_group": "Dos", "equipment": "Machine"}
                      ).status_code == 404
    client.post("/exercises", json={"name": "A", "muscle_group": "Dos", "equipment": "Machine"})
    client.post("/exercises", json={"name": "B", "muscle_group": "Dos", "equipment": "Machine"})
    assert client.put("/exercises/b", json={"name": "a", "muscle_group": "Dos", "equipment": "Machine"}
                      ).status_code == 409
    assert client.put("/exercises/b", json={"name": "B", "muscle_group": "Dos", "equipment": "Poulie"}
                      ).json()["equipment"] == "Poulie"          # same name, own slug: fine
    assert client.put("/exercises/b", json={"name": "", "muscle_group": "Dos", "equipment": "Poulie"}
                      ).status_code == 422


def test_delete_exercise_rules(client):
    assert client.delete("/exercises/squat").status_code == 403
    assert client.delete("/exercises/inconnu").status_code == 404
    client.post("/exercises", json={"name": "Libre", "muscle_group": "Dos", "equipment": "Machine"})
    client.post("/exercises", json={"name": "Utilisé", "muscle_group": "Dos", "equipment": "Machine"})
    client.post("/exercises", json={"name": "Historique", "muscle_group": "Dos", "equipment": "Machine"})
    client.put("/sessions/HA", json={"name": "Haut A", "focus": "Force",
                                     "exercises": [{"slug": "utilise", "sets": 3, "reps": "10"}]})
    client.put(f"/workouts/{uuid.uuid4()}", json={
        "session_id": "HA", "name": "x", "focus": "Libre", "format": "complete", "date": "2026-10-01",
        "started_at": "2026-10-01T08:00:00", "finished_at": "2026-10-01T09:00:00",
        "exercises": [{"slug": "historique", "target_reps": "10", "sets": []}]})
    assert client.delete("/exercises/utilise").status_code == 409
    assert client.delete("/exercises/historique").status_code == 409
    assert client.delete("/exercises/libre").status_code == 204
    assert client.delete("/exercises/libre").status_code == 404
    assert "libre" not in [e["slug"] for e in client.get("/exercises").json()]


# --- 4. stable ids across exports ---

def count_all(db):
    return (db.query(Workout).count(), db.query(ProteinEntry).count(), db.query(WeightEntry).count())


def test_protein_line_inserted_earlier_in_the_day_does_not_duplicate(client, db):
    day = "2026-09-24"
    post_import(client, {"protein": {day: [["Poulet", 35], ["Skyr", 15], ["Poulet", 35]]}})
    assert db.query(ProteinEntry).count() == 3
    out = post_import(client, {"protein": {day: [["Whey", 24], ["Poulet", 35], ["Skyr", 15], ["Poulet", 35]]}}).json()
    assert (out["protein_created"], out["protein_updated"]) == (1, 3)
    assert db.query(ProteinEntry).count() == 4
    assert sorted(p.label for p in db.query(ProteinEntry)) == ["Poulet", "Poulet", "Skyr", "Whey"]


def test_edited_rep_updates_the_same_workout(client, db):
    post_import(client, {"logs": [log()]})
    edited = log(ex=[{"n": "Squat", "r": "5-6", "sets": [{"w": "100", "r": "6"}]}])
    out = post_import(client, {"logs": [edited]}).json()
    assert (out["workouts_created"], out["workouts_updated"]) == (0, 1)
    assert db.query(Workout).one().exercises[0].sets[0].reps == 6


def test_workouts_without_started_at_use_session_and_name(client, db):
    post_import(client, {"logs": [log(started=None)]})
    edited = log(started=None, ex=[{"n": "Squat", "r": "5-6", "sets": [{"w": "105", "r": "5"}]}])
    post_import(client, {"logs": [edited, log(started=None, name="Bas A")]})
    assert db.query(Workout).count() == 2


def test_new_workout_added_before_does_not_shift_the_others(client, db):
    older = log(date="2026-09-18", started=1758200000000)
    post_import(client, {"logs": [log()]})
    out = post_import(client, {"logs": [older, log()]}).json()
    assert (out["workouts_created"], out["workouts_updated"]) == (1, 1)


def test_deleted_imported_workout_does_not_come_back(client, db):
    post_import(client, {"logs": [log(), log(date="2026-09-22", started=1758532800000)]})
    victim = db.query(Workout).filter(Workout.date == dt.date(2026, 9, 20)).one()
    cid = victim.client_id
    assert client.delete(f"/workouts/{victim.id}").status_code == 204
    assert db.get(WorkoutTombstone, cid) is not None
    out = post_import(client, {"logs": [log(), log(date="2026-09-22", started=1758532800000)]}).json()
    assert out["skipped_deleted"] == 1 and out["workouts_created"] == 0
    assert db.query(Workout).count() == 1
    # a late sync of the same workout is ignored as well
    late = client.put(f"/workouts/{cid}", json={
        "session_id": "HA", "name": "x", "focus": "Libre", "format": "complete", "date": "2026-09-20",
        "started_at": "2026-09-20T08:00:00", "finished_at": "2026-09-20T09:00:00", "exercises": []})
    assert late.status_code == 200 and late.json() == {"id": 0, "created": False}
    assert db.query(Workout).count() == 1


def test_abandoning_a_server_side_workout_needs_no_tombstone(client, db):
    w = client.post("/workouts", json={"session_id": "HA", "format": "complete"}).json()
    assert client.delete(f"/workouts/{w['id']}").status_code == 204
    assert db.query(WorkoutTombstone).count() == 0


# --- 6. one weigh-in per day ---

def put_weight(client, kg, created, cid=None, day=TODAY):
    cid = cid or str(uuid.uuid4())
    r = client.put(f"/weights/{cid}", json={"date": day.isoformat(), "kg": kg, "created_at": created.isoformat()})
    return cid, r


def test_second_weigh_in_of_a_day_replaces_the_first(client, db):
    first, _ = put_weight(client, 88.0, NOW - dt.timedelta(hours=3))
    second, r = put_weight(client, 87.5, NOW)
    assert r.status_code == 201
    assert [(w.client_id, w.kg) for w in db.query(WeightEntry)] == [(second, 87.5)]
    assert client.get("/snapshot").json()["weights"][0]["kg"] == 87.5
    # the replaced one cannot come back through a retry
    assert client.put(f"/weights/{first}", json={"date": TODAY.isoformat(), "kg": 88.0,
                                                 "created_at": NOW.isoformat()}).json() == {"id": 0, "created": False}
    assert db.query(WeightEntry).count() == 1


def test_older_weigh_in_arriving_late_loses(client, db):
    latest, _ = put_weight(client, 87.5, NOW)
    old, r = put_weight(client, 88.0, NOW - dt.timedelta(hours=5))
    assert r.status_code == 200 and r.json() == {"id": 0, "created": False}
    assert [w.client_id for w in db.query(WeightEntry)] == [latest]


def test_deleting_the_latest_does_not_resurrect_an_older_same_day_value(client, db):
    put_weight(client, 88.0, NOW - dt.timedelta(hours=3))
    latest, _ = put_weight(client, 87.5, NOW)
    assert client.delete(f"/weights/{latest}").status_code == 204
    assert db.query(WeightEntry).count() == 0
    assert client.get("/snapshot").json()["weights"] == []


def test_correcting_a_weigh_in_keeps_a_single_row(client, db):
    cid, _ = put_weight(client, 88.0, NOW)
    _, r = put_weight(client, 87.9, NOW, cid=cid)
    assert r.status_code == 200
    assert [w.kg for w in db.query(WeightEntry)] == [87.9]


def test_different_days_are_independent(client, db):
    put_weight(client, 88.0, NOW)
    put_weight(client, 89.0, NOW, day=TODAY - dt.timedelta(days=1))
    assert db.query(WeightEntry).count() == 2


def test_import_keeps_one_weight_per_day_and_yields_to_real_ones(client, db):
    day = TODAY - dt.timedelta(days=2)
    real, _ = put_weight(client, 86.0, dt.datetime.combine(day, dt.time(20, 0)), day=day)
    out = post_import(client, {"weights": [{"date": day.isoformat(), "kg": 90}, {"date": day.isoformat(), "kg": 91},
                                           {"date": (day - dt.timedelta(days=1)).isoformat(), "kg": 92}]}).json()
    assert (out["weights_created"], out["weights_skipped"]) == (1, 1)
    assert sorted(w.kg for w in db.query(WeightEntry)) == [86.0, 92.0]
    again = post_import(client, {"weights": [{"date": (day - dt.timedelta(days=1)).isoformat(), "kg": 93}]}).json()
    assert again["weights_updated"] == 1 and sorted(w.kg for w in db.query(WeightEntry)) == [86.0, 93.0]


# --- startable sessions ---

def test_empty_sessions_are_marked_not_startable_in_the_snapshot(client):
    sid = client.post("/sessions", json={"name": "Vide", "focus": "Libre"}).json()["id"]
    sessions = {s["id"]: s for s in client.get("/snapshot").json()["sessions"]}
    assert sessions[sid]["startable"] is False and sessions[sid]["formats"]["complete"] == []
    assert sessions["HA"]["startable"] is True
    client.put(f"/sessions/{sid}", json={"name": "Vide", "focus": "Libre",
                                         "exercises": [{"slug": "squat", "sets": 3, "reps": "8"}]})
    sessions = {s["id"]: s for s in client.get("/snapshot").json()["sessions"]}
    assert sessions[sid]["startable"] is True


# --- weigh-in follow-ups: imports never overwrite, moves win ---

def test_import_never_overwrites_an_early_phone_weigh_in(client, db):
    day = TODAY - dt.timedelta(days=3)
    phone, _ = put_weight(client, 86.0, dt.datetime.combine(day, dt.time(7, 5)), day=day)
    data = {"weights": [{"date": day.isoformat(), "kg": 90}]}
    out = post_import(client, data).json()
    assert (out["weights_created"], out["weights_skipped"]) == (0, 1)
    assert [(w.client_id, w.kg) for w in db.query(WeightEntry)] == [(phone, 86.0)]
    # the phone can still edit its row afterwards
    _, r = put_weight(client, 85.5, dt.datetime.combine(day, dt.time(7, 5)), cid=phone, day=day)
    assert r.status_code == 200
    assert [w.kg for w in db.query(WeightEntry)] == [85.5]
    # a re-import changes nothing
    again = post_import(client, data).json()
    assert (again["weights_created"], again["weights_updated"], again["weights_skipped"]) == (0, 0, 1)
    assert [(w.client_id, w.kg) for w in db.query(WeightEntry)] == [(phone, 85.5)]


def test_imported_weigh_ins_are_stamped_at_midnight(client, db):
    day = TODAY - dt.timedelta(days=4)
    post_import(client, {"weights": [{"date": day.isoformat(), "kg": 90}]})
    assert db.query(WeightEntry).one().created_at == dt.datetime.combine(day, dt.time(0, 0))
    # a real weigh-in later the same day supersedes the imported one
    _, r = put_weight(client, 89.0, dt.datetime.combine(day, dt.time(7, 5)), day=day)
    assert r.status_code == 201
    assert [w.kg for w in db.query(WeightEntry)] == [89.0]


def test_moving_a_weigh_in_onto_a_day_with_a_later_one_wins(client, db):
    day_a, day_b = TODAY - dt.timedelta(days=6), TODAY - dt.timedelta(days=5)
    a, _ = put_weight(client, 88.0, dt.datetime.combine(day_a, dt.time(8, 0)), day=day_a)
    b, _ = put_weight(client, 87.0, dt.datetime.combine(day_b, dt.time(20, 0)), day=day_b)
    _, r = put_weight(client, 88.2, dt.datetime.combine(day_a, dt.time(8, 0)), cid=a, day=day_b)
    assert r.status_code == 200 and r.json()["id"] != 0
    rows = db.query(WeightEntry).all()
    assert [(w.client_id, w.date, w.kg) for w in rows] == [(a, day_b, 88.2)]
    from app.models import WeightTombstone
    assert db.get(WeightTombstone, b) is not None
    late_b = client.put(f"/weights/{b}", json={"date": day_b.isoformat(), "kg": 87.0,
                                               "created_at": NOW.isoformat()})
    assert late_b.json() == {"id": 0, "created": False}


def test_brand_new_older_weigh_in_arriving_late_is_still_dropped(client, db):
    day = TODAY - dt.timedelta(days=7)
    kept, _ = put_weight(client, 87.0, dt.datetime.combine(day, dt.time(20, 0)), day=day)
    _, r = put_weight(client, 88.0, dt.datetime.combine(day, dt.time(7, 0)), day=day)
    assert r.json() == {"id": 0, "created": False}
    assert [w.client_id for w in db.query(WeightEntry)] == [kept]
