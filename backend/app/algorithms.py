"""Pure business logic from SPEC §5. No database, no web — just inputs → outputs.

Keeping these as plain functions means they're trivial to unit-test
and can be reused by the API, the coach, or a script.
"""

from dataclasses import dataclass
from typing import Literal

# Each check-in question has 3 emoji buttons, stored as 1 / 3 / 5.
# That spacing is what makes the spec's score range 4–20 work:
#   3 questions × (1..5) + (6 − soreness) where soreness is 1..5 → 4..20
ANSWER_VALUES = (1, 3, 5)

Level = Literal["vert", "orange", "rouge"]
Format = Literal["complete", "minimale", "express"]


@dataclass
class Readiness:
    score: int
    level: Level
    format: Format


@dataclass(frozen=True)  # frozen: can't be modified after creation
class PlannedExercise:
    """One line of a session plan, e.g. Squat — 4 sets of 5-6 reps."""
    slug: str
    name: str
    sets: int
    reps: str


def compute_readiness(
    energy: int, sleep: int, motivation: int, soreness: int, minutes: int
) -> Readiness:
    """Traffic-light readiness from the morning check-in (SPEC §5.1).

    KEEP IN SYNC with computeReadiness() in frontend/logic.js (offline check-in).
    """
    for name, value in [("energy", energy), ("sleep", sleep),
                        ("motivation", motivation), ("soreness", soreness)]:
        if value not in ANSWER_VALUES:
            raise ValueError(f"{name} must be one of {ANSWER_VALUES}, got {value}")

    score = energy + sleep + motivation + (6 - soreness)

    if score >= 15:
        level = "vert"
    elif score >= 10:
        level = "orange"
    else:
        level = "rouge"

    if minutes <= 30:
        format = "express"
    elif level == "rouge":
        format = "express"
    elif level == "vert" and minutes >= 60:  # deux conditions à la fois
        format = "complete"
    else:                                    # tous les autres cas
        format = "minimale"

    return Readiness(score, level, format)


def apply_format(exercises: list[PlannedExercise], format: Format) -> list[PlannedExercise]:
    """Shorten a session to the day's format (SPEC §5.2).

    complete → every exercise, unchanged
    minimale → first 3 exercises, max 3 sets each
    express  → first 2 exercises, max 3 sets each
    """
    if format == "complete":
        return exercises
    elif format == "minimale":
        count = 3
    else:
        count = 2

    kept = exercises[:count]

    result = []
    for exercise in kept:
        result.append(PlannedExercise(slug=exercise.slug,
                                      name=exercise.name,
                                      sets=min(exercise.sets, 3),
                                      reps=exercise.reps))

    return result


def rest_seconds(focus: str, is_isolation: bool) -> int:
    """Automatic rest timer after a validated set (SPEC §4.2)."""
    if is_isolation:
        return 60
    if focus == "Force":
        return 150
    return 90


@dataclass(frozen=True)
class LoggedSet:
    """One set as logged at the gym. weight/reps stay None until typed in."""
    weight: float | None
    reps: int | None
    done: bool


def tonnage(sets: list[LoggedSet]) -> float:
    """Total weight lifted in a workout: weight × reps, added up (SPEC §4.2 recap).

    Only validated sets count. A set with no weight or no reps
    (e.g. a bodyweight exercise) counts for 0.

    KEEP IN SYNC with tonnage() in frontend/logic.js (offline workout recap).
    """
    total = 0
    for s in sets:
        if s.done and s.weight is not None and s.reps is not None:
            total += s.weight * s.reps
    return total


def latest_weights(history: list[tuple[str, float]]) -> dict[str, float]:
    """Last weight used on each exercise, to prefill the next workout offline.

    `history` is every validated set as (exercise slug, weight), OLDEST FIRST, e.g.
        [("squat", 80), ("squat", 82.5), ("rowing-barre", 60)]
    → {"squat": 82.5, "rowing-barre": 60}
    """
    weights = {}
    for slug, weight in history:
        weights[slug] = weight  # later entries overwrite earlier ones: the latest wins
    return weights