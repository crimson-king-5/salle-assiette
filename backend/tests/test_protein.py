import datetime as dt
import sqlite3
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.main import init_database
from app.models import ProteinEntry, UserProfile
from app.queries import get_profile

NOW = dt.datetime.now().replace(microsecond=0)


def protein_doc(**overrides):
    doc = {"date": NOW.date().isoformat(), "label": "Blanc de poulet", "grams": 35,
           "created_at": NOW.isoformat()}
    doc.update(overrides)
    return doc


def put_protein(client, doc=None, client_id=None):
    return client.put(f"/protein/{client_id or uuid.uuid4()}", json=doc or protein_doc())


# --- PUT /protein/{client_id} ---

def test_put_protein_creates_a_line(client, db):
    cid = str(uuid.uuid4())
    response = put_protein(client, client_id=cid)
    assert response.status_code == 201
    assert response.json()["created"] is True
    row = db.query(ProteinEntry).one()
    assert (row.client_id, row.label, row.grams, row.created_at) == (cid, "Blanc de poulet", 35, NOW)


def test_put_protein_twice_is_idempotent_and_replaces_values(client, db):
    cid = str(uuid.uuid4())
    first = put_protein(client, client_id=cid)
    second = put_protein(client, protein_doc(label="Poulet rôti", grams=28), cid)
    assert second.status_code == 200
    assert second.json() == {"id": first.json()["id"], "created": False}
    put_protein(client, protein_doc(label="Poulet rôti", grams=28), cid)  # exact repeat
    row = db.query(ProteinEntry).one()
    assert (row.label, row.grams) == ("Poulet rôti", 28)


def test_label_is_trimmed_and_limited_to_80_chars(client, db):
    assert put_protein(client, protein_doc(label="  Skyr  ")).status_code == 201
    assert db.query(ProteinEntry).one().label == "Skyr"
    assert put_protein(client, protein_doc(label="x" * 80)).status_code == 201
    assert put_protein(client, protein_doc(label="x" * 81)).status_code == 422
    assert put_protein(client, protein_doc(label="")).status_code == 422
    assert put_protein(client, protein_doc(label="   ")).status_code == 422


@pytest.mark.parametrize("grams,expected", [(0, 201), (300, 201), (-1, 422), (301, 422), (12.5, 422), ("a", 422)])
def test_grams_bounds(client, grams, expected):
    assert put_protein(client, protein_doc(grams=grams)).status_code == expected


def test_bad_id_and_missing_fields(client):
    assert client.put("/protein/not-a-uuid", json=protein_doc()).status_code == 422
    for field in ("date", "label", "grams", "created_at"):
        doc = protein_doc()
        del doc[field]
        assert put_protein(client, doc).status_code == 422


def test_created_at_must_be_naive(client):
    assert put_protein(client, protein_doc(created_at="2026-10-10T12:00:00Z")).status_code == 422
    assert put_protein(client, protein_doc(created_at="2026-10-10T12:00:00+02:00")).status_code == 422


def test_client_id_casing_is_normalized(client, db):
    cid = uuid.uuid4()
    put_protein(client, client_id=str(cid).upper())
    assert put_protein(client, protein_doc(grams=10), str(cid)).status_code == 200
    assert db.query(ProteinEntry).count() == 1


# --- DELETE /protein/{client_id} ---

def test_delete_removes_the_line_and_is_idempotent(client, db):
    cid = str(uuid.uuid4())
    other = str(uuid.uuid4())
    put_protein(client, client_id=cid)
    put_protein(client, client_id=other)
    assert client.delete(f"/protein/{cid}").status_code == 204
    assert [r.client_id for r in db.query(ProteinEntry)] == [other]
    assert client.delete(f"/protein/{cid}").status_code == 204  # already gone: still fine
    assert client.delete(f"/protein/{uuid.uuid4()}").status_code == 204  # never existed
    assert db.query(ProteinEntry).count() == 1


def test_delete_with_a_bad_id_is_refused(client):
    assert client.delete("/protein/nope").status_code == 422


def test_a_late_put_cannot_bring_a_deleted_line_back(client, db):
    cid = str(uuid.uuid4())
    put_protein(client, client_id=cid)
    client.delete(f"/protein/{cid}")
    late = put_protein(client, protein_doc(grams=99), cid)  # a retry that was already on its way
    assert late.status_code == 200  # not a 4xx: the phone must drop it from its queue
    assert late.json()["created"] is False
    assert db.query(ProteinEntry).count() == 0
    assert client.get("/snapshot").json()["protein_today"] == []


def test_delete_before_put_also_wins(client, db):
    cid = str(uuid.uuid4())
    client.delete(f"/protein/{cid}")  # the DELETE overtook the PUT
    assert put_protein(client, client_id=cid).status_code == 200
    assert db.query(ProteinEntry).count() == 0


def test_a_tombstone_only_blocks_its_own_id(client, db):
    gone, other = str(uuid.uuid4()), str(uuid.uuid4())
    client.delete(f"/protein/{gone}")
    assert put_protein(client, client_id=other).status_code == 201
    client.delete(f"/protein/{gone}")  # deleting twice is fine
    assert db.query(ProteinEntry).count() == 1


# --- characters the server can't store: a clean 422, never a 500 ---
# The JSON text below has a backslash-u escape: json.loads turns it into a lone surrogate.

def raw_put(client, label_json):
    body = ('{"date": "%s", "label": %s, "grams": 20, "created_at": "%s"}'
            % (NOW.date().isoformat(), label_json, NOW.isoformat()))
    return client.put(f"/protein/{uuid.uuid4()}", content=body,
                      headers={"Content-Type": "application/json"})


HALF_EMOJI = chr(92) + "ud83c"  # backslash + ud83c, as written in JSON text


def test_lone_surrogate_in_label_is_a_clean_422(client, db):
    response = raw_put(client, '"Poulet ' + HALF_EMOJI + '"')
    assert response.status_code == 422
    assert "ud83c" not in response.text  # the bad input is not echoed back
    assert db.query(ProteinEntry).count() == 0


def test_too_long_label_with_a_lone_surrogate_is_a_clean_422(client):
    assert raw_put(client, '"' + "x" * 90 + chr(92) + 'ud800"').status_code == 422


def test_valid_emoji_label_is_accepted(client, db):
    pair = chr(92) + "ud83c" + chr(92) + "udf57"  # a surrogate pair = one real emoji
    assert raw_put(client, '"Poulet ' + pair + '"').status_code == 201
    assert db.query(ProteinEntry).one().label == "Poulet " + chr(0x1F357)


def test_validation_error_body_has_no_input(client):
    body = put_protein(client, protein_doc(grams=999)).json()
    assert body["detail"][0]["loc"][-1] == "grams"
    assert "input" not in body["detail"][0]


# --- snapshot ---

def test_snapshot_has_protein_goal_and_todays_lines_in_order(client):
    snap = client.get("/snapshot").json()
    assert snap["protein_goal_g"] == 160
    assert snap["protein_today"] == []

    first, second = str(uuid.uuid4()), str(uuid.uuid4())
    put_protein(client, protein_doc(label="Skyr", grams=15, created_at=NOW.isoformat()), second)
    put_protein(client, protein_doc(label="Œufs", grams=18,
                                    created_at=(NOW - dt.timedelta(hours=3)).isoformat()), first)
    yesterday = (NOW - dt.timedelta(days=1)).date().isoformat()
    put_protein(client, protein_doc(label="Hier", date=yesterday))

    lines = client.get("/snapshot").json()["protein_today"]
    assert [(l["id"], l["label"], l["grams"]) for l in lines] == [(first, "Œufs", 18), (second, "Skyr", 15)]
    assert lines[0]["date"] == NOW.date().isoformat()


def test_snapshot_follows_the_profile(client, db):
    profile = get_profile(db)
    profile.protein_goal_g, profile.sessions_goal = 175, 3
    db.commit()
    snap = client.get("/snapshot").json()
    assert (snap["protein_goal_g"], snap["week_goal"]) == (175, 3)


def test_finish_summary_uses_the_sessions_goal(client, db):
    get_profile(db).sessions_goal = 2
    db.commit()
    workout = client.post("/workouts", json={"session_id": "BA", "format": "express"}).json()
    assert client.post(f"/workouts/{workout['id']}/finish").json()["week_goal"] == 2


# --- user_profile: fresh DB and old DB ---

def test_profile_defaults(db):
    assert db.query(UserProfile).count() == 0
    profile = get_profile(db)
    assert (profile.id, profile.objective, profile.sessions_goal) == (1, "recompo", 4)
    assert (profile.protein_goal_g, profile.target_weight_kg) == (160, 79.0)
    get_profile(db)
    assert db.query(UserProfile).count() == 1  # never a second row


def test_init_database_on_a_fresh_file(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'fresh.db'}")
    init_database(engine)
    init_database(engine)  # every startup runs it
    with Session(engine) as db:
        assert db.query(UserProfile).one().protein_goal_g == 160
        assert db.query(ProteinEntry).count() == 0
    engine.dispose()


def test_init_database_upgrades_an_existing_database(tmp_path):
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.execute("""CREATE TABLE workout (id INTEGER PRIMARY KEY, session_id VARCHAR(20) NOT NULL,
        name VARCHAR(80) NOT NULL, focus VARCHAR(20) NOT NULL, format VARCHAR(10) NOT NULL,
        date DATE NOT NULL, started_at DATETIME NOT NULL, finished_at DATETIME)""")
    conn.execute("INSERT INTO workout (session_id, name, focus, format, date, started_at) "
                 "VALUES ('HA', 'Haut A', 'Force', 'complete', '2026-09-01', '2026-09-01 10:00:00')")
    conn.commit()
    conn.close()

    engine = create_engine(f"sqlite:///{path}")
    init_database(engine)
    init_database(engine)
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM workout")).scalar() == 1  # data kept
        assert conn.execute(text("SELECT protein_goal_g, sessions_goal FROM user_profile")).one() == (160, 4)
        assert conn.execute(text("SELECT count(*) FROM user_profile")).scalar() == 1
        assert conn.execute(text("SELECT count(*) FROM protein_log")).scalar() == 0
        assert conn.execute(text("SELECT count(*) FROM protein_tombstone")).scalar() == 0
    engine.dispose()
