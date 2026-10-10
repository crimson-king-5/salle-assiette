"""Database tables (SPEC §3.2). One class = one table, one attribute = one column."""

import datetime as dt

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.algorithms import PlannedExercise
from app.database import Base


class Exercise(Base):
    """The exercise library (SPEC §6). Images, muscles and cues come later."""
    __tablename__ = "exercise"

    slug: Mapped[str] = mapped_column(String(80), primary_key=True)  # e.g. "squat"
    name_fr: Mapped[str] = mapped_column(String(120))
    muscle_group: Mapped[str] = mapped_column(String(40))  # Pectoraux, Dos, Jambes…
    equipment: Mapped[str] = mapped_column(String(40))     # Poids libre, Machine, Poulie…
    is_isolation: Mapped[bool] = mapped_column(default=False)
    is_lower: Mapped[bool] = mapped_column(default=False)


class SessionTemplate(Base):
    """A session of the rotation: Haut A, Bas A… (SPEC §3.2 session_template)."""
    __tablename__ = "session_template"

    id: Mapped[str] = mapped_column(String(20), primary_key=True)  # "HA", "BA"…
    name: Mapped[str] = mapped_column(String(80))
    focus: Mapped[str] = mapped_column(String(20))  # Force | Hypertrophie | Libre
    position: Mapped[int]  # place in the rotation: 0, 1, 2…

    exercises: Mapped[list["SessionExercise"]] = relationship(
        order_by="SessionExercise.position", cascade="all, delete-orphan"
    )


class SessionExercise(Base):
    """One exercise inside a session: which one, in what order, how many sets/reps."""
    __tablename__ = "session_exercise"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("session_template.id"))
    position: Mapped[int]
    exercise_slug: Mapped[str] = mapped_column(ForeignKey("exercise.slug"))
    sets: Mapped[int]
    reps: Mapped[str] = mapped_column(String(20))  # a range like "5-6", so text

    exercise: Mapped[Exercise] = relationship()


def to_planned(template: SessionTemplate) -> list[PlannedExercise]:
    """DB rows → the plain PlannedExercise objects used by the algorithms."""
    return [
        PlannedExercise(slug=se.exercise_slug, name=se.exercise.name_fr, sets=se.sets, reps=se.reps)
        for se in template.exercises
    ]


class Workout(Base):
    """A workout actually done (or in progress) at the gym (SPEC §3.2 workout)."""
    __tablename__ = "workout"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[str] = mapped_column(String(20))  # which template it came from
    name: Mapped[str] = mapped_column(String(80))         # copied: history survives template edits
    focus: Mapped[str] = mapped_column(String(20))
    format: Mapped[str] = mapped_column(String(10))
    date: Mapped[dt.date] = mapped_column(index=True)
    started_at: Mapped[dt.datetime]
    finished_at: Mapped[dt.datetime | None]  # None while the workout is in progress
    # UUID made by the phone (offline sync): sending the same workout twice updates this row
    client_id: Mapped[str | None] = mapped_column(String(36), unique=True, index=True)

    exercises: Mapped[list["WorkoutExercise"]] = relationship(
        order_by="WorkoutExercise.position", cascade="all, delete-orphan"
    )


class WorkoutExercise(Base):
    __tablename__ = "workout_exercise"

    id: Mapped[int] = mapped_column(primary_key=True)
    workout_id: Mapped[int] = mapped_column(ForeignKey("workout.id"))
    position: Mapped[int]
    exercise_slug: Mapped[str] = mapped_column(ForeignKey("exercise.slug"))
    target_reps: Mapped[str] = mapped_column(String(20))

    exercise: Mapped[Exercise] = relationship()
    sets: Mapped[list["WorkoutSet"]] = relationship(
        order_by="WorkoutSet.position", cascade="all, delete-orphan"
    )


class WorkoutSet(Base):
    __tablename__ = "workout_set"

    id: Mapped[int] = mapped_column(primary_key=True)
    workout_exercise_id: Mapped[int] = mapped_column(ForeignKey("workout_exercise.id"))
    position: Mapped[int]
    weight: Mapped[float | None]  # kg; None until typed in
    reps: Mapped[int | None]
    done: Mapped[bool] = mapped_column(default=False)


class Checkin(Base):
    __tablename__ = "checkin"

    id: Mapped[int] = mapped_column(primary_key=True)  # 1, 2, 3… assigned by the DB
    date: Mapped[dt.date] = mapped_column(index=True)
    created_at: Mapped[dt.datetime]
    client_id: Mapped[str | None] = mapped_column(String(36), unique=True, index=True)  # see Workout

    # The answers, as received
    energy: Mapped[int]
    sleep: Mapped[int]
    motivation: Mapped[int]
    soreness: Mapped[int]
    minutes: Mapped[int]

    # The result of compute_readiness()
    score: Mapped[int]
    level: Mapped[str] = mapped_column(String(10))
    format: Mapped[str] = mapped_column(String(10))


class UserProfile(Base):
    """Settings of the one user (SPEC §3.2 user_profile). Always a single row, id = 1.

    The Réglages screen (a later block) will edit these; for now they are the defaults.
    """
    __tablename__ = "user_profile"

    id: Mapped[int] = mapped_column(primary_key=True)  # always PROFILE_ID
    objective: Mapped[str] = mapped_column(String(20), default="recompo")
    sessions_goal: Mapped[int] = mapped_column(default=4)        # workouts per week
    protein_goal_g: Mapped[int] = mapped_column(default=160)     # grams of protein per day
    target_weight_kg: Mapped[float] = mapped_column(default=79.0)


class ProteinTombstone(Base):
    """A deleted journal line's id. Stops a late PUT (a retry already on its way) from bringing it back."""
    __tablename__ = "protein_tombstone"

    client_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    deleted_at: Mapped[dt.datetime]


class ProteinEntry(Base):
    """One line of the protein journal (SPEC §3.2 protein_log)."""
    __tablename__ = "protein_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    # UUID made by the phone: the same line sent twice updates this row (offline sync)
    client_id: Mapped[str] = mapped_column(String(36), unique=True, index=True)
    date: Mapped[dt.date] = mapped_column(index=True)
    label: Mapped[str] = mapped_column(String(80))
    grams: Mapped[int]  # grams of protein (not of food)
    created_at: Mapped[dt.datetime]
