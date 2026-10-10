"""Exercise library routes (SPEC §4.4): search, and creation of custom exercises."""

from fastapi import APIRouter, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.algorithms import matches_query, normalize_text, slugify
from app.database import DbSession
from app.models import Exercise, SessionExercise, WorkoutExercise
from app.schemas import ExerciseCreateIn, ExerciseOut

router = APIRouter(prefix="/exercises", tags=["exercises"])

CUSTOM_FILTER = "perso"  # ?muscle=Perso lists the custom exercises (SPEC §4.4)


def aliases_of(exercise: Exercise) -> list[str]:
    return [a for a in exercise.aliases.split("|") if a]


def to_exercise_out(e: Exercise) -> ExerciseOut:
    return ExerciseOut(
        slug=e.slug, name=e.name_fr, muscle_group=e.muscle_group, equipment=e.equipment,
        is_isolation=e.is_isolation, is_lower=e.is_lower, is_cardio=e.is_cardio,
        cardio_unit=e.cardio_unit, is_custom=e.is_custom, needs_review=e.needs_review, aliases=aliases_of(e),
    )


def find_by_name(db: Session, name: str, with_aliases: bool = True) -> Exercise | None:
    """Exercise whose name (or alias) is `name`, accent- and case-insensitive. Names win over aliases."""
    wanted = normalize_text(name)
    exercises = list(db.scalars(select(Exercise)))
    for e in exercises:
        if normalize_text(e.name_fr) == wanted:
            return e
    if with_aliases:
        for e in exercises:
            if wanted in (normalize_text(a) for a in aliases_of(e)):
                return e
    return None


def unique_slug(db: Session, name: str) -> str:
    base = slugify(name)
    slug, n = base, 2
    while db.get(Exercise, slug) is not None:
        slug = f"{base[:76]}-{n}"
        n += 1
    return slug


def add_custom_exercise(
    db: Session, name: str, muscle_group: str, equipment: str, is_isolation: bool = False,
    is_lower: bool = False, is_cardio: bool = False, cardio_unit: str | None = None, needs_review: bool = False,
) -> Exercise:
    """Insert a custom exercise (no commit). The caller checked the name is free."""
    exercise = Exercise(
        slug=unique_slug(db, name), name_fr=name, muscle_group=muscle_group, equipment=equipment,
        is_isolation=is_isolation, is_lower=is_lower,
        is_cardio=is_cardio or muscle_group == "Cardio", cardio_unit=cardio_unit,
        aliases="", is_custom=True, owner_id=None, needs_review=needs_review,
    )
    db.add(exercise)
    db.flush()
    return exercise


@router.get("")
def search_exercises(
    db: DbSession, q: str = "", muscle: str = "", equipment: str = "",
) -> list[ExerciseOut]:
    """Library search, accent-insensitive on name, group, equipment and aliases.

    `muscle` filters on the group ("Perso" = custom exercises), `equipment` on the kind.
    Sorted by name.
    """
    result = []
    for e in db.scalars(select(Exercise)):
        if muscle:
            wanted = normalize_text(muscle)
            if wanted == CUSTOM_FILTER:
                if not e.is_custom:
                    continue
            elif normalize_text(e.muscle_group) != wanted:
                continue
        if equipment and normalize_text(e.equipment) != normalize_text(equipment):
            continue
        if q and not matches_query(q, [e.name_fr, e.muscle_group, e.equipment, *aliases_of(e)]):
            continue
        result.append(e)
    result.sort(key=lambda e: normalize_text(e.name_fr))
    return [to_exercise_out(e) for e in result]


@router.post("", status_code=201)
def create_exercise(body: ExerciseCreateIn, db: DbSession) -> ExerciseOut:
    """Create a custom exercise (name + group + equipment, no animation)."""
    if find_by_name(db, body.name, with_aliases=False) is not None:
        raise HTTPException(status_code=409, detail="Un exercice porte déjà ce nom")
    exercise = add_custom_exercise(
        db, body.name, body.muscle_group, body.equipment, body.is_isolation, body.is_lower,
        body.is_cardio, body.cardio_unit,
    )
    db.commit()
    return to_exercise_out(exercise)


def get_custom(db: Session, slug: str) -> Exercise:
    exercise = db.get(Exercise, slug)
    if exercise is None:
        raise HTTPException(status_code=404, detail="Exercice inconnu")
    if not exercise.is_custom:
        raise HTTPException(status_code=403, detail="Seuls les exercices perso peuvent être modifiés")
    return exercise


@router.put("/{slug}")
def update_exercise(slug: str, body: ExerciseCreateIn, db: DbSession) -> ExerciseOut:
    """Edit a custom exercise. Stats and tips use the current values, so past workouts follow."""
    exercise = get_custom(db, slug)
    other = find_by_name(db, body.name, with_aliases=False)
    if other is not None and other.slug != slug:
        raise HTTPException(status_code=409, detail="Un exercice porte déjà ce nom")
    exercise.name_fr = body.name
    exercise.muscle_group = body.muscle_group
    exercise.equipment = body.equipment
    exercise.is_isolation = body.is_isolation
    exercise.is_lower = body.is_lower
    exercise.is_cardio = body.is_cardio or body.muscle_group == "Cardio"
    exercise.cardio_unit = body.cardio_unit
    exercise.needs_review = False
    db.commit()
    return to_exercise_out(exercise)


@router.delete("/{slug}", status_code=204)
def delete_exercise(slug: str, db: DbSession) -> Response:
    """Delete a custom exercise that no session and no workout uses."""
    exercise = get_custom(db, slug)
    in_session = db.scalars(select(SessionExercise.id).where(SessionExercise.exercise_slug == slug).limit(1)).first()
    in_workout = db.scalars(select(WorkoutExercise.id).where(WorkoutExercise.exercise_slug == slug).limit(1)).first()
    if in_session is not None or in_workout is not None:
        raise HTTPException(status_code=409, detail="Cet exercice est utilisé dans une séance ou dans l'historique")
    db.delete(exercise)
    db.commit()
    return Response(status_code=204)
