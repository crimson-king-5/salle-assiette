"""The coach's 7 tools (SPEC §7.4): server functions the model can call.

Everything the model sends is untrusted. Each tool has a strict Pydantic model for its arguments
(unknown fields refused), then goes through the SAME route functions and schemas as the normal API
(program.py, exercises.py, profile.py, protein.py, weights.py), so the same bounds and checks apply.
A tool never raises to the model: ToolError carries a French message (and the close names, so the
model can fix itself on the next step, SPEC §7.4).

Each tool returns (summary, result for the model, undo data). The undo data is what is needed to
put things back; undo_tool() applies it.
"""

import datetime as dt
import difflib
import uuid
from typing import Annotated, Any, Callable, Literal

from fastapi import HTTPException, Response
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import exercises as exercises_routes
from app import profile as profile_routes
from app import program as program_routes
from app import protein as protein_routes
from app import weights as weights_routes
from app.algorithms import EQUIPMENT_KINDS, Equipment, Focus, MuscleGroup, Objective, matches_query, normalize_text
from app.models import Exercise, ProteinEntry, ProteinTombstone, SessionExercise, SessionTemplate, WeightEntry, WeightTombstone
from app.queries import get_profile
from app.schemas import (
    ExerciseCreateIn, ProfileUpdateIn, ProteinSyncIn, RotationIn, SessionCreateIn,
    SessionExerciseIn, SessionPreset, SessionUpdateIn, WeightSyncIn,
)

MAX_CLOSE_NAMES = 6


class ToolError(Exception):
    """A refused tool call. `close` = nearby names offered to the model for its next attempt."""

    def __init__(self, message: str, close: list[str] | None = None):
        super().__init__(message)
        self.message = message
        self.close = close or []


class ToolOutcome:
    def __init__(self, summary: str, result: dict, undo: dict, read_only: bool = False):
        self.summary, self.result, self.undo = summary, result, undo
        self.read_only = read_only  # nothing changed: no action record, no Annuler button


# --- Argument models (the model's JSON is checked here before anything is touched) ---

def _as_text(value: Any) -> Any:
    return str(value) if isinstance(value, int) and not isinstance(value, bool) else value


Reps = Annotated[str, BeforeValidator(_as_text), Field(min_length=1, max_length=20)]


class Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EditExerciseArgs(Args):
    session: str = Field(min_length=1, max_length=80)
    action: Literal["add", "remove", "replace", "update", "move"]
    exercise: str = Field(min_length=1, max_length=120)
    new_exercise: str | None = Field(default=None, max_length=120)
    sets: int | None = Field(default=None, ge=1, le=10)
    reps: Reps | None = None
    position: int | None = Field(default=None, ge=1, le=20)


class LineArgs(Args):
    exercise: str = Field(min_length=1, max_length=120)
    sets: int = Field(default=3, ge=1, le=10)
    reps: Reps = "8-12"


class CreateSessionArgs(Args):
    name: str = Field(min_length=1, max_length=80)
    focus: Focus = "Libre"
    template: SessionPreset = "vide"
    exercises: list[LineArgs] = Field(default_factory=list, max_length=20)


class UpdateSessionArgs(Args):
    session: str = Field(min_length=1, max_length=80)
    name: str | None = Field(default=None, min_length=1, max_length=80)
    focus: Focus | None = None
    delete: bool = False


class RotationArgs(Args):
    order: list[str] = Field(min_length=1, max_length=100)


class CustomExerciseArgs(Args):
    name: str = Field(min_length=1, max_length=120)
    muscle_group: MuscleGroup
    equipment: Equipment
    is_isolation: bool = False
    is_lower: bool = False
    is_cardio: bool = False
    cardio_unit: str | None = Field(default=None, max_length=20)


MAX_SEARCH_RESULTS = 15


class SearchExercisesArgs(Args):
    query: str = Field(default="", max_length=120)
    group: MuscleGroup | None = None
    equipment: Equipment | None = None
    max_results: int = Field(default=10, ge=1, le=MAX_SEARCH_RESULTS)


class GoalsArgs(Args):
    # No coach_enabled here: the coach must not be able to switch itself on or off
    objective: Objective | None = None
    sessions_goal: int | None = Field(default=None, ge=1, le=7)
    protein_goal_g: int | None = Field(default=None, ge=60, le=300)
    target_weight_kg: float | None = Field(default=None, ge=30, le=250)


class LogTodayArgs(Args):
    protein_g: int | None = Field(default=None, ge=1, le=300)
    protein_label: str = Field(default="Ajout du coach", min_length=1, max_length=80)
    weight_kg: float | None = Field(default=None, ge=30, le=250)


def describe_validation(error: ValidationError) -> str:
    """The first problems of a ValidationError, without echoing the received values."""
    parts = []
    for e in error.errors()[:3]:
        where = ".".join(str(x) for x in e["loc"]) or "arguments"
        parts.append(f"{where} : {e['msg']}")
    return "Arguments refusés (" + " ; ".join(parts) + ")."


def http_message(error: HTTPException) -> str:
    detail = error.detail
    return detail if isinstance(detail, str) else "Valeurs refusées par le serveur."


# --- Tolerant name resolution (SPEC §7.4 findEx / findSession) ---

def find_session(db: Session, ref: str) -> SessionTemplate:
    templates = program_routes.ordered_templates(db)
    wanted = normalize_text(ref)
    for t in templates:
        if t.id.lower() == ref.strip().lower():
            return t
    for t in templates:
        if normalize_text(t.name) == wanted:
            return t
    partial = [t for t in templates if wanted and wanted in normalize_text(t.name)]
    if len(partial) == 1:
        return partial[0]
    pool = partial or templates
    raise ToolError(
        f"Séance introuvable ou ambiguë : « {ref} ».",
        close=[f"{t.name} (id {t.id})" for t in pool[:MAX_CLOSE_NAMES]],
    )


def find_exercise(db: Session, name: str) -> Exercise:
    exact = exercises_routes.find_by_name(db, name)
    if exact is not None:
        return exact
    library = list(db.scalars(select(Exercise)))
    words = normalize_text(name).split()
    partial = [e for e in library
               if exercises_routes.matches_words(words, [e.name_fr, *exercises_routes.aliases_of(e)])]
    # Only one of Karim's own exercises is picked on a partial match: a library hit may be the wrong variant
    if len(partial) == 1 and not partial[0].is_library:
        return partial[0]
    if partial:
        close = [e.name_fr for e in partial]
    else:
        names = {normalize_text(e.name_fr): e.name_fr for e in library}
        close = [names[n] for n in difflib.get_close_matches(normalize_text(name), list(names),
                                                             n=MAX_CLOSE_NAMES, cutoff=0.5)]
    raise ToolError(f"Exercice introuvable ou ambigu : « {name} ».", close=close[:MAX_CLOSE_NAMES])


# --- Program snapshot / restore (undo of every session tool) ---

def capture_program(db: Session) -> dict:
    """The whole rotation as plain data: session id -> name, type, place, exercise lines."""
    return {
        t.id: {
            "name": t.name, "focus": t.focus, "position": t.position,
            "exercises": [[se.exercise_slug, se.sets, se.reps] for se in t.exercises],
        }
        for t in program_routes.ordered_templates(db)
    }


def restore_program(db: Session, state: dict) -> None:
    """Make the rotation equal to `state` again (sessions created since are removed)."""
    known = set(db.scalars(select(Exercise.slug)))
    existing = {t.id: t for t in program_routes.ordered_templates(db)}
    for session_id, template in existing.items():
        if session_id not in state:
            db.delete(template)
    for session_id, data in state.items():
        template = existing.get(session_id)
        if template is None:
            template = SessionTemplate(id=session_id)
            db.add(template)
        template.name, template.focus, template.position = data["name"], data["focus"], data["position"]
        template.exercises = [
            SessionExercise(position=i, exercise_slug=slug, sets=sets, reps=reps)
            for i, (slug, sets, reps) in enumerate(l for l in data["exercises"] if l[0] in known)
        ]
    db.commit()


def lines_of(template: SessionTemplate) -> list[dict]:
    return [{"slug": se.exercise_slug, "name": se.exercise.name_fr, "sets": se.sets, "reps": se.reps}
            for se in template.exercises]


def save_lines(db: Session, template: SessionTemplate, lines: list[dict]) -> None:
    """Replace a session's exercise list through the normal route (same checks as the editor)."""
    try:
        body = SessionUpdateIn(
            name=template.name, focus=template.focus,
            exercises=[SessionExerciseIn(slug=l["slug"], sets=l["sets"], reps=l["reps"]) for l in lines],
        )
    except ValidationError as error:
        raise ToolError(describe_validation(error)) from None
    program_routes.update_session(template.id, body, db)


def brief(template: SessionTemplate) -> list[str]:
    return [f"{l['name']} {l['sets']}×{l['reps']}" for l in lines_of(template)]


# --- The tools ---

def edit_session_exercise(db: Session, a: EditExerciseArgs) -> ToolOutcome:
    template = find_session(db, a.session)
    lines = lines_of(template)
    target = find_exercise(db, a.exercise)
    index = next((i for i, l in enumerate(lines) if l["slug"] == target.slug), None)

    if a.action == "add":
        if index is not None:
            raise ToolError(f"« {target.name_fr} » est déjà dans {template.name}.")
        cardio = target.is_cardio
        line = {"slug": target.slug, "name": target.name_fr,
                "sets": a.sets or (2 if cardio else 3), "reps": a.reps or ("20" if cardio else "8-12")}
        place = len(lines) if a.position is None else min(a.position - 1, len(lines))
        lines.insert(place, line)
        summary = f"Ajouté « {target.name_fr} » ({line['sets']}×{line['reps']}) à {template.name}"
    else:
        if index is None:
            raise ToolError(f"« {target.name_fr} » n'est pas dans {template.name}.",
                            close=[l["name"] for l in lines][:MAX_CLOSE_NAMES * 2])
        if a.action == "remove":
            lines.pop(index)
            summary = f"Retiré « {target.name_fr} » de {template.name}"
        elif a.action == "replace":
            if not a.new_exercise:
                raise ToolError("replace demande new_exercise (le nouvel exercice).")
            new = find_exercise(db, a.new_exercise)
            if any(l["slug"] == new.slug for l in lines):
                raise ToolError(f"« {new.name_fr} » est déjà dans {template.name}.")
            lines[index] = {"slug": new.slug, "name": new.name_fr,
                            "sets": a.sets or lines[index]["sets"], "reps": a.reps or lines[index]["reps"]}
            summary = f"Remplacé « {target.name_fr} » par « {new.name_fr} » dans {template.name}"
        elif a.action == "update":
            if a.sets is None and a.reps is None:
                raise ToolError("update demande sets et/ou reps.")
            lines[index]["sets"] = a.sets or lines[index]["sets"]
            lines[index]["reps"] = a.reps or lines[index]["reps"]
            summary = (f"« {target.name_fr} » dans {template.name} : "
                       f"{lines[index]['sets']}×{lines[index]['reps']}")
        else:  # move
            if a.position is None:
                raise ToolError("move demande position (1 = premier).")
            line = lines.pop(index)
            place = min(a.position - 1, len(lines))
            lines.insert(place, line)
            summary = f"Déplacé « {target.name_fr} » en position {place + 1} dans {template.name}"

    before = capture_program(db)
    save_lines(db, template, lines)
    return ToolOutcome(summary, {"session": template.name, "exercices": brief(template)},
                       {"program": before})


def create_session(db: Session, a: CreateSessionArgs) -> ToolOutcome:
    # Resolve every name first, so a bad name fails before anything is created
    lines = []
    for l in a.exercises:
        e = find_exercise(db, l.exercise)
        lines.append({"slug": e.slug, "name": e.name_fr, "sets": l.sets, "reps": l.reps})
    if len({l["slug"] for l in lines}) != len(lines):
        raise ToolError("Un exercice apparaît deux fois dans la liste.")
    before = capture_program(db)
    created = program_routes.create_session(
        SessionCreateIn(name=a.name, focus=a.focus, template=a.template), db)
    template = db.get(SessionTemplate, created.id)
    if lines:
        save_lines(db, template, lines)
    return ToolOutcome(f"Séance « {template.name} » créée ({len(template.exercises)} exercices)",
                       {"id": template.id, "session": template.name, "exercices": brief(template)},
                       {"program": before})


def update_session(db: Session, a: UpdateSessionArgs) -> ToolOutcome:
    template = find_session(db, a.session)
    before = capture_program(db)
    if a.delete:
        if a.name or a.focus:
            raise ToolError("delete ne se combine pas avec name/focus.")
        name = template.name
        program_routes.delete_session(template.id, db)
        return ToolOutcome(f"Séance « {name} » supprimée", {"deleted": name}, {"program": before})
    if not a.name and not a.focus:
        raise ToolError("Rien à changer : donne name et/ou focus (ou delete=true).")
    old = template.name
    try:
        body = SessionUpdateIn(
            name=a.name or template.name, focus=a.focus or template.focus,
            exercises=[SessionExerciseIn(slug=l["slug"], sets=l["sets"], reps=l["reps"]) for l in lines_of(template)],
        )
    except ValidationError as error:
        raise ToolError(describe_validation(error)) from None
    out = program_routes.update_session(template.id, body, db)
    return ToolOutcome(f"Séance « {old} » : maintenant « {out.name} » ({out.focus})",
                       {"session": out.name, "focus": out.focus}, {"program": before})


def set_rotation_order(db: Session, a: RotationArgs) -> ToolOutcome:
    ids = [find_session(db, ref).id for ref in a.order]
    before = capture_program(db)
    result = program_routes.set_rotation(RotationIn(order=ids), db)
    names = [s.name for s in result]
    return ToolOutcome("Nouvel ordre de la rotation : " + " → ".join(names), {"order": names}, {"program": before})


def create_custom_exercise(db: Session, a: CustomExerciseArgs) -> ToolOutcome:
    try:
        body = ExerciseCreateIn(**a.model_dump())
    except ValidationError as error:
        raise ToolError(describe_validation(error)) from None
    created = exercises_routes.create_exercise(body, db)
    return ToolOutcome(f"Exercice perso « {created.name} » créé ({created.muscle_group}, {created.equipment})",
                       {"slug": created.slug, "name": created.name}, {"exercise_slug": created.slug})


def search_exercises(db: Session, a: SearchExercisesArgs) -> ToolOutcome:
    """Read-only: look up the big library (names, French aliases, group, equipment)."""
    found = exercises_routes.search_library(db, a.query, a.group or "", a.equipment or "")
    shown = found[:a.max_results]
    result = {
        "résultats": [{"nom": e.name_fr, "groupe": e.muscle_group, "matériel": e.equipment} for e in shown],
        "total_trouvés": len(found),
    }
    if len(found) > len(shown):
        result["note"] = "Il y en a d'autres : précise la recherche (mot-clé, groupe ou matériel)."
    return ToolOutcome(f"{len(found)} exercice(s) trouvé(s)", result, {}, read_only=True)


def update_goals(db: Session, a: GoalsArgs) -> ToolOutcome:
    changes = a.model_dump(exclude_none=True)
    if not changes:
        raise ToolError("Rien à changer : donne au moins un objectif.")
    profile = get_profile(db)
    old = {field: getattr(profile, field) for field in changes}
    out = profile_routes.update_profile(ProfileUpdateIn(**changes), db)
    labels = {"objective": "objectif", "sessions_goal": "séances/semaine",
              "protein_goal_g": "protéines/jour (g)", "target_weight_kg": "poids visé (kg)"}
    summary = "Objectifs : " + ", ".join(f"{labels[k]} → {v}" for k, v in changes.items())
    return ToolOutcome(summary, {"objectifs": out.model_dump(include=set(changes))},
                       {"profile": old, "profile_after": {field: getattr(profile, field) for field in changes}})


def log_today(db: Session, a: LogTodayArgs) -> ToolOutcome:
    if a.protein_g is None and a.weight_kg is None:
        raise ToolError("Rien à noter : donne protein_g et/ou weight_kg.")
    now = dt.datetime.now().replace(microsecond=0)
    # Validate everything BEFORE writing anything, so a refusal never leaves half of it behind
    try:
        protein_body = (ProteinSyncIn(date=now.date(), label=a.protein_label, grams=a.protein_g, created_at=now)
                        if a.protein_g is not None else None)
        weight_body = (WeightSyncIn(date=now.date(), kg=a.weight_kg, created_at=now)
                       if a.weight_kg is not None else None)
    except ValidationError as error:
        raise ToolError(describe_validation(error)) from None
    undo: dict = {}
    parts = []
    try:
        if weight_body is not None:  # the only step that can be refused on its own: do it first
            rivals = [{"client_id": r.client_id, "date": r.date.isoformat(), "kg": r.kg,
                       "created_at": r.created_at.isoformat()}
                      for r in db.scalars(select(WeightEntry).where(WeightEntry.date == now.date()))]
            client_id = uuid.uuid4()
            out = weights_routes.sync_weight(client_id, weight_body, db, Response())
            if out.id == 0:
                raise ToolError("Une pesée plus récente existe déjà aujourd'hui.")
            undo["weight_client_id"] = str(client_id)
            undo["weights_replaced"] = rivals
            undo["weight_kg"] = weight_body.kg
            parts.append(f"pesée {a.weight_kg} kg")
        if protein_body is not None:
            client_id = uuid.uuid4()
            protein_routes.sync_protein(client_id, protein_body, db, Response())
            undo["protein_client_id"] = str(client_id)
            undo["protein"] = {"grams": protein_body.grams, "label": protein_body.label}
            parts.append(f"{a.protein_g} g de protéines ({protein_body.label})")
    except BaseException:
        db.rollback()
        if undo:
            undo_tool(db, "log_today", undo, check=False)  # take back the part already written
        raise
    return ToolOutcome("Noté aujourd'hui : " + " + ".join(parts), {"noté": parts}, undo)


# --- Registry: argument model, function, and the declaration shown to Gemini ---

def _obj(properties: dict, required: list[str] | None = None) -> dict:
    schema: dict = {"type": "OBJECT", "properties": properties}
    if required:
        schema["required"] = required
    return schema


S = {"type": "STRING"}
INT = {"type": "INTEGER"}
NUM = {"type": "NUMBER"}
BOOL = {"type": "BOOLEAN"}


def _enum(*values: str, description: str = "") -> dict:
    schema: dict = {"type": "STRING", "enum": list(values)}
    if description:
        schema["description"] = description
    return schema


class Tool:
    def __init__(self, name: str, model: type[Args], run: Callable, description: str, parameters: dict):
        self.name, self.model, self.run = name, model, run
        self.declaration = {"name": name, "description": description, "parameters": parameters}


TOOLS: dict[str, Tool] = {t.name: t for t in [
    Tool("edit_session_exercise", EditExerciseArgs, edit_session_exercise,
         "Modifie un exercice d'une séance : add, remove, replace, update (séries/reps) ou move (position).",
         _obj({
             "session": {**S, "description": "Nom ou id de la séance"},
             "action": _enum("add", "remove", "replace", "update", "move"),
             "exercise": {**S, "description": "Nom de l'exercice visé (bibliothèque)"},
             "new_exercise": {**S, "description": "Pour replace : le nouvel exercice"},
             "sets": {**INT, "description": "Séries, 1 à 10"},
             "reps": {**S, "description": "Répétitions, ex. 8-12"},
             "position": {**INT, "description": "Place dans la séance, 1 = premier (add, move)"},
         }, ["session", "action", "exercise"])),
    Tool("create_session", CreateSessionArgs, create_session,
         "Crée une séance et l'ajoute à la fin de la rotation.",
         _obj({
             "name": S, "focus": _enum("Force", "Hypertrophie", "Libre"),
             "template": _enum("vide", "pecs-epaules-triceps", "dos-biceps-avant-bras", "jambes-abdos",
                               "full-body", "cardio-gainage", description="Modèle de départ (défaut : vide)"),
             "exercises": {"type": "ARRAY", "items": _obj(
                 {"exercise": S, "sets": INT, "reps": S}, ["exercise"]),
                 "description": "Liste d'exercices (remplace le contenu du modèle)"},
         }, ["name"])),
    Tool("update_session", UpdateSessionArgs, update_session,
         "Renomme une séance, change son type, ou la supprime (delete=true). Garde au moins une séance.",
         _obj({"session": {**S, "description": "Nom ou id"}, "name": S,
               "focus": _enum("Force", "Hypertrophie", "Libre"), "delete": BOOL}, ["session"])),
    Tool("set_rotation_order", RotationArgs, set_rotation_order,
         "Réordonne la rotation. `order` liste TOUTES les séances (noms ou ids), une fois chacune.",
         _obj({"order": {"type": "ARRAY", "items": S}}, ["order"])),
    Tool("create_custom_exercise", CustomExerciseArgs, create_custom_exercise,
         "Crée un exercice perso dans la bibliothèque.",
         _obj({
             "name": S,
             "muscle_group": _enum("Pectoraux", "Dos", "Épaules", "Biceps", "Triceps", "Avant-bras",
                                   "Abdos", "Jambes", "Cardio"),
             "equipment": _enum(*EQUIPMENT_KINDS),
             "is_isolation": BOOL, "is_lower": BOOL, "is_cardio": BOOL, "cardio_unit": S,
         }, ["name", "muscle_group", "equipment"])),
    Tool("search_exercises", SearchExercisesArgs, search_exercises,
         "Cherche dans la bibliothèque (1300+ exercices, noms français, mots-clés anglais acceptés) sans rien modifier. "
         "À utiliser avant d'ajouter un exercice absent de la liste du contexte. 15 résultats maximum.",
         _obj({
             "query": {**S, "description": "Mot-clé, ex. presse, butterfly, poulie, smith, hack squat"},
             "group": _enum("Pectoraux", "Dos", "Épaules", "Biceps", "Triceps", "Avant-bras",
                            "Abdos", "Jambes", "Cardio"),
             "equipment": _enum(*EQUIPMENT_KINDS),
             "max_results": {**INT, "description": "1 à 15 (défaut 10)"},
         })),
    Tool("update_goals", GoalsArgs, update_goals,
         "Change les objectifs : objectif principal, séances/semaine (1-7), protéines/jour (60-300 g), poids visé (30-250 kg).",
         _obj({"objective": _enum("recompo", "muscle", "perte", "forme"), "sessions_goal": INT,
               "protein_goal_g": INT, "target_weight_kg": NUM})),
    Tool("log_today", LogTodayArgs, log_today,
         "Note pour aujourd'hui des protéines mangées (protein_g, 1-300) et/ou la pesée du jour (weight_kg).",
         _obj({"protein_g": INT, "protein_label": {**S, "description": "Ce qui a été mangé, court"},
               "weight_kg": NUM})),
]}

PROGRAM_TOOLS = {"edit_session_exercise", "create_session", "update_session", "set_rotation_order"}
THREAD_TOOLS = {
    "training": list(TOOLS),
    "nutrition": ["update_goals", "log_today"],
}


def execute_tool(db: Session, name: str, raw_args: Any, allowed: list[str] | None = None) -> ToolOutcome:
    """Run one tool call from the model. Raises ToolError for anything refused."""
    tool = TOOLS.get(name) if isinstance(name, str) else None
    if tool is None or (allowed is not None and name not in allowed):
        shown = name[:60] if isinstance(name, str) else "?"
        raise ToolError(f"Outil inconnu ou indisponible : « {shown} ». Outils disponibles : "
                        f"{', '.join(allowed if allowed is not None else TOOLS)}.")
    if raw_args is None:
        raw_args = {}
    if not isinstance(raw_args, dict):
        raise ToolError("Les arguments doivent être un objet JSON.")
    try:
        args = tool.model(**raw_args)
    except ValidationError as error:
        raise ToolError(describe_validation(error)) from None
    before = capture_program(db) if name in PROGRAM_TOOLS else None
    try:
        outcome = tool.run(db, args)
        if before is not None:  # what the program looks like right after: undo refuses if it moved since
            outcome.undo["after_program"] = capture_program(db)
        return outcome
    except Exception as error:  # nothing the model sends may escape as a 500 or leave half a change
        db.rollback()
        if before is not None and capture_program(db) != before:
            restore_program(db, before)  # a half-done change must not stay (a plain refusal changed nothing)
        if isinstance(error, ToolError):
            raise
        if isinstance(error, HTTPException):
            raise ToolError(http_message(error)) from None
        if isinstance(error, ValidationError):
            raise ToolError(describe_validation(error)) from None
        raise ToolError("Ce changement a échoué côté serveur. Rien n'a été modifié.") from None


# --- Undo ---

CHANGED_SINCE = "Ce que le coach avait fait a été modifié depuis : annulation impossible."


def undo_tool(db: Session, tool: str, undo: dict, check: bool = True) -> None:
    """Put back what a tool changed. Raises ToolError when it can't: the thing was changed by hand
    since (then undoing would wipe that work) or the exercise is now in use."""
    if "program" in undo:
        if check and "after_program" in undo and capture_program(db) != undo["after_program"]:
            raise ToolError("Le programme a changé depuis, annulation impossible.")
        restore_program(db, undo["program"])
    elif tool == "create_custom_exercise":
        try:
            exercises_routes.delete_exercise(undo["exercise_slug"], db)
        except HTTPException as error:
            if error.status_code != 404:
                raise ToolError(http_message(error)) from None
    elif tool == "update_goals":
        profile = get_profile(db)
        if check and any(getattr(profile, f) != v for f, v in undo.get("profile_after", {}).items()):
            raise ToolError("Tes objectifs ont changé depuis, annulation impossible.")
        profile_routes.update_profile(ProfileUpdateIn(**undo["profile"]), db)
    elif tool == "log_today":
        if check:
            if undo.get("protein_client_id"):
                row = db.scalars(select(ProteinEntry).where(ProteinEntry.client_id == undo["protein_client_id"])).first()
                expected = undo.get("protein")
                if row is None or (expected and (row.grams, row.label) != (expected["grams"], expected["label"])):
                    raise ToolError(CHANGED_SINCE)
            if undo.get("weight_client_id"):
                row = db.scalars(select(WeightEntry).where(WeightEntry.client_id == undo["weight_client_id"])).first()
                if row is None or ("weight_kg" in undo and row.kg != undo["weight_kg"]):
                    raise ToolError(CHANGED_SINCE)
        # All three reverts in ONE commit: either everything is put back or nothing is
        now = dt.datetime.now()
        try:
            if undo.get("protein_client_id"):
                key = undo["protein_client_id"]
                row = db.scalars(select(ProteinEntry).where(ProteinEntry.client_id == key)).first()
                if row is not None:
                    db.delete(row)
                if db.get(ProteinTombstone, key) is None:
                    db.add(ProteinTombstone(client_id=key, deleted_at=now))
            if undo.get("weight_client_id"):
                key = undo["weight_client_id"]
                row = db.scalars(select(WeightEntry).where(WeightEntry.client_id == key)).first()
                if row is not None:
                    db.delete(row)
                if db.get(WeightTombstone, key) is None:
                    db.add(WeightTombstone(client_id=key, deleted_at=now))
                for r in undo.get("weights_replaced", []):
                    if db.scalars(select(WeightEntry).where(WeightEntry.client_id == r["client_id"])).first():
                        continue
                    db.add(WeightEntry(client_id=r["client_id"], date=dt.date.fromisoformat(r["date"]),
                                       kg=r["kg"], created_at=dt.datetime.fromisoformat(r["created_at"])))
                    tomb = db.get(WeightTombstone, r["client_id"])
                    if tomb is not None:
                        db.delete(tomb)
            db.commit()
        except Exception:
            db.rollback()
            raise
    else:
        raise ToolError("Cette action ne peut pas être annulée.")
