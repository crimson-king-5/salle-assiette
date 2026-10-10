"""POST /import/prototype: load the prototype's localStorage JSON (SPEC §3.1, §11).

Safe to run twice: everything that is not the program or the goals gets an id derived from
its content (uuid5), so a second import finds the same ids and updates instead of duplicating.
Lines deleted since the first import (tombstones) are not brought back.

Rules of thumb:
- structure, types and numeric bounds are strict (422 otherwise);
- blank set values are fine (None); over-long labels are cut to the column size;
- an exercise name the library doesn't know becomes a custom exercise (at most MAX_NEW_EXERCISES
  per import), with attributes guessed from its name unless the file describes it; guessed ones
  are flagged needs_review.

Ids derive from what identifies a line, not from its position in the file, so a newer export
updates the same rows instead of duplicating them:
  weight   = its date (one per day)
  protein  = date + label + grams + n-th identical line that day
  workout  = date + startedAt (or session + name when absent) + n-th such workout that day
"""

import datetime as dt
import json
import re
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, ValidationError, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.algorithms import (
    EQUIPMENT_KINDS, MUSCLE_GROUPS, PROTOTYPE_RENAMES, Focus, Objective, guess_exercise,
    normalize_text,
)
from app.database import DbSession
from app.exercises import add_custom_exercise
from app.models import (
    Exercise, ProteinEntry, ProteinTombstone, SessionExercise, SessionTemplate, WeightTombstone,
    Workout, WorkoutExercise, WorkoutSet, WorkoutTombstone,
)
from app.queries import get_profile
from app.weights import store_weight

router = APIRouter(tags=["import"])

MAX_BYTES = 5 * 1024 * 1024
NAMESPACE = uuid.UUID("5a11e0a5-51e7-4e00-8a55-1e7a55e77e00")  # fixed: ids must be the same on every run
SESSION_ID = re.compile(r"^[A-Za-z0-9_-]{1,20}$")
FORMATS = {"complete": "complete", "complète": "complete",
           "minimale": "minimale", "express": "express"}


# --- Shape of the prototype JSON ---

def parse_number(value: Any, low: float, high: float) -> float | None:
    """"82,5" → 82.5, "" or None → None. Not a number or out of bounds → error."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, bool):
        raise ValueError("nombre attendu")
    try:
        number = float(value.strip().replace(",", ".")) if isinstance(value, str) else float(value)
    except (TypeError, ValueError):
        raise ValueError("nombre attendu") from None
    if not low <= number <= high:
        raise ValueError(f"valeur hors limites ({low:g} à {high:g})")
    return number


class ProtoExercise(BaseModel):
    n: str = Field(min_length=1, max_length=120)
    s: int = Field(ge=1, le=10)
    r: str = Field(max_length=20)

    @field_validator("r", mode="before")
    @classmethod
    def reps_as_text(cls, value):
        return str(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else value


class ProtoSession(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    focus: Focus
    ex: list[ProtoExercise] = Field(max_length=20)


class ProtoGoals(BaseModel):
    sessions: int | None = Field(default=None, ge=1, le=7)
    protein: int | None = Field(default=None, ge=60, le=300)
    targetWeight: float | None = Field(default=None, ge=30, le=250)
    objective: Objective | None = None
    coach: bool | None = None


class ProtoSet(BaseModel):
    w: float | None = None
    r: int | None = None
    done: bool | None = None  # absent in finished logs: every logged set was validated

    @field_validator("w", mode="before")
    @classmethod
    def weight(cls, value):
        return parse_number(value, 0, 500)

    @field_validator("r", mode="before")
    @classmethod
    def reps(cls, value):
        number = parse_number(value, 0, 100)
        return None if number is None else int(number)


class ProtoLogExercise(BaseModel):
    n: str = Field(min_length=1, max_length=120)
    r: str = Field(default="", max_length=20)
    sets: list[ProtoSet] = Field(max_length=20)

    @field_validator("r", mode="before")
    @classmethod
    def reps_as_text(cls, value):
        if value is None:
            return ""
        return str(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else value


class ProtoLog(BaseModel):
    session: str = Field(default="", max_length=60)
    name: str = Field(min_length=1, max_length=200)
    focus: str = "Libre"
    fmt: str = "complete"
    date: dt.date
    startedAt: int | None = Field(default=None, ge=0, le=4_000_000_000_000)   # ms since epoch
    finishedAt: int | None = Field(default=None, ge=0, le=4_000_000_000_000)
    dur: float | None = Field(default=None, ge=0, le=1440)                      # minutes
    ex: list[ProtoLogExercise] = Field(max_length=30)


class ProtoWeight(BaseModel):
    date: dt.date
    kg: float = Field(ge=30, le=250)


class ProtoCustom(BaseModel):
    g: str = ""
    m: list[str] = []
    iso: int | bool = 0
    lower: int | bool = 0
    eq: str = ""
    cardio: int | bool = 0
    unit: str | None = Field(default=None, max_length=20)


class Prototype(BaseModel):
    program: dict[str, ProtoSession] = Field(default_factory=dict, max_length=50)
    order: list[str] = Field(default_factory=list, max_length=50)
    goals: ProtoGoals | None = None
    logs: list[ProtoLog] = Field(default_factory=list, max_length=5000)
    weights: list[ProtoWeight] = Field(default_factory=list, max_length=5000)
    protein: dict[dt.date, list[tuple[str, float]]] = Field(default_factory=dict, max_length=5000)
    custom: dict[str, ProtoCustom] = Field(default_factory=dict, max_length=500)

    @field_validator("protein")
    @classmethod
    def protein_lines_are_sane(cls, value):
        for day, lines in value.items():
            if len(lines) > 200:
                raise ValueError(f"trop de lignes de protéines le {day}")
            for label, grams in lines:
                if not 0 <= grams <= 300:
                    raise ValueError(f"protéines hors limites le {day} : {grams:g}")
                try:
                    label.encode("utf-8")  # a lone surrogate can't be stored
                except UnicodeEncodeError:
                    raise ValueError("libellé avec des caractères invalides") from None
        return value


class ReviewOut(BaseModel):
    slug: str
    name: str
    muscle_group: str
    equipment: str
    is_cardio: bool


class ImportOut(BaseModel):
    dry_run: bool                  # True: nothing was written, these are the counts it WOULD give
    sessions_created: int
    sessions_updated: int
    goals_updated: bool
    exercises_created: list[str]   # names of the custom exercises made for unknown names
    exercises_to_review: list[ReviewOut]  # the guessed ones: check them with PUT /exercises/{slug}
    workouts_created: int
    workouts_updated: int          # already imported before: refreshed, not duplicated
    weights_created: int
    weights_updated: int
    weights_skipped: int           # a later weigh-in of the same day already exists
    protein_created: int
    protein_updated: int
    skipped_deleted: int           # lines / workouts deleted since an earlier import


MAX_NEW_EXERCISES = 200


# --- Helpers ---

def stable_id(*parts: Any) -> str:
    return str(uuid.uuid5(NAMESPACE, "|".join(str(p) for p in parts)))


def base_name(name: str) -> str:
    """Normalized name without parentheses: "Pec deck (butterfly)" -> "pec deck"."""
    return normalize_text(re.sub(r"\(.*?\)", " ", name))


class Library:
    """The exercise library loaded once, to match prototype names without scanning per name."""

    def __init__(self, db) -> None:
        self.names: dict[str, str] = {}          # Karim's own exercises
        self.aliases: dict[str, str] = {}
        self.bases: dict[str, str] = {}
        self.library_names: dict[str, str] = {}  # imported library
        self.library_aliases: dict[str, str | None] = {}  # None = shared by several imported exercises
        self.library_bases: dict[str, str | None] = {}
        for e in db.scalars(select(Exercise).order_by(Exercise.is_library, Exercise.slug)):
            self.add(e)

    @staticmethod
    def _claim(table: dict, key: str, slug: str) -> None:
        table[key] = None if key in table else slug

    def add(self, e: Exercise) -> None:
        name, base = normalize_text(e.name_fr), base_name(e.name_fr)
        aliases = [normalize_text(a) for a in e.aliases.split("|") if a]
        if e.is_library:  # an alias or base name shared by many library exercises must not pick one at random
            self.library_names.setdefault(name, e.slug)
            self._claim(self.library_bases, base, e.slug)
            for key in aliases:
                self._claim(self.library_aliases, key, e.slug)
        else:
            self.names.setdefault(name, e.slug)
            self.bases.setdefault(base, e.slug)
            for key in aliases:
                self.aliases.setdefault(key, e.slug)

    @staticmethod
    def canonical(name: str) -> str:
        """Apply the prototype's own renames (SPEC section 11)."""
        return PROTOTYPE_RENAMES.get(normalize_text(name), name.strip())

    def find(self, name: str) -> str | None:
        name = self.canonical(name)
        key = normalize_text(name)
        base = base_name(name)
        return (self.names.get(key) or self.aliases.get(key) or self.library_names.get(key)
                or self.library_aliases.get(key) or self.bases.get(base) or self.library_bases.get(base))


async def read_limited_json(request: Request) -> Any:
    content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if content_type != "application/json":
        # Also what stops a cross-site "simple" request (text/plain form or fetch) from importing
        raise HTTPException(status_code=415, detail="Le contenu doit être en application/json")
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_BYTES:
        raise HTTPException(status_code=413, detail="Fichier trop gros (5 Mo maximum)")
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_BYTES:
            raise HTTPException(status_code=413, detail="Fichier trop gros (5 Mo maximum)")
        chunks.append(chunk)
    try:
        return json.loads(b"".join(chunks))
    except (ValueError, RecursionError):
        raise HTTPException(status_code=422, detail="Ce fichier n'est pas du JSON valide") from None


def validation_detail(exc: ValidationError) -> list[dict]:
    return [{"loc": list(e["loc"]), "msg": e["msg"], "type": e["type"]} for e in exc.errors()]


def to_datetime_ms(ms: int) -> dt.datetime:
    return dt.datetime.fromtimestamp(ms / 1000)


def all_exercise_names(data: Prototype) -> list[str]:
    names = [e.n for session in data.program.values() for e in session.ex]
    names += [e.n for log in data.logs for e in log.ex]
    return names


@router.post("/import/prototype")
async def import_prototype(
    request: Request, db: DbSession, program: bool = True, goals: bool = True, dry_run: bool = False,
) -> ImportOut:
    """Import the prototype's JSON (the body IS that JSON, Content-Type application/json).

    `?dry_run=true` returns the counts an import would give without writing anything (preview).
    `?program=false` keeps the current sessions and rotation; `?goals=false` keeps the settings.
    Importing the program replaces the sessions that have the same id and puts the imported
    rotation first; sessions that are not in the file stay after them.
    """
    raw = await read_limited_json(request)
    if not isinstance(raw, dict):
        raise HTTPException(status_code=422, detail="Le fichier doit être un objet JSON")
    try:
        data = Prototype.model_validate(raw)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=validation_detail(exc)) from None
    for session_id in data.program:
        if not SESSION_ID.match(session_id):
            raise HTTPException(status_code=422, detail=f"Identifiant de séance invalide : {session_id}")

    # Everything that can be refused is refused here, before anything is written
    library = Library(db)
    names = all_exercise_names(data)
    if any(not base_name(n) for n in names):
        raise HTTPException(status_code=422, detail="Nom d'exercice vide dans le fichier")
    unknown = {normalize_text(Library.canonical(n)) for n in names if library.find(n) is None}
    if len(unknown) > MAX_NEW_EXERCISES:
        raise HTTPException(
            status_code=422,
            detail=f"Trop d'exercices inconnus dans le fichier ({len(unknown)}, {MAX_NEW_EXERCISES} maximum)")

    out = ImportOut(
        dry_run=dry_run, sessions_created=0, sessions_updated=0, goals_updated=False,
        exercises_created=[], exercises_to_review=[], workouts_created=0, workouts_updated=0,
        weights_created=0, weights_updated=0, weights_skipped=0, protein_created=0,
        protein_updated=0, skipped_deleted=0,
    )
    custom_by_name = {normalize_text(name): c for name, c in data.custom.items()}
    slug_cache: dict[str, str] = {}

    def resolve(name: str) -> str:
        """Library slug for a prototype exercise name; unknown names become custom exercises."""
        canonical = Library.canonical(name)
        key = normalize_text(canonical)
        if key in slug_cache:
            return slug_cache[key]
        slug = library.find(name)
        if slug is None:
            described = custom_by_name.get(normalize_text(name)) or custom_by_name.get(key)
            if described is not None:  # the file says what it is: no guessing
                group = described.g if described.g in MUSCLE_GROUPS else next(
                    (m for m in described.m if m in MUSCLE_GROUPS), "Jambes")
                attrs = {
                    "muscle_group": group,
                    "equipment": described.eq if described.eq in EQUIPMENT_KINDS else "Poids libre",
                    "is_isolation": bool(described.iso), "is_lower": bool(described.lower),
                    "is_cardio": bool(described.cardio), "cardio_unit": described.unit,
                }
            else:
                attrs = guess_exercise(canonical)
            created = add_custom_exercise(db, canonical, needs_review=described is None, **attrs)
            library.add(created)
            slug = created.slug
            out.exercises_created.append(canonical)
            if described is None:
                out.exercises_to_review.append(ReviewOut(
                    slug=slug, name=canonical, muscle_group=created.muscle_group,
                    equipment=created.equipment, is_cardio=created.is_cardio))
        slug_cache[key] = slug
        return slug

    try:
        if goals and data.goals is not None:
            profile = get_profile(db)
            g = data.goals
            for field, value in (("sessions_goal", g.sessions), ("protein_goal_g", g.protein),
                                 ("target_weight_kg", g.targetWeight), ("objective", g.objective),
                                 ("coach_enabled", g.coach)):
                if value is not None:
                    setattr(profile, field, value)
            out.goals_updated = True

        if program and data.program:
            import_program(db, data, resolve, out)

        import_workouts(db, data, resolve, out)
        import_weights(db, data, out)
        import_protein(db, data, out)
        if dry_run:
            db.rollback()
        else:
            db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Import interrompu par un conflit, réessaie") from None
    except HTTPException:
        db.rollback()  # nothing must stay half-imported
        raise
    return out


def import_program(db, data: Prototype, resolve, out: ImportOut) -> None:
    existing = {t.id: t for t in db.scalars(select(SessionTemplate).order_by(SessionTemplate.position))}
    order = [i for i in dict.fromkeys(data.order) if i in data.program]
    order += [i for i in data.program if i not in order]
    for session_id in order:
        proto = data.program[session_id]
        rows = [
            SessionExercise(position=i, exercise_slug=resolve(e.n), sets=e.s, reps=e.r or "8-12")
            for i, e in enumerate(proto.ex)
        ]
        template = existing.get(session_id)
        if template is None:
            template = SessionTemplate(id=session_id, name=proto.name, focus=proto.focus, position=0)
            db.add(template)
            existing[session_id] = template
            out.sessions_created += 1
        else:
            out.sessions_updated += 1
        template.name, template.focus = proto.name, proto.focus
        template.exercises = rows
    rest = [t for i, t in existing.items() if i not in order]
    rest.sort(key=lambda t: t.position)
    for position, session_id in enumerate(order + [t.id for t in rest]):
        existing[session_id].position = position


def import_workouts(db, data: Prototype, resolve, out: ImportOut) -> None:
    known = {w.client_id: w for w in db.scalars(select(Workout).where(Workout.client_id.is_not(None)))}
    dead = set(db.scalars(select(WorkoutTombstone.client_id)))
    seen: dict[tuple, int] = {}
    for log in data.logs:
        anchor = log.startedAt if log.startedAt is not None else f"{log.session}|{log.name}"
        slot = (log.date, anchor)
        seen[slot] = seen.get(slot, 0) + 1  # the n-th such workout that day
        key = stable_id("workout", log.date.isoformat(), anchor, seen[slot])
        if key in dead:
            out.skipped_deleted += 1
            continue

        started = to_datetime_ms(log.startedAt) if log.startedAt is not None \
            else dt.datetime.combine(log.date, dt.time(12, 0))
        if log.finishedAt is not None:
            finished = to_datetime_ms(log.finishedAt)
        elif log.dur is not None:
            finished = started + dt.timedelta(minutes=log.dur)
        else:
            finished = started
        finished = max(finished, started)

        exercises = [
            WorkoutExercise(
                position=i, exercise_slug=resolve(ex.n), target_reps=ex.r,
                sets=[
                    WorkoutSet(position=n, weight=s.w, reps=s.r, done=True if s.done is None else s.done)
                    for n, s in enumerate(ex.sets)
                ],
            )
            for i, ex in enumerate(log.ex)
        ]
        workout = known.get(key)
        if workout is None:
            workout = Workout(client_id=key)
            db.add(workout)
            known[key] = workout
            out.workouts_created += 1
        else:
            out.workouts_updated += 1
        workout.session_id = (log.session.strip() or "import")[:20]
        workout.name = log.name.strip()[:80] or "Séance"
        workout.focus = log.focus if log.focus in ("Force", "Hypertrophie", "Libre") else "Libre"
        workout.format = FORMATS.get(log.fmt.strip().lower(), "complete")
        workout.date = log.date
        workout.started_at, workout.finished_at = started, finished
        workout.exercises = exercises


def import_weights(db, data: Prototype, out: ImportOut) -> None:
    dead = set(db.scalars(select(WeightTombstone.client_id)))
    per_day = {w.date: w for w in data.weights}  # several the same day: the last one in the file
    for day, w in per_day.items():
        key = stable_id("weight", day.isoformat())
        if key in dead:
            out.skipped_deleted += 1
            continue
        row, created = store_weight(
            db, key, day, w.kg, dt.datetime.combine(day, dt.time(0, 0)), imported=True)
        if row is None:
            out.weights_skipped += 1
        elif created:
            out.weights_created += 1
        else:
            out.weights_updated += 1


def import_protein(db, data: Prototype, out: ImportOut) -> None:
    known = {p.client_id: p for p in db.scalars(select(ProteinEntry))}
    dead = set(db.scalars(select(ProteinTombstone.client_id)))
    for day, lines in data.protein.items():
        seen: dict[tuple, int] = {}
        for index, (label, grams) in enumerate(lines):
            label = label.strip()[:80] or "Sans nom"
            same = (label, round(grams))
            seen[same] = seen.get(same, 0) + 1  # the n-th identical line that day
            key = stable_id("protein", day.isoformat(), label, round(grams), seen[same])
            if key in dead:
                out.skipped_deleted += 1
                continue
            row = known.get(key)
            if row is None:
                row = ProteinEntry(client_id=key)
                db.add(row)
                known[key] = row
                out.protein_created += 1
            else:
                out.protein_updated += 1
            row.date, row.label, row.grams = day, label, round(grams)
            row.created_at = dt.datetime.combine(day, dt.time(12, 0)) + dt.timedelta(seconds=index)
