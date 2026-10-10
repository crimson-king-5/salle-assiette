"""Program editor routes (SPEC §4.3), online only.

Past workouts copy the session's name and exercises (see Workout), so renaming or deleting a
session template never touches the history.
"""

import secrets

from fastapi import APIRouter, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import DbSession
from app.models import Exercise, SessionExercise, SessionTemplate, to_planned
from app.schemas import (
    RotationIn, SessionCreateIn, SessionExerciseIn, SessionOut, SessionPresetOut, SessionUpdateIn,
)

router = APIRouter(tags=["program"])

# preset id → (label, [(exercise slug, sets, reps)])
PRESETS = {
    "vide": ("Séance vide", []),
    "pecs-epaules-triceps": ("Pecs · épaules · triceps", [
        ("developpe-couche-barre", 4, "6-8"), ("developpe-incline-halteres", 3, "8-12"),
        ("developpe-militaire", 3, "8-10"), ("elevations-laterales", 3, "12-15"),
        ("extension-triceps-poulie", 3, "10-12"),
    ]),
    "dos-biceps-avant-bras": ("Dos · biceps · avant-bras", [
        ("tirage-vertical", 4, "8-10"), ("rowing-barre", 4, "6-8"),
        ("tirage-horizontal", 3, "10-12"), ("face-pull", 3, "15-20"),
        ("curl-halteres", 3, "10-12"), ("curl-incline", 2, "12-15"),
    ]),
    "jambes-abdos": ("Jambes · abdos", [
        ("squat", 4, "6-8"), ("presse-cuisses", 3, "10-12"), ("leg-curl", 3, "10-12"),
        ("leg-extension", 3, "12-15"), ("mollets-debout", 4, "12-20"),
    ]),
    "full-body": ("Full body", [
        ("squat", 3, "6-8"), ("developpe-couche-barre", 3, "6-8"), ("rowing-barre", 3, "8-10"),
        ("developpe-militaire", 2, "8-10"), ("sdt-roumain", 2, "8-10"), ("curl-halteres", 2, "10-12"),
    ]),
}
CARDIO_PRESET = ("cardio-gainage", "Cardio · gainage")


def cardio_preset_lines(db: Session) -> list[tuple[str, int, str]]:
    """"Cardio · gainage": two cardio blocks + core work. Empty when the library has no cardio."""
    cardio = db.scalars(
        select(Exercise).where(Exercise.is_cardio).order_by(Exercise.slug).limit(2)
    ).all()
    if not cardio:
        return []
    core = db.scalars(
        select(Exercise).where(Exercise.muscle_group == "Abdos").order_by(Exercise.slug).limit(2)
    ).all()
    return [(e.slug, 2, "20") for e in cardio] + [(e.slug, 3, "30-45") for e in core]


def to_session_out(template: SessionTemplate) -> SessionOut:
    return SessionOut(id=template.id, name=template.name, focus=template.focus,
                      exercises=to_planned(template))


def new_session_id(db: Session) -> str:
    """"S" + 6 random hex digits, never already used."""
    while True:
        candidate = "S" + secrets.token_hex(3)
        if db.get(SessionTemplate, candidate) is None:
            return candidate


def make_exercise_rows(db: Session, lines: list[SessionExerciseIn]) -> list[SessionExercise]:
    known = set(db.scalars(select(Exercise.slug)))
    for line in lines:
        if line.slug not in known:
            raise HTTPException(status_code=422, detail=f"Exercice inconnu : {line.slug}")
    return [
        SessionExercise(position=i, exercise_slug=l.slug, sets=l.sets, reps=l.reps)
        for i, l in enumerate(lines)
    ]


def ordered_templates(db: Session) -> list[SessionTemplate]:
    return list(db.scalars(select(SessionTemplate).order_by(SessionTemplate.position)))


@router.get("/session-templates")
def list_session_presets(db: DbSession) -> list[SessionPresetOut]:
    """Presets offered when creating a session ("Cardio · gainage" only if the library has cardio)."""
    presets = [SessionPresetOut(id=pid, label=label) for pid, (label, _) in PRESETS.items()]
    if cardio_preset_lines(db):
        presets.append(SessionPresetOut(id=CARDIO_PRESET[0], label=CARDIO_PRESET[1]))
    return presets


@router.post("/sessions", status_code=201)
def create_session(body: SessionCreateIn, db: DbSession) -> SessionOut:
    """New session, added at the end of the rotation, optionally filled from a preset."""
    if body.template == CARDIO_PRESET[0]:
        lines = cardio_preset_lines(db)
        if not lines:
            raise HTTPException(status_code=422, detail="Aucun exercice de cardio dans la bibliothèque")
    else:
        known = set(db.scalars(select(Exercise.slug)))
        # a preset silently skips an exercise that is not in the library
        lines = [l for l in PRESETS[body.template][1] if l[0] in known]
    template = SessionTemplate(
        id=new_session_id(db), name=body.name, focus=body.focus,
        position=len(ordered_templates(db)),
        exercises=[
            SessionExercise(position=i, exercise_slug=slug, sets=sets, reps=reps)
            for i, (slug, sets, reps) in enumerate(lines)
        ],
    )
    db.add(template)
    db.commit()
    return to_session_out(template)


@router.put("/sessions/{session_id}")
def update_session(session_id: str, body: SessionUpdateIn, db: DbSession) -> SessionOut:
    """Rename, change the type and replace the whole ordered exercise list."""
    template = db.get(SessionTemplate, session_id)
    if template is None:
        raise HTTPException(status_code=404, detail="Séance inconnue")
    rows = make_exercise_rows(db, body.exercises)
    template.name = body.name
    template.focus = body.focus
    template.exercises = rows  # the old rows are deleted (delete-orphan)
    db.commit()
    return to_session_out(template)


@router.delete("/sessions/{session_id}", status_code=204)
def delete_session(session_id: str, db: DbSession) -> Response:
    """Delete a session from the rotation. Past workouts keep their own copy of it."""
    templates = ordered_templates(db)
    template = next((t for t in templates if t.id == session_id), None)
    if template is None:
        raise HTTPException(status_code=404, detail="Séance inconnue")
    if len(templates) == 1:
        raise HTTPException(status_code=409, detail="Il faut garder au moins une séance")
    db.delete(template)
    for position, t in enumerate(t for t in templates if t.id != session_id):
        t.position = position
    db.commit()
    return Response(status_code=204)


@router.put("/rotation")
def set_rotation(body: RotationIn, db: DbSession) -> list[SessionOut]:
    """Reorder the rotation. `order` must list every session id exactly once."""
    by_id = {t.id: t for t in ordered_templates(db)}
    if len(set(body.order)) != len(body.order):
        raise HTTPException(status_code=422, detail="Une séance apparaît plusieurs fois")
    if set(body.order) != set(by_id):
        raise HTTPException(
            status_code=422, detail="La liste doit contenir toutes les séances, une seule fois chacune")
    for position, session_id in enumerate(body.order):
        by_id[session_id].position = position
    db.commit()
    return [to_session_out(by_id[i]) for i in body.order]
