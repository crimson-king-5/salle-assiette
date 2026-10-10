import datetime as dt

from app.models import Checkin

GREAT_DAY = {"energy": 5, "sleep": 5, "motivation": 5, "soreness": 1, "minutes": 60}
RED_DAY = {"energy": 1, "sleep": 1, "motivation": 1, "soreness": 5, "minutes": 60}


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_phone_app_is_served(client):
    response = client.get("/app/")
    assert response.status_code == 200
    assert "Check-in du jour" in response.text


def test_app_files_are_always_revalidated(client):
    # otherwise phones can keep running an old app.js after an update
    assert client.get("/app/app.js").headers["cache-control"] == "no-cache"


def test_root_redirects_to_phone_app(client):
    response = client.get("/", follow_redirects=False)
    assert response.headers["location"] == "/app/"


def test_checkin_returns_readiness(client):
    response = client.post("/checkin", json=GREAT_DAY)
    assert response.status_code == 200
    assert response.json() == {"score": 20, "level": "vert", "format": "complete"}


def test_checkin_red_day_is_express(client):
    response = client.post("/checkin", json=RED_DAY)
    assert response.json()["format"] == "express"


def test_checkin_rejects_invalid_answer(client):
    # 2 is not one of the emoji values → Pydantic refuses it before our code runs
    response = client.post("/checkin", json={**GREAT_DAY, "energy": 2})
    assert response.status_code == 422


def test_checkin_rejects_missing_field(client):
    response = client.post("/checkin", json={"energy": 5})
    assert response.status_code == 422


def test_checkin_is_saved_in_database(client, db):
    client.post("/checkin", json=RED_DAY)

    rows = db.query(Checkin).all()
    assert len(rows) == 1
    row = rows[0]
    assert row.date == dt.date.today()
    assert (row.energy, row.sleep, row.motivation, row.soreness, row.minutes) == (1, 1, 1, 5, 60)
    assert (row.score, row.level, row.format) == (4, "rouge", "express")


def test_today_without_checkin_is_404(client):
    assert client.get("/checkin/today").status_code == 404


def test_today_returns_latest_checkin(client):
    client.post("/checkin", json=RED_DAY)
    client.post("/checkin", json=GREAT_DAY)  # changed my mind: redo the check-in

    response = client.get("/checkin/today")
    assert response.status_code == 200
    assert response.json()["level"] == "vert"
