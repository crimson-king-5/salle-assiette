"""Shapes of the JSON the API receives and sends back.

Pydantic checks incoming data against these classes automatically:
a wrong type or a value outside the allowed ones → FastAPI answers 422
before our code even runs.
"""

import datetime as dt
import uuid
from typing import Literal

from pydantic import BaseModel, Field, NaiveDatetime, model_validator

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


class SnapshotExercise(BaseModel):
    slug: str
    name: str
    sets: int
    reps: str
    rest_seconds: int


class SnapshotSession(BaseModel):
    id: str
    name: str
    focus: str
    formats: dict[FormatName, list[SnapshotExercise]]  # the session in each format


class SnapshotOut(BaseModel):
    """Everything the phone needs to work with the PC switched off."""
    generated_at: dt.datetime
    sessions: list[SnapshotSession]  # rotation order
    next_session_id: str
    last_weights: dict[str, float]   # exercise slug → last weight used
    checkin_today: CheckinOut | None
    week_count: int
    week_goal: int


class WorkoutSummaryOut(BaseModel):
    """The end-of-workout recap (SPEC §4.2 "Fin de séance")."""
    sets_done: int
    tonnage: float
    duration_min: int
    week_count: int   # finished workouts this week, including this one
    week_goal: int


# --- Offline sync: documents sent by the phone with PUT ---

# The phone's UUID. FastAPI parses it as a real UUID (422 otherwise); routes store str(client_id),
# which is always the lowercase hyphenated form.
ClientId = uuid.UUID


class SyncSetIn(BaseModel):
    weight: float | None = Field(default=None, ge=0, le=500)
    reps: int | None = Field(default=None, ge=0, le=100)
    done: bool


class SyncExerciseIn(BaseModel):
    slug: str = Field(max_length=80)
    target_reps: str = Field(max_length=20)
    sets: list[SyncSetIn] = Field(max_length=10)


class WorkoutSyncIn(BaseModel):
    """A FINISHED workout, as logged on the phone."""
    session_id: str = Field(max_length=20)  # may point to a template deleted since
    name: str = Field(max_length=80)
    focus: str = Field(max_length=20)
    format: FormatName
    date: dt.date
    started_at: NaiveDatetime   # phone local time, no timezone (like the server's own clock)
    finished_at: NaiveDatetime
    exercises: list[SyncExerciseIn] = Field(max_length=20)

    @model_validator(mode="after")
    def clamp_finish(self):
        # A phone clock that jumped back (DST, manual fix) must not get a real workout refused
        if self.finished_at < self.started_at:
            self.finished_at = self.started_at
        return self


class CheckinSyncIn(CheckinIn):
    """A check-in made on the phone. The result is NOT sent: the server recomputes it."""
    date: dt.date
    created_at: NaiveDatetime


class SyncOut(BaseModel):
    id: int
    created: bool  # False when the document was already on the server (replaced)
