"""Database tables (SPEC §3.2). One class = one table, one attribute = one column."""

import datetime as dt

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

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


class Checkin(Base):
    __tablename__ = "checkin"

    id: Mapped[int] = mapped_column(primary_key=True)  # 1, 2, 3… assigned by the DB
    date: Mapped[dt.date] = mapped_column(index=True)
    created_at: Mapped[dt.datetime]

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
