"""Shapes of the JSON the API receives and sends back.

Pydantic checks incoming data against these classes automatically:
a wrong type or a value outside the allowed ones → FastAPI answers 422
before our code even runs.
"""

import datetime as dt
from typing import Literal

from pydantic import BaseModel, Field

from app.algorithms import PlannedExercise

Answer = Literal[1, 3, 5]  # the 3 emoji buttons of each check-in question


class CheckinIn(BaseModel):
    """What the phone sends after the 5-tap morning check-in."""
    energy: Answer
    sleep: Answer
    motivation: Answer
    soreness: Answer
    minutes: int = Field(ge=15, le=180)  # time available today


class CheckinOut(BaseModel):
    """What the API sends back: the traffic light and the workout format."""
    score: int
    level: Literal["vert", "orange", "rouge"]
    format: Literal["complete", "minimale", "express"]


class SessionOut(BaseModel):
    """A session of the rotation with its exercises (possibly shortened)."""
    id: str
    name: str
    focus: str
    exercises: list[PlannedExercise]


# --- Workouts (the "Séance" screen) ---

FormatName = Literal["complete", "minimale", "express"]


class WorkoutStartIn(BaseModel):
    session_id: str
    format: FormatName


class SetOut(BaseModel):
    id: int
    weight: float | None
    reps: int | None
    done: bool


class WorkoutExerciseOut(BaseModel):
    slug: str
    name: str
    target_reps: str        # e.g. "8-12"
    rest_seconds: int       # rest timer after each validated set
    last_weight: float | None  # last weight used on this exercise, to prefill
    sets: list[SetOut]


class WorkoutOut(BaseModel):
    id: int
    session_id: str
    name: str
    focus: str
    format: FormatName
    started_at: dt.datetime
    exercises: list[WorkoutExerciseOut]


class SetUpdateIn(BaseModel):
    """Sent when a set is validated (done=true) or un-validated (done=false)."""
    weight: float | None = Field(default=None, ge=0, le=500)
    reps: int | None = Field(default=None, ge=0, le=100)
    done: bool


class WorkoutSummaryOut(BaseModel):
    """The end-of-workout recap (SPEC §4.2 "Fin de séance")."""
    sets_done: int
    tonnage: float
    duration_min: int
    week_count: int   # finished workouts this week, including this one
    week_goal: int
