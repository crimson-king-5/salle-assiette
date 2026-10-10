"""Shapes of the JSON the API receives and sends back.

Pydantic checks incoming data against these classes automatically:
a wrong type or a value outside the allowed ones → FastAPI answers 422
before our code even runs.
"""

import datetime as dt
import uuid
from typing import Literal

from pydantic import BaseModel, Field, NaiveDatetime, field_validator, model_validator

from app.algorithms import Equipment, Focus, MuscleGroup, Objective, PlannedExercise

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
    is_cardio: bool = False
    cardio_unit: str | None = None


class SnapshotSession(BaseModel):
    id: str
    name: str
    focus: str
    formats: dict[FormatName, list[SnapshotExercise]]  # the session in each format
    startable: bool = True   # False when the session has no exercise: don't offer to start it


class ProteinEntryOut(BaseModel):
    """One line of today's protein journal."""
    id: str            # the phone's UUID (client_id)
    date: dt.date
    label: str
    grams: int
    created_at: dt.datetime


class WeightOut(BaseModel):
    """One weigh-in of the chart."""
    id: str            # the phone's UUID (client_id)
    date: dt.date
    kg: float
    created_at: dt.datetime  # when it was made: the phone uses it to pick one weigh-in per day


class RecordOut(BaseModel):
    """Best set on one exercise (cardio excluded)."""
    slug: str
    name: str
    weight: float
    reps: int
    date: dt.date


class RecentWorkoutOut(BaseModel):
    id: int
    name: str
    date: dt.date
    format: FormatName
    duration_min: int
    sets_done: int
    tonnage: float


class ProgressOut(BaseModel):
    """Numbers of the Progrès screen (SPEC §4.8), computed by the server."""
    total_workouts: int
    weeks_held: int                 # "semaines tenues d'affilée" (SPEC §5.5)
    cardio_minutes_week: int
    cardio_week_target_min: int     # reference line, 150
    records: list[RecordOut]        # alphabetical by exercise name
    recent_workouts: list[RecentWorkoutOut]  # last 6 finished, newest first


class SnapshotOut(BaseModel):
    """Everything the phone needs to work with the PC switched off."""
    generated_at: dt.datetime
    sessions: list[SnapshotSession]  # rotation order
    next_session_id: str
    last_weights: dict[str, float]   # exercise slug → last weight used
    checkin_today: CheckinOut | None
    week_count: int
    week_goal: int
    protein_goal_g: int
    protein_today: list[ProteinEntryOut]
    target_weight_kg: float
    objective: Objective
    coach_enabled: bool
    kcal_hint: str
    weights: list[WeightOut]         # last 120 days, oldest first, one per day
    progress: ProgressOut
    tips: dict[str, str]             # exercise slug → progression tip (French)


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


class ProteinSyncIn(BaseModel):
    """One journal line made on the phone (or corrected there)."""
    date: dt.date
    label: str = Field(max_length=80)
    grams: int = Field(ge=0, le=300)  # grams of protein in the line
    created_at: NaiveDatetime

    @field_validator("label")
    @classmethod
    def label_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("label must not be empty")
        try:
            value.encode("utf-8")  # a lone surrogate (broken emoji) can't be stored: refuse it cleanly
        except UnicodeEncodeError:
            raise ValueError("label contains invalid characters") from None
        return value


class SyncOut(BaseModel):
    id: int
    created: bool  # False when the document was already on the server (replaced)


class WeightSyncIn(BaseModel):
    """One weigh-in made on the phone."""
    date: dt.date
    kg: float = Field(ge=30, le=250)
    created_at: NaiveDatetime


# --- Settings ---

class ProfileUpdateIn(BaseModel):
    """Every field is optional: only the ones sent are changed."""
    objective: Objective | None = None
    sessions_goal: int | None = Field(default=None, ge=1, le=7)
    protein_goal_g: int | None = Field(default=None, ge=60, le=300)
    target_weight_kg: float | None = Field(default=None, ge=30, le=250)
    coach_enabled: bool | None = None


class ProfileOut(BaseModel):
    objective: Objective
    sessions_goal: int
    protein_goal_g: int
    target_weight_kg: float
    coach_enabled: bool
    kcal_hint: str
    kcal_hints: dict[str, str]              # objective → hint, for the picker
    latest_weight_kg: float | None
    auto_protein_goal_g: int | None         # from latest weight + objective; None without weigh-in


# --- Exercise library and program editor ---

def _clean_text(value: str) -> str:
    value = value.strip()
    try:
        value.encode("utf-8")  # a lone surrogate can't be stored
    except UnicodeEncodeError:
        raise ValueError("invalid characters") from None
    return value


class ExerciseOut(BaseModel):
    slug: str
    name: str
    muscle_group: str
    equipment: str
    is_isolation: bool
    is_lower: bool
    is_cardio: bool
    cardio_unit: str | None
    is_custom: bool
    needs_review: bool   # imported with guessed attributes; cleared by PUT /exercises/{slug}
    aliases: list[str]


class ExerciseCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    muscle_group: MuscleGroup
    equipment: Equipment
    is_isolation: bool = False
    is_lower: bool = False
    is_cardio: bool = False  # forced to True when muscle_group is "Cardio"
    cardio_unit: str | None = Field(default=None, max_length=20)

    @field_validator("name")
    @classmethod
    def name_not_blank(cls, value: str) -> str:
        value = _clean_text(value)
        if not value:
            raise ValueError("name must not be empty")
        return value


class SessionExerciseIn(BaseModel):
    slug: str = Field(max_length=80)
    sets: int = Field(ge=1, le=10)
    reps: str = Field(min_length=1, max_length=20)

    @field_validator("reps")
    @classmethod
    def reps_not_blank(cls, value: str) -> str:
        value = _clean_text(value)
        if not value:
            raise ValueError("reps must not be empty")
        return value


class SessionNameMixin(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    focus: Focus

    @field_validator("name")
    @classmethod
    def name_not_blank(cls, value: str) -> str:
        value = _clean_text(value)
        if not value:
            raise ValueError("name must not be empty")
        return value


SessionPreset = Literal[
    "vide", "pecs-epaules-triceps", "dos-biceps-avant-bras", "jambes-abdos", "full-body",
    "cardio-gainage",
]


class SessionCreateIn(SessionNameMixin):
    template: SessionPreset = "vide"


class SessionUpdateIn(SessionNameMixin):
    exercises: list[SessionExerciseIn] = Field(max_length=20)


class RotationIn(BaseModel):
    order: list[str] = Field(min_length=1, max_length=100)  # every session id, in the new order


class SessionPresetOut(BaseModel):
    id: SessionPreset
    label: str
