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


def compute_readiness(
    energy: int, sleep: int, motivation: int, soreness: int, minutes: int
) -> Readiness:
    """Traffic-light readiness from the morning check-in (SPEC §5.1)."""
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