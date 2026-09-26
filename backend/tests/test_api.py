from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)  # fake HTTP client: calls the app without starting a server


def test_health():
    assert client.get("/health").json() == {"status": "ok"}


def test_checkin_returns_readiness():
    response = client.post("/checkin", json={
        "energy": 5, "sleep": 5, "motivation": 5, "soreness": 1, "minutes": 60,
    })
    assert response.status_code == 200
    assert response.json() == {"score": 20, "level": "vert", "format": "complete"}


def test_checkin_red_day_is_express():
    response = client.post("/checkin", json={
        "energy": 1, "sleep": 1, "motivation": 1, "soreness": 5, "minutes": 60,
    })
    assert response.json()["format"] == "express"


def test_checkin_rejects_invalid_answer():
    # 2 is not one of the emoji values → Pydantic refuses it before our code runs
    response = client.post("/checkin", json={
        "energy": 2, "sleep": 3, "motivation": 3, "soreness": 3, "minutes": 60,
    })
    assert response.status_code == 422


def test_checkin_rejects_missing_field():
    response = client.post("/checkin", json={"energy": 5})
    assert response.status_code == 422
