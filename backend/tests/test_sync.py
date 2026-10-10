import datetime as dt
import sqlite3
import uuid

import pytest
from sqlalchemy import create_engine, inspect, text

from app.migrations import add_client_id_columns
from app.models import Checkin, Workout, WorkoutExercise, WorkoutSet

NOW = dt.datetime.now().replace(microsecond=0)


def workout_doc(session_id="BA", sets=None, **overrides):
    doc = {
        "session_id": session_id,
        "name": "Bas A",
        "focus": "Force",
        "format": "express",
        "date": NOW.date().isoformat(),
        "started_at": (NOW - dt.timedelta(minutes=40)).isoformat(),
        "finished_at": NOW.isoformat(),
        "exercises": [
            {"slug": "squat", "target_reps": "5-6",
             "sets": sets or [{"weight": 80, "reps": 6, "done": True},
                              {"weight": 82.5, "reps": 5, "done": True}]},
        ],
    }
    doc.update(overrides)
    return doc


def put_workout(client, doc, client_id=None):
    return client.put(f"/workouts/{client_id or uuid.uuid4()}", json=doc)


def checkin_doc(**overrides):
    doc = {"energy": 5, "sleep": 5, "motivation": 5, "soreness": 1, "minutes": 60,
           "date": NOW.date().isoformat(), "created_at": NOW.isoformat()}
    doc.update(overrides)
    return doc


# --- PUT /workouts/{client_id} ---

def test_put_workout_creates_it(client, db):
    cid = str(uuid.uuid4())
    response = put_workout(client, workout_doc(), cid)
    assert response.status_code == 201
    assert response.json()["created"] is True

    row = db.query(Workout).one()
    assert row.client_id == cid
    assert row.finished_at is not None
    assert [(s.weight, s.reps, s.done) for s in row.exercises[0].sets] == [(80, 6, True), (82.5, 5, True)]


def test_put_workout_twice_is_idempotent_and_replaces_values(client, db):
    cid = str(uuid.uuid4())
    first = put_workout(client, workout_doc(), cid)
    changed = workout_doc(sets=[{"weight": 90, "reps": 4, "done": True}], name="Bas A bis")
    second = put_workout(client, changed, cid)

    assert second.status_code == 200
    assert second.json() == {"id": first.json()["id"], "created": False}
    assert db.query(Workout).count() == 1
    assert db.query(WorkoutExercise).count() == 1
    assert db.query(WorkoutSet).count() == 1  # old sets are gone, not duplicated
    row = db.query(Workout).one()
    assert row.name == "Bas A bis"
    assert row.exercises[0].sets[0].weight == 90

    # sending the exact same thing again changes nothing
    put_workout(client, changed, cid)
    assert db.query(Workout).count() == 1
    assert db.query(WorkoutSet).count() == 1


def test_two_different_ids_make_two_workouts(client, db):
    put_workout(client, workout_doc())
    put_workout(client, workout_doc())
    assert db.query(Workout).count() == 2


@pytest.mark.parametrize("bad_set", [
    {"weight": 501, "reps": 5, "done": True},
    {"weight": -1, "reps": 5, "done": True},
    {"weight": 80, "reps": 101, "done": True},
    {"weight": 80, "reps": -1, "done": True},
])
def test_put_workout_rejects_out_of_range_sets(client, bad_set):
    assert put_workout(client, workout_doc(sets=[bad_set])).status_code == 422


def test_put_workout_rejects_unknown_exercise(client, db):
    doc = workout_doc()
    doc["exercises"][0]["slug"] = "does-not-exist"
    assert put_workout(client, doc).status_code == 422
    assert db.query(Workout).count() == 0


def test_put_workout_rejects_too_many_sets_and_exercises(client):
    too_many_sets = [{"weight": 10, "reps": 5, "done": True}] * 11
    assert put_workout(client, workout_doc(sets=too_many_sets)).status_code == 422
    exercise = {"slug": "squat", "target_reps": "5", "sets": []}
    assert put_workout(client, workout_doc(exercises=[exercise] * 21)).status_code == 422


def test_put_workout_rejects_bad_id_and_missing_finished_at(client):
    assert client.put("/workouts/not-a-uuid", json=workout_doc()).status_code == 422
    doc = workout_doc()
    del doc["finished_at"]
    assert put_workout(client, doc).status_code == 422


def test_put_workout_accepts_a_deleted_template(client):
    assert put_workout(client, workout_doc(session_id="OLD")).status_code == 201


def test_synced_workout_counts_in_snapshot(client):
    put_workout(client, workout_doc("BA"))
    snap = client.get("/snapshot").json()
    assert snap["week_count"] == 1
    assert snap["next_session_id"] == "HB"       # after BA in HA, BA, HB, BB
    assert snap["last_weights"] == {"squat": 82.5}
    assert client.get("/sessions/next").json() == {"id": "HB"}


def test_unfinished_sets_do_not_feed_last_weights(client):
    sets = [{"weight": 80, "reps": 6, "done": True}, {"weight": 100, "reps": None, "done": False}]
    put_workout(client, workout_doc(sets=sets))
    assert client.get("/snapshot").json()["last_weights"] == {"squat": 80}


# --- PUT /checkins/{client_id} ---

def test_put_checkin_creates_and_shows_in_snapshot(client):
    response = client.put(f"/checkins/{uuid.uuid4()}", json=checkin_doc())
    assert response.status_code == 201
    snap = client.get("/snapshot").json()
    assert snap["checkin_today"] == {"score": 20, "level": "vert", "format": "complete"}
    assert client.get("/checkin/today").json()["level"] == "vert"


def test_put_checkin_recomputes_result_server_side(client):
    # the phone tries to forge the result: extra fields are ignored
    forged = checkin_doc(energy=1, sleep=1, motivation=1, soreness=5, level="vert",
                         format="complete", score=20)
    client.put(f"/checkins/{uuid.uuid4()}", json=forged)
    assert client.get("/checkin/today").json() == {"score": 4, "level": "rouge", "format": "express"}


def test_put_checkin_is_idempotent(client, db):
    cid = str(uuid.uuid4())
    client.put(f"/checkins/{cid}", json=checkin_doc())
    second = client.put(f"/checkins/{cid}", json=checkin_doc(energy=1, minutes=30))
    assert second.status_code == 200
    assert db.query(Checkin).count() == 1
    assert db.query(Checkin).one().format == "express"


def test_put_checkin_validation(client):
    cid = uuid.uuid4()
    assert client.put(f"/checkins/{cid}", json=checkin_doc(energy=2)).status_code == 422
    assert client.put(f"/checkins/{cid}", json=checkin_doc(minutes=5)).status_code == 422
    assert client.put("/checkins/nope", json=checkin_doc()).status_code == 422


def test_late_synced_older_checkin_does_not_override_newer_one(client):
    newer = checkin_doc(energy=1, sleep=1, motivation=1, soreness=5, created_at=NOW.isoformat())
    older = checkin_doc(created_at=(NOW - dt.timedelta(hours=2)).isoformat())
    client.put(f"/checkins/{uuid.uuid4()}", json=newer)
    client.put(f"/checkins/{uuid.uuid4()}", json=older)  # arrives later, but was made earlier
    assert client.get("/checkin/today").json()["level"] == "rouge"


# --- migration of an old salle.db ---

OLD_SCHEMA = [
    """CREATE TABLE workout (id INTEGER PRIMARY KEY, session_id VARCHAR(20) NOT NULL,
       name VARCHAR(80) NOT NULL, focus VARCHAR(20) NOT NULL, format VARCHAR(10) NOT NULL,
       date DATE NOT NULL, started_at DATETIME NOT NULL, finished_at DATETIME)""",
    """CREATE TABLE checkin (id INTEGER PRIMARY KEY, date DATE NOT NULL, created_at DATETIME NOT NULL,
       energy INTEGER NOT NULL, sleep INTEGER NOT NULL, motivation INTEGER NOT NULL,
       soreness INTEGER NOT NULL, minutes INTEGER NOT NULL, score INTEGER NOT NULL,
       level VARCHAR(10) NOT NULL, format VARCHAR(10) NOT NULL)""",
]


def test_migration_adds_client_id_to_an_old_database(tmp_path):
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    for statement in OLD_SCHEMA:
        conn.execute(statement)
    conn.execute("INSERT INTO workout (session_id, name, focus, format, date, started_at) "
                 "VALUES ('HA', 'Haut A', 'Force', 'complete', '2026-09-01', '2026-09-01 10:00:00')")
    conn.commit()
    conn.close()

    engine = create_engine(f"sqlite:///{path}")
    add_client_id_columns(engine)
    add_client_id_columns(engine)  # running it again is harmless

    inspector = inspect(engine)
    for table in ("workout", "checkin"):
        assert "client_id" in [c["name"] for c in inspector.get_columns(table)]
        index = [i for i in inspector.get_indexes(table) if i["column_names"] == ["client_id"]]
        assert index and index[0]["unique"]
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM workout")).scalar() == 1  # data kept
    engine.dispose()


def test_migration_skips_missing_tables(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'empty.db'}")
    add_client_id_columns(engine)  # no tables yet: must not crash
    engine.dispose()


# --- review fixes ---

def test_put_rejects_timezone_aware_datetimes(client):
    assert put_workout(client, workout_doc(finished_at="2026-10-10T12:00:00Z")).status_code == 422
    assert client.put(f"/checkins/{uuid.uuid4()}",
                      json=checkin_doc(created_at="2026-10-10T12:00:00+02:00")).status_code == 422


def test_put_workout_clamps_finish_before_start(client, db):
    doc = workout_doc(started_at=NOW.isoformat(), finished_at=(NOW - dt.timedelta(minutes=1)).isoformat())
    assert put_workout(client, doc).status_code == 201
    assert db.query(Workout).one().finished_at == NOW


def test_client_id_must_be_a_real_uuid_and_is_normalized(client, db):
    assert client.put("/workouts/" + "z" * 36, json=workout_doc()).status_code == 422
    cid = uuid.uuid4()
    put_workout(client, workout_doc(), str(cid).upper())
    again = put_workout(client, workout_doc(name="Autre"), str(cid))  # same id, other casing
    assert again.status_code == 200
    assert db.query(Workout).count() == 1
    assert db.query(Workout).one().client_id == str(cid)


def test_put_workout_survives_a_concurrent_insert_of_the_same_id(client, db, monkeypatch):
    cid = str(uuid.uuid4())
    real_commit = db.commit
    state = {"first": True}

    def racing_commit():
        if state["first"]:
            state["first"] = False
            # another request "wins": its row for this client_id lands before ours
            with db.no_autoflush:  # our own pending row must not be flushed first
                db.execute(text(
                    "INSERT INTO workout (session_id, name, focus, format, date, started_at, finished_at, client_id) "
                    "VALUES ('HA', 'x', 'Force', 'complete', '2026-10-10', '2026-10-10 10:00:00', "
                    "'2026-10-10 11:00:00', :cid)"), {"cid": cid})
                db.connection().connection.commit()  # the other request's row is really saved
        real_commit()

    monkeypatch.setattr(db, "commit", racing_commit)
    response = put_workout(client, workout_doc(name="Mine"), cid)
    assert response.status_code == 200 and response.json()["created"] is False
    assert db.query(Workout).count() == 1
    assert db.query(Workout).one().name == "Mine"


def test_unknown_host_header_is_refused():
    from fastapi.testclient import TestClient
    from app.main import app
    assert TestClient(app, base_url="http://evil.example").get("/health").status_code == 400
    ok = TestClient(app, base_url="http://desktop-m7c8gnc.tail696942.ts.net").get("/health")
    assert ok.status_code == 200
