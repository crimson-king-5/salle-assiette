import datetime as dt
import uuid

import pytest

from app.models import UserProfile, WeightEntry

NOW = dt.datetime.now().replace(microsecond=0)
TODAY = NOW.date()


def weight_doc(kg=88.4, day=TODAY, created=NOW):
    return {"date": day.isoformat(), "kg": kg, "created_at": created.isoformat()}


def put_weight(client, doc=None, cid=None):
    return client.put(f"/weights/{cid or uuid.uuid4()}", json=doc or weight_doc())


# --- weights ---

def test_put_weight_creates_then_replaces(client, db):
    cid = str(uuid.uuid4())
    first = put_weight(client, cid=cid)
    assert first.status_code == 201 and first.json()["created"] is True
    second = put_weight(client, weight_doc(kg=87.9), cid)
    assert second.status_code == 200
    assert second.json() == {"id": first.json()["id"], "created": False}
    put_weight(client, weight_doc(kg=87.9), cid)  # exact repeat
    row = db.query(WeightEntry).one()
    assert (row.client_id, row.kg, row.date, row.created_at) == (cid, 87.9, TODAY, NOW)


@pytest.mark.parametrize("kg,expected", [(30, 201), (250, 201), (29.9, 422), (250.1, 422), ("a", 422)])
def test_weight_bounds(client, kg, expected):
    assert put_weight(client, weight_doc(kg=kg)).status_code == expected


def test_weight_rejects_bad_uuid_and_bad_date(client):
    assert client.put("/weights/not-a-uuid", json=weight_doc()).status_code == 422
    doc = weight_doc()
    doc["date"] = "hier"
    assert put_weight(client, doc).status_code == 422


def test_delete_weight_is_idempotent_and_leaves_a_tombstone(client, db):
    cid = str(uuid.uuid4())
    put_weight(client, cid=cid)
    assert client.delete(f"/weights/{cid}").status_code == 204
    assert client.delete(f"/weights/{cid}").status_code == 204
    assert db.query(WeightEntry).count() == 0
    late = put_weight(client, cid=cid)  # a retry already on its way
    assert late.status_code == 200 and late.json() == {"id": 0, "created": False}
    assert db.query(WeightEntry).count() == 0


def test_delete_of_an_unknown_weight_still_blocks_its_late_put(client, db):
    cid = str(uuid.uuid4())
    assert client.delete(f"/weights/{cid}").status_code == 204
    put_weight(client, cid=cid)
    assert db.query(WeightEntry).count() == 0


def test_snapshot_weights_last_120_days_one_per_day_sorted(client):
    old = TODAY - dt.timedelta(days=121)
    put_weight(client, weight_doc(90, old, NOW))
    edge = TODAY - dt.timedelta(days=120)
    put_weight(client, weight_doc(89, edge, NOW))
    yesterday = TODAY - dt.timedelta(days=1)
    put_weight(client, weight_doc(88.0, yesterday, NOW - dt.timedelta(hours=5)))
    keep = str(uuid.uuid4())
    put_weight(client, weight_doc(87.6, yesterday, NOW), keep)       # later created_at wins
    put_weight(client, weight_doc(88.4, TODAY, NOW))
    weights = client.get("/snapshot").json()["weights"]
    assert [(w["date"], w["kg"]) for w in weights] == [
        (edge.isoformat(), 89), (yesterday.isoformat(), 87.6), (TODAY.isoformat(), 88.4)]
    assert weights[1]["id"] == keep


# --- profile ---

def test_get_profile_defaults(client):
    p = client.get("/profile").json()
    assert p["objective"] == "recompo" and p["sessions_goal"] == 4
    assert (p["protein_goal_g"], p["target_weight_kg"], p["coach_enabled"]) == (160, 79, True)
    assert p["kcal_hint"] == "~2 150–2 350 kcal/j"
    assert set(p["kcal_hints"]) == {"recompo", "muscle", "perte", "forme"}
    assert p["latest_weight_kg"] is None and p["auto_protein_goal_g"] is None


def test_put_profile_changes_only_sent_fields(client, db):
    r = client.put("/profile", json={"objective": "muscle", "sessions_goal": 5, "coach_enabled": False})
    assert r.status_code == 200
    p = r.json()
    assert (p["objective"], p["sessions_goal"], p["coach_enabled"]) == ("muscle", 5, False)
    assert (p["protein_goal_g"], p["target_weight_kg"]) == (160, 79)
    assert "indicatif" in p["kcal_hint"]
    assert db.query(UserProfile).count() == 1
    assert client.put("/profile", json={"protein_goal_g": 170, "target_weight_kg": 80.5}).json()[
        "coach_enabled"] is False  # untouched by this second call


def test_profile_suggests_protein_from_latest_weight(client):
    put_weight(client, weight_doc(90, TODAY - dt.timedelta(days=3), NOW))
    put_weight(client, weight_doc(88.4, TODAY, NOW))
    p = client.get("/profile").json()
    assert p["latest_weight_kg"] == 88.4 and p["auto_protein_goal_g"] == 177
    client.put("/profile", json={"objective": "forme"})
    assert client.get("/profile").json()["auto_protein_goal_g"] == 141


@pytest.mark.parametrize("body", [
    {"objective": "bulk"}, {"sessions_goal": 0}, {"sessions_goal": 8}, {"protein_goal_g": 59},
    {"protein_goal_g": 301}, {"target_weight_kg": 29}, {"target_weight_kg": 251},
    {"coach_enabled": "peut-être"}, {"sessions_goal": 2.5},
])
def test_put_profile_validation(client, body):
    assert client.put("/profile", json=body).status_code == 422


def test_put_profile_bounds_are_inclusive(client):
    body = {"sessions_goal": 7, "protein_goal_g": 60, "target_weight_kg": 250}
    assert client.put("/profile", json=body).status_code == 200
    body = {"sessions_goal": 1, "protein_goal_g": 300, "target_weight_kg": 30}
    assert client.put("/profile", json=body).status_code == 200


def test_snapshot_carries_the_profile(client):
    client.put("/profile", json={"objective": "perte", "target_weight_kg": 78, "coach_enabled": False,
                                 "protein_goal_g": 170, "sessions_goal": 3})
    snap = client.get("/snapshot").json()
    assert (snap["objective"], snap["target_weight_kg"], snap["coach_enabled"]) == ("perte", 78, False)
    assert (snap["week_goal"], snap["protein_goal_g"]) == (3, 170)
    assert "indicatif" in snap["kcal_hint"]


def test_snapshot_weights_carry_created_at(client, db):
    cid = str(uuid.uuid4())
    put_weight(client, cid=cid)
    weights = client.get("/snapshot").json()["weights"]
    assert weights == [{"id": cid, "date": TODAY.isoformat(), "kg": 88.4, "created_at": NOW.isoformat()}]
