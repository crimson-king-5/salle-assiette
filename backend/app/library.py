"""The big exercise library (SPEC §6): import of the third-party dataset, French search aliases, media route.

The dataset lives in `vendor/exercises-dataset` (git-ignored, cloned by hand). Its DATA is MIT; its MEDIA
(180x180 jpg/gif) belongs to Gym visual: it may only be shown at that size, with the attribution
"© Gym visual — gymvisual.com", and is never copied into git — the files are served from the vendor folder.

The dataset is UNTRUSTED input: it is read with plain json.load, every record is validated (types,
lengths, media paths) and a malformed record is skipped. Nothing in that folder is ever executed.
"""

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.algorithms import normalize_text, slugify
from app.models import Exercise

VENDOR_DIR = Path(__file__).resolve().parents[2] / "vendor" / "exercises-dataset"
MAX_DATASET_BYTES = 60 * 1024 * 1024  # the real file is ~17 MB; refuse anything absurd
log = logging.getLogger(__name__)
ATTRIBUTION = "© Gym visual — gymvisual.com"

MAX_NAME = 100
MAX_RECORDS = 3000  # the real dataset has 1,324: more than this is not our dataset
MAX_STEPS = 30
MAX_STEP_CHARS = 600
MAX_SECONDARY = 8

# --- Dataset values -> the app's French values ---

# Dataset `target` -> app muscle group (algorithms.MuscleGroup)
TARGET_GROUP = {
    "pectorals": "Pectoraux", "serratus anterior": "Pectoraux",
    "lats": "Dos", "upper back": "Dos", "spine": "Dos", "traps": "Dos", "levator scapulae": "Dos",
    "delts": "Épaules", "biceps": "Biceps", "triceps": "Triceps", "forearms": "Avant-bras", "abs": "Abdos",
    "glutes": "Jambes", "quads": "Jambes", "hamstrings": "Jambes", "calves": "Jambes",
    "adductors": "Jambes", "abductors": "Jambes", "cardiovascular system": "Cardio",
}
# Fallback when `target` is unknown: the body part
BODY_PART_GROUP = {
    "chest": "Pectoraux", "back": "Dos", "neck": "Dos", "shoulders": "Épaules", "upper arms": "Biceps",
    "lower arms": "Avant-bras", "waist": "Abdos", "upper legs": "Jambes", "lower legs": "Jambes", "cardio": "Cardio",
}
LOWER_TARGETS = {"glutes", "quads", "hamstrings", "calves", "adductors", "abductors"}

# Dataset `equipment` -> app equipment (algorithms.Equipment)
EQUIPMENT_MAP = {
    "body weight": "Poids du corps", "weighted": "Poids du corps",
    "dumbbell": "Poids libre", "barbell": "Poids libre", "olympic barbell": "Poids libre",
    "trap bar": "Poids libre", "hammer": "Poids libre",
    "leverage machine": "Machine", "sled machine": "Machine", "assisted": "Machine",
    "skierg machine": "Machine", "upper body ergometer": "Machine", "stationary bike": "Machine",
    "elliptical machine": "Machine", "stepmill machine": "Machine",
    "cable": "Poulie", "rope": "Poulie",
    "smith machine": "Smith", "kettlebell": "Kettlebell", "ez barbell": "Barre EZ",
    "band": "Élastique", "resistance band": "Élastique",
    "stability ball": "Autre", "medicine ball": "Autre", "bosu ball": "Autre", "roller": "Autre",
    "wheel roller": "Autre", "tire": "Autre",
}
CARDIO_LEVEL_EQUIPMENT = {"stationary bike", "elliptical machine", "stepmill machine", "skierg machine",
                          "upper body ergometer"}

# English muscle names -> French, for the badges of the viewer
MUSCLE_FR = {
    "pectorals": "Pectoraux", "chest": "Pectoraux", "upper chest": "Haut des pectoraux",
    "serratus anterior": "Dentelé antérieur", "lats": "Grands dorsaux", "latissimus dorsi": "Grands dorsaux",
    "upper back": "Haut du dos", "back": "Dos", "lower back": "Lombaires", "spine": "Colonne",
    "traps": "Trapèzes", "trapezius": "Trapèzes", "rhomboids": "Rhomboïdes",
    "levator scapulae": "Élévateur de la scapula", "delts": "Deltoïdes", "deltoids": "Deltoïdes",
    "shoulders": "Épaules", "rear deltoids": "Deltoïdes postérieurs", "rotator cuff": "Coiffe des rotateurs",
    "biceps": "Biceps", "brachialis": "Brachial", "triceps": "Triceps", "forearms": "Avant-bras",
    "wrist flexors": "Fléchisseurs du poignet", "wrist extensors": "Extenseurs du poignet",
    "wrists": "Poignets", "hands": "Mains", "grip muscles": "Muscles de la prise",
    "abs": "Abdominaux", "abdominals": "Abdominaux", "lower abs": "Bas des abdos", "obliques": "Obliques",
    "core": "Gainage", "hip flexors": "Fléchisseurs de hanche", "glutes": "Fessiers", "quads": "Quadriceps",
    "quadriceps": "Quadriceps", "hamstrings": "Ischio-jambiers", "calves": "Mollets", "soleus": "Soléaire",
    "adductors": "Adducteurs", "abductors": "Abducteurs", "inner thighs": "Intérieur des cuisses",
    "groin": "Aine", "ankles": "Chevilles", "ankle stabilizers": "Stabilisateurs de cheville",
    "feet": "Pieds", "shins": "Tibias", "sternocleidomastoid": "Sterno-cléido-mastoïdien",
    "cardiovascular system": "Cardio",
}

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_ID = re.compile(r"[A-Za-z0-9_-]{1,16}\Z")
_IMAGE_PATH = re.compile(r"images/[A-Za-z0-9_-][A-Za-z0-9_.-]{0,120}\.(?:jpg|jpeg|png)\Z")
_GIF_PATH = re.compile(r"videos/[A-Za-z0-9_-][A-Za-z0-9_.-]{0,120}\.gif\Z")
_MEDIA_FILE = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9_.-]{0,120}\Z")
_MEDIA_KIND_EXT = {"images": (".jpg", ".jpeg", ".png"), "videos": (".gif",)}


@dataclass
class Record:
    """One validated dataset record, already mapped to the app's vocabulary."""
    dataset_id: str
    name: str
    muscle_group: str
    equipment: str
    is_isolation: bool
    is_lower: bool
    is_cardio: bool
    cardio_unit: str | None
    target: str
    secondary: list[str]
    steps: list[str]
    image: str
    gif: str
    source_equipment: str = ""  # dataset value, for French equipment aliases
    english: str = ""  # normalized english name, for alias rules
    aliases: list[str] = field(default_factory=list)


# --- Validation and mapping ---

def _clean(value: object, limit: int) -> str | None:
    """A single-line text, trimmed, no control characters; None when it isn't text or is empty/too long."""
    if not isinstance(value, str):
        return None
    try:
        value.encode("utf-8")  # a lone surrogate can't be stored (and would crash the import)
    except UnicodeEncodeError:
        return None
    text = re.sub(r"\s+", " ", _CONTROL.sub(" ", value)).strip()
    return text if 0 < len(text) <= limit else None


def clean_name(raw: object) -> str | None:
    name = _clean(raw, MAX_NAME * 2)
    if name is None:
        return None
    name = name.replace("в°", "°")  # mojibake present in a few dataset names
    name = name[0].upper() + name[1:]
    return name if len(name) <= MAX_NAME else None


ISOLATION_WORDS = ("curl", "extension", "raise", " fly", "flye", "kickback", "pushdown", "pull down", "shrug",
                   "pullover", "adduction", "abduction", "crossover", "cross over", "wrist", "calf", "crunch",
                   "sit up", "leg raise")


def is_isolation_name(english: str) -> bool:
    return any(word in f" {english} " for word in ISOLATION_WORDS)


def parse_record(raw: object) -> Record | None:
    """Validate and map one dataset record; None (skip it) when anything essential is malformed."""
    if not isinstance(raw, dict):
        return None
    dataset_id = raw.get("id")
    if not isinstance(dataset_id, str) or not _ID.match(dataset_id):
        return None
    name = clean_name(raw.get("name"))
    if name is None:
        return None
    image, gif = raw.get("image"), raw.get("gif_url")
    if not (isinstance(image, str) and _IMAGE_PATH.match(image) and isinstance(gif, str) and _GIF_PATH.match(gif)):
        return None
    steps_block = raw.get("instruction_steps")
    steps_raw = steps_block.get("fr") if isinstance(steps_block, dict) else None
    if not isinstance(steps_raw, list):
        return None
    steps = [s for s in (_clean(item, MAX_STEP_CHARS) for item in steps_raw[:MAX_STEPS]) if s]
    if not steps:
        return None

    body_part = (_clean(raw.get("body_part"), 40) or "").lower()
    equipment_raw = (_clean(raw.get("equipment"), 40) or "").lower()
    target_raw = (_clean(raw.get("target"), 40) or "").lower()
    group = TARGET_GROUP.get(target_raw) or BODY_PART_GROUP.get(body_part) or "Jambes"
    is_cardio = group == "Cardio" or body_part == "cardio"
    if is_cardio:
        group = "Cardio"
    secondary_raw = raw.get("secondary_muscles")
    secondary = []
    if isinstance(secondary_raw, list):
        for item in secondary_raw[:MAX_SECONDARY]:
            text = _clean(item, 40)
            if text:
                label = MUSCLE_FR.get(text.lower(), text[0].upper() + text[1:])
                if label not in secondary:
                    secondary.append(label)
    english = normalize_text(name)
    return Record(
        dataset_id=dataset_id, name=name, muscle_group=group,
        equipment=EQUIPMENT_MAP.get(equipment_raw, "Autre"),
        is_isolation=not is_cardio and is_isolation_name(english),
        is_lower=target_raw in LOWER_TARGETS,
        is_cardio=is_cardio,
        cardio_unit=("niveau" if equipment_raw in CARDIO_LEVEL_EQUIPMENT else None) if is_cardio else None,
        target=MUSCLE_FR.get(target_raw, target_raw[:1].upper() + target_raw[1:]) if target_raw else "",
        secondary=secondary, steps=steps, image=image, gif=gif, english=english,
        source_equipment=equipment_raw,
    )


def load_records(dataset_dir: Path | None = None) -> dict[str, Record]:
    """Every valid record of the dataset, by dataset id. {} when the dataset isn't there or is unreadable."""
    path = (dataset_dir or VENDOR_DIR) / "data" / "exercises.json"
    try:
        if not path.is_file() or path.stat().st_size > MAX_DATASET_BYTES:
            return {}
        with path.open(encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError, RecursionError):
        return {}
    if not isinstance(data, list):
        return {}
    if len(data) > MAX_RECORDS:
        log.warning("Exercise library import skipped: %d records (maximum %d)", len(data), MAX_RECORDS)
        return {}
    records: dict[str, Record] = {}
    seen_names: dict[str, int] = {}
    for raw in data:
        record = parse_record(raw)
        if record is None or record.dataset_id in records:
            continue
        # A few dataset names are shared by two different exercises: keep names unique
        key = normalize_text(record.name)
        seen_names[key] = seen_names.get(key, 0) + 1
        if seen_names[key] > 1:
            record.name = f"{record.name} ({seen_names[key]})"
        record.aliases = french_aliases(record)
        records[record.dataset_id] = record
    return records


# --- French search aliases (SPEC §4.4: "presse à cuisses", "butterfly", "poulie haute"…) ---

# (regex on the normalized english name, aliases). Every matching rule adds its words.
ALIAS_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (r"\bleg press\b", ("presse a cuisses", "presse cuisses", "presse a jambes", "presse")),
    (r"\bhack squat\b", ("hack squat", "presse hack", "squat hack")),
    (r"^lever seated fly$", ("pec deck", "pec deck butterfly", "butterfly", "papillon", "ecarte machine", "ecartes machine")),
    (r"^lever seated reverse fly", ("pec deck inverse", "reverse pec deck", "oiseau machine", "butterfly inverse")),
    (r"\b(cable|band) .*\bfly\b|\bcable cross over\b|\bcable crossover\b", ("ecarte poulie", "ecartes poulie", "vis a vis", "croise poulie")),
    (r"\bdumbbell (incline |decline )?(one arm )?fly\b|\bdumbbell fly\b", ("ecarte halteres", "ecartes halteres")),
    (r"\b(reverse|rear) (delt )?fly\b|\brear lateral raise\b", ("oiseau", "oiseaux", "deltoides posterieurs")),
    (r"(?<!straight arm )\bpulldown\b", ("tirage vertical", "tirage poitrine", "poulie haute", "lat pulldown")),
    (r"\bstraight arm pulldown\b", ("pull over poulie", "pullover poulie", "bras tendus")),
    (r"\b(seated|low|narrow grip|wide grip|straight back) .*\brow\b|\bseated row\b|\bcable low seated row\b", ("tirage horizontal", "rowing assis", "poulie basse")),
    (r"\bhigh row\b", ("tirage haut", "rowing haut", "poulie haute")),
    (r"\bt bar row\b", ("rowing barre t", "t bar", "rowing t")),
    (r"\b(barbell|dumbbell|ez barbell|smith|lever|ez bar) .*(bent over )?row\b|\bbent over row\b|\bone arm row\b", ("rowing", "tirage buste penche")),
    (r"\bbarbell (incline |decline |wide )?bench press$|\bbarbell bench press\b|\bdumbbell bench press\b|\bbench press\b", ("developpe couche", "bench press", "bench")),
    (r"\bincline (bench )?press\b|\bincline chest press\b", ("developpe incline",)),
    (r"\bdecline (bench )?press\b|\bdecline chest press\b", ("developpe decline",)),
    (r"\bchest press\b", ("developpe couche machine", "presse pectoraux", "developpe machine")),
    (r"\bmilitary press\b|\boverhead press\b|\bshoulder press\b|\bseated press\b", ("developpe militaire", "developpe epaules", "overhead press")),
    (r"\barnold press\b", ("developpe arnold",)),
    (r"\bclose grip bench press\b|\bclose grip press\b", ("developpe prise serree",)),
    (r"\btriceps (pushdown|extension|press)|\bcable pushdown\b|\bpushdown\b|\bskull crusher\b|\boverhead triceps\b", ("extension triceps", "triceps poulie", "barre au front")),
    (r"\bdip\b|\bdips\b", ("dips", "barres paralleles")),
    (r"\bcurl\b", ("curl",)),
    (r"\bpreacher curl\b", ("curl pupitre", "pupitre", "larry scott")),
    (r"\bhammer curl\b|\bcable hammer curl\b", ("curl marteau", "marteau")),
    (r"\bconcentration curl\b", ("curl concentre", "curl concentration")),
    (r"\bleg extension\b", ("leg extension", "extension jambes", "extension quadriceps", "quadriceps")),
    (r"\b(lying |seated |kneeling )?leg curl\b", ("leg curl", "curl jambes", "ischios", "ischio jambiers")),
    (r"\bhip adduction\b|\badductor\b", ("adducteurs", "adducteur", "machine adducteurs")),
    (r"\bhip abduction\b|\babductor\b", ("abducteurs", "abducteur", "machine abducteurs")),
    (r"\bcalf (raise|press)\b|\bdonkey calf\b|\brotary calf\b", ("mollets", "releve de mollets")),
    (r"\bseated calf\b", ("mollets assis", "soleaire")),
    (r"\bstanding calf\b", ("mollets debout",)),
    (r"\bsquat\b", ("squat",)),
    (r"\bfront squat\b", ("squat avant",)),
    (r"\b(single leg |bulgarian )?split squat\b", ("squat bulgare", "fente statique")),
    (r"\bdeadlift\b", ("souleve de terre", "sdt")),
    (r"\bromanian deadlift\b|\bstiff leg deadlift\b|\bstraight leg deadlift\b", ("souleve de terre roumain", "sdt roumain", "jambes tendues")),
    (r"\blunge\b", ("fentes", "fente")),
    (r"\bstep up\b", ("montee sur banc", "step up")),
    (r"\b(hip thrust|glute bridge)\b", ("hip thrust", "pont fessier", "fessiers")),
    (r"\bglute kickback\b|\bdonkey kick\b|\bkickback\b", ("kickback", "fessiers")),
    (r"\bpull up\b|\bpullup\b|\bchin up\b", ("traction", "tractions", "pull up")),
    (r"\bpush up\b|\bpushup\b", ("pompes", "pompe")),
    (r"\bsit up\b|\bcrunch\b", ("crunch", "abdos", "abdominaux")),
    (r"\bplank\b", ("gainage", "planche")),
    (r"\b(hanging |lying )?leg raise\b|\bknee raise\b", ("releve de jambes", "releve de genoux")),
    (r"\bshrug\b", ("shrugs", "haussements d epaules", "trapezes")),
    (r"\blateral raise\b", ("elevations laterales", "elevation laterale")),
    (r"\bfront raise\b", ("elevations frontales", "elevation frontale")),
    (r"\bupright row\b", ("rowing menton", "tirage menton")),
    (r"\bface pull\b|\brear delt row\b", ("face pull", "tirage visage")),
    (r"\bpullover\b|\bpull over\b", ("pull over", "pullover")),
    (r"\bback extension\b|\bhyperextension\b", ("lombaires", "extension lombaire", "banc a lombaires")),
    (r"\bkettlebell swing\b", ("swing kettlebell", "swing")),
    (r"\bwrist curl\b|\bwrist extension\b", ("poignets", "avant bras")),
    (r"\b(stationary bike|air bike|bike)\b", ("velo", "velo stationnaire")),
    (r"\b(treadmill|run|jog)\w*\b", ("course", "tapis de course")),
    (r"\belliptical\b", ("velo elliptique", "elliptique")),
    (r"\bjump rope\b|\bskipping\b", ("corde a sauter",)),
)
_COMPILED_RULES = tuple((re.compile(pattern), words) for pattern, words in ALIAS_RULES)

EQUIPMENT_ALIASES = {
    "Smith": ("smith", "multipower"),
    "Poulie": ("cable", "poulie"),
    "Élastique": ("elastique", "bande elastique", "band"),
    "Barre EZ": ("barre ez", "barre z", "ez bar"),
}
SOURCE_EQUIPMENT_ALIASES = {
    "barbell": ("barre",), "olympic barbell": ("barre",), "dumbbell": ("halteres", "haltere"), "cable": ("poulie",),
    "leverage machine": ("machine",), "sled machine": ("machine",), "assisted": ("machine", "assiste"),
    "body weight": ("poids du corps",), "kettlebell": ("kettlebell",), "ez barbell": ("barre ez",),
    "band": ("elastique",), "resistance band": ("elastique",), "smith machine": ("smith", "multipower"),
    "trap bar": ("barre hexagonale",), "rope": ("corde",),
}
MAX_ALIAS_CHARS = 200


def french_aliases(record: Record) -> list[str]:
    """Search words (French + common english) for an imported exercise, from rules on its name/equipment."""
    words: list[str] = []
    english = record.english  # normalized ("cross-over" -> "cross over"): the rules are written on that form
    for pattern, aliases in _COMPILED_RULES:
        if pattern.search(english):
            words.extend(aliases)
    words.extend(EQUIPMENT_ALIASES.get(record.equipment, ()))
    words.extend(SOURCE_EQUIPMENT_ALIASES.get(record.source_equipment, ()))
    result, size = [], 0
    for word in words:
        if word in result or word == normalize_text(record.name):
            continue
        if size + len(word) + 1 > MAX_ALIAS_CHARS:
            break
        result.append(word)
        size += len(word) + 1
    return result


# --- Linking the app's own exercises (seed + prototype) to their dataset match ---

# app slug -> dataset id. They keep their French names; they gain the animation and the steps.
SEED_LINKS = {
    "developpe-couche-barre": "0025",      # barbell bench press
    "rowing-barre": "0027",                # barbell bent over row
    "developpe-militaire": "0091",         # barbell seated overhead press
    "tirage-vertical": "0150",             # cable bar lateral pulldown
    "curl-halteres": "0294",               # dumbbell biceps curl
    "extension-triceps-poulie": "0201",    # cable pushdown
    "squat": "0043",                       # barbell full squat
    "sdt-roumain": "0085",                 # barbell romanian deadlift
    "presse-cuisses": "1463",              # sled 45° leg press (side pov)
    "leg-curl": "0586",                    # lever lying leg curl
    "mollets-assis": "0594",               # lever seated calf raise
    "developpe-incline-halteres": "0314",  # dumbbell incline bench press
    "tirage-horizontal": "0861",           # cable seated row
    "elevations-laterales": "0334",        # dumbbell lateral raise
    "developpe-prise-serree": "0030",      # barbell close-grip bench press
    "curl-incline": "0318",                # dumbbell incline curl
    "face-pull": "0233",                   # cable standing rear delt row (with rope): the dataset's face pull
    "squat-bulgare": "0410",               # dumbbell single leg split squat
    "hip-thrust": "1409",                  # barbell glute bridge
    "leg-extension": "0585",               # lever leg extension
    "leg-curl-assis": "0599",              # lever seated leg curl
    "mollets-debout": "0605",              # lever standing calf raise
}


def apply_media(exercise: Exercise, record: Record) -> None:
    exercise.dataset_id = record.dataset_id
    exercise.media_image = record.image
    exercise.media_gif = record.gif
    exercise.steps_fr = json.dumps(record.steps, ensure_ascii=False)
    exercise.target_muscle = record.target or None
    exercise.secondary_muscles = json.dumps(record.secondary, ensure_ascii=False)


def link_existing(db: Session, records: dict[str, Record]) -> int:
    """Attach the seeded exercises to their dataset match. Never touches their name, group or aliases."""
    linked = 0
    for slug, dataset_id in SEED_LINKS.items():
        exercise = db.get(Exercise, slug)
        record = records.get(dataset_id)
        if exercise is None or record is None or exercise.is_custom or exercise.is_library:
            continue
        if exercise.dataset_id not in (None, dataset_id):
            continue  # linked to something else on purpose: leave it
        before = (exercise.dataset_id, exercise.media_image, exercise.media_gif, exercise.steps_fr,
                  exercise.target_muscle, exercise.secondary_muscles)
        apply_media(exercise, record)
        if before != (exercise.dataset_id, exercise.media_image, exercise.media_gif, exercise.steps_fr,
                      exercise.target_muscle, exercise.secondary_muscles):
            linked += 1
    return linked


def _library_slug(db: Session, record: Record, taken: set[str]) -> str:
    base = slugify(record.name)
    slug = base
    if slug in taken or db.get(Exercise, slug) is not None:
        slug = f"{base[:60].strip('-')}-{record.dataset_id}"
    taken.add(slug)
    return slug


def import_library(db: Session, dataset_dir: Path | None = None) -> dict[str, int]:
    """Idempotent: add / refresh the dataset exercises, keyed on dataset id. Does nothing without the dataset.

    - an existing row with that dataset id is refreshed (library rows fully; linked seed rows only their media);
    - custom exercises are never touched (they never carry a dataset id);
    - one commit at the end.
    """
    records = load_records(dataset_dir)
    stats = {"records": len(records), "created": 0, "updated": 0, "linked": 0}
    if not records:
        return stats
    stats["linked"] = link_existing(db, records)
    by_dataset = {e.dataset_id: e for e in db.scalars(select(Exercise).where(Exercise.dataset_id.is_not(None)))}
    taken: set[str] = set()
    for dataset_id, record in records.items():
        row = by_dataset.get(dataset_id)
        is_new = row is None
        if row is None:
            row = Exercise(slug=_library_slug(db, record, taken), name_fr=record.name, is_library=True)
            db.add(row)
            stats["created"] += 1
        elif not row.is_library:
            continue  # a seeded exercise linked to this record: it was already refreshed by link_existing
        fields = {
            "name_fr": record.name, "muscle_group": record.muscle_group, "equipment": record.equipment,
            "is_isolation": record.is_isolation, "is_lower": record.is_lower, "is_cardio": record.is_cardio,
            "cardio_unit": record.cardio_unit, "aliases": "|".join(record.aliases),
            "is_custom": False, "needs_review": False,
        }
        for key, value in fields.items():
            if getattr(row, key, None) != value:
                setattr(row, key, value)
        apply_media(row, record)
        if not is_new and db.is_modified(row):
            stats["updated"] += 1
    db.commit()
    return stats


# --- Reading the stored fields back ---

def steps_of(exercise: Exercise) -> list[str]:
    return _json_list(exercise.steps_fr)


def secondary_of(exercise: Exercise) -> list[str]:
    return _json_list(exercise.secondary_muscles)


def _json_list(text: str | None) -> list[str]:
    try:
        value = json.loads(text) if text else []
    except ValueError:
        return []
    return [s for s in value if isinstance(s, str)] if isinstance(value, list) else []


def media_file(path: str | None) -> str | None:
    """File name of a stored media path ("images/0001-x.jpg" -> "0001-x.jpg"); None when absent."""
    return path.split("/", 1)[1] if path and "/" in path else None


# --- Serving the media: GET /media/ex/{images|videos}/{file} ---

media_router = APIRouter(tags=["media"])
CACHE_HEADERS = {"Cache-Control": "public, max-age=31536000, immutable"}


@media_router.get("/media/ex/{kind}/{filename}", include_in_schema=False)
def get_media(kind: str, filename: str) -> FileResponse:
    """One image or GIF of the dataset. Only a strict file name inside images/ or videos/ is accepted."""
    extensions = _MEDIA_KIND_EXT.get(kind)
    if extensions is None or not _MEDIA_FILE.match(filename) or ".." in filename \
            or not filename.lower().endswith(extensions):
        raise HTTPException(status_code=404, detail="Média introuvable")
    root = VENDOR_DIR.resolve()
    folder = (VENDOR_DIR / kind).resolve()
    path = (folder / filename).resolve()
    if folder.parent != root or path.parent != folder or not path.is_file():
        raise HTTPException(status_code=404, detail="Média introuvable")
    return FileResponse(path, headers=CACHE_HEADERS)
