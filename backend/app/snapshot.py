"""GET /snapshot: one request that gives the phone everything it needs offline.

The phone stores the answer and uses it when the PC is switched off.
"""

import datetime as dt

from fastapi import APIRouter
from sqlalchemy import select

from app.algorithms import KCAL_HINTS, apply_format, latest_weights, rest_seconds
from app.database import DbSession
from app.exercises import to_exercise_detail
from app.library import media_file
from app.models import SessionTemplate, to_planned
from app.progress import build_progress, build_tips
from app.queries import (
    get_profile, next_session_id, today_checkin, today_protein, weight_history, weights_since,
)
from app.schemas import (
    CheckinOut, ProteinEntryOut, SnapshotExercise, SnapshotOut, SnapshotSession, WeightOut,
)
from app.workouts import week_count

router = APIRouter(tags=["offline"])

FORMATS = ("complete", "minimale", "express")
WEIGHT_DAYS = 120  # how far back the weight chart goes


def snapshot_session(template: SessionTemplate) -> SnapshotSession:
    exercises = {se.exercise_slug: se.exercise for se in template.exercises}
    formats = {}
    for fmt in FORMATS:
        formats[fmt] = [
            SnapshotExercise(
                slug=p.slug, name=p.name, sets=p.sets, reps=p.reps,
                rest_seconds=rest_seconds(template.focus, exercises[p.slug].is_isolation),
                is_cardio=exercises[p.slug].is_cardio, cardio_unit=exercises[p.slug].cardio_unit,
                thumb=media_file(exercises[p.slug].media_image), gif=media_file(exercises[p.slug].media_gif),
            )
            for p in apply_format(to_planned(template), fmt)
        ]
    return SnapshotSession(id=template.id, name=template.name, focus=template.focus, formats=formats,
                           startable=bool(template.exercises))


@router.get("/snapshot")
def get_snapshot(db: DbSession) -> SnapshotOut:
    templates = db.scalars(select(SessionTemplate).order_by(SessionTemplate.position)).all()
    checkin = today_checkin(db)
    profile = get_profile(db)
    today = dt.date.today()
    return SnapshotOut(
        generated_at=dt.datetime.now(),
        sessions=[snapshot_session(t) for t in templates],
        next_session_id=next_session_id(db),
        last_weights=latest_weights(weight_history(db)),
        checkin_today=(
            CheckinOut(score=checkin.score, level=checkin.level, format=checkin.format)
            if checkin else None
        ),
        week_count=week_count(db, today),
        week_goal=profile.sessions_goal,
        protein_goal_g=profile.protein_goal_g,
        protein_today=[
            ProteinEntryOut(id=e.client_id, date=e.date, label=e.label, grams=e.grams, created_at=e.created_at)
            for e in today_protein(db)
        ],
        target_weight_kg=profile.target_weight_kg,
        objective=profile.objective,
        coach_enabled=profile.coach_enabled,
        kcal_hint=KCAL_HINTS.get(profile.objective, ""),
        weights=[
            WeightOut(id=w.client_id, date=w.date, kg=w.kg, created_at=w.created_at)
            for w in weights_since(db, today - dt.timedelta(days=WEIGHT_DAYS))
        ],
        progress=build_progress(db, today, profile.sessions_goal),
        tips=build_tips(db),
        exercise_details={
            se.exercise_slug: to_exercise_detail(se.exercise) for t in templates for se in t.exercises
        },
    )
