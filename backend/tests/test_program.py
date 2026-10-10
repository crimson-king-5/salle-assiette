import datetime as dt
import uuid

import pytest

from app.models import Exercise, SessionTemplate, Workout

NOW = dt.datetime.now().replace(microsecond=0)


def line(slug="squat", sets=3, reps="8-12"):
    return {"slug": slug, "sets": sets, "reps": reps}


def create(client, name="Ma séance", focus="Libre", **extra):
    return client.post("/sessions", json={"name": name, "focus": focus, **extra})


def rotation(client):
    return [s["id"] for s in client.get("/sessions").json()]


def finished_workout(client, session_id="BA", name="Bas A"):
    doc = {"session_id": session_id, "name": name, "focus": "Force", "format": "complete",
           "date": NOW.date().isoformat(), "started_at": (NOW - dt.timedelta(minutes=50)).isoformat(),
           "finished_at": NOW.isoformat(),
           "exercises": [{"slug": "squat", "target_reps": "5-6",
                          "sets": [{"weight": 100, "reps": 5, "done": True}]}]}
    assert client.put(f"/workouts/{uuid.uuid4()}", json=doc).status_code == 201


# --- exercises: search ---

def test_search_lists_everything_sorted_by_name(client):
    names = [e["name"] for e in client.get("/exercises").json()]
    assert len(names) == 22


def test_search_is_accent_insensitive_on_name_group_equipment(client):
    by_name = {e["slug"] for e in client.get("/exercises", params={"q": "developpe"}).json()}
    assert "developpe-militaire" in by_name and "developpe-couche-barre" in by_name
    epaules = {e["slug"] for e in client.get("/exercises", params={"q": "EPAULES"}).json()}
    assert {"developpe-militaire", "elevations-laterales", "face-pull"} <= epaules
    machines = client.get("/exercises", params={"q": "machine jambes"}).json()
    assert {e["slug"] for e in machines} >= {"presse-cuisses", "leg-curl"}
    assert all(e["equipment"] == "Machine" for e in machines)


def test_search_matches_aliases(client):
    found = client.get("/exercises", params={"q": "lat pulldown"}).json()
    assert [e["slug"] for e in found] == ["tirage-vertical"]
    assert "poulie haute" in found[0]["aliases"]


def test_search_filters(client):
    dos = client.get("/exercises", params={"muscle": "dos"}).json()
    assert {e["slug"] for e in dos} == {"rowing-barre", "tirage-vertical", "tirage-horizontal"}
    poulie = client.get("/exercises", params={"muscle": "Dos", "equipment": "poulie"}).json()
    assert {e["slug"] for e in poulie} == {"tirage-vertical", "tirage-horizontal"}
    assert client.get("/exercises", params={"q": "zzz"}).json() == []
    assert client.get("/exercises", params={"muscle": "Perso"}).json() == []


# --- exercises: create ---

def test_create_custom_exercise(client, db):
    r = client.post("/exercises", json={"name": "Ma machine à dos", "muscle_group": "Dos",
                                        "equipment": "Machine", "is_isolation": True})
    assert r.status_code == 201
    body = r.json()
    assert body["slug"] == "ma-machine-a-dos" and body["is_custom"] is True
    assert (body["is_isolation"], body["is_lower"], body["is_cardio"]) == (True, False, False)
    row = db.get(Exercise, "ma-machine-a-dos")
    assert row.is_custom and row.owner_id is None
    perso = client.get("/exercises", params={"muscle": "Perso"}).json()
    assert [e["slug"] for e in perso] == ["ma-machine-a-dos"]
    # it can be used in a session straight away
    sid = create(client).json()["id"]
    assert client.put(f"/sessions/{sid}", json={"name": "X", "focus": "Libre",
                                                "exercises": [line("ma-machine-a-dos")]}).status_code == 200


def test_custom_exercise_slug_is_unique_and_name_conflict_is_409(client):
    body = {"name": "Squat", "muscle_group": "Jambes", "equipment": "Poids libre"}
    assert client.post("/exercises", json=body).status_code == 409           # same name as a library one
    assert client.post("/exercises", json={**body, "name": "squat "}).status_code == 409
    # "SDT roumain" is a new name, but its slug is the one of the library's "sdt-roumain"
    a = client.post("/exercises", json={**body, "name": "SDT roumain"}).json()
    assert a["slug"] == "sdt-roumain-2"
    assert client.post("/exercises", json={**body, "name": "sdt  Roumain"}).status_code == 409


def test_cardio_group_makes_a_cardio_exercise(client):
    r = client.post("/exercises", json={"name": "Vélo", "muscle_group": "Cardio",
                                        "equipment": "Machine", "cardio_unit": "niveau"})
    assert (r.json()["is_cardio"], r.json()["cardio_unit"]) == (True, "niveau")


@pytest.mark.parametrize("body", [
    {"name": "", "muscle_group": "Dos", "equipment": "Machine"},
    {"name": "   ", "muscle_group": "Dos", "equipment": "Machine"},
    {"name": "x" * 121, "muscle_group": "Dos", "equipment": "Machine"},
    {"name": "A", "muscle_group": "Nuque", "equipment": "Machine"},
    {"name": "A", "muscle_group": "Dos", "equipment": "Elastique"},
    {"muscle_group": "Dos", "equipment": "Machine"},
])
def test_create_exercise_validation(client, body):
    assert client.post("/exercises", json=body).status_code == 422


# --- sessions: create ---

def test_create_empty_session_goes_to_the_end_of_the_rotation(client):
    r = create(client, "Mobilité", "Libre")
    assert r.status_code == 201
    body = r.json()
    assert body["id"].startswith("S") and len(body["id"]) <= 20
    assert body["exercises"] == [] and body["name"] == "Mobilité"
    assert rotation(client) == ["HA", "BA", "HB", "BB", body["id"]]


def test_create_session_from_presets(client):
    for preset, first in [("pecs-epaules-triceps", "developpe-couche-barre"),
                          ("dos-biceps-avant-bras", "tirage-vertical"),
                          ("jambes-abdos", "squat"), ("full-body", "squat")]:
        r = create(client, preset, "Hypertrophie", template=preset)
        assert r.status_code == 201
        exercises = r.json()["exercises"]
        assert exercises and exercises[0]["slug"] == first
        assert all(1 <= e["sets"] <= 10 for e in exercises)


def test_cardio_preset_only_when_the_library_has_cardio(client):
    assert "cardio-gainage" not in [p["id"] for p in client.get("/session-templates").json()]
    assert create(client, template="cardio-gainage").status_code == 422
    client.post("/exercises", json={"name": "Tapis", "muscle_group": "Cardio", "equipment": "Machine"})
    presets = client.get("/session-templates").json()
    assert presets[0] == {"id": "vide", "label": "Séance vide"}
    assert {"id": "cardio-gainage", "label": "Cardio · gainage"} in presets
    r = create(client, "Cardio", template="cardio-gainage")
    assert r.status_code == 201 and r.json()["exercises"][0]["slug"] == "tapis"


@pytest.mark.parametrize("body", [
    {"name": "", "focus": "Libre"}, {"name": "A", "focus": "Cardio"}, {"name": "x" * 81, "focus": "Force"},
    {"name": "A", "focus": "Force", "template": "inconnu"}, {"focus": "Force"},
])
def test_create_session_validation(client, body):
    assert client.post("/sessions", json=body).status_code == 422


# --- sessions: update ---

def test_update_session_replaces_name_focus_and_ordered_exercises(client):
    body = {"name": "Haut A+", "focus": "Hypertrophie",
            "exercises": [line("face-pull", 3, "15-20"), line("squat", 5, "3-5")]}
    r = client.put("/sessions/HA", json=body)
    assert r.status_code == 200
    assert [(e["slug"], e["sets"], e["reps"]) for e in r.json()["exercises"]] == [
        ("face-pull", 3, "15-20"), ("squat", 5, "3-5")]
    again = client.get("/sessions/HA").json()
    assert again["name"] == "Haut A+" and again["focus"] == "Hypertrophie"
    assert [e["slug"] for e in again["exercises"]] == ["face-pull", "squat"]
    assert client.get("/sessions/HA", params={"format": "express"}).json()["exercises"][1]["sets"] == 3


def test_update_session_can_empty_it_and_allows_20_exercises(client):
    assert client.put("/sessions/HA", json={"name": "A", "focus": "Libre", "exercises": []}).status_code == 200
    twenty = [line("squat")] * 20
    assert client.put("/sessions/HA", json={"name": "A", "focus": "Libre", "exercises": twenty}).status_code == 200


@pytest.mark.parametrize("exercises", [
    [line("squat")] * 21, [line("inconnu")], [line(sets=0)], [line(sets=11)], [line(reps="")],
    [line(reps="x" * 21)], [{"slug": "squat", "sets": 3}],
])
def test_update_session_validation(client, exercises):
    r = client.put("/sessions/HA", json={"name": "A", "focus": "Libre", "exercises": exercises})
    assert r.status_code == 422
    assert len(client.get("/sessions/HA").json()["exercises"]) == 6  # nothing changed


def test_update_unknown_session_is_404(client):
    assert client.put("/sessions/ZZ", json={"name": "A", "focus": "Libre", "exercises": []}).status_code == 404


def test_error_details_are_in_french(client):
    r = client.put("/sessions/HA", json={"name": "A", "focus": "Libre", "exercises": [line("inconnu")]})
    assert r.json()["detail"] == "Exercice inconnu : inconnu"
    assert client.put("/sessions/ZZ", json={"name": "A", "focus": "Libre", "exercises": []}
                      ).json()["detail"] == "Séance inconnue"


# --- sessions: delete / history ---

def test_history_survives_rename_and_delete_of_its_template(client, db):
    finished_workout(client, "BA", "Bas A")
    client.put("/sessions/BA", json={"name": "Jambes", "focus": "Libre", "exercises": [line("squat")]})
    assert client.delete("/sessions/BA").status_code == 204
    workout = db.query(Workout).one()
    assert (workout.session_id, workout.name) == ("BA", "Bas A")
    assert workout.exercises[0].exercise_slug == "squat"
    snap = client.get("/snapshot").json()
    assert snap["progress"]["total_workouts"] == 1
    assert snap["progress"]["recent_workouts"][0]["name"] == "Bas A"
    assert snap["next_session_id"] == "HA"  # its template is gone: the rotation starts over


def test_delete_session_reindexes_the_rotation(client, db):
    assert client.delete("/sessions/BA").status_code == 204
    assert rotation(client) == ["HA", "HB", "BB"]
    assert [t.position for t in db.query(SessionTemplate).order_by(SessionTemplate.position)] == [0, 1, 2]
    assert client.get("/sessions/BA").status_code == 404


def test_delete_unknown_is_404_and_last_session_is_409(client):
    assert client.delete("/sessions/ZZ").status_code == 404
    for sid in ["BA", "HB", "BB"]:
        assert client.delete(f"/sessions/{sid}").status_code == 204
    r = client.delete("/sessions/HA")
    assert r.status_code == 409 and r.json()["detail"] == "Il faut garder au moins une séance"
    assert rotation(client) == ["HA"]


# --- rotation ---

def test_put_rotation_reorders(client):
    r = client.put("/rotation", json={"order": ["BB", "HA", "HB", "BA"]})
    assert r.status_code == 200
    assert [s["id"] for s in r.json()] == ["BB", "HA", "HB", "BA"]
    assert rotation(client) == ["BB", "HA", "HB", "BA"]
    assert client.get("/sessions/next").json() == {"id": "BB"}


@pytest.mark.parametrize("order", [
    ["HA", "BA", "HB"], ["HA", "BA", "HB", "BB", "ZZ"], ["HA", "HA", "BA", "HB", "BB"],
    ["HA", "BA", "HB", "ZZ"], [],
])
def test_put_rotation_must_be_a_permutation(client, order):
    assert client.put("/rotation", json={"order": order}).status_code == 422
    assert rotation(client) == ["HA", "BA", "HB", "BB"]
