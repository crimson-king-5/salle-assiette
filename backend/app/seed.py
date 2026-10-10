"""Default program: 4-day Upper/Lower split (SPEC §6 "Programme par défaut").

Inserted once, on first start. Never overwrites existing sessions,
so edits made later in the app are kept.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Exercise, SessionExercise, SessionTemplate

# slug: (name, muscle group, equipment, is_isolation, is_lower)
EXERCISES = {
    "developpe-couche-barre": ("Développé couché barre", "Pectoraux", "Poids libre", False, False),
    "rowing-barre": ("Rowing barre", "Dos", "Poids libre", False, False),
    "developpe-militaire": ("Développé militaire", "Épaules", "Poids libre", False, False),
    "tirage-vertical": ("Tirage vertical", "Dos", "Poulie", False, False),
    "curl-halteres": ("Curl haltères", "Biceps", "Poids libre", True, False),
    "extension-triceps-poulie": ("Extension triceps poulie", "Triceps", "Poulie", True, False),
    "squat": ("Squat", "Jambes", "Poids libre", False, True),
    "sdt-roumain": ("Soulevé de terre roumain", "Jambes", "Poids libre", False, True),
    "presse-cuisses": ("Presse à cuisses", "Jambes", "Machine", False, True),
    "leg-curl": ("Leg curl allongé", "Jambes", "Machine", True, True),
    "mollets-assis": ("Mollets assis", "Jambes", "Machine", True, True),
    "developpe-incline-halteres": ("Développé incliné haltères", "Pectoraux", "Poids libre", False, False),
    "tirage-horizontal": ("Tirage horizontal", "Dos", "Poulie", False, False),
    "elevations-laterales": ("Élévations latérales", "Épaules", "Poids libre", True, False),
    "developpe-prise-serree": ("Développé couché prise serrée", "Triceps", "Poids libre", False, False),
    "curl-incline": ("Curl incliné haltères", "Biceps", "Poids libre", True, False),
    "face-pull": ("Face pull", "Épaules", "Poulie", True, False),
    "squat-bulgare": ("Squat bulgare", "Jambes", "Poids libre", False, True),
    "hip-thrust": ("Hip thrust", "Jambes", "Poids libre", False, True),
    "leg-extension": ("Leg extension", "Jambes", "Machine", True, True),
    "leg-curl-assis": ("Leg curl assis", "Jambes", "Machine", True, True),
    "mollets-debout": ("Mollets debout", "Jambes", "Machine", True, True),
}

# Search words (SPEC §4.4); stored in exercise.aliases, "|"-separated
ALIASES = {
    "developpe-couche-barre": ["bench press", "bench", "developpe couche"],
    "rowing-barre": ["barbell row", "tirage barre", "bent over row"],
    "developpe-militaire": ["overhead press", "military press", "ohp"],
    "tirage-vertical": ["lat pulldown", "poulie haute", "tirage poitrine", "pulldown"],
    "curl-halteres": ["biceps curl", "dumbbell curl"],
    "extension-triceps-poulie": ["pushdown", "triceps pushdown", "poulie haute triceps"],
    "squat": ["back squat", "squat barre"],
    "sdt-roumain": ["romanian deadlift", "rdl", "souleve de terre jambes tendues"],
    "presse-cuisses": ["leg press", "presse a cuisses", "presse inclinee"],
    "leg-curl": ["ischios", "ischio-jambiers", "hamstring curl"],
    "mollets-assis": ["calf raise", "seated calf", "soleaire"],
    "developpe-incline-halteres": ["incline press", "incline dumbbell press"],
    "tirage-horizontal": ["seated row", "rowing poulie", "poulie basse"],
    "elevations-laterales": ["lateral raise", "side raise"],
    "developpe-prise-serree": ["close grip bench", "close-grip"],
    "curl-incline": ["incline curl", "incline dumbbell curl"],
    "face-pull": ["facepull", "tirage visage"],
    "squat-bulgare": ["bulgarian split squat", "split squat"],
    "hip-thrust": ["pont fessier", "glute bridge", "fessiers"],
    "leg-extension": ["extension jambes", "quadriceps", "quads"],
    "leg-curl-assis": ["seated leg curl", "ischios assis"],
    "mollets-debout": ["standing calf raise", "calf raise", "mollets"],
}

# (id, name, focus, [(exercise slug, sets, reps), …]) — in rotation order
DEFAULT_PROGRAM = [
    ("HA", "Haut A", "Force", [
        ("developpe-couche-barre", 4, "5-6"),
        ("rowing-barre", 4, "6-8"),
        ("developpe-militaire", 3, "6-10"),
        ("tirage-vertical", 3, "8-10"),
        ("curl-halteres", 2, "10-12"),
        ("extension-triceps-poulie", 2, "10-12"),
    ]),
    ("BA", "Bas A", "Force", [
        ("squat", 4, "5-6"),
        ("sdt-roumain", 3, "6-8"),
        ("presse-cuisses", 3, "10-12"),
        ("leg-curl", 3, "10-12"),
        ("mollets-assis", 4, "10-15"),
    ]),
    ("HB", "Haut B", "Hypertrophie", [
        ("developpe-incline-halteres", 4, "8-12"),
        ("tirage-horizontal", 4, "10-12"),
        ("elevations-laterales", 4, "12-15"),
        ("developpe-prise-serree", 3, "10-12"),
        ("curl-incline", 3, "12-15"),
        ("face-pull", 3, "15-20"),
    ]),
    ("BB", "Bas B", "Hypertrophie", [
        ("squat-bulgare", 3, "10-12"),
        ("hip-thrust", 3, "10-12"),
        ("leg-extension", 3, "12-15"),
        ("leg-curl-assis", 3, "12-15"),
        ("mollets-debout", 4, "12-20"),
    ]),
]


def seed_default_program(db: Session) -> None:
    if db.scalar(select(SessionTemplate.id).limit(1)) is not None:
        return  # a program already exists: leave it alone

    for slug, (name, group, equipment, iso, lower) in EXERCISES.items():
        if db.get(Exercise, slug) is None:
            db.add(Exercise(slug=slug, name_fr=name, muscle_group=group,
                            equipment=equipment, is_isolation=iso, is_lower=lower,
                            aliases="|".join(ALIASES.get(slug, []))))

    for position, (session_id, name, focus, exercises) in enumerate(DEFAULT_PROGRAM):
        db.add(SessionTemplate(
            id=session_id, name=name, focus=focus, position=position,
            exercises=[
                SessionExercise(position=i, exercise_slug=slug, sets=sets, reps=reps)
                for i, (slug, sets, reps) in enumerate(exercises)
            ],
        ))
    db.commit()


def backfill_aliases(db: Session) -> None:
    """Give the seeded exercises of an older database their search aliases (only if empty)."""
    changed = False
    for slug, words in ALIASES.items():
        exercise = db.get(Exercise, slug)
        if exercise is not None and not exercise.aliases and not exercise.is_custom:
            exercise.aliases = "|".join(words)
            changed = True
    if changed:
        db.commit()
