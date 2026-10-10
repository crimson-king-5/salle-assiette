"""GET /export: every piece of the user's data as one JSON file (SPEC §4.9)."""

import datetime as dt

from fastapi import APIRouter
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from sqlalchemy import select

from app.algorithms import KCAL_HINTS
from app.database import DbSession
from app.exercises import aliases_of
from app.models import Checkin, Exercise, ProteinEntry, SessionTemplate, WeightEntry, Workout, WorkoutExercise
from app.queries import get_profile

router = APIRouter(tags=["export"])


@router.get("/export")
def export_data(db: DbSession) -> JSONResponse:
    """Download everything (profile, program, workouts with sets, check-ins, protein, weights)."""
    profile = get_profile(db)
    templates = db.scalars(select(SessionTemplate).order_by(SessionTemplate.position)).all()
    # The imported library (1 300+ rows) can be re-imported from its dataset: only the ones the program
    # or the history actually use are listed, so slugs in the file always have their exercise
    used = {se.exercise_slug for t in templates for se in t.exercises}
    used |= set(db.scalars(select(WorkoutExercise.exercise_slug).distinct()))
    data = {
        "app": "salle-et-assiette",
        "version": 1,
        "exported_at": dt.datetime.now(),
        "profile": {
            "objective": profile.objective,
            "sessions_goal": profile.sessions_goal,
            "protein_goal_g": profile.protein_goal_g,
            "target_weight_kg": profile.target_weight_kg,
            "coach_enabled": profile.coach_enabled,
            "kcal_hint": KCAL_HINTS.get(profile.objective, ""),
        },
        "exercises": [
            {
                "slug": e.slug, "name": e.name_fr, "muscle_group": e.muscle_group,
                "equipment": e.equipment, "is_isolation": e.is_isolation, "is_lower": e.is_lower,
                "is_cardio": e.is_cardio, "cardio_unit": e.cardio_unit, "is_custom": e.is_custom,
                "aliases": aliases_of(e), "dataset_id": e.dataset_id,
            }
            for e in db.scalars(select(Exercise).order_by(Exercise.slug))
            if not e.is_library or e.slug in used
        ],
        "program": {
            "rotation": [t.id for t in templates],
            "sessions": [
                {
                    "id": t.id, "name": t.name, "focus": t.focus, "position": t.position,
                    "exercises": [
                        {"slug": se.exercise_slug, "sets": se.sets, "reps": se.reps}
                        for se in t.exercises
                    ],
                }
                for t in templates
            ],
        },
        "workouts": [
            {
                "id": w.client_id, "session_id": w.session_id, "name": w.name, "focus": w.focus,
                "format": w.format, "date": w.date, "started_at": w.started_at,
                "finished_at": w.finished_at,
                "exercises": [
                    {
                        "slug": we.exercise_slug, "name": we.exercise.name_fr,
                        "target_reps": we.target_reps,
                        "sets": [{"weight": s.weight, "reps": s.reps, "done": s.done} for s in we.sets],
                    }
                    for we in w.exercises
                ],
            }
            for w in db.scalars(select(Workout).order_by(Workout.date, Workout.id))
        ],
        "checkins": [
            {
                "id": c.client_id, "date": c.date, "created_at": c.created_at, "energy": c.energy,
                "sleep": c.sleep, "motivation": c.motivation, "soreness": c.soreness,
                "minutes": c.minutes, "score": c.score, "level": c.level, "format": c.format,
            }
            for c in db.scalars(select(Checkin).order_by(Checkin.date, Checkin.id))
        ],
        "protein": [
            {"id": p.client_id, "date": p.date, "label": p.label, "grams": p.grams,
             "created_at": p.created_at}
            for p in db.scalars(select(ProteinEntry).order_by(ProteinEntry.date, ProteinEntry.id))
        ],
        "weights": [
            {"id": w.client_id, "date": w.date, "kg": w.kg, "created_at": w.created_at}
            for w in db.scalars(select(WeightEntry).order_by(WeightEntry.date, WeightEntry.id))
        ],
    }
    filename = f"salle-assiette-{dt.date.today().isoformat()}.json"
    return JSONResponse(
        jsonable_encoder(data),
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
