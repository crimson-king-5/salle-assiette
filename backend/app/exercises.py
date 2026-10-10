"""Exercise library routes (SPEC §4.4): search, and creation of custom exercises."""

from fastapi import APIRouter, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.algorithms import normalize_text, slugify
from app.database import DbSession
from app.models import Exercise, SessionExercise, WorkoutExercise
from app.library import ATTRIBUTION, media_file, secondary_of, steps_of
from app.schemas import ExerciseCreateIn, ExerciseDetailOut, ExerciseOut

router = APIRouter(prefix="/exercises", tags=["exercises"])

CUSTOM_FILTER = "perso"  # ?muscle=Perso lists the custom exercises (SPEC §4.4)


def aliases_of(exercise: Exercise) -> list[str]:
    return [a for a in exercise.aliases.split("|") if a]


def to_exercise_out(e: Exercise) -> ExerciseOut:
    return ExerciseOut(
        slug=e.slug, name=e.name_fr, muscle_group=e.muscle_group, equipment=e.equipment,
        is_isolation=e.is_isolation, is_lower=e.is_lower, is_cardio=e.is_cardio,
        cardio_unit=e.cardio_unit, is_custom=e.is_custom, needs_review=e.needs_review, aliases=aliases_of(e),
        is_library=e.is_library, thumb=media_file(e.media_image),
    )


def to_exercise_detail(e: Exercise) -> ExerciseDetailOut:
    gif = media_file(e.media_gif)
    return ExerciseDetailOut(
        **to_exercise_out(e).model_dump(), gif=gif, steps=steps_of(e), target=e.target_muscle,
        secondary=secondary_of(e), attribution=ATTRIBUTION if gif or e.media_image else None,
    )


def find_by_name(db: Session, name: str, with_aliases: bool = True) -> Exercise | None:
    """Exercise whose name (or alias) is `name`, accent- and case-insensitive. Names win over aliases.

    Karim's own exercises (seed, custom) win over the imported library on the same name or alias.
    An alias shared by several library exercises ("rowing") is ambiguous: no answer, rather than a random one.
    """
    wanted = normalize_text(name)
    exercises = list(db.scalars(select(Exercise)))
    own = [e for e in exercises if not e.is_library]
    lib = [e for e in exercises if e.is_library]
    for group in (own, lib):
        for e in group:
            if normalize_text(e.name_fr) == wanted:
                return e
        if with_aliases:
            hits = [e for e in group if wanted in (normalize_text(a) for a in aliases_of(e))]
            if group is own and hits:
                return hits[0]
            if group is lib and len(hits) == 1:
                return hits[0]
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


def matches_words(words: list[str], fields: list[str]) -> bool:
    """True when every query word STARTS a word of the fields ("velo" is not inside "developpe")."""
    tokens = " ".join(normalize_text(f) for f in fields).split()
    return all(any(t.startswith(w) for t in tokens) for w in words)


def search_library(db: Session, q: str = "", muscle: str = "", equipment: str = "") -> list[Exercise]:
    """Library search, accent-insensitive on name, group, equipment and aliases. Sorted by name.

    `muscle` filters on the group ("Perso" = custom exercises), `equipment` on the kind.
    """
    result = []
    words = normalize_text(q).split()
    wanted_group = normalize_text(muscle)
    wanted_equipment = normalize_text(equipment)
    for e in db.scalars(select(Exercise)):
        if muscle:
            if wanted_group == CUSTOM_FILTER:
                if not e.is_custom:
                    continue
            elif normalize_text(e.muscle_group) != wanted_group:
                continue
        if equipment and normalize_text(e.equipment) != wanted_equipment:
            continue
        if q and not matches_words(words, [e.name_fr, e.muscle_group, e.equipment, *aliases_of(e)]):
            continue
        result.append(e)
    if q:  # best matches first: the phrase in the name, then all words in the name, then aliases/other fields
        phrase = normalize_text(q)

        def rank(e: Exercise) -> tuple:
            name = normalize_text(e.name_fr)
            tier = 0 if phrase in name else 1 if all(w in name for w in words) else 2
            return (tier, e.is_library, name)

        result.sort(key=rank)
    else:
        result.sort(key=lambda e: normalize_text(e.name_fr))
    return result


@router.get("")
def list_exercises(
    db: DbSession, q: str = "", muscle: str = "", equipment: str = "",
) -> list[ExerciseOut]:
    """The library, lean (no steps: GET /exercises/{slug} has them), optionally searched / filtered."""
    return [to_exercise_out(e) for e in search_library(db, q, muscle, equipment)]


@router.get("/{slug}")
def get_exercise(slug: str, db: DbSession) -> ExerciseDetailOut:
    """One exercise with its how-to steps, animation and muscles (the full-screen viewer)."""
    exercise = db.get(Exercise, slug)
    if exercise is None:
        raise HTTPException(status_code=404, detail="Exercice inconnu")
    return to_exercise_detail(exercise)


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
