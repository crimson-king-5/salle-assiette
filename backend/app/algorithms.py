"""Pure business logic from SPEC §5. No database, no web — just inputs → outputs.

Keeping these as plain functions means they're trivial to unit-test
and can be reused by the API, the coach, or a script.
"""

import datetime as dt
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from typing import Literal, get_args

# Each check-in question has 3 emoji buttons, stored as 1 / 3 / 5.
# That spacing is what makes the spec's score range 4–20 work:
#   3 questions × (1..5) + (6 − soreness) where soreness is 1..5 → 4..20
ANSWER_VALUES = (1, 3, 5)

Level = Literal["vert", "orange", "rouge"]
Format = Literal["complete", "minimale", "express"]

MuscleGroup = Literal[
    "Pectoraux", "Dos", "Épaules", "Biceps", "Triceps", "Avant-bras", "Abdos", "Jambes", "Cardio",
]
Equipment = Literal["Poids libre", "Machine", "Poulie", "Poids du corps"]
Objective = Literal["recompo", "muscle", "perte", "forme"]
Focus = Literal["Force", "Hypertrophie", "Libre"]
MUSCLE_GROUPS: tuple[str, ...] = get_args(MuscleGroup)
EQUIPMENT_KINDS: tuple[str, ...] = get_args(Equipment)
OBJECTIVES: tuple[str, ...] = get_args(Objective)


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


# --- Progress (SPEC §4.8, §5.3, §5.5) ---

CARDIO_WEEK_TARGET_MIN = 150  # SPEC §4.8 reference line


@dataclass(frozen=True)
class HistorySet:
    """One logged set with the context needed for records."""
    slug: str
    weight: float | None
    reps: int | None
    date: dt.date
    is_cardio: bool
    done: bool = True


def week_start(day: dt.date) -> dt.date:
    """Monday of the week containing `day`."""
    return day - dt.timedelta(days=day.weekday())


def weeks_held(workout_dates: list[dt.date], today: dt.date, sessions_goal: int) -> int:
    """"Semaines tenues d'affilée" (SPEC §5.5).

    Counts consecutive Monday→Sunday weeks, going back, with at least max(2, goal − 1) workouts.
    The CURRENT week counts only when it is already met; when it is not (yet), it is skipped
    rather than breaking the streak, since the week isn't over.
    """
    threshold = max(2, sessions_goal - 1)
    per_week = Counter(week_start(d) for d in workout_dates)
    if not per_week:
        return 0
    this_monday = week_start(today)
    streak = 1 if per_week[this_monday] >= threshold else 0
    earliest = min(per_week)
    cursor = this_monday - dt.timedelta(days=7)
    while cursor >= earliest and per_week[cursor] >= threshold:
        streak += 1
        cursor -= dt.timedelta(days=7)
    return streak


def cardio_minutes(sets: list[LoggedSet]) -> int:
    """Minutes of cardio: cardio sets store minutes in `reps` (SPEC §5.4). Validated sets only."""
    return sum(s.reps or 0 for s in sets if s.done)


def weight_records(sets: list[HistorySet]) -> dict[str, HistorySet]:
    """Best set per exercise: heaviest weight, then most reps (SPEC §4.8).

    Cardio, unvalidated sets and sets without a weight are ignored. On an exact tie the
    earliest set keeps the record (it was achieved first).
    """
    best: dict[str, HistorySet] = {}
    for s in sorted(sets, key=lambda s: s.date):  # stable: same-day sets keep their order
        if s.is_cardio or not s.done or s.weight is None or s.weight <= 0:
            continue
        current = best.get(s.slug)
        if current is None or (s.weight, s.reps or 0) > (current.weight, current.reps or 0):
            best[s.slug] = s
    return best


def format_kg(value: float) -> str:
    """82.5 → "82,5", 85.0 → "85" (French decimal comma)."""
    return f"{round(value, 2):g}".replace(".", ",")


def top_of_range(target_reps: str) -> int | None:
    """Upper end of a rep range: "5-6" → 6, "10" → 10, "AMRAP" → None."""
    numbers = [int(n) for n in re.findall(r"\d+", target_reps)]
    return max(numbers) if numbers else None


def progression_tip(
    last_sets: list[LoggedSet], target_reps: str,
    is_isolation: bool, is_lower: bool, is_cardio: bool,
) -> str:
    """Short French hint for the next time on an exercise (SPEC §5.3).

    `last_sets` = ALL the sets (validated or not) of the last workout that contained the exercise;
    empty, or none validated = never done.
    """
    done = [s for s in last_sets if s.done]

    if is_cardio:
        if not done:
            return "Commence doucement : 10 à 15 min à un rythme où tu peux encore parler"
        total = sum(s.reps or 0 for s in done)
        return f"La dernière fois : {total} min. Ajoute 2 à 5 min ou monte un peu le niveau"

    if not done:
        return "Prends une charge où il te reste 2 à 3 reps en réserve"

    top = top_of_range(target_reps)
    # SPEC §5.3 "toutes les séries": every set of last time validated AND at the top of the range
    at_top = top is not None and all(s.done and (s.reps or 0) >= top for s in last_sets)
    weights = [s.weight for s in done if s.weight]
    if not weights:  # bodyweight: no load to raise
        if at_top:
            return "Tout est au haut de la fourchette : ajoute du lest ou ralentis le mouvement"
        return "Vise une répétition de plus par série"

    working = max(weights)
    if at_top:
        step = 1 if is_isolation else (5 if is_lower else 2.5)
        return f"Tout était au haut de la fourchette : passe à {format_kg(working + step)} kg"
    return f"Garde {format_kg(working)} kg et vise une répétition de plus par série"


# --- Settings (SPEC §4.9) ---

PROTEIN_PER_KG = {"recompo": 2.0, "perte": 2.0, "muscle": 1.8, "forme": 1.6}
KCAL_HINTS = {
    "recompo": "~2 150–2 350 kcal/j",
    "muscle": "~2 600–2 900 kcal/j (indicatif)",
    "perte": "~1 900–2 100 kcal/j (indicatif)",
    "forme": "~2 300–2 500 kcal/j (indicatif)",
}


def auto_protein_goal(weight_kg: float, objective: str) -> int:
    """Daily protein target from body weight: 2 g/kg recompo & perte, 1.8 muscle, 1.6 forme."""
    grams = int(weight_kg * PROTEIN_PER_KG[objective] + 0.5)
    return max(60, min(300, grams))


# --- Search (SPEC §4.4) ---

def normalize_text(text: str) -> str:
    """Lowercase, no accents, punctuation → single spaces: "Épaules" → "epaules"."""
    text = text.casefold().replace("œ", "oe").replace("æ", "ae")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def matches_query(query: str, fields: list[str]) -> bool:
    """True when every word of the query appears (accent-insensitive) in one of the fields."""
    words = normalize_text(query).split()
    haystack = " ".join(normalize_text(f) for f in fields)
    return all(w in haystack for w in words)


def slugify(name: str) -> str:
    return normalize_text(name).replace(" ", "-")[:70].strip("-") or "exercice"


# --- Guessing attributes of an exercise the library doesn't know (prototype import) ---

# Names the prototype renamed over time (SPEC §11): normalized old name → current name
PROTOTYPE_RENAMES = {"pec deck": "Pec deck (butterfly)"}

# (keywords, cardio unit) — checked against the normalized name
CARDIO_KEYWORDS = (
    (("tapis", "course", "jogging", "footing", "marche", "marche rapide"), "km/h"),
    (("velo", "bike", "elliptique", "stepper", "escalier", "escaliers"), "niveau"),
    (("rameur", "rowing machine", "natation", "nage"), "m"),
    (("corde a sauter",), "tours"),
    (("cardio",), None),
)
GROUP_KEYWORDS = (
    ("Avant-bras", ("avant bras", "poignet", "wrist")),
    ("Abdos", ("abdo", "crunch", "gainage", "planche", "releve de jambes", "releve de genoux", "gainage")),
    ("Triceps", ("triceps", "dips", "barre au front", "kickback")),
    ("Biceps", ("biceps", "curl marteau", "pupitre")),
    ("Jambes", ("leg ", "squat", "presse", "fente", "mollet", "hip thrust", "souleve de terre", "sdt",
                "ischio", "fessier", "cuisse", "adducteur", "abducteur", "hack", "pont")),
    ("Biceps", ("curl",)),
    ("Épaules", ("epaule", "militaire", "elevation", "face pull", "oiseau", "shrug", "arnold")),
    ("Pectoraux", ("pec", "developpe couche", "developpe incline", "developpe decline", "ecarte",
                   "pompe", "butterfly", "papillon", "pull over")),
    ("Dos", ("dos", "tirage", "rowing", "traction", "pull", "lombaire", "lat ")),
)
ISOLATION_KEYWORDS = ("curl", "extension", "elevation", "ecarte", "pec deck", "butterfly", "papillon",
                      "face pull", "mollet", "kickback", "oiseau", "shrug", "adducteur", "abducteur",
                      "leg curl", "poignet")


def guess_exercise(name: str) -> dict:
    """Best guess of an unknown exercise's attributes from its name (keywords, not magic).

    Returns {muscle_group, equipment, is_isolation, is_lower, is_cardio, cardio_unit}.
    """
    n = " " + normalize_text(name) + " "
    for words, unit in CARDIO_KEYWORDS:
        if any(f" {w} " in n for w in words):  # whole words: "marchées" is not "marche"
            return {"muscle_group": "Cardio", "equipment": "Machine" if unit in ("niveau", "km/h", "m") else "Poids du corps",
                    "is_isolation": False, "is_lower": False, "is_cardio": True, "cardio_unit": unit}
    group = "Jambes"
    for g, words in GROUP_KEYWORDS:
        if any(w in n for w in words):
            group = g
            break
    if any(w in n for w in ("poulie", "cable", "tirage", "pushdown")):
        equipment = "Poulie"
    elif any(w in n for w in ("machine", "presse", "pec deck", "butterfly", "leg ", "hack", "smith", "convergente")):
        equipment = "Machine"
    elif any(w in n for w in ("pompe", "traction", "dips", "gainage", "planche", "crunch", "poids du corps")):
        equipment = "Poids du corps"
    else:
        equipment = "Poids libre"
    return {
        "muscle_group": group, "equipment": equipment,
        "is_isolation": any(w in n for w in ISOLATION_KEYWORDS),
        "is_lower": group == "Jambes", "is_cardio": False, "cardio_unit": None,
    }
