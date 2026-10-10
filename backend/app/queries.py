"""Small database lookups shared by several routes."""

import datetime as dt

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import (
    Checkin, ProteinEntry, SessionTemplate, UserProfile, WeightEntry, Workout, WorkoutExercise,
    WorkoutSet,
)

PROFILE_ID = 1


def get_profile(db: Session) -> UserProfile:
    """The user's settings. Creates the single row with the defaults if it is missing."""
    profile = db.get(UserProfile, PROFILE_ID)
    if profile is None:
        db.add(UserProfile(id=PROFILE_ID))  # column defaults: recompo, 4 sessions, 160 g, 79 kg
        try:
            db.commit()
        except IntegrityError:
            db.rollback()  # another request created it at the same moment
        profile = db.get(UserProfile, PROFILE_ID)
    return profile


def today_protein(db: Session) -> list[ProteinEntry]:
    """Today's journal lines, oldest first."""
    return list(db.scalars(
        select(ProteinEntry).where(ProteinEntry.date == dt.date.today())
        .order_by(ProteinEntry.created_at, ProteinEntry.id)
    ).all())


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


def weights_since(db: Session, since: dt.date) -> list[WeightEntry]:
    """Weigh-ins from `since` on, oldest first. Several on one day → only the latest made."""
    rows = db.scalars(
        select(WeightEntry).where(WeightEntry.date >= since)
        .order_by(WeightEntry.date, WeightEntry.created_at, WeightEntry.id)
    ).all()
    per_day = {}
    for row in rows:  # sorted ascending, so the last one of a day overwrites the others
        per_day[row.date] = row
    return list(per_day.values())


def latest_weight(db: Session) -> float | None:
    """Most recent weigh-in (latest date, then latest created_at), if any."""
    row = db.scalars(
        select(WeightEntry).order_by(WeightEntry.date.desc(), WeightEntry.created_at.desc(),
                                     WeightEntry.id.desc())
    ).first()
    return row.kg if row else None
