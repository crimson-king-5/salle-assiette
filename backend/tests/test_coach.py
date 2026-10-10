"""Block 4: the AI coach. The Gemini HTTP call is always faked: no test touches the network."""

import copy
import datetime as dt
import json
import uuid

import httpx
import pytest
from sqlalchemy import select

from app import coach, coach_tools, gemini
from app.models import (
    CoachAction, CoachMessage, Exercise, ProteinEntry, SessionTemplate, UserProfile, WeightEntry,
)
from app.queries import get_profile

KEY = "test-key-123"
NOW = dt.datetime.now().replace(microsecond=0)


# --- Fake Gemini ---

def text_answer(text):
    return {"candidates": [{"content": {"role": "model", "parts": [{"text": text}]}}]}


def call_answer(*calls):
    """calls = (name, args) pairs, answered as ONE model turn with parallel function calls."""
    parts = [{"functionCall": {"name": n, "args": a, "id": f"id{i}"}, "thoughtSignature": f"sig{i}"}
             for i, (n, a) in enumerate(calls)]
    return {"candidates": [{"content": {"role": "model", "parts": parts}}]}


class FakeGemini:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.payloads = []
        self.keys = []

    def __call__(self, payload, api_key, model, timeout_s=None):
        self.payloads.append(copy.deepcopy(payload))
        self.keys.append((api_key, model))
        if not self.responses:
            raise AssertionError("Gemini was called more often than the test expected")
        answer = self.responses.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(gemini, "read_settings", lambda: (KEY, "gemini-test"))


@pytest.fixture
def fake(monkeypatch, configured):
    """fake(answer, ...) installs a FakeGemini and returns it."""
    def install(*responses):
        f = FakeGemini(*responses)
        monkeypatch.setattr(gemini, "generate", f)
        return f
    return install


def chat(client, message="Salut", thread="training", allow_changes=True, **extra):
    return client.post("/coach/chat", json={"thread": thread, "message": message, "allow_changes": allow_changes, **extra})


def session_names(db, session_id):
    db.expire_all()
    return [se.exercise.name_fr for se in db.get(SessionTemplate, session_id).exercises]


def last_actions(client, thread="training"):
    messages = client.get("/coach/history", params={"thread": thread}).json()["messages"]
    return [a for m in messages for a in m["actions"]]


# --- Configuration ---

def test_missing_key_gives_503_everywhere(client, monkeypatch, tmp_path):
    monkeypatch.setattr(gemini, "ENV_PATH", tmp_path / "missing.env")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    for response in (
        chat(client),
        client.post("/coach/meal", json={"text": "du poulet"}),
        client.post("/coach/exercise-advice", json={"slug": "squat"}),
    ):
        assert response.status_code == 503
        assert response.json()["detail"] == "Coach non configuré : ajoute GEMINI_API_KEY dans backend/.env"
    status = client.get("/coach/status").json()
    assert status == {"configured": False, "enabled": True, "model": None}


def test_settings_come_from_env_file(monkeypatch, tmp_path):
    env = tmp_path / ".env"
    env.write_text("GEMINI_API_KEY=abc123\nGEMINI_MODEL=gemini-x-1\n", encoding="utf-8")
    monkeypatch.setattr(gemini, "ENV_PATH", env)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    assert gemini.read_settings() == ("abc123", "gemini-x-1")
    env.write_text("GEMINI_API_KEY=abc123\nGEMINI_MODEL=../evil?x=1\n", encoding="utf-8")
    assert gemini.read_settings() == ("abc123", gemini.DEFAULT_MODEL)  # a bad model name can't reach the URL
    env.write_text("GEMINI_API_KEY=\n", encoding="utf-8")
    assert gemini.read_settings() == (None, gemini.DEFAULT_MODEL)


def test_status_never_reveals_the_key(client, configured):
    response = client.get("/coach/status")
    assert response.json() == {"configured": True, "enabled": True, "model": "gemini-test"}
    assert KEY not in response.text


def test_coach_disabled_in_profile_gives_403(client, db, fake):
    f = fake()
    get_profile(db).coach_enabled = False
    db.commit()
    for response in (
        chat(client),
        client.post("/coach/meal", json={"text": "des pâtes"}),
        client.post("/coach/exercise-advice", json={"slug": "squat"}),
    ):
        assert response.status_code == 403
        assert "désactivé" in response.json()["detail"]
    assert f.payloads == []
    assert client.get("/coach/status").json()["enabled"] is False


# --- Chat basics ---

def test_chat_stores_both_messages_and_builds_real_context(client, db, fake):
    db.add(ProteinEntry(client_id=str(uuid.uuid4()), date=dt.date.today(), label="Skyr", grams=25, created_at=NOW))
    db.commit()
    f = fake(text_answer("Bien joué !"))
    response = chat(client, "Comment progresser au squat ?")
    assert response.status_code == 200
    messages = response.json()["messages"]
    assert [(m["role"], m["text"]) for m in messages] == [
        ("user", "Comment progresser au squat ?"), ("model", "Bien joué !")]
    payload = f.payloads[0]
    system = payload["systemInstruction"]["parts"][0]["text"]
    assert "Haut A" in system and "Squat" in system  # the program
    assert "25 g de protéines" in system and "recompo" in system  # protein today + profile
    assert payload["contents"][-1] == {"role": "user", "parts": [{"text": "Comment progresser au squat ?"}]}
    assert {d["name"] for d in payload["tools"][0]["functionDeclarations"]} == set(coach_tools.TOOLS)
    assert f.keys == [(KEY, "gemini-test")]
    assert KEY not in response.text and KEY not in json.dumps(payload)  # key only travels as a header


def test_system_prompt_states_the_consent_rule(client, fake):
    f = fake(text_answer("ok"))
    chat(client)
    system = f.payloads[0]["systemInstruction"]["parts"][0]["text"]
    assert "explicitement" in system and "sans appeler d'outil" in system


def test_threads_are_separate_and_tools_differ(client, fake):
    f = fake(text_answer("t"), text_answer("n"))
    chat(client, "entraînement", "training")
    chat(client, "nutrition", "nutrition")
    assert [m["text"] for m in client.get("/coach/history", params={"thread": "training"}).json()["messages"]] == ["entraînement", "t"]
    assert [m["text"] for m in client.get("/coach/history", params={"thread": "nutrition"}).json()["messages"]] == ["nutrition", "n"]
    assert [len(f.payloads[1]["contents"])] == [1]  # the nutrition turn sees none of the training chat
    nutrition_tools = {d["name"] for d in f.payloads[1]["tools"][0]["functionDeclarations"]}
    assert nutrition_tools == {"update_goals", "log_today"}


def test_bad_inputs_are_422(client, fake):
    f = fake()
    assert chat(client, "").status_code == 422
    assert chat(client, "   ").status_code == 422
    assert chat(client, "x" * (coach.MAX_MESSAGE_CHARS + 1)).status_code == 422
    assert chat(client, "ok" * 3, thread="cuisine").status_code == 422
    lone = client.post("/coach/chat", content=b'{"thread": "training", "message": "a\\ud800b"}',
                       headers={"Content-Type": "application/json"})
    assert lone.status_code == 422  # a lone surrogate can't be stored
    assert client.post("/coach/chat", json={"thread": "training"}).status_code == 422
    assert f.payloads == []


def test_history_sent_to_the_model_is_capped_and_stored_history_pruned(client, db, fake):
    for i in range(60):
        db.add(CoachMessage(thread="training", role="user" if i % 2 == 0 else "model", text=f"m{i}",
                            created_at=NOW + dt.timedelta(seconds=i)))
    db.commit()
    f = fake(text_answer("réponse"))
    messages = chat(client, "dernier").json()["messages"]
    contents = f.payloads[0]["contents"]
    assert len(contents) <= coach.HISTORY_SENT + 1
    assert contents[0]["role"] == "user" and contents[-1]["parts"][0]["text"] == "dernier"
    assert len(messages) == coach.HISTORY_KEPT
    assert db.query(CoachMessage).filter_by(thread="training").count() == coach.HISTORY_KEPT


def test_clear_history(client, fake):
    fake(text_answer("a"))
    chat(client)
    assert client.delete("/coach/history", params={"thread": "training"}).json()["messages"] == []
    assert client.get("/coach/history", params={"thread": "training"}).json()["messages"] == []


# --- The function-calling loop ---

def test_tool_loop_applies_change_and_feeds_the_result_back(client, db, fake):
    f = fake(
        call_answer(("edit_session_exercise", {"session": "Haut A", "action": "add", "exercise": "Face pull", "sets": 3, "reps": 15})),
        text_answer("C'est fait, Face pull ajouté."),
    )
    response = chat(client, "Oui vas-y, ajoute du face pull à Haut A")
    assert response.status_code == 200
    assert "Face pull" in session_names(db, "HA")
    reply = response.json()["messages"][-1]
    assert reply["text"] == "C'est fait, Face pull ajouté."
    assert len(reply["actions"]) == 1
    assert reply["actions"][0]["tool"] == "edit_session_exercise" and reply["actions"][0]["can_undo"] is True
    assert "Face pull" in reply["actions"][0]["summary"]
    # Second model call: our raw model turn (with its thought signature) + one functionResponse with the same id
    second = f.payloads[1]["contents"]
    assert second[-2]["role"] == "model" and second[-2]["parts"][0]["thoughtSignature"] == "sig0"
    fr = second[-1]["parts"][0]["functionResponse"]
    assert fr["name"] == "edit_session_exercise" and fr["id"] == "id0"
    assert fr["response"]["ok"] is True


def test_past_changes_are_written_into_the_next_turns_history(client, db, fake):
    fake(call_answer(("update_goals", {"sessions_goal": 5})), text_answer("Passé à 5."))
    chat(client, "mets 5 séances par semaine, oui")
    f = fake(text_answer("ok"))
    chat(client, "et maintenant ?")
    model_turn = f.payloads[0]["contents"][1]
    assert "[Modifications appliquées : Objectifs : séances/semaine → 5]" in model_turn["parts"][0]["text"]


def test_unknown_tool_is_rejected_without_side_effects(client, db, fake):
    f = fake(call_answer(("drop_all_tables", {"x": 1})), text_answer("Je ne peux pas."))
    before = db.query(SessionTemplate).count()
    response = chat(client, "fais n'importe quoi")
    assert response.status_code == 200
    fr = f.payloads[1]["contents"][-1]["parts"][0]["functionResponse"]["response"]
    assert "inconnu" in fr["error"]
    assert db.query(SessionTemplate).count() == before and db.query(CoachAction).count() == 0


def test_tool_outside_the_thread_is_rejected(client, db, fake):
    f = fake(call_answer(("create_session", {"name": "Intrus"})), text_answer("non"))
    chat(client, "crée une séance", "nutrition")
    assert "indisponible" in f.payloads[1]["contents"][-1]["parts"][0]["functionResponse"]["response"]["error"]
    assert db.get(SessionTemplate, "HA") is not None and db.query(SessionTemplate).count() == 4


def test_bad_exercise_name_returns_close_names_and_model_can_retry(client, db, fake):
    f = fake(
        call_answer(("edit_session_exercise", {"session": "Haut A", "action": "add", "exercise": "Hip trust"})),
        call_answer(("edit_session_exercise", {"session": "Haut A", "action": "add", "exercise": "Hip thrust"})),
        text_answer("Ajouté."),
    )
    chat(client, "ajoute hip thrust à Haut A, oui")
    first = f.payloads[1]["contents"][-1]["parts"][0]["functionResponse"]["response"]
    assert "error" in first and "Hip thrust" in first["noms_proches"]
    assert "Hip thrust" in session_names(db, "HA")


def test_arguments_are_validated_like_the_api(client, db, fake):
    bad_calls = [
        ("edit_session_exercise", {"session": "Haut A", "action": "add", "exercise": "Face pull", "sets": 99}),
        ("edit_session_exercise", {"session": "Haut A", "action": "explode", "exercise": "Face pull"}),
        ("edit_session_exercise", {"session": "Haut A", "action": "add", "exercise": "Face pull", "extra": 1}),
        ("update_goals", {"protein_goal_g": 5000}),
        ("update_goals", {"coach_enabled": False}),  # the coach cannot switch itself off
        ("log_today", {"weight_kg": 5}),
        ("log_today", {"protein_g": 9999}),
        ("log_today", {}),
        ("update_session", {"session": "Haut A", "focus": "Cardio"}),
    ]
    f = fake(call_answer(*bad_calls), text_answer("rien"))
    chat(client, "oui")
    responses = [p["functionResponse"]["response"] for p in f.payloads[1]["contents"][-1]["parts"]]
    assert len(responses) == len(bad_calls) and all("error" in r for r in responses)
    assert db.query(CoachAction).count() == 0
    assert get_profile(db).coach_enabled is True and get_profile(db).protein_goal_g == 160
    assert len(session_names(db, "HA")) == 6 and db.query(WeightEntry).count() == 0


def test_no_tools_offered_and_none_run_when_changes_not_allowed(client, db, fake):
    f = fake(call_answer(("update_goals", {"sessions_goal": 6})), text_answer("Je ne peux que conseiller."))
    chat(client, "mets 6 séances", allow_changes=False)
    assert "tools" not in f.payloads[0]
    assert "aucun outil" in f.payloads[0]["systemInstruction"]["parts"][0]["text"]
    assert "indisponible" in f.payloads[1]["contents"][-1]["parts"][0]["functionResponse"]["response"]["error"]
    assert get_profile(db).sessions_goal == 4 and db.query(CoachAction).count() == 0


def test_loop_is_capped(client, db, fake):
    endless = [call_answer(("log_today", {"protein_g": 10})) for _ in range(coach.MAX_STEPS + 3)]
    f = fake(*endless)
    response = chat(client, "oui")
    assert response.status_code == 200
    assert len(f.payloads) == coach.MAX_STEPS
    assert response.json()["messages"][-1]["text"] == coach.GIVE_UP
    assert db.query(ProteinEntry).count() == coach.MAX_STEPS  # the changes made stay, and can be undone


def test_actions_per_turn_are_capped(client, db, fake):
    many = [("log_today", {"protein_g": 5}) for _ in range(coach.MAX_ACTIONS_PER_TURN + 3)]
    f = fake(call_answer(*many), text_answer("fait"))
    chat(client, "oui")
    assert db.query(ProteinEntry).count() == coach.MAX_ACTIONS_PER_TURN
    last = f.payloads[1]["contents"][-1]["parts"][-1]["functionResponse"]["response"]
    assert "Trop de modifications" in last["error"] or "Trop d'appels" in last["error"]


def test_empty_model_answer_gets_a_polite_fallback(client, fake):
    fake({"candidates": [{"content": {"role": "model", "parts": []}}]})
    assert chat(client).json()["messages"][-1]["text"] == coach.EMPTY


def test_blocked_prompt_is_reported_cleanly(client, db, fake):
    fake({"promptFeedback": {"blockReason": "SAFETY"}})
    response = chat(client)
    assert response.status_code == 422 and "refusé" in response.json()["detail"]
    assert db.query(CoachMessage).count() == 0


def test_model_text_is_stored_verbatim_as_text(client, fake):
    evil = '<img src=x onerror=alert(1)> <script>alert(1)</script>'
    fake(text_answer(evil))
    assert chat(client).json()["messages"][-1]["text"] == evil  # the screen shows it with textContent/esc()


# --- Upstream errors ---

def test_upstream_error_without_changes_forgets_the_question(client, db, fake):
    fake(gemini.GeminiError(429, "Le quota Gemini est atteint. Réessaie dans quelques minutes."))
    response = chat(client)
    assert response.status_code == 429 and "quota" in response.json()["detail"]
    assert db.query(CoachMessage).count() == 0


def test_upstream_error_after_a_change_keeps_a_trace_to_undo(client, db, fake):
    fake(call_answer(("update_goals", {"sessions_goal": 6})),
         gemini.GeminiError(502, "Gemini est indisponible pour le moment. Réessaie dans un instant."))
    response = chat(client, "oui, 6 séances")
    assert response.status_code == 200
    reply = response.json()["messages"][-1]
    assert reply["text"].startswith("Interrompu") and len(reply["actions"]) == 1
    assert get_profile(db).sessions_goal == 6


def test_concurrent_turn_is_refused(client, fake):
    f = fake(text_answer("a"))
    assert coach._turn_lock.acquire(blocking=False)
    try:
        assert chat(client).status_code == 429
    finally:
        coach._turn_lock.release()
    assert f.payloads == []


# --- Each tool, with its validation and its undo ---

def run(db, name, args):
    outcome = coach_tools.execute_tool(db, name, args)
    return outcome


def undo(db, name, outcome):
    coach_tools.undo_tool(db, name, json.loads(json.dumps(outcome.undo)))  # as stored: through JSON


def test_edit_exercise_all_actions_and_undo(db):
    original = session_names(db, "HA")
    steps = [
        {"action": "add", "exercise": "Hip thrust", "sets": 4, "reps": "10"},
        {"action": "update", "exercise": "Hip thrust", "sets": 2},
        {"action": "move", "exercise": "Hip thrust", "position": 1},
        {"action": "replace", "exercise": "Hip thrust", "new_exercise": "Face pull"},
        {"action": "remove", "exercise": "Rowing barre"},
    ]
    outcomes = []
    for step in steps:
        outcomes.append(run(db, "edit_session_exercise", {"session": "ha", **step}))
    names = session_names(db, "HA")
    assert names[0] == "Face pull" and "Rowing barre" not in names and "Hip thrust" not in names
    line = db.get(SessionTemplate, "HA").exercises[0]
    assert (line.sets, line.reps) == (2, "10")  # kept the line's sets/reps on replace
    for step, outcome in zip(reversed(steps), reversed(outcomes)):
        undo(db, "edit_session_exercise", outcome)
    assert session_names(db, "HA") == original


def test_edit_exercise_errors(db):
    cases = [
        ({"session": "Nulle part", "action": "add", "exercise": "Squat"}, "Séance introuvable"),
        ({"session": "HA", "action": "add", "exercise": "Rowing barre"}, "déjà dans"),
        ({"session": "HA", "action": "remove", "exercise": "Hip thrust"}, "n'est pas dans"),
        ({"session": "HA", "action": "replace", "exercise": "Rowing barre"}, "new_exercise"),
        ({"session": "HA", "action": "update", "exercise": "Rowing barre"}, "sets et/ou reps"),
        ({"session": "HA", "action": "move", "exercise": "Rowing barre"}, "position"),
        ({"session": "HA", "action": "add", "exercise": "Zumba aquatique"}, "introuvable"),
    ]
    original = session_names(db, "HA")
    for args, fragment in cases:
        with pytest.raises(coach_tools.ToolError) as error:
            run(db, "edit_session_exercise", args)
        assert fragment in error.value.message
    assert session_names(db, "HA") == original


def test_edit_exercise_respects_the_20_exercise_limit(db):
    in_ha = set(session_names(db, "HA"))
    others = [e.name_fr for e in db.scalars(select(Exercise)) if e.name_fr not in in_ha]
    for name in others[:14]:
        run(db, "edit_session_exercise", {"session": "HA", "action": "add", "exercise": name})
    assert len(session_names(db, "HA")) == 20
    with pytest.raises(coach_tools.ToolError):
        run(db, "edit_session_exercise", {"session": "HA", "action": "add", "exercise": others[14]})
    assert len(session_names(db, "HA")) == 20


def test_reps_may_arrive_as_a_number(db):
    run(db, "edit_session_exercise", {"session": "HA", "action": "add", "exercise": "Face pull", "reps": 15})
    assert db.get(SessionTemplate, "HA").exercises[-1].reps == "15"


def test_create_session_with_exercises_then_undo(db):
    before = db.query(SessionTemplate).count()
    outcome = run(db, "create_session", {
        "name": "Bras", "focus": "Hypertrophie",
        "exercises": [{"exercise": "Curl haltères", "sets": 3, "reps": "10-12"}, {"exercise": "Extension triceps poulie"}],
    })
    new = db.scalars(select(SessionTemplate).where(SessionTemplate.name == "Bras")).one()
    assert db.query(SessionTemplate).count() == before + 1 and new.position == before
    assert [se.exercise.name_fr for se in new.exercises] == ["Curl haltères", "Extension triceps poulie"]
    undo(db, "create_session", outcome)
    db.expire_all()
    assert db.query(SessionTemplate).count() == before


def test_create_session_unknown_exercise_creates_nothing(db):
    with pytest.raises(coach_tools.ToolError):
        run(db, "create_session", {"name": "Bras", "exercises": [{"exercise": "Machin inconnu"}]})
    assert db.query(SessionTemplate).count() == 4
    with pytest.raises(coach_tools.ToolError):
        run(db, "create_session", {"name": "Bras", "template": "inconnu"})


def test_update_session_rename_and_delete_with_undo(db):
    outcome = run(db, "update_session", {"session": "Bas B", "name": "Jambes B", "focus": "Force"})
    db.expire_all()
    t = db.get(SessionTemplate, "BB")
    assert (t.name, t.focus, len(t.exercises)) == ("Jambes B", "Force", 5)
    undo(db, "update_session", outcome)
    db.expire_all()
    assert db.get(SessionTemplate, "BB").name == "Bas B"

    outcome = run(db, "update_session", {"session": "BB", "delete": True})
    db.expire_all()
    assert db.get(SessionTemplate, "BB") is None
    assert [t.position for t in db.scalars(select(SessionTemplate).order_by(SessionTemplate.position))] == [0, 1, 2]
    undo(db, "update_session", outcome)
    db.expire_all()
    restored = db.get(SessionTemplate, "BB")
    assert restored is not None and restored.position == 3 and len(restored.exercises) == 5


def test_cannot_delete_the_last_session_or_combine_delete(db):
    for sid in ("HB", "BA", "BB"):
        run(db, "update_session", {"session": sid, "delete": True})
    with pytest.raises(coach_tools.ToolError) as error:
        run(db, "update_session", {"session": "HA", "delete": True})
    assert "au moins une séance" in error.value.message
    assert db.get(SessionTemplate, "HA") is not None
    with pytest.raises(coach_tools.ToolError):
        run(db, "update_session", {"session": "HA", "delete": True, "name": "X"})
    with pytest.raises(coach_tools.ToolError):
        run(db, "update_session", {"session": "HA"})


def test_set_rotation_order_and_undo(db):
    outcome = run(db, "set_rotation_order", {"order": ["Bas B", "BA", "haut b", "HA"]})
    db.expire_all()
    assert [t.id for t in db.scalars(select(SessionTemplate).order_by(SessionTemplate.position))] == ["BB", "BA", "HB", "HA"]
    undo(db, "set_rotation_order", outcome)
    db.expire_all()
    assert [t.id for t in db.scalars(select(SessionTemplate).order_by(SessionTemplate.position))] == ["HA", "BA", "HB", "BB"]
    for bad in (["HA", "BA"], ["HA", "HA", "BA", "HB", "BB"], ["HA", "BA", "HB", "Inconnue"]):
        with pytest.raises(coach_tools.ToolError):
            run(db, "set_rotation_order", {"order": bad})


def test_create_custom_exercise_and_undo(db):
    outcome = run(db, "create_custom_exercise", {"name": "Curl marteau corde", "muscle_group": "Biceps", "equipment": "Poulie"})
    e = db.scalars(select(Exercise).where(Exercise.name_fr == "Curl marteau corde")).one()
    assert e.is_custom and e.equipment == "Poulie"
    with pytest.raises(coach_tools.ToolError) as error:
        run(db, "create_custom_exercise", {"name": "curl marteau corde", "muscle_group": "Biceps", "equipment": "Poulie"})
    assert "déjà" in error.value.message
    for bad in ({"name": "X", "muscle_group": "Cerveau", "equipment": "Poulie"},
                {"name": "X", "muscle_group": "Dos", "equipment": "Laser"}):
        with pytest.raises(coach_tools.ToolError):
            run(db, "create_custom_exercise", bad)
    undo(db, "create_custom_exercise", outcome)
    db.expire_all()
    assert db.scalars(select(Exercise).where(Exercise.name_fr == "Curl marteau corde")).first() is None


def test_undo_of_a_used_custom_exercise_is_refused(db):
    outcome = run(db, "create_custom_exercise", {"name": "Mon exo", "muscle_group": "Dos", "equipment": "Poulie"})
    run(db, "edit_session_exercise", {"session": "HA", "action": "add", "exercise": "Mon exo"})
    with pytest.raises(coach_tools.ToolError):
        undo(db, "create_custom_exercise", outcome)


def test_update_goals_and_undo(db):
    outcome = run(db, "update_goals", {"objective": "muscle", "sessions_goal": 5, "protein_goal_g": 180, "target_weight_kg": 82.5})
    db.expire_all()
    p = get_profile(db)
    assert (p.objective, p.sessions_goal, p.protein_goal_g, p.target_weight_kg) == ("muscle", 5, 180, 82.5)
    undo(db, "update_goals", outcome)
    db.expire_all()
    p = get_profile(db)
    assert (p.objective, p.sessions_goal, p.protein_goal_g, p.target_weight_kg) == ("recompo", 4, 160, 79.0)


@pytest.mark.parametrize("args", [
    {"sessions_goal": 0}, {"sessions_goal": 8}, {"protein_goal_g": 59}, {"protein_goal_g": 301},
    {"target_weight_kg": 29}, {"target_weight_kg": 251}, {"objective": "bulk"}, {},
])
def test_update_goals_bounds(db, args):
    with pytest.raises(coach_tools.ToolError):
        run(db, "update_goals", args)
    db.expire_all()
    assert get_profile(db).protein_goal_g == 160 and get_profile(db).sessions_goal == 4


def test_log_today_protein_and_weight_with_undo(db):
    db.add(WeightEntry(client_id=str(uuid.uuid4()), date=dt.date.today(), kg=85.0, created_at=NOW - dt.timedelta(hours=3)))
    db.commit()
    outcome = run(db, "log_today", {"protein_g": 32, "protein_label": "Skyr + whey", "weight_kg": 84.4})
    db.expire_all()
    line = db.query(ProteinEntry).one()
    assert (line.grams, line.label, line.date) == (32, "Skyr + whey", dt.date.today())
    assert [w.kg for w in db.query(WeightEntry)] == [84.4]  # one weigh-in per day: replaced the 85.0
    undo(db, "log_today", outcome)
    db.expire_all()
    assert db.query(ProteinEntry).count() == 0
    assert [w.kg for w in db.query(WeightEntry)] == [85.0]  # the old one is back


def test_log_today_label_too_long_is_refused(db):
    with pytest.raises(coach_tools.ToolError):
        run(db, "log_today", {"protein_g": 20, "protein_label": "x" * 81})
    assert db.query(ProteinEntry).count() == 0


# --- Undo endpoint ---

def test_undo_endpoint_only_the_newest_change_first(client, db, fake):
    fake(call_answer(("update_goals", {"sessions_goal": 6}), ("log_today", {"protein_g": 30})), text_answer("Fait."))
    chat(client, "oui, fais les deux")
    first, second = last_actions(client)
    assert (first["can_undo"], second["can_undo"]) == (False, True)

    refused = client.post(f"/coach/undo/{first['id']}")
    assert refused.status_code == 409 and "plus récente" in refused.json()["detail"]
    assert get_profile(db).sessions_goal == 6

    assert client.post(f"/coach/undo/{second['id']}").status_code == 200
    assert db.query(ProteinEntry).count() == 0
    body = client.post(f"/coach/undo/{first['id']}").json()
    assert get_profile(db).sessions_goal == 4
    assert [a["status"] for m in body["messages"] for a in m["actions"]] == ["undone", "undone"]
    assert client.post(f"/coach/undo/{first['id']}").status_code == 409  # already undone
    assert client.post("/coach/undo/9999").status_code == 404


def test_undone_changes_are_marked_in_the_next_history(client, db, fake):
    fake(call_answer(("update_goals", {"sessions_goal": 6})), text_answer("ok"))
    chat(client, "oui")
    client.post(f"/coach/undo/{last_actions(client)[0]['id']}")
    f = fake(text_answer("d'accord"))
    chat(client, "ça marche ?")
    assert "(annulée)" in f.payloads[0]["contents"][1]["parts"][0]["text"]


# --- Origin check (the middleware of main.py covers the new POSTs) ---

@pytest.mark.parametrize("path,body", [
    ("/coach/chat", {"thread": "training", "message": "salut"}),
    ("/coach/meal", {"text": "du riz"}),
    ("/coach/exercise-advice", {"slug": "squat"}),
    ("/coach/undo/1", None),
])
def test_foreign_origin_is_refused_before_anything_runs(client, fake, path, body):
    f = fake()
    response = client.post(path, json=body, headers={"Origin": "https://evil.example"})
    assert response.status_code == 403
    assert f.payloads == []
    assert client.delete("/coach/history", params={"thread": "training"}, headers={"Origin": "https://evil.example"}).status_code == 403


def test_own_origin_is_accepted(client, fake):
    fake(text_answer("ok"))
    response = client.post("/coach/chat", json={"thread": "training", "message": "salut"},
                           headers={"Origin": "http://127.0.0.1:8001"})
    assert response.status_code == 200


# --- Meal advice ---

def meal_answer(**fields):
    data = {"score": "top", "message": "Super repas.", "tip": "Ajoute un yaourt.", "protein_g": 42,
            "kcal": 650, "rest_of_day": "Reste léger ce soir."}
    data.update(fields)
    return text_answer(json.dumps(data))


def test_meal_advice(client, fake):
    f = fake(meal_answer())
    response = client.post("/coach/meal", json={"text": "Poulet riz brocoli"})
    assert response.status_code == 200
    assert response.json() == {"score": "top", "message": "Super repas.", "tip": "Ajoute un yaourt.",
                               "protein_g": 42, "kcal": 650, "rest_of_day": "Reste léger ce soir."}
    config = f.payloads[0]["generationConfig"]
    assert config["responseMimeType"] == "application/json" and "responseSchema" in config
    assert "tools" not in f.payloads[0]  # advice can't modify anything


def test_meal_answer_is_clamped_and_cleaned(client, fake):
    fake(meal_answer(score="parfait", protein_g=9999, kcal=-5, message="x" * 2000))
    body = client.post("/coach/meal", json={"text": "un festin"}).json()
    assert body["score"] == "correct" and body["protein_g"] == 300 and body["kcal"] == 0
    assert len(body["message"]) <= 500


def test_meal_answer_with_garbage_is_a_clean_error(client, fake):
    fake(text_answer("pas du json"))
    response = client.post("/coach/meal", json={"text": "un repas"})
    assert response.status_code == 502 and "illisible" in response.json()["detail"]


def test_meal_answer_with_code_fence_is_accepted(client, fake):
    fake(text_answer("```json\n" + json.dumps({"score": "correct", "message": "ok", "tip": "t",
                                                "protein_g": 20, "kcal": 400, "rest_of_day": "r"}) + "\n```"))
    assert client.post("/coach/meal", json={"text": "salade"}).json()["protein_g"] == 20


def test_meal_input_limits(client, fake):
    fake()
    assert client.post("/coach/meal", json={"text": ""}).status_code == 422
    assert client.post("/coach/meal", json={"text": "x" * (coach.MAX_MEAL_CHARS + 1)}).status_code == 422


# --- Exercise-add advice ---

def advice_answer(**fields):
    data = {"verdict": "bien", "message": "Bonne idée !", "move_to": None, "swap_for": None, "order_tip": None}
    data.update(fields)
    return text_answer(json.dumps(data))


def test_exercise_advice_validates_targets(client, fake):
    fake(advice_answer(verdict="mieux_ailleurs", move_to="BA", order_tip="En fin de séance"))
    body = client.post("/coach/exercise-advice", json={"slug": "face-pull", "session_id": "HA"}).json()
    assert (body["verdict"], body["move_to"], body["order_tip"]) == ("mieux_ailleurs", "BA", "En fin de séance")

    fake(advice_answer(verdict="mieux_ailleurs", move_to="ZZZ"))  # invented session
    body = client.post("/coach/exercise-advice", json={"slug": "face-pull", "session_id": "HA"}).json()
    assert (body["verdict"], body["move_to"]) == ("bien", None)

    fake(advice_answer(verdict="meilleure_option", swap_for="rowing barre"))
    body = client.post("/coach/exercise-advice", json={"slug": "face-pull"}).json()
    assert (body["verdict"], body["swap_for"]) == ("meilleure_option", "Rowing barre")  # exact library name

    fake(advice_answer(verdict="meilleure_option", swap_for="Exercice inventé"))
    body = client.post("/coach/exercise-advice", json={"slug": "face-pull"}).json()
    assert (body["verdict"], body["swap_for"]) == ("bien", None)


def test_exercise_advice_unknown_exercise(client, fake):
    fake()
    assert client.post("/coach/exercise-advice", json={"slug": "nope"}).status_code == 404


# --- The HTTP layer (httpx MockTransport: still no network) ---

@pytest.fixture
def http(monkeypatch):
    seen = []

    def install(handler):
        def wrapped(request):
            seen.append(request)
            return handler(request)
        monkeypatch.setattr(gemini, "transport", httpx.MockTransport(wrapped))
        return seen
    return install


def test_request_shape_and_key_in_header_only(http):
    seen = http(lambda r: httpx.Response(200, json=text_answer("ok")))
    assert gemini.generate({"contents": []}, KEY, "gemini-test") == text_answer("ok")
    request = seen[0]
    assert request.url.path.endswith("/models/gemini-test:generateContent")
    assert request.headers["x-goog-api-key"] == KEY and KEY not in str(request.url)


@pytest.mark.parametrize("status,fragment,expected", [
    (401, "refuse la clé", 502), (403, "refuse la clé", 502), (429, "quota", 429),
    (404, "GEMINI_MODEL", 502), (500, "indisponible", 502), (503, "indisponible", 502), (418, "refusé la demande", 502),
])
def test_upstream_status_is_mapped_to_french_messages(http, status, fragment, expected):
    http(lambda r: httpx.Response(status, json={"error": {"message": f"details secrets {KEY}"}}))
    with pytest.raises(gemini.GeminiError) as error:
        gemini.generate({}, KEY, "m")
    assert error.value.status == expected and fragment in error.value.message
    assert KEY not in error.value.message and "secrets" not in error.value.message


def test_bad_api_key_400_is_recognised(http):
    http(lambda r: httpx.Response(400, json={"error": {"message": "API key not valid. Please pass a valid API key."}}))
    with pytest.raises(gemini.GeminiError) as error:
        gemini.generate({}, KEY, "m")
    assert "GEMINI_API_KEY" in error.value.message


def test_timeout_and_network_errors(http):
    def timeout(request):
        raise httpx.ReadTimeout("slow", request=request)
    http(timeout)
    with pytest.raises(gemini.GeminiError) as error:
        gemini.generate({}, KEY, "m")
    assert error.value.status == 504 and "trop de temps" in error.value.message

    def down(request):
        raise httpx.ConnectError("no route", request=request)
    http(down)
    with pytest.raises(gemini.GeminiError) as error:
        gemini.generate({}, KEY, "m")
    assert error.value.status == 502 and "joindre" in error.value.message


def test_non_json_answer(http):
    http(lambda r: httpx.Response(200, content=b"<html>oops</html>"))
    with pytest.raises(gemini.GeminiError) as error:
        gemini.generate({}, KEY, "m")
    assert "illisible" in error.value.message


def test_full_chat_through_the_http_layer(client, configured, http):
    http(lambda r: httpx.Response(401, json={"error": {"message": "nope"}}))
    response = chat(client)
    assert response.status_code == 502
    assert response.json()["detail"] == "Gemini refuse la clé : vérifie GEMINI_API_KEY dans backend/.env."
    assert KEY not in response.text


# --- Review fixes ---

def all_actions_attached(db):
    db.expire_all()
    return all(a.message_id is not None for a in db.query(CoachAction))


def test_blank_label_and_blank_name_are_tool_errors_not_500s(client, db, fake):
    f = fake(call_answer(("update_goals", {"protein_goal_g": 200})),
             call_answer(("log_today", {"protein_g": 30, "protein_label": "   "}),
                         ("create_session", {"name": "  "})),
             text_answer("ok"))
    response = chat(client, "oui")
    assert response.status_code == 200
    errors = [p["functionResponse"]["response"] for p in f.payloads[2]["contents"][-1]["parts"]]
    assert all("error" in e for e in errors)
    assert db.query(ProteinEntry).count() == 0 and db.query(SessionTemplate).count() == 4
    assert all_actions_attached(db) and db.query(CoachAction).count() == 1


def test_unexpected_tool_exception_is_a_clean_tool_error(client, db, fake, monkeypatch):
    def boom(db, args):
        raise RuntimeError("secret internals")
    monkeypatch.setattr(coach_tools.TOOLS["log_today"], "run", boom)
    f = fake(call_answer(("log_today", {"protein_g": 30})), text_answer("ok"))
    assert chat(client, "oui").status_code == 200
    error = f.payloads[1]["contents"][-1]["parts"][0]["functionResponse"]["response"]["error"]
    assert "secret" not in error and "échoué" in error


def test_unexpected_exception_in_the_loop_still_writes_a_reply_with_the_actions(client, db, fake):
    fake(call_answer(("update_goals", {"sessions_goal": 6})), RuntimeError("boom"))
    response = chat(client, "oui")
    assert response.status_code == 200
    reply = response.json()["messages"][-1]
    assert reply["text"].startswith("Interrompu") and len(reply["actions"]) == 1
    assert all_actions_attached(db)


def test_undo_refused_when_the_program_was_edited_by_hand(client, db, fake):
    fake(call_answer(("edit_session_exercise", {"session": "HA", "action": "remove", "exercise": "Curl haltères"})),
         text_answer("ok"))
    chat(client, "oui")
    action = last_actions(client)[0]
    assert client.post("/sessions", json={"name": "Manuelle", "focus": "Libre", "template": "full-body"}).status_code == 201
    refused = client.post(f"/coach/undo/{action['id']}")
    assert refused.status_code == 409 and "programme a changé" in refused.json()["detail"]
    db.expire_all()
    assert "Manuelle" in [t.name for t in db.scalars(select(SessionTemplate))]
    assert "Curl haltères" not in session_names(db, "HA")


def test_undo_refused_when_goals_were_edited_by_hand(client, db, fake):
    fake(call_answer(("update_goals", {"sessions_goal": 6})), text_answer("ok"))
    chat(client, "oui")
    assert client.put("/profile", json={"sessions_goal": 3}).status_code == 200
    refused = client.post(f"/coach/undo/{last_actions(client)[0]['id']}")
    assert refused.status_code == 409
    assert get_profile(db).sessions_goal == 3


def test_undo_refused_when_the_logged_line_was_edited_or_deleted(client, db, fake):
    fake(call_answer(("log_today", {"protein_g": 30})), text_answer("ok"),
         call_answer(("log_today", {"protein_g": 20})), text_answer("ok"))
    chat(client, "oui")
    chat(client, "encore oui")
    second, first = sorted(last_actions(client), key=lambda a: -a["id"])
    row = db.query(ProteinEntry).order_by(ProteinEntry.id.desc()).first()
    client.delete(f"/protein/{row.client_id}")
    assert client.post(f"/coach/undo/{second['id']}").status_code == 409
    other = db.query(ProteinEntry).one()
    client.put(f"/protein/{other.client_id}", json={"date": other.date.isoformat(), "label": other.label,
                                                    "grams": 99, "created_at": other.created_at.isoformat()})
    # the second one is still the newest applied: it blocks the first
    assert client.post(f"/coach/undo/{first['id']}").status_code == 409


def test_clearing_one_thread_does_not_block_undo_in_the_other(client, db, fake):
    fake(call_answer(("update_goals", {"sessions_goal": 3})), text_answer("ok"),
         call_answer(("log_today", {"protein_g": 20})), text_answer("ok"))
    chat(client, "oui", "training")
    training_action = last_actions(client, "training")[0]
    chat(client, "oui", "nutrition")
    nutrition_action = db.query(CoachAction).filter_by(tool="log_today").one()
    client.delete("/coach/history", params={"thread": "nutrition"})
    history = client.get("/coach/history", params={"thread": "training"}).json()
    assert [a["can_undo"] for m in history["messages"] for a in m["actions"]] == [True]
    assert client.post(f"/coach/undo/{training_action['id']}").status_code == 200
    expired = client.post(f"/coach/undo/{nutrition_action.id}")
    assert expired.status_code == 409 and "plus annulable" in expired.json()["detail"]


def test_log_today_refused_weight_leaves_no_orphan_protein(client, db, fake):
    db.add(WeightEntry(client_id=str(uuid.uuid4()), date=dt.date.today(), kg=80,
                       created_at=dt.datetime.now() + dt.timedelta(hours=2)))
    db.commit()
    f = fake(call_answer(("log_today", {"protein_g": 40, "weight_kg": 81})), text_answer("ok"))
    chat(client, "oui")
    assert "error" in f.payloads[1]["contents"][-1]["parts"][0]["functionResponse"]["response"]
    db.expire_all()
    assert db.query(ProteinEntry).count() == 0 and db.query(CoachAction).count() == 0


def test_http_timeout_never_exceeds_the_remaining_turn_budget(client, fake, monkeypatch):
    seen = []

    def spy(payload, key, model, timeout_s=None):
        seen.append(timeout_s)
        return text_answer("ok")
    monkeypatch.setattr(gemini, "generate", spy)
    monkeypatch.setattr(coach, "TURN_BUDGET_S", 5)
    chat(client)
    assert 0 < seen[0] <= 5
    monkeypatch.setattr(coach, "TURN_BUDGET_S", 75)
    chat(client)
    assert seen[1] <= gemini.REQUEST_TIMEOUT_S


def test_odd_shaped_answers_do_not_crash():
    assert coach.read_candidate({"promptFeedback": "weird", "candidates": "no"}) == ({"role": "model", "parts": []}, [], "")
    assert coach.read_candidate({"candidates": [None]})[2] == ""
    with pytest.raises(gemini.GeminiError):
        coach.read_candidate({"promptFeedback": {"blockReason": "SAFETY"}})


def test_changes_are_off_when_allow_changes_is_omitted(client, db, fake):
    f = fake(text_answer("ok"))
    client.post("/coach/chat", json={"thread": "training", "message": "salut"})
    assert "tools" not in f.payloads[0]


def test_only_the_first_calls_of_one_answer_are_run(client, db, fake):
    many = [("log_today", {"protein_g": 5}) for _ in range(coach.MAX_CALLS_PER_RESPONSE + 4)]
    f = fake(call_answer(*many), text_answer("fait"))
    chat(client, "oui")
    parts = f.payloads[1]["contents"][-1]["parts"]
    assert len(parts) == len(many)  # one answer per call, as Gemini wants
    assert db.query(ProteinEntry).count() == coach.MAX_CALLS_PER_RESPONSE


def test_failed_lookup_does_not_rewrite_the_program(db, monkeypatch):
    restored = []
    monkeypatch.setattr(coach_tools, "restore_program", lambda *a: restored.append(a))
    with pytest.raises(coach_tools.ToolError):
        run(db, "edit_session_exercise", {"session": "HA", "action": "add", "exercise": "Zumba aquatique"})
    assert restored == []


def test_log_today_undo_is_all_or_nothing(db, monkeypatch):
    db.add(WeightEntry(client_id=str(uuid.uuid4()), date=dt.date.today(), kg=85.0,
                       created_at=NOW - dt.timedelta(hours=3)))
    db.commit()
    outcome = run(db, "log_today", {"protein_g": 32, "weight_kg": 84.4})
    real_commit = db.commit

    def failing_commit():
        raise RuntimeError("disk full")
    monkeypatch.setattr(db, "commit", failing_commit)
    with pytest.raises(RuntimeError):
        undo(db, "log_today", outcome)
    monkeypatch.setattr(db, "commit", real_commit)
    db.expire_all()
    assert db.query(ProteinEntry).count() == 1  # nothing was half-reverted
    assert [w.kg for w in db.query(WeightEntry)] == [84.4]
    undo(db, "log_today", outcome)  # and a retry works
    db.expire_all()
    assert db.query(ProteinEntry).count() == 0 and [w.kg for w in db.query(WeightEntry)] == [85.0]


def test_calls_beyond_the_per_response_cap_get_their_own_message(client, db, fake):
    many = [("log_today", {"protein_g": 5}) for _ in range(coach.MAX_CALLS_PER_RESPONSE + 1)]
    f = fake(call_answer(*many), text_answer("fait"))
    chat(client, "oui")
    last = f.payloads[1]["contents"][-1]["parts"][-1]["functionResponse"]["response"]
    assert "appels dans une seule réponse" in last["error"]
