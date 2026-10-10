"""GET /snapshot: one request that gives the phone everything it needs offline.

The phone stores the answer and uses it when the PC is switched off.
"""

import datetime as dt

from fastapi import APIRouter
from sqlalchemy import select

from app.algorithms import apply_format, latest_weights, rest_seconds
from app.database import DbSession
from app.models import SessionTemplate, to_planned
from app.queries import next_session_id, today_checkin, weight_history
from app.schemas import CheckinOut, SnapshotExercise, SnapshotOut, SnapshotSession
from app.workouts import WEEK_GOAL, week_count

router = APIRouter(tags=["offline"])

FORMATS = ("complete", "minimale", "express")


def snapshot_session(template: SessionTemplate) -> SnapshotSession:
    isolation = {se.exercise_slug: se.exercise.is_isolation for se in template.exercises}
    formats = {}
    for fmt in FORMATS:
        formats[fmt] = [
            SnapshotExercise(
                slug=p.slug, name=p.name, sets=p.sets, reps=p.reps,
                rest_seconds=rest_seconds(template.focus, isolation[p.slug]),
            )
            for p in apply_format(to_planned(template), fmt)
        ]
    return SnapshotSession(id=template.id, name=template.name, focus=template.focus, formats=formats)


@router.get("/snapshot")
def get_snapshot(db: DbSession) -> SnapshotOut:
    templates = db.scalars(select(SessionTemplate).order_by(SessionTemplate.position)).all()
    checkin = today_checkin(db)
    return SnapshotOut(
        generated_at=dt.datetime.now(),
        sessions=[snapshot_session(t) for t in templates],
        next_session_id=next_session_id(db),
        last_weights=latest_weights(weight_history(db)),
        checkin_today=(
            CheckinOut(score=checkin.score, level=checkin.level, format=checkin.format)
            if checkin else None
        ),
        week_count=week_count(db, dt.date.today()),
        week_goal=WEEK_GOAL,
    )
