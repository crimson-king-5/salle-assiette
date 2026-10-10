"""Small database lookups shared by several routes."""

import datetime as dt

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Checkin, SessionTemplate, Workout, WorkoutExercise, WorkoutSet


def today_checkin(db: Session) -> Checkin | None:
    """Today's latest check-in (a redone check-in replaces the earlier one).

    Sorted by created_at first: a check-in made offline can reach the server late.
    """
    return db.scalars(
        select(Checkin).where(Checkin.date == dt.date.today())
        .order_by(Checkin.created_at.desc(), Checkin.id.desc())
    ).first()


def next_session_id(db: Session) -> str:
    """Next session of the rotation: the one after the last finished workout."""
    rotation = db.scalars(select(SessionTemplate.id).order_by(SessionTemplate.position)).all()
    last = db.scalars(
        select(Workout.session_id)
        .where(Workout.finished_at.is_not(None))
        .order_by(Workout.finished_at.desc())
    ).first()
    if last not in rotation:  # no workout yet, or its session was deleted
        return rotation[0]
    return rotation[(rotation.index(last) + 1) % len(rotation)]


def weight_history(db: Session) -> list[tuple[str, float]]:
    """Every validated set with a weight, as (exercise slug, weight), oldest first."""
    rows = db.execute(
        select(WorkoutExercise.exercise_slug, WorkoutSet.weight)
        .join(WorkoutExercise, WorkoutSet.workout_exercise_id == WorkoutExercise.id)
        .join(Workout, WorkoutExercise.workout_id == Workout.id)
        .where(Workout.finished_at.is_not(None), WorkoutSet.done, WorkoutSet.weight.is_not(None))
        .order_by(Workout.finished_at, WorkoutSet.position)
    ).all()
    return [(slug, weight) for slug, weight in rows]
