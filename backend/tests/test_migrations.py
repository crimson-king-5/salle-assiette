"""init_database on a salle.db created before the Progrès / Réglages work (schema at commit 54676a6)."""

import sqlite3

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session

from app.database import get_db
from app.main import app, init_database
from app.models import Exercise, UserProfile

OLD_SCHEMA = """
CREATE TABLE exercise (slug VARCHAR(80) PRIMARY KEY, name_fr VARCHAR(120) NOT NULL,
    muscle_group VARCHAR(40) NOT NULL, equipment VARCHAR(40) NOT NULL,
    is_isolation BOOLEAN NOT NULL, is_lower BOOLEAN NOT NULL);
CREATE TABLE session_template (id VARCHAR(20) PRIMARY KEY, name VARCHAR(80) NOT NULL,
    focus VARCHAR(20) NOT NULL, position INTEGER NOT NULL);
CREATE TABLE session_exercise (id INTEGER PRIMARY KEY, session_id VARCHAR(20) NOT NULL,
    position INTEGER NOT NULL, exercise_slug VARCHAR(80) NOT NULL, sets INTEGER NOT NULL,
    reps VARCHAR(20) NOT NULL);
CREATE TABLE user_profile (id INTEGER PRIMARY KEY, objective VARCHAR(20) NOT NULL,
    sessions_goal INTEGER NOT NULL, protein_goal_g INTEGER NOT NULL, target_weight_kg FLOAT NOT NULL);
CREATE TABLE workout (id INTEGER PRIMARY KEY, session_id VARCHAR(20) NOT NULL, name VARCHAR(80) NOT NULL,
    focus VARCHAR(20) NOT NULL, format VARCHAR(10) NOT NULL, date DATE NOT NULL,
    started_at DATETIME NOT NULL, finished_at DATETIME, client_id VARCHAR(36));
CREATE UNIQUE INDEX ix_workout_client_id ON workout (client_id);
CREATE TABLE workout_exercise (id INTEGER PRIMARY KEY, workout_id INTEGER NOT NULL, position INTEGER NOT NULL,
    exercise_slug VARCHAR(80) NOT NULL, target_reps VARCHAR(20) NOT NULL);
CREATE TABLE workout_set (id INTEGER PRIMARY KEY, workout_exercise_id INTEGER NOT NULL,
    position INTEGER NOT NULL, weight FLOAT, reps INTEGER, done BOOLEAN NOT NULL);
CREATE TABLE checkin (id INTEGER PRIMARY KEY, date DATE NOT NULL, created_at DATETIME NOT NULL,
    client_id VARCHAR(36), energy INTEGER NOT NULL, sleep INTEGER NOT NULL, motivation INTEGER NOT NULL,
    soreness INTEGER NOT NULL, minutes INTEGER NOT NULL, score INTEGER NOT NULL, level VARCHAR(10) NOT NULL,
    format VARCHAR(10) NOT NULL);
CREATE UNIQUE INDEX ix_checkin_client_id ON checkin (client_id);
CREATE TABLE protein_log (id INTEGER PRIMARY KEY, client_id VARCHAR(36) NOT NULL, date DATE NOT NULL,
    label VARCHAR(80) NOT NULL, grams INTEGER NOT NULL, created_at DATETIME NOT NULL);
CREATE TABLE protein_tombstone (client_id VARCHAR(36) PRIMARY KEY, deleted_at DATETIME NOT NULL);
INSERT INTO exercise VALUES ('squat', 'Squat', 'Jambes', 'Poids libre', 0, 1);
INSERT INTO exercise VALUES ('tirage-vertical', 'Tirage vertical', 'Dos', 'Poulie', 0, 0);
INSERT INTO session_template VALUES ('HA', 'Haut A', 'Force', 0);
INSERT INTO session_exercise VALUES (1, 'HA', 0, 'squat', 4, '5-6');
INSERT INTO user_profile VALUES (1, 'muscle', 5, 170, 78.0);
INSERT INTO workout VALUES (1, 'HA', 'Haut A', 'Force', 'complete', '2026-09-01', '2026-09-01 10:00:00',
    '2026-09-01 11:00:00', NULL);
INSERT INTO workout_exercise VALUES (1, 1, 0, 'squat', '5-6');
INSERT INTO workout_set VALUES (1, 1, 0, 100.0, 5, 1);
INSERT INTO protein_log VALUES (1, '11111111-1111-1111-1111-111111111111', '2026-09-01', 'Skyr', 15,
    '2026-09-01 12:00:00');
"""


def make_old_db(path):
    conn = sqlite3.connect(path)
    conn.executescript(OLD_SCHEMA)
    conn.commit()
    conn.close()


def test_init_database_adds_the_new_columns_and_tables(tmp_path):
    path = tmp_path / "old.db"
    make_old_db(path)
    engine = create_engine(f"sqlite:///{path}")
    init_database(engine)
    init_database(engine)  # idempotent: every startup runs it

    inspector = inspect(engine)
    exercise_columns = {c["name"] for c in inspector.get_columns("exercise")}
    assert {"is_cardio", "cardio_unit", "aliases", "is_custom", "owner_id", "needs_review"} <= exercise_columns
    assert "coach_enabled" in {c["name"] for c in inspector.get_columns("user_profile")}
    assert {"weight_log", "weight_tombstone", "workout_tombstone"} <= set(inspector.get_table_names())
    weight_columns = {c["name"] for c in inspector.get_columns("weight_log")}
    assert {"client_id", "date", "kg", "created_at"} <= weight_columns

    with Session(engine) as db:
        squat = db.get(Exercise, "squat")
        assert (squat.is_cardio, squat.is_custom, squat.cardio_unit, squat.owner_id, squat.needs_review) == (
            False, False, None, None, False)
        assert "back squat" in squat.aliases          # seeded aliases are back-filled
        profile = db.get(UserProfile, 1)
        assert (profile.objective, profile.sessions_goal, profile.protein_goal_g) == ("muscle", 5, 170)
        assert profile.coach_enabled is True            # default for the existing row
    with engine.connect() as conn:                      # existing data is intact
        assert conn.execute(text("SELECT count(*) FROM workout_set")).scalar() == 1
        assert conn.execute(text("SELECT count(*) FROM protein_log")).scalar() == 1
        assert conn.execute(text("SELECT count(*) FROM weight_log")).scalar() == 0
    engine.dispose()


def test_a_migrated_database_serves_the_new_routes(tmp_path):
    path = tmp_path / "old.db"
    make_old_db(path)
    engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
    init_database(engine)
    session = Session(engine)
    app.dependency_overrides[get_db] = lambda: session
    try:
        client = TestClient(app)
        snap = client.get("/snapshot").json()
        assert snap["progress"]["total_workouts"] == 1
        assert snap["progress"]["records"][0]["slug"] == "squat"
        assert snap["coach_enabled"] is True and snap["objective"] == "muscle"
        assert client.get("/export").status_code == 200
        assert client.post("/exercises", json={"name": "Ma machine", "muscle_group": "Dos",
                                               "equipment": "Machine"}).status_code == 201
        assert client.put("/profile", json={"coach_enabled": False}).json()["coach_enabled"] is False
    finally:
        app.dependency_overrides.clear()
        session.close()
        engine.dispose()


def test_init_database_on_a_fresh_file_has_the_new_columns(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'fresh.db'}")
    init_database(engine)
    init_database(engine)
    with Session(engine) as db:
        assert db.get(UserProfile, 1).coach_enabled is True
        assert db.get(Exercise, "tirage-vertical").aliases.startswith("lat pulldown")
    engine.dispose()


def test_old_database_gets_the_library_columns_and_the_import_runs_at_startup(tmp_path, monkeypatch):
    from app import library
    from tests.test_library import GOOD, write_dataset  # noqa: PLC0415
    monkeypatch.setattr(library, "VENDOR_DIR", write_dataset(tmp_path / "vendor", GOOD, media=False))
    path = tmp_path / "old.db"
    make_old_db(path)
    engine = create_engine(f"sqlite:///{path}")
    init_database(engine)
    init_database(engine)  # idempotent
    inspector = inspect(engine)
    assert {"dataset_id", "is_library", "media_image", "media_gif", "steps_fr", "target_muscle",
            "secondary_muscles"} <= {c["name"] for c in inspector.get_columns("exercise")}
    assert any(i["name"] == "ix_exercise_dataset_id" and i["unique"] for i in inspector.get_indexes("exercise"))
    with Session(engine) as db:
        assert db.query(Exercise).filter(Exercise.is_library).count() == 5
        assert db.get(Exercise, "squat").dataset_id == "0043"  # the old squat got its animation
    engine.dispose()
