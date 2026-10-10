import copy
import datetime as dt
import json
import uuid

import pytest
from sqlalchemy import select

from app.models import (
    Exercise, ProteinEntry, SessionTemplate, WeightEntry, Workout, WorkoutSet,
)

NOW = dt.datetime.now().replace(microsecond=0)

# A small prototype export following SPEC §3.1
PROTOTYPE = {
    "program": {
        "HA": {"name": "Haut A", "focus": "Force", "ex": [
            {"n": "Développé couché barre", "s": 4, "r": "5-6"},
            {"n": "Pec deck (butterfly)", "s": 3, "r": "10-12"},
        ]},
        "XY": {"name": "Perso", "focus": "Libre", "ex": [{"n": "Ma machine", "s": 2, "r": "12"}]},
    },
    "order": ["XY", "HA"],
    "goals": {"sessions": 5, "protein": 170, "targetWeight": 78.5, "objective": "muscle", "coach": False},
    "plan": [{"j": "Lundi", "m": "Midi"}],
    "checkin": {"date": "2026-09-24", "res": {"sc": 14, "lvl": "orange", "fmt": "minimale"}, "session": "HA"},
    "draft": None,
    "logs": [
        {"session": "HA", "name": "Haut A", "focus": "Force", "fmt": "complete", "date": "2026-09-20",
         "startedAt": 1758360000000,
         "ex": [{"n": "Développé couché barre", "r": "5-6",
                 "sets": [{"w": "80", "r": "6"}, {"w": "82,5", "r": "5"}, {"w": "", "r": ""}]},
                {"n": "Pec deck (butterfly)", "r": "10-12", "sets": [{"w": "45", "r": "12", "done": False}]}]},
        {"session": "HA", "name": "Haut A", "focus": "Force", "fmt": "express", "date": "2026-09-22",
         "ex": [{"n": "squat", "r": "5-6", "sets": [{"w": 100, "r": 5}]}]},
    ],
    "weights": [{"date": "2026-09-20", "kg": 89.0}, {"date": "2026-09-24", "kg": 88.4}],
    "protein": {"2026-09-24": [["Blanc de poulet 150 g", 35], ["Skyr", 15]], "2026-09-25": [["Whey", 24]]},
    "custom": {"Ma machine": {"g": "Dos", "m": ["Dos"], "s": [], "c": [], "iso": 1, "lower": 0,
                               "eq": "Machine", "a": []}},
    "chat": {"training": [{"role": "user", "content": "salut"}], "food": []},
}


def post(client, data=None, **params):
    body = json.dumps(PROTOTYPE if data is None else data)
    return client.post("/import/prototype", content=body, params=params,
                       headers={"content-type": "application/json"})


# --- import ---

def test_import_maps_everything(client, db):
    r = post(client)
    assert r.status_code == 200, r.text
    out = r.json()
    assert (out["sessions_created"], out["sessions_updated"]) == (1, 1)  # XY new, HA replaced
    assert out["goals_updated"] is True
    assert sorted(out["exercises_created"]) == ["Ma machine", "Pec deck (butterfly)"]
    assert (out["workouts_created"], out["weights_created"], out["protein_created"]) == (2, 2, 3)

    # program + rotation: imported order first, untouched sessions after
    rotation = [t.id for t in db.query(SessionTemplate).order_by(SessionTemplate.position)]
    assert rotation == ["XY", "HA", "BA", "HB", "BB"]
    ha = db.get(SessionTemplate, "HA")
    assert [(e.exercise_slug, e.sets, e.reps) for e in ha.exercises] == [
        ("developpe-couche-barre", 4, "5-6"), ("pec-deck-butterfly", 3, "10-12")]

    # unknown names became custom exercises; "Ma machine" took its data from `custom`
    machine = db.get(Exercise, "ma-machine")
    assert (machine.is_custom, machine.muscle_group, machine.equipment, machine.is_isolation) == (
        True, "Dos", "Machine", True)
    assert db.get(Exercise, "pec-deck-butterfly").is_custom

    # goals
    profile = client.get("/profile").json()
    assert (profile["sessions_goal"], profile["protein_goal_g"], profile["target_weight_kg"],
            profile["objective"], profile["coach_enabled"]) == (5, 170, 78.5, "muscle", False)

    # workouts: set values parsed ("82,5"), blank set is empty, exercise matched case-insensitively
    first = db.query(Workout).filter(Workout.date == dt.date(2026, 9, 20)).one()
    assert first.client_id and first.focus == "Force" and first.format == "complete"
    sets = [(s.weight, s.reps, s.done) for s in first.exercises[0].sets]
    assert sets == [(80, 6, True), (82.5, 5, True), (None, None, True)]
    assert first.exercises[1].sets[0].done is False
    assert first.finished_at is not None and first.finished_at >= first.started_at
    second = db.query(Workout).filter(Workout.date == dt.date(2026, 9, 22)).one()
    assert second.exercises[0].exercise_slug == "squat" and second.format == "express"

    assert sorted((w.date.isoformat(), w.kg) for w in db.query(WeightEntry)) == [
        ("2026-09-20", 89.0), ("2026-09-24", 88.4)]
    protein = db.query(ProteinEntry).order_by(ProteinEntry.created_at).all()
    assert [(p.date.isoformat(), p.label, p.grams) for p in protein] == [
        ("2026-09-24", "Blanc de poulet 150 g", 35), ("2026-09-24", "Skyr", 15), ("2026-09-25", "Whey", 24)]


def test_import_twice_does_not_duplicate(client, db):
    post(client)
    counts = lambda: (db.query(Workout).count(), db.query(WorkoutSet).count(), db.query(WeightEntry).count(),
                      db.query(ProteinEntry).count(), db.query(Exercise).count(),
                      db.query(SessionTemplate).count())
    before = counts()
    out = post(client).json()
    assert counts() == before
    assert (out["workouts_created"], out["workouts_updated"]) == (0, 2)
    assert (out["weights_created"], out["protein_created"], out["sessions_created"]) == (0, 0, 0)
    assert out["exercises_created"] == []


def test_identical_logs_in_one_file_stay_two_workouts_and_reimport_is_stable(client, db):
    data = copy.deepcopy(PROTOTYPE)
    data["logs"] = [data["logs"][1], data["logs"][1]]
    post(client, data)
    post(client, data)
    assert db.query(Workout).count() == 2


def test_import_does_not_bring_back_deleted_lines(client, db):
    post(client)
    row = db.query(WeightEntry).filter(WeightEntry.kg == 88.4).one()
    assert client.delete(f"/weights/{row.client_id}").status_code == 204
    protein = db.query(ProteinEntry).filter(ProteinEntry.label == "Skyr").one()
    assert client.delete(f"/protein/{protein.client_id}").status_code == 204
    out = post(client).json()
    assert out["skipped_deleted"] == 2
    assert db.query(WeightEntry).count() == 1 and db.query(ProteinEntry).count() == 2


def test_import_flags_keep_program_and_goals(client, db):
    out = post(client, program="false", goals="false").json()
    assert out["goals_updated"] is False and out["sessions_created"] == 0
    assert db.get(SessionTemplate, "XY") is None
    assert client.get("/profile").json()["sessions_goal"] == 4
    assert db.query(Workout).count() == 2  # history is still imported (unknown exercises -> custom)


def test_imported_history_feeds_the_progress_screen(client):
    post(client)
    p = client.get("/snapshot").json()["progress"]
    assert p["total_workouts"] == 2
    assert {r["slug"]: r["weight"] for r in p["records"]} == {
        "developpe-couche-barre": 82.5, "squat": 100}  # the undone butterfly set is no record


def test_minimal_file_is_fine(client):
    r = post(client, {})
    assert r.status_code == 200 and r.json()["workouts_created"] == 0


@pytest.mark.parametrize("mutate", [
    lambda d: d["goals"].update(sessions=9),
    lambda d: d["goals"].update(protein=10),
    lambda d: d["goals"].update(targetWeight=500),
    lambda d: d["goals"].update(objective="bulk"),
    lambda d: d["program"]["HA"].update(focus="Cardio"),
    lambda d: d["program"]["HA"]["ex"][0].update(s=11),
    lambda d: d["program"]["HA"]["ex"][0].update(n=""),
    lambda d: d["program"]["HA"]["ex"][0].update(r="x" * 21),
    lambda d: d["program"].update({"id with spaces!": d["program"]["HA"]}),
    lambda d: d["logs"][0]["ex"][0]["sets"][0].update(w="lourd"),
    lambda d: d["logs"][0]["ex"][0]["sets"][0].update(w="900"),
    lambda d: d["logs"][0]["ex"][0]["sets"][0].update(r="101"),
    lambda d: d["logs"][0].update(date="hier"),
    lambda d: d["logs"][0].pop("ex"),
    lambda d: d["weights"][0].update(kg=20),
    lambda d: d["weights"][0].pop("date"),
    lambda d: d["protein"].update({"2026-09-26": [["Trop", 999]]}),
    lambda d: d["protein"].update({"pas une date": [["Poulet", 30]]}),
    lambda d: d.update(program=[]),
])
def test_strict_validation_is_422_and_changes_nothing(client, db, mutate):
    data = copy.deepcopy(PROTOTYPE)
    mutate(data)
    r = post(client, data)
    assert r.status_code == 422
    assert db.query(Workout).count() == 0 and db.query(WeightEntry).count() == 0
    assert db.query(Exercise).count() == 22 and db.query(SessionTemplate).count() == 4
    assert client.get("/profile").json()["sessions_goal"] == 4


@pytest.mark.parametrize("content", [b"pas du json", b"[1, 2]", b'"texte"', b""])
def test_non_object_or_invalid_json_is_422(client, content):
    r = client.post("/import/prototype", content=content, headers={"content-type": "application/json"})
    assert r.status_code == 422


def test_import_size_cap(client):
    huge = b'{"chat": {"x": "' + b"a" * (5 * 1024 * 1024) + b'"}}'
    r = client.post("/import/prototype", content=huge, headers={"content-type": "application/json"})
    assert r.status_code == 413
    assert r.json()["detail"] == "Fichier trop gros (5 Mo maximum)"


def test_protein_label_is_cut_to_80_chars(client, db):
    data = {"protein": {"2026-09-24": [["x" * 200, 30], ["   ", 10]]}}
    assert post(client, data).status_code == 200
    labels = sorted(p.label for p in db.query(ProteinEntry))
    assert labels == ["Sans nom", "x" * 80]


# --- export ---

def test_export_contains_everything_and_is_a_download(client, db):
    post(client)
    client.put(f"/checkins/{uuid.uuid4()}", json={
        "energy": 5, "sleep": 5, "motivation": 5, "soreness": 1, "minutes": 60,
        "date": NOW.date().isoformat(), "created_at": NOW.isoformat()})
    r = client.get("/export")
    assert r.status_code == 200
    disposition = r.headers["content-disposition"]
    assert disposition.startswith("attachment; filename=") and disposition.endswith('.json"')
    data = r.json()
    assert set(data) == {"app", "version", "exported_at", "profile", "exercises", "program",
                         "workouts", "checkins", "protein", "weights"}
    assert data["profile"]["objective"] == "muscle" and data["profile"]["protein_goal_g"] == 170
    assert data["program"]["rotation"] == ["XY", "HA", "BA", "HB", "BB"]
    ha = next(s for s in data["program"]["sessions"] if s["id"] == "HA")
    assert ha["exercises"][0] == {"slug": "developpe-couche-barre", "sets": 4, "reps": "5-6"}
    assert len(data["workouts"]) == 2
    w = next(w for w in data["workouts"] if w["date"] == "2026-09-20")
    assert w["exercises"][0]["sets"][1] == {"weight": 82.5, "reps": 5, "done": True}
    assert w["finished_at"] is not None
    assert len(data["checkins"]) == 1 and data["checkins"][0]["level"] == "vert"
    assert len(data["protein"]) == 3 and len(data["weights"]) == 2
    assert any(e["slug"] == "ma-machine" and e["is_custom"] for e in data["exercises"])


def test_export_has_no_secrets(client):
    text = client.get("/export").text.lower()
    for word in ("password", "token", "secret", "api_key"):
        assert word not in text


def test_export_on_a_fresh_install(client):
    data = client.get("/export").json()
    assert data["workouts"] == [] and data["weights"] == [] and len(data["program"]["sessions"]) == 4


# --- Interrupted sessions (a later log of the same day contains the earlier one) ---

def _log(started, exercises, session="s1789550124005", date="2026-09-27"):
    return {"session": session, "name": "Haut", "focus": "Force", "fmt": "complete", "date": date,
            "startedAt": started, "ex": exercises}


def _sets(*pairs):
    return [{"w": str(w), "r": str(r)} for w, r in pairs]


BENCH = [(40, 12), (60, 10), (60, 10), (70, 4)]


def test_interrupted_duplicate_is_skipped(client, db):
    early = _log(1790492257973, [{"n": "Développé couché barre", "r": "", "sets": _sets(*BENCH)},
                                 {"n": "Développé incliné haltères", "r": "", "sets": _sets((14, 12))}])
    late = _log(1790493330790, [{"n": "Développé couché barre", "r": "", "sets": _sets(*BENCH)},
                                {"n": "Développé incliné haltères", "r": "", "sets": _sets((14, 12), (18, 10), (20, 10))},
                                {"n": "Pec deck (butterfly)", "r": "", "sets": _sets((4.5, 12))}])
    body = {"logs": [late, early]}  # file order does not matter
    preview = post(client, body, dry_run="true").json()
    assert (preview["skipped_partial"], preview["workouts_created"]) == (1, 1)
    out = post(client, body).json()
    assert (out["skipped_partial"], out["workouts_created"]) == (1, 1)
    workouts = db.scalars(select(Workout)).all()
    assert len(workouts) == 1 and len(workouts[0].exercises) == 3
    assert post(client, body).json()["workouts_updated"] == 1  # idempotent


def test_different_logs_the_same_day_are_both_kept(client, db):
    a = _log(1790492257973, [{"n": "Squat", "r": "", "sets": _sets((100, 5), (100, 5))}])
    b = _log(1790493330790, [{"n": "Squat", "r": "", "sets": _sets((100, 5), (105, 5), (110, 3))}])  # 2nd set differs
    c = _log(1790493330999, [{"n": "Squat", "r": "", "sets": _sets((100, 5))},
                             {"n": "Rowing barre", "r": "", "sets": _sets((60, 8))}], session="other")
    d = _log(1790494000000, [{"n": "Rowing barre", "r": "", "sets": _sets((60, 8))}])  # exercise missing in a
    out = post(client, {"logs": [a, b, c, d]}).json()
    assert (out["skipped_partial"], out["workouts_created"]) == (0, 4)
    assert post(client, {"logs": [a]}, dry_run="true").json()["skipped_partial"] == 0


def test_prototype_names_land_on_real_exercises(db):
    from app.prototype_import import Library
    lib = Library(db)
    for name, slug in {"Curl incliné": "curl-incline", "Tirage horizontal poulie": "tirage-horizontal"}.items():
        assert lib.find(name) == slug, name


def test_wrist_curls_and_elliptical_resolve_to_the_right_library_exercises(db, tmp_path):
    import json as _json
    from pathlib import Path
    from app import library
    from app.prototype_import import Library
    real = Path(__file__).resolve().parents[2] / "vendor" / "exercises-dataset"
    if not (real / "data" / "exercises.json").is_file():
        pytest.skip("vendor dataset not present")
    library.import_library(db, real, names_fr=library.load_french_names())
    lib = Library(db)
    assert lib.find("Curl poignets") == "dumbbell-over-bench-wrist-curl"
    assert lib.find("Curl poignets inversé") == "dumbbell-over-bench-revers-wrist-curl"
    assert lib.find("Elliptique") == "walk-elliptical-cross-trainer"


def test_same_day_logs_far_apart_are_both_kept(client, db):
    h = 3600 * 1000
    cardio = [{"n": "Elliptique", "r": "", "sets": _sets((0, 20))}]
    morning = _log(1790460000000, cardio)
    evening = _log(1790460000000 + 10 * h, cardio)
    assert post(client, {"logs": [morning, evening]}).json()["skipped_partial"] == 0
    evening_more = _log(1790460000000 + 10 * h, cardio + [{"n": "Crunch", "r": "", "sets": _sets((0, 20))}])
    out = post(client, {"logs": [morning, evening_more]}).json()
    assert (out["skipped_partial"], out["workouts_created"] + out["workouts_updated"]) == (0, 2)
    # restarted within 3 h but long after the first one ended: also two sessions
    ended = _log(1790460000000, cardio)
    ended["finishedAt"] = 1790460000000 + 1 * h
    later = _log(1790460000000 + 2 * h, cardio + [{"n": "Crunch", "r": "", "sets": _sets((0, 20))}])
    assert post(client, {"logs": [ended, later]}).json()["skipped_partial"] == 0
    # no startedAt: nothing is guessed
    a, b = _log(None, cardio), _log(None, cardio + [{"n": "Crunch", "r": "", "sets": _sets((0, 20))}])
    assert post(client, {"logs": [a, b]}).json()["skipped_partial"] == 0
