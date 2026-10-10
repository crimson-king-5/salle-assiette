"""Server-side numbers for the Progrès screen and the progression tips (SPEC §4.8, §5.3).

The SQL lives here; the rules are the pure functions of algorithms.py.
"""

import datetime as dt

from sqlalchemy import select

from app.algorithms import (
    CARDIO_WEEK_TARGET_MIN, HistorySet, LoggedSet, cardio_minutes, progression_tip, tonnage,
    week_start, weeks_held, weight_records,
)
from app.database import DbSession
from app.models import Exercise, SessionExercise, Workout, WorkoutExercise, WorkoutSet
from app.schemas import ProgressOut, RecentWorkoutOut, RecordOut

RECENT_WORKOUTS = 6


def _history_rows(db: DbSession):
    """Every set of every FINISHED workout, oldest workout first."""
    return db.execute(
        select(
            Workout.id, Workout.date, WorkoutExercise.exercise_slug, WorkoutExercise.target_reps,
            Exercise.name_fr, Exercise.is_cardio,
            WorkoutSet.weight, WorkoutSet.reps, WorkoutSet.done,
        )
        .join(WorkoutExercise, WorkoutExercise.workout_id == Workout.id)
        .join(Exercise, Exercise.slug == WorkoutExercise.exercise_slug)
        .join(WorkoutSet, WorkoutSet.workout_exercise_id == WorkoutExercise.id)
        .where(Workout.finished_at.is_not(None))
        .order_by(Workout.finished_at, Workout.id, WorkoutExercise.position, WorkoutSet.position)
    ).all()


def build_progress(db: DbSession, today: dt.date, sessions_goal: int) -> ProgressOut:
    finished = list(db.scalars(
        select(Workout).where(Workout.finished_at.is_not(None))
        .order_by(Workout.finished_at.desc(), Workout.id.desc())
    ).all())

    history, names, week_cardio = [], {}, []
    monday = week_start(today)
    for _, date, slug, _, name, is_cardio, weight, reps, done in _history_rows(db):
        names[slug] = name
        history.append(HistorySet(slug, weight, reps, date, bool(is_cardio), bool(done)))
        if is_cardio and done and monday <= date < monday + dt.timedelta(days=7):
            week_cardio.append(LoggedSet(weight, reps, done))

    records = [
        RecordOut(slug=slug, name=names[slug], weight=s.weight, reps=s.reps or 0, date=s.date)
        for slug, s in weight_records(history).items()
    ]
    records.sort(key=lambda r: r.name.casefold())

    recent = []
    for w in finished[:RECENT_WORKOUTS]:
        all_sets = [(we.exercise.is_cardio, s) for we in w.exercises for s in we.sets]
        recent.append(RecentWorkoutOut(
            id=w.id, name=w.name, date=w.date, format=w.format,
            duration_min=round((w.finished_at - w.started_at).total_seconds() / 60),
            sets_done=sum(1 for _, s in all_sets if s.done),
            # Cardio is excluded from the tonnage (SPEC §5.4)
            tonnage=tonnage([LoggedSet(s.weight, s.reps, s.done) for cardio, s in all_sets if not cardio]),
        ))

    return ProgressOut(
        total_workouts=len(finished),
        weeks_held=weeks_held([w.date for w in finished], today, sessions_goal),
        cardio_minutes_week=cardio_minutes(week_cardio),
        cardio_week_target_min=CARDIO_WEEK_TARGET_MIN,
        records=records,
        recent_workouts=recent,
    )


def build_tips(db: DbSession) -> dict[str, str]:
    """A progression tip for every exercise of the library.

    "Last time" = the last finished workout in which the exercise has at least one validated set.
    """
    per_exercise: dict[tuple[int, str], list[LoggedSet]] = {}
    last_target: dict[str, str] = {}
    for wid, _, slug, target, _, _, weight, reps, done in _history_rows(db):
        per_exercise.setdefault((wid, slug), []).append(LoggedSet(weight, reps, bool(done)))
        last_target[slug] = target
    last_sets: dict[str, list[LoggedSet]] = {}
    for (_, slug), sets in per_exercise.items():  # insertion order = oldest workout first
        if any(s.done for s in sets):
            last_sets[slug] = sets

    program_reps: dict[str, str] = {}
    for slug, reps in db.execute(
        select(SessionExercise.exercise_slug, SessionExercise.reps).order_by(SessionExercise.id)
    ):
        program_reps.setdefault(slug, reps)

    return {
        ex.slug: progression_tip(
            last_sets.get(ex.slug, []),
            program_reps.get(ex.slug) or last_target.get(ex.slug, ""),
            ex.is_isolation, ex.is_lower, ex.is_cardio,
        )
        for ex in db.scalars(select(Exercise).order_by(Exercise.slug))
    }
