def test_rotation_is_the_default_program_in_order(client):
    sessions = client.get("/sessions").json()
    assert [s["id"] for s in sessions] == ["HA", "BA", "HB", "BB"]
    assert sessions[0]["name"] == "Haut A"
    assert sessions[0]["exercises"][0] == {
        "slug": "developpe-couche-barre", "name": "Développé couché barre", "sets": 4, "reps": "5-6",
    }


def test_session_complete_by_default(client):
    session = client.get("/sessions/HA").json()
    assert len(session["exercises"]) == 6


def test_session_express_is_shortened(client):
    session = client.get("/sessions/BA", params={"format": "express"}).json()
    assert [e["slug"] for e in session["exercises"]] == ["squat", "sdt-roumain"]
    assert [e["sets"] for e in session["exercises"]] == [3, 3]


def test_unknown_session_is_404(client):
    assert client.get("/sessions/ZZ").status_code == 404


def test_unknown_format_is_422(client):
    assert client.get("/sessions/HA", params={"format": "flemme"}).status_code == 422
