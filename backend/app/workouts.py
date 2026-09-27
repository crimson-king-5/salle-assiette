"""Workout routes: start, log sets, finish or abandon (SPEC §4.2).

A router is a group of routes kept in its own file;
main.py plugs it in with app.include_router().
"""

import datetime as dt

from fastapi import APIRouter, HTTPException, Response
from sqlalchemy import select

from app.algorithms import LoggedSet, apply_format, rest_seconds, tonnage
from app.database import DbSession
from app.models import SessionTemplate, Workout, WorkoutExercise, WorkoutSet, to_planned
from app.schemas import (
    SetOut, SetUpdateIn, WorkoutExerciseOut, WorkoutOut, WorkoutStartIn, WorkoutSummaryOut,
)

router = APIRouter(prefix="/workouts", tags=["workouts"])

WEEK_GOAL = 4  # SPEC §1; becomes a user setting with the Réglages screen


def find_current(db: DbSession) -> Workout | None:
    """The workout in progress, if any (started, not finished)."""
    return db.scalars(
        select(Workout).where(Workout.finished_at.is_(None)).order_by(Workout.id.desc())
    ).first()


def last_weight(db: DbSession, slug: str, before_workout_id: int) -> float | None:
    """Weight of the most recent validated set on this exercise in a finished workout."""
    return db.scalars(
        select(WorkoutSet.weight)
        .join(WorkoutExercise, WorkoutSet.workout_exercise_id == WorkoutExercise.id)
        .join(Workout, WorkoutExercise.workout_id == Workout.id)
        .where(
            WorkoutExercise.exercise_slug == slug,
            Workout.id != before_workout_id,
            Workout.finished_at.is_not(None),
            WorkoutSet.done,
            WorkoutSet.weight.is_not(None),
        )
        .order_by(Workout.finished_at.desc(), WorkoutSet.position.desc())
    ).first()


def to_out(db: DbSession, workout: Workout) -> WorkoutOut:
    return WorkoutOut(
        id=workout.id,
        session_id=workout.session_id,
        name=workout.name,
        focus=workout.focus,
        format=workout.format,
        started_at=workout.started_at,
        exercises=[
            WorkoutExerciseOut(
                slug=we.exercise_slug,
                name=we.exercise.name_fr,
                target_reps=we.target_reps,
                rest_seconds=rest_seconds(workout.focus, we.exercise.is_isolation),
                last_weight=last_weight(db, we.exercise_slug, workout.id),
                sets=[SetOut(id=s.id, weight=s.weight, reps=s.reps, done=s.done) for s in we.sets],
            )
            for we in workout.exercises
        ],
    )


@router.post("", status_code=201)
def start_workout(body: WorkoutStartIn, db: DbSession) -> WorkoutOut:
    """Create a workout from a session template, shortened to the day's format."""
    if find_current(db) is not None:
        raise HTTPException(status_code=409, detail="Une séance est déjà en cours")
    template = db.get(SessionTemplate, body.session_id)
    if template is None:
        raise HTTPException(status_code=404, detail="Séance inconnue")

    # Same shortening rule as the Today screen
    planned = apply_format(to_planned(template), body.format)
    now = dt.datetime.now()
    workout = Workout(
        session_id=template.id, name=template.name, focus=template.focus,
        format=body.format, date=now.date(), started_at=now,
        exercises=[
            WorkoutExercise(
                position=i, exercise_slug=p.slug, target_reps=p.reps,
                sets=[WorkoutSet(position=n, done=False) for n in range(p.sets)],
            )
            for i, p in enumerate(planned)
        ],
    )
    db.add(workout)
    db.commit()
    return to_out(db, workout)


@router.get("/current")
def get_current_workout(db: DbSession) -> WorkoutOut:
    """The workout in progress, so the app can offer "Reprendre"."""
    workout = find_current(db)
    if workout is None:
        raise HTTPException(status_code=404, detail="Aucune séance en cours")
    return to_out(db, workout)


@router.patch("/sets/{set_id}")
def update_set(set_id: int, body: SetUpdateIn, db: DbSession) -> SetOut:
    """Validate (1 tap) or un-validate a set, with its weight and reps."""
    workout_set = db.get(WorkoutSet, set_id)
    if workout_set is None:
        raise HTTPException(status_code=404, detail="Série inconnue")
    workout_set.weight = body.weight
    workout_set.reps = body.reps
    workout_set.done = body.done
    db.commit()
    return SetOut(id=workout_set.id, weight=workout_set.weight,
                  reps=workout_set.reps, done=workout_set.done)


@router.post("/{workout_id}/finish")
def finish_workout(workout_id: int, db: DbSession) -> WorkoutSummaryOut:
    """End the workout and return the recap."""
    workout = db.get(Workout, workout_id)
    if workout is None:
        raise HTTPException(status_code=404, detail="Séance inconnue")
    if workout.finished_at is None:
        workout.finished_at = dt.datetime.now()
        db.commit()

    logged = [
        LoggedSet(weight=s.weight, reps=s.reps, done=s.done)
        for we in workout.exercises for s in we.sets
    ]
    monday = workout.date - dt.timedelta(days=workout.date.weekday())
    week_count = len(db.scalars(
        select(Workout.id).where(
            Workout.finished_at.is_not(None),
            Workout.date >= monday,
            Workout.date < monday + dt.timedelta(days=7),
        )
    ).all())
    duration = workout.finished_at - workout.started_at
    return WorkoutSummaryOut(
        sets_done=sum(1 for s in logged if s.done),
        tonnage=tonnage(logged),
        duration_min=round(duration.total_seconds() / 60),
        week_count=week_count,
        week_goal=WEEK_GOAL,
    )


@router.delete("/{workout_id}", status_code=204)
def abandon_workout(workout_id: int, db: DbSession) -> Response:
    """Abandon: the workout and its sets are deleted."""
    workout = db.get(Workout, workout_id)
    if workout is None:
        raise HTTPException(status_code=404, detail="Séance inconnue")
    db.delete(workout)
    db.commit()
    return Response(status_code=204)
