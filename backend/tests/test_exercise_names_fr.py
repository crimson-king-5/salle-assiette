"""French names of the exercise library (app/data/exercise_names_fr.json, applied by library.import_library)."""

import json
import re
from pathlib import Path

import pytest
from sqlalchemy import select

from app import coach_tools, library
from app.algorithms import normalize_text
from app.exercises import find_by_name, search_library
from app.models import Exercise
from app.prototype_import import Library
from app.seed import EXERCISES

REAL_DATASET = Path(__file__).resolve().parents[2] / "vendor" / "exercises-dataset"
needs_real = pytest.mark.skipif(not (REAL_DATASET / "data" / "exercises.json").is_file(),
                                reason="vendor dataset not present")
CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def record(dataset_id, name, **over):
    base = {
        "id": dataset_id, "name": name, "body_part": "chest", "equipment": "barbell", "target": "pectorals",
        "secondary_muscles": ["triceps"], "instruction_steps": {"fr": ["Étape un."]},
        "image": f"images/{dataset_id}-abc.jpg", "gif_url": f"videos/{dataset_id}-abc.gif",
    }
    base.update(over)
    return base


def write_dataset(folder: Path, records: list) -> Path:
    (folder / "data").mkdir(parents=True, exist_ok=True)
    (folder / "data" / "exercises.json").write_text(json.dumps(records), encoding="utf-8")
    return folder


RECORDS = [
    record("0025", "barbell bench press"),                       # linked to a seeded exercise
    record("0596", "lever seated fly", equipment="leverage machine"),
    record("0150", "cable bar lateral pulldown", body_part="back", equipment="cable", target="lats"),  # seeded link
    record("3001", "stationary bike", body_part="cardio", equipment="stationary bike", target="cardiovascular system"),
    record("7001", "lever lat pulldown", body_part="back", equipment="leverage machine", target="lats"),
]
NAMES = {"0025": "Développé couché à la barre", "0596": "Pec deck (butterfly)", "0150": "Tirage à la poulie",
         "3001": "Vélo stationnaire", "7001": "Tirage poitrine à la machine"}


@pytest.fixture
def dataset(tmp_path):
    return write_dataset(tmp_path / "dataset", RECORDS)


def rows(db):
    return {e.dataset_id: e for e in db.scalars(select(Exercise)) if e.dataset_id}


# --- The translation file itself ---

def test_translation_file_is_well_formed_and_unique():
    names = library.load_french_names()
    raw = json.loads(library.NAMES_FR_PATH.read_text(encoding="utf-8"))
    assert len(names) == len(raw) > 1300  # nothing was dropped by the validation
    normalized = [normalize_text(n) for n in names.values()]
    assert len(set(normalized)) == len(normalized), "French names must be unique (accent/case-insensitive)"
    for dataset_id, name in names.items():
        assert name and name == name.strip() and len(name) <= library.MAX_NAME, dataset_id
        assert not CONTROL.search(name), dataset_id
    # none takes the name of one of Karim's seeded exercises (those keep their own French names)
    seeded = {normalize_text(spec[0]) for spec in EXERCISES.values()}
    assert not [i for i, n in names.items() if normalize_text(n) in seeded]


@needs_real
def test_every_dataset_record_has_a_translation():
    records = library.load_records(REAL_DATASET)
    names = library.load_french_names()
    assert len(records) > 1300
    assert set(records) <= set(names), sorted(set(records) - set(names))
    assert set(names) <= set(records), sorted(set(names) - set(records))  # no typo'd id


def test_load_french_names_skips_bad_entries_and_missing_file(tmp_path):
    path = tmp_path / "names.json"
    path.write_text(json.dumps({"0001": "Bon nom", "bad id!": "x", "0002": "", "0003": 5, "0004": "a" * 300,
                                "0005": "tab\there"}), encoding="utf-8")
    assert library.load_french_names(path) == {"0001": "Bon nom", "0005": "tab here"}
    assert library.load_french_names(tmp_path / "missing.json") == {}
    path.write_text("[1, 2]", encoding="utf-8")
    assert library.load_french_names(path) == {}
    path.write_text("not json", encoding="utf-8")
    assert library.load_french_names(path) == {}


# --- Applying them ---

def test_french_names_apply_to_library_rows_keep_slugs_and_english_aliases(db, dataset):
    stats = library.import_library(db, dataset, names_fr=NAMES)
    assert stats["french"] == 3 and stats["clashes"] == 0  # 0025 and 0150 are seeded exercises: not renamed
    by_id = rows(db)
    fly = by_id["0596"]
    assert (fly.name_fr, fly.slug, fly.is_library) == ("Pec deck (butterfly)", "lever-seated-fly", True)
    assert "lever seated fly" in fly.aliases.split("|") and "butterfly" in fly.aliases.split("|")
    assert normalize_text(fly.name_fr) not in fly.aliases.split("|")
    assert by_id["3001"].name_fr == "Vélo stationnaire" and by_id["3001"].slug == "stationary-bike"
    assert by_id["7001"].name_fr == "Tirage poitrine à la machine"


def test_application_is_idempotent_and_survives_a_reimport_without_translations(db, dataset):
    library.import_library(db, dataset, names_fr=NAMES)
    state = sorted((e.slug, e.name_fr, e.aliases) for e in db.scalars(select(Exercise)))
    again = library.import_library(db, dataset, names_fr=NAMES)
    assert (again["created"], again["updated"], again["linked"]) == (0, 0, 0)
    assert sorted((e.slug, e.name_fr, e.aliases) for e in db.scalars(select(Exercise))) == state
    # a run without the translation file puts the English names back, the slugs never move
    library.import_library(db, dataset, names_fr={})
    assert rows(db)["0596"].name_fr == "Lever seated fly" and rows(db)["0596"].slug == "lever-seated-fly"
    library.import_library(db, dataset, names_fr=NAMES)
    assert sorted((e.slug, e.name_fr, e.aliases) for e in db.scalars(select(Exercise))) == state


def test_existing_english_rows_are_renamed_in_place(db, dataset):
    library.import_library(db, dataset, names_fr={})  # what the DB looked like before the translations
    before = {e.dataset_id: e.slug for e in db.scalars(select(Exercise).where(Exercise.is_library))}
    stats = library.import_library(db, dataset, names_fr=NAMES)
    assert stats["created"] == 0 and stats["updated"] == 3
    assert {e.dataset_id: e.slug for e in db.scalars(select(Exercise).where(Exercise.is_library))} == before


def test_seeded_and_custom_exercises_are_never_renamed(db, dataset):
    custom = Exercise(slug="mon-pec-deck", name_fr="Mon pec deck", muscle_group="Pectoraux", equipment="Machine",
                      is_custom=True, aliases="perso")
    db.add(custom)
    db.commit()
    seeded = {e.slug: (e.name_fr, e.aliases) for e in db.scalars(select(Exercise)) if not e.is_custom}
    library.import_library(db, dataset, names_fr=NAMES)
    library.import_library(db, dataset, names_fr=NAMES)
    db.refresh(custom)
    assert (custom.name_fr, custom.aliases, custom.dataset_id) == ("Mon pec deck", "perso", None)
    for slug, (name, aliases) in seeded.items():
        e = db.get(Exercise, slug)
        assert (e.name_fr, e.aliases) == (name, aliases), slug
    assert db.get(Exercise, "developpe-couche-barre").name_fr == "Développé couché barre"


def test_a_name_taken_by_karims_own_exercise_falls_back_without_crashing(db, dataset):
    db.add(Exercise(slug="velo", name_fr="velo STATIONNAIRE", muscle_group="Cardio", equipment="Machine",
                    is_custom=True, is_cardio=True))
    db.commit()
    stats = library.import_library(db, dataset, names_fr=NAMES)
    assert stats["clashes"] == 1
    bike = rows(db)["3001"]
    assert bike.name_fr == "Stationary bike" and bike.slug == "stationary-bike"  # the clash-free English name
    assert rows(db)["0596"].name_fr == "Pec deck (butterfly)"  # the others are not affected
    # his exercise is deleted: the next startup gives the row its French name
    db.delete(db.get(Exercise, "velo"))
    db.commit()
    library.import_library(db, dataset, names_fr=NAMES)
    assert rows(db)["3001"].name_fr == "Vélo stationnaire"


def test_two_translations_with_the_same_name_do_not_both_win(db, dataset):
    names = {"0596": "Même nom", "3001": "même  NOM", "7001": "Autre"}
    stats = library.import_library(db, dataset, names_fr=names)
    assert stats["clashes"] == 1
    by_id = rows(db)
    assert by_id["0596"].name_fr == "Même nom" and by_id["3001"].name_fr == "Stationary bike"
    assert len({normalize_text(e.name_fr) for e in db.scalars(select(Exercise))}) == db.query(Exercise).count()


def test_default_translations_only_apply_to_the_default_dataset(db, dataset, tmp_path, monkeypatch):
    library.import_library(db, dataset)  # another dataset folder: ids mean something else, no translations
    assert rows(db)["0596"].name_fr == "Lever seated fly"
    names_file = tmp_path / "names.json"
    names_file.write_text(json.dumps(NAMES), encoding="utf-8")
    monkeypatch.setattr(library, "NAMES_FR_PATH", names_file)
    monkeypatch.setattr(library, "VENDOR_DIR", dataset)
    library.import_library(db)  # what the startup calls
    assert rows(db)["0596"].name_fr == "Pec deck (butterfly)"


# --- The rest of the app with French names + English aliases ---

def test_search_is_accent_insensitive_and_finds_english_names(db, client, dataset):
    library.import_library(db, dataset, names_fr=NAMES)
    def names(q):
        return [e["name"] for e in client.get("/exercises", params={"q": q}).json()]
    assert "Vélo stationnaire" in names("velo") and "Vélo stationnaire" in names("VÉLO")
    assert names("lever seated fly") == ["Pec deck (butterfly)"]      # English name = alias
    assert names("butterfly") == ["Pec deck (butterfly)"] and names("pec deck") == ["Pec deck (butterfly)"]
    assert "Tirage poitrine à la machine" in names("lat pulldown")
    assert names("TIRAGE poitrine")[0] == "Tirage poitrine à la machine"  # the name beats the seed's alias
    assert [e.name_fr for e in search_library(db, "stationary bike")] == ["Vélo stationnaire"]


def test_coach_name_resolution_and_search_with_french_names(db, dataset):
    library.import_library(db, dataset, names_fr=NAMES)
    assert coach_tools.find_exercise(db, "Pec deck (butterfly)").dataset_id == "0596"
    assert coach_tools.find_exercise(db, "pec deck butterfly").dataset_id == "0596"
    assert coach_tools.find_exercise(db, "lever seated fly").dataset_id == "0596"  # the old English name still works
    assert coach_tools.find_exercise(db, "vélo stationnaire").dataset_id == "3001"
    out = coach_tools.execute_tool(db, "search_exercises", {"query": "lat pulldown"})
    assert "Tirage poitrine à la machine" in [r["nom"] for r in out.result["résultats"]]
    out = coach_tools.execute_tool(db, "edit_session_exercise", {
        "session": "HA", "action": "add", "exercise": "Pec deck (butterfly)", "sets": 3, "reps": "10-12"})
    assert "Pec deck (butterfly)" in out.summary


def test_prototype_names_resolve_in_french_and_in_english(db, dataset):
    library.import_library(db, dataset, names_fr=NAMES)
    lib = Library(db)
    assert lib.find("Pec deck (butterfly)") == "lever-seated-fly"  # the prototype's own name for it
    assert lib.find("Lever seated fly") == "lever-seated-fly"      # the dataset's English name
    assert lib.find("Vélo stationnaire") == "stationary-bike" and lib.find("stationary bike") == "stationary-bike"
    assert lib.find("Presse à cuisses") == "presse-cuisses"        # Karim's own exercise
    assert find_by_name(db, "lever seated fly").name_fr == "Pec deck (butterfly)"


def test_export_carries_the_french_name_and_the_unchanged_slug(db, client, dataset):
    library.import_library(db, dataset, names_fr=NAMES)
    session = client.get("/sessions/HA").json()
    lines = [{"slug": e["slug"], "sets": e["sets"], "reps": e["reps"]} for e in session["exercises"]]
    lines.append({"slug": "lever-seated-fly", "sets": 3, "reps": "10"})
    assert client.put("/sessions/HA", json={"name": "Haut A", "focus": "Force", "exercises": lines}).status_code == 200
    exported = {e["slug"]: e for e in client.get("/export").json()["exercises"]}
    assert exported["lever-seated-fly"]["name"] == "Pec deck (butterfly)"


# --- The real dataset with the real translation file ---

@needs_real
def test_real_dataset_gets_french_names_everywhere(db):
    library.import_library(db, REAL_DATASET, names_fr={})  # English first, as an existing DB has it
    english = {e.slug: e.dataset_id for e in db.scalars(select(Exercise).where(Exercise.is_library))}
    seeded = {e.slug: (e.name_fr, e.aliases) for e in db.scalars(select(Exercise)) if not e.is_library}
    stats = library.import_library(db, REAL_DATASET, names_fr=library.load_french_names())
    assert stats["created"] == 0 and stats["clashes"] == 0 and stats["french"] == len(english)
    lib_rows = list(db.scalars(select(Exercise).where(Exercise.is_library)))
    assert {e.slug: e.dataset_id for e in lib_rows} == english  # slugs never move
    assert all(e.name_fr != e.slug for e in lib_rows)
    all_names = [normalize_text(e.name_fr) for e in db.scalars(select(Exercise))]
    assert len(set(all_names)) == len(all_names)
    for slug, (name, aliases) in seeded.items():
        e = db.get(Exercise, slug)
        assert (e.name_fr, e.aliases) == (name, aliases)  # Karim's 22 seeded exercises are untouched
    again = library.import_library(db, REAL_DATASET, names_fr=library.load_french_names())
    assert (again["created"], again["updated"], again["linked"]) == (0, 0, 0)

    wanted = {  # query -> a word one of the first results must have in its name
        "développé": "développé", "developpe incline": "incliné", "presse": "presse", "presse a cuisses": "presse",
        "poulie": "poulie", "tirage vertical": "tirage", "butterfly": "pec deck", "pec deck": "pec deck",
        "hack squat": "hack squat", "leg curl": "leg curl", "leg extension": "leg extension", "hip thrust": "hip thrust",
        "face pull": "face pull", "pompes": "pompes", "tractions": "tractions", "gainage": "gainage",
        "mollets debout": "mollets debout", "fente": "fente", "soulevé de terre roumain": "roumain",
        "kettlebell": "kettlebell", "smith": "smith", "élastique": "élastique",
        # the old English names, now aliases
        "lat pulldown": "tirage", "leg press": "presse", "lever seated fly": "pec deck", "barbell curl": "curl",
        "cable crossover": "vis-à-vis", "bench press": "développé", "dumbbell lateral raise": "élévations",
    }
    for query, word in wanted.items():
        found = search_library(db, query)
        assert found, query
        assert any(word in e.name_fr.lower() for e in found[:15]), (query, [e.name_fr for e in found[:5]])
    assert coach_tools.find_exercise(db, "Hack squat à la barre").dataset_id == "0046"
    assert Library(db).find("Lat pulldown") == "tirage-vertical"
