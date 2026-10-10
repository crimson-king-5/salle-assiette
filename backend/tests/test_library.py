"""The big exercise library: dataset import, seed links, French aliases, media route, light payloads, coach search."""

import json
import tempfile
from pathlib import Path

import pytest
from sqlalchemy import select

from app import coach, coach_tools, library
from app.exercises import find_by_name, search_library, to_exercise_out
from app.models import CoachAction, Exercise, SessionExercise
from app.prototype_import import Library
from app.seed import EXERCISES

REAL_DATASET = Path(__file__).resolve().parents[2] / "vendor" / "exercises-dataset"
HAS_REAL = (REAL_DATASET / "data" / "exercises.json").is_file()
needs_real = pytest.mark.skipif(not HAS_REAL, reason="vendor dataset not present")


def record(dataset_id, name, **over):
    base = {
        "id": dataset_id, "name": name, "body_part": "chest", "equipment": "barbell", "target": "pectorals",
        "secondary_muscles": ["triceps", "shoulders"],
        "instruction_steps": {"fr": ["Étape un.", "Étape deux."], "en": ["Step one."]},
        "image": f"images/{dataset_id}-abc.jpg", "gif_url": f"videos/{dataset_id}-abc.gif",
        "attribution": "© Gym visual — https://gymvisual.com/",
    }
    base.update(over)
    return base


def write_dataset(folder: Path, records: list, media: bool = True) -> Path:
    (folder / "data").mkdir(parents=True, exist_ok=True)
    (folder / "data" / "exercises.json").write_text(json.dumps(records), encoding="utf-8")
    if media:
        (folder / "images").mkdir(exist_ok=True)
        (folder / "videos").mkdir(exist_ok=True)
        (folder / "images" / "0001-abc.jpg").write_bytes(b"\xff\xd8jpg")
        (folder / "videos" / "0001-abc.gif").write_bytes(b"GIF89a")
        (folder / "secret.txt").write_text("secret")
    return folder


GOOD = [
    record("0025", "barbell bench press"),
    record("0201", "cable pushdown", body_part="upper arms", equipment="cable", target="triceps"),
    record("0596", "lever seated fly", equipment="leverage machine"),
    record("0585", "lever leg extension", body_part="upper legs", equipment="leverage machine", target="quads"),
    record("3001", "stationary bike", body_part="cardio", equipment="stationary bike", target="cardiovascular system"),
    record("0043", "barbell full squat", body_part="upper legs", target="glutes"),
]


@pytest.fixture
def dataset(tmp_path):
    return write_dataset(tmp_path / "dataset", GOOD)


# --- Validation and mapping ---

def test_malformed_records_are_skipped(tmp_path):
    bad = [
        "not a dict", None, 12,
        record("", "no id"), record("../evil", "bad id"), record("1", ""), record("2", "x" * 500),
        record("3", "bad image", image="../../etc/passwd.jpg"), record("4", "bad gif", gif_url="videos/a.exe"),
        record("5", "abs path", image="/images/a.jpg"), record("6", "no steps", instruction_steps={"fr": []}),
        record("7", "steps wrong type", instruction_steps={"fr": "text"}),
        record("8", "steps missing", instruction_steps=None),
        {**record("9", "x"), "name": ["a"]}, record("10", "windows path", image="images\\a.jpg"),
        record("12", "dup of ok", id="11"),
        record("11", "ok"),
    ]
    folder = write_dataset(tmp_path / "d", bad, media=False)
    records = library.load_records(folder)
    assert list(records) == ["11"]


def test_odd_but_usable_values_are_cleaned_not_trusted(tmp_path):
    folder = write_dataset(tmp_path / "d", [
        record("20", "sled 45в° leg press", target="made-up-muscle", body_part="chest", equipment="alien device",
               secondary_muscles=["glutes", 5, None, "x" * 100, "glutes"],
               instruction_steps={"fr": ["Ok", 7, "", "y" * 700, "  Spaces   inside  "]}),
    ], media=False)
    r = library.load_records(folder)["20"]
    assert r.name == "Sled 45° leg press"
    assert (r.muscle_group, r.equipment) == ("Pectoraux", "Autre")  # unknown target -> body part; unknown kit -> Autre
    assert r.secondary == ["Fessiers"]
    assert r.steps == ["Ok", "Spaces inside"]


def test_missing_or_broken_dataset_does_nothing(db, tmp_path):
    assert library.import_library(db, tmp_path / "nowhere")["records"] == 0
    broken = tmp_path / "broken"
    (broken / "data").mkdir(parents=True)
    (broken / "data" / "exercises.json").write_text("{not json", encoding="utf-8")
    assert library.load_records(broken) == {}
    (broken / "data" / "exercises.json").write_text('{"a": 1}', encoding="utf-8")
    assert library.load_records(broken) == {}
    assert db.query(Exercise).count() == 22  # only the seed


def test_equipment_group_and_flags_are_mapped(db, dataset):
    library.import_library(db, dataset)
    by_id = {e.dataset_id: e for e in db.scalars(select(Exercise))}
    fly = by_id["0596"]
    assert (fly.muscle_group, fly.equipment, fly.is_library, fly.is_custom) == ("Pectoraux", "Machine", True, False)
    assert fly.is_isolation and not fly.is_lower
    ext = by_id["0585"]
    assert (ext.muscle_group, ext.is_lower, ext.is_isolation) == ("Jambes", True, True)
    push = by_id["0201"]
    assert (push.muscle_group, push.equipment) == ("Triceps", "Poulie")
    bike = by_id["3001"]
    assert (bike.muscle_group, bike.is_cardio, bike.cardio_unit) == ("Cardio", True, "niveau")
    assert by_id["0025"].equipment == "Poids libre" and not by_id["0025"].is_isolation


def test_duplicate_dataset_names_get_unique_names_and_slugs(db, tmp_path):
    folder = write_dataset(tmp_path / "d", [record("30", "lever chest press"), record("31", "lever chest press")],
                           media=False)
    library.import_library(db, folder)
    rows = db.scalars(select(Exercise).where(Exercise.is_library)).all()
    assert sorted(e.name_fr for e in rows) == ["Lever chest press", "Lever chest press (2)"]
    assert len({e.slug for e in rows}) == 2


# --- Import: idempotence, no overwrite of Karim's data ---

def test_import_is_idempotent(db, dataset):
    first = library.import_library(db, dataset)
    assert (first["created"], first["linked"]) == (2, 4)  # 4 of the 6 records are the seed's own matches
    count = db.query(Exercise).count()
    state = sorted((e.slug, e.name_fr, e.aliases, e.steps_fr) for e in db.scalars(select(Exercise)))
    second = library.import_library(db, dataset)
    assert second["created"] == 0 and second["updated"] == 0
    assert db.query(Exercise).count() == count
    assert sorted((e.slug, e.name_fr, e.aliases, e.steps_fr) for e in db.scalars(select(Exercise))) == state


def test_reimport_refreshes_library_rows_only(db, tmp_path):
    folder = write_dataset(tmp_path / "d", [record("40", "cable fly", equipment="cable")], media=False)
    library.import_library(db, folder)
    row = db.scalars(select(Exercise).where(Exercise.dataset_id == "40")).one()
    row.equipment = "Machine"  # tampered
    db.commit()
    write_dataset(folder, [record("40", "cable fly", equipment="cable", instruction_steps={"fr": ["Nouvelle étape"]})], media=False)
    stats = library.import_library(db, folder)
    db.refresh(row)
    assert (row.equipment, library.steps_of(row), stats["updated"]) == ("Poulie", ["Nouvelle étape"], 1)


def test_custom_exercises_are_never_overwritten_or_linked(db, tmp_path):
    custom = Exercise(slug="barbell-bench-press", name_fr="Barbell bench press", muscle_group="Dos",
                      equipment="Machine", is_custom=True, aliases="mon alias")
    db.add(custom)
    db.commit()
    folder = write_dataset(tmp_path / "d", [record("9025", "barbell bench press")], media=False)
    library.import_library(db, folder)
    library.import_library(db, folder)
    db.refresh(custom)
    assert (custom.muscle_group, custom.equipment, custom.aliases, custom.dataset_id, custom.is_library) == \
        ("Dos", "Machine", "mon alias", None, False)
    imported = db.scalars(select(Exercise).where(Exercise.dataset_id == "9025")).one()
    assert imported.slug != "barbell-bench-press" and imported.is_library  # slug taken: no collision, no overwrite


def test_own_exercise_wins_over_the_library_on_the_same_name(db, tmp_path):
    folder = write_dataset(tmp_path / "d", [record("50", "my special curl")], media=False)
    library.import_library(db, folder)
    own = Exercise(slug="perso-curl", name_fr="My special curl", muscle_group="Biceps", equipment="Poids libre",
                   is_custom=True)
    db.add(own)
    db.commit()
    assert find_by_name(db, "my special curl").slug == "perso-curl"


# --- Linking the seeded exercises ---

def seed_records():
    return [record(i, f"seed match {i}", body_part="upper legs", target="quads") for i in set(library.SEED_LINKS.values())]


def test_seeded_exercises_get_media_and_steps_but_keep_their_french_names(db, tmp_path):
    folder = write_dataset(tmp_path / "d", seed_records() + [record("0596", "lever seated fly")], media=False)
    before = {e.slug: (e.name_fr, e.muscle_group, e.equipment, e.aliases) for e in db.scalars(select(Exercise))}
    stats = library.import_library(db, folder)
    assert stats["linked"] == 22 and stats["created"] == 1
    for slug, dataset_id in library.SEED_LINKS.items():
        e = db.get(Exercise, slug)
        assert e.dataset_id == dataset_id and e.media_gif and e.media_image and library.steps_of(e)
        assert (e.name_fr, e.muscle_group, e.equipment, e.aliases) == before[slug]
        assert not e.is_library  # still Karim's seed exercise
    assert db.query(Exercise).count() == 22 + 1  # the matched records are not imported a second time
    stats = library.import_library(db, folder)
    assert stats["linked"] == 0 and stats["created"] == 0 and stats["updated"] == 0


@needs_real
def test_seed_links_point_at_real_dataset_records_with_matching_meaning():
    records = library.load_records(REAL_DATASET)
    assert len(records) > 1300
    assert set(library.SEED_LINKS) == set(EXERCISES)  # every seeded exercise has a link
    for slug, dataset_id in library.SEED_LINKS.items():
        r = records[dataset_id]
        group = EXERCISES[slug][1]
        assert r.muscle_group == group or (group == "Jambes" and r.is_lower) \
            or slug in {"developpe-prise-serree", "face-pull"}, slug
    assert records["0025"].name == "Barbell bench press"
    assert records["0594"].name == "Lever seated calf raise"


@needs_real
def test_real_dataset_imports_and_is_found_in_french(db):
    stats = library.import_library(db, REAL_DATASET)
    assert stats["records"] > 1300 and db.query(Exercise).count() == stats["records"]
    again = library.import_library(db, REAL_DATASET)
    assert again["created"] == 0 and again["updated"] == 0 and again["linked"] == 0
    wanted = {  # French search -> a word one of the first results must have in its name
        "presse": "leg press", "presse a cuisses": "leg press", "butterfly": "seated fly", "pec deck": "seated fly",
        "poulie haute": "pulldown", "poulie basse": "row", "tirage vertical": "pulldown",
        "tirage horizontal": "row", "smith": "smith", "multipower": "smith", "hack squat": "hack squat",
        "leg extension": "leg extension", "leg curl": "leg curl", "adducteurs": "adduction",
        "abducteurs": "abduction", "mollets": "calf", "developpe couche": "bench press",
        "developpe incline": "incline", "developpe militaire": "press", "rowing": "row", "curl": "curl",
        "extension triceps": "triceps", "pompes": "push-up", "traction": "pull-up",
    }
    for query, word in wanted.items():
        found = search_library(db, query)
        assert found, query
        assert any(word in e.name_fr.lower() or word in e.slug for e in found[:15]), (query, [e.name_fr for e in found[:5]])
    assert search_library(db, "PRESSE À CUISSES")[0].name_fr == "Presse à cuisses"  # accents/case; own exercise first
    assert db.query(Exercise).filter(Exercise.is_library).count() > 1000
    for e in db.scalars(select(Exercise)):
        assert library.steps_of(e) and e.media_gif and e.media_image, e.slug


# --- Aliases and search ---

def test_french_aliases_make_machines_findable(db, client, dataset):
    library.import_library(db, dataset)
    found = client.get("/exercises", params={"q": "butterfly"}).json()
    assert [e["name"] for e in found] == ["Lever seated fly"]
    assert client.get("/exercises", params={"q": "PEC DECK"}).json()[0]["name"] == "Lever seated fly"
    assert "Leg extension" in {e["name"] for e in client.get("/exercises", params={"q": "extension jambes"}).json()}
    assert "Extension triceps poulie" in {e["name"] for e in client.get("/exercises", params={"q": "poulie"}).json()}
    assert client.get("/exercises", params={"q": "velo"}).json()[0]["name"] == "Stationary bike"


def test_alias_lists_stay_short():
    r = library.parse_record(record("1", "barbell bench press"))
    assert len("|".join(library.french_aliases(r))) <= library.MAX_ALIAS_CHARS


def test_equipment_filter_accepts_the_new_kinds(db, client, tmp_path):
    folder = write_dataset(tmp_path / "d", [
        record("60", "smith squat", equipment="smith machine"), record("61", "kettlebell swing", equipment="kettlebell"),
        record("62", "ez bar curl", equipment="ez barbell"), record("63", "band row", equipment="band"),
    ], media=False)
    library.import_library(db, folder)
    for kind in ("Smith", "Kettlebell", "Barre EZ", "Élastique"):
        assert len(client.get("/exercises", params={"equipment": kind}).json()) == 1, kind
    body = {"name": "Mon exo smith", "muscle_group": "Jambes", "equipment": "Smith"}
    assert client.post("/exercises", json=body).status_code == 201


def test_prototype_names_resolve_to_the_library(db):
    assert Library(db).find("Presse à cuisses") == "presse-cuisses"
    records = [record("0596", "lever seated fly"), record("70", "dumbbell row"), record("71", "barbell row")]
    with tempfile.TemporaryDirectory() as tmp:
        library.import_library(db, write_dataset(Path(tmp), records, media=False))
    lib = Library(db)
    assert lib.find("Pec deck (butterfly)") == "lever-seated-fly"
    assert lib.find("Rowing") is None  # "rowing" is shared by several library exercises: no random pick
    assert lib.find("Squat") == "squat"  # Karim's own exercise


# --- Detail route, light payloads ---

def test_exercise_detail_route(db, client, dataset):
    library.import_library(db, dataset)
    slug = db.scalars(select(Exercise.slug).where(Exercise.dataset_id == "0596")).one()
    body = client.get(f"/exercises/{slug}").json()
    assert body["steps"] == ["Étape un.", "Étape deux."]
    assert body["gif"] == "0596-abc.gif" and body["thumb"] == "0596-abc.jpg"
    assert body["target"] == "Pectoraux" and body["secondary"] == ["Triceps", "Épaules"]
    assert "gymvisual" in body["attribution"] and body["is_library"] is True
    assert client.get("/exercises/does-not-exist").status_code == 404
    custom = client.post("/exercises", json={"name": "Perso", "muscle_group": "Dos", "equipment": "Machine"}).json()
    detail = client.get(f"/exercises/{custom['slug']}").json()
    assert detail["steps"] == [] and detail["gif"] is None and detail["attribution"] is None


def test_library_exercises_are_read_only(db, client, dataset):
    library.import_library(db, dataset)
    slug = db.scalars(select(Exercise.slug).where(Exercise.dataset_id == "0596")).one()
    body = {"name": "x", "muscle_group": "Dos", "equipment": "Machine"}
    assert client.put(f"/exercises/{slug}", json=body).status_code == 403
    assert client.delete(f"/exercises/{slug}").status_code == 403


def test_list_payload_never_carries_the_steps():
    out = to_exercise_out(Exercise(slug="a", name_fr="A", muscle_group="Dos", equipment="Machine", aliases="",
                                   is_isolation=False, is_lower=False, is_cardio=False, is_custom=False,
                                   needs_review=False, is_library=True, media_image="images/x.jpg", steps_fr=json.dumps(["long"] * 30)))
    assert "steps" not in out.model_dump() and out.thumb == "x.jpg"


@needs_real
def test_snapshot_stays_small_with_the_whole_library(db, client):
    library.import_library(db, REAL_DATASET)
    snap = client.get("/snapshot")
    assert len(snap.content) < 200_000, len(snap.content)  # 1324 exercises in the library, none of them in the snapshot
    body = snap.json()
    program_slugs = {e["slug"] for s in body["sessions"] for f in s["formats"].values() for e in f}
    assert set(body["exercise_details"]) == program_slugs and len(program_slugs) == 22
    squat = body["exercise_details"]["squat"]
    assert squat["steps"] and squat["gif"].endswith(".gif")
    assert body["sessions"][1]["formats"]["complete"][0]["gif"] == squat["gif"]


def test_snapshot_details_cover_only_the_program(db, client, dataset):
    library.import_library(db, dataset)
    body = client.get("/snapshot").json()
    assert set(body["exercise_details"]) == {e.exercise_slug for e in db.scalars(select(SessionExercise))}


# --- Media route ---

@pytest.fixture
def media(monkeypatch, tmp_path):
    folder = write_dataset(tmp_path / "vendor", GOOD)
    monkeypatch.setattr(library, "VENDOR_DIR", folder)
    return folder


def test_media_is_served_with_long_cache_headers(client, media):
    r = client.get("/media/ex/images/0001-abc.jpg")
    assert (r.status_code, r.content) == (200, b"\xff\xd8jpg")
    assert "max-age=31536000" in r.headers["cache-control"] and "immutable" in r.headers["cache-control"]
    g = client.get("/media/ex/videos/0001-abc.gif")
    assert g.status_code == 200 and g.headers["content-type"] == "image/gif"


@pytest.mark.parametrize("path", [
    "/media/ex/images/../data/exercises.json", "/media/ex/images/..%2fdata%2fexercises.json",
    "/media/ex/images/%2e%2e/data/exercises.json", "/media/ex/images/%2e%2e%2fsecret.txt",
    "/media/ex/images/..%5csecret.txt", "/media/ex/images/%252e%252e%252fsecret.txt",
    "/media/ex/videos/..%2fimages%2f0001-abc.jpg", "/media/ex/data/exercises.json", "/media/ex/../secret.txt",
    "/media/ex/images/secret.txt", "/media/ex/images/.jpg", "/media/ex/images/C:%5cWindows%5cwin.ini",
    "/media/ex/images/0001-abc.jpg%00.gif", "/media/ex/images/missing.jpg", "/media/ex/images/0001-abc.gif",
    "/media/ex/videos/0001-abc.jpg", "/media/ex/", "/media/ex/images/",
])
def test_media_path_traversal_and_foreign_files_are_refused(client, media, path):
    assert client.get(path).status_code in (404, 422), path


def test_media_route_without_dataset_is_404(client):
    assert client.get("/media/ex/images/0001-abc.jpg").status_code == 404


# --- Coach: search_exercises and the short library text ---

def test_search_exercises_tool_is_read_only_and_capped(db, tmp_path):
    many = [record(f"{i:04d}", f"dumbbell curl variant {i}", body_part="upper arms", target="biceps",
                   equipment="dumbbell") for i in range(1, 40)]
    library.import_library(db, write_dataset(tmp_path / "d", many + GOOD, media=False))
    before = db.query(Exercise).count()
    out = coach_tools.execute_tool(db, "search_exercises", {"query": "curl", "max_results": 15})
    assert out.read_only and out.undo == {}
    assert len(out.result["résultats"]) == 15 and out.result["total_trouvés"] >= 39 and "note" in out.result
    assert set(out.result["résultats"][0]) == {"nom", "groupe", "matériel"}
    default = coach_tools.execute_tool(db, "search_exercises", {"query": "curl"})
    assert len(default.result["résultats"]) == 10
    filtered = coach_tools.execute_tool(db, "search_exercises",
                                        {"query": "squat", "group": "Jambes", "equipment": "Poids libre"})
    assert len(filtered.result["résultats"]) >= 1
    assert db.query(Exercise).count() == before


@pytest.mark.parametrize("args", [{"max_results": 16}, {"max_results": 0}, {"group": "Cou"}, {"equipment": "Laser"},
                                  {"query": "x" * 200}, {"unknown": 1}])
def test_search_exercises_validates_arguments(db, args):
    with pytest.raises(coach_tools.ToolError):
        coach_tools.execute_tool(db, "search_exercises", args)


def test_coach_context_lists_own_exercises_but_not_the_whole_library(db, dataset):
    library.import_library(db, dataset)
    text = coach.library_text(db)
    assert "Presse à cuisses" in text and "Squat" in text  # Karim's own exercises
    assert "Lever seated fly" not in text and "search_exercises" in text  # library-only: found by the tool
    assert len(text) < 3_000


def test_coach_context_keeps_library_exercises_that_are_in_the_program(db, client, dataset):
    library.import_library(db, dataset)
    slug = db.scalars(select(Exercise.slug).where(Exercise.dataset_id == "0596")).one()
    session = client.get("/sessions/HA").json()
    lines = [{"slug": e["slug"], "sets": e["sets"], "reps": e["reps"]} for e in session["exercises"]]
    lines.append({"slug": slug, "sets": 3, "reps": "10"})
    assert client.put("/sessions/HA", json={"name": "Haut A", "focus": "Force", "exercises": lines}).status_code == 200
    assert "Lever seated fly" in coach.library_text(db)


def test_name_resolution_with_the_big_library(db, dataset):
    library.import_library(db, dataset)
    assert coach_tools.find_exercise(db, "Presse à cuisses").slug == "presse-cuisses"
    assert coach_tools.find_exercise(db, "lever seated fly").dataset_id == "0596"
    assert coach_tools.find_exercise(db, "butterfly").dataset_id == "0596"  # unique alias
    with pytest.raises(coach_tools.ToolError):
        coach_tools.find_exercise(db, "curl inconnu xyz")


def test_edit_session_exercise_adds_a_library_exercise_by_its_name(db, dataset):
    library.import_library(db, dataset)
    out = coach_tools.execute_tool(db, "edit_session_exercise", {
        "session": "HA", "action": "add", "exercise": "Lever seated fly", "sets": 3, "reps": "10-12"})
    assert "Lever seated fly" in out.summary


def test_run_call_does_not_record_a_lookup_as_a_change(db):
    applied: list = []
    result = coach.run_call(db, "training", {"name": "search_exercises", "args": {"query": "squat"}},
                            list(coach_tools.TOOLS), applied)
    assert result["ok"] and "résultats" in result and applied == [] and db.query(CoachAction).count() == 0


def test_export_lists_only_the_library_exercises_that_are_used(db, client, dataset):
    library.import_library(db, dataset)
    slugs = {e["slug"] for e in client.get("/export").json()["exercises"]}
    assert len(slugs) == 22 and not any(e.is_library and e.slug in slugs for e in db.scalars(select(Exercise)))
    fly = db.scalars(select(Exercise.slug).where(Exercise.dataset_id == "0596")).one()
    session = client.get("/sessions/HA").json()
    lines = [{"slug": e["slug"], "sets": e["sets"], "reps": e["reps"]} for e in session["exercises"]]
    lines.append({"slug": fly, "sets": 3, "reps": "10"})
    client.put("/sessions/HA", json={"name": "Haut A", "focus": "Force", "exercises": lines})
    exported = {e["slug"]: e for e in client.get("/export").json()["exercises"]}
    assert fly in exported and exported[fly]["dataset_id"] == "0596"


# --- Review fixes ---

def test_lone_surrogate_in_the_dataset_is_skipped_not_fatal(tmp_path):
    bad = record("80", "bad \ud800 name")
    bad_step = record("81", "ok name", instruction_steps={"fr": ["fine", "broken \udc00"]})
    folder = tmp_path / "d"
    (folder / "data").mkdir(parents=True)
    (folder / "data" / "exercises.json").write_text(json.dumps([bad, bad_step]), encoding="utf-8")  # \u escapes
    records = library.load_records(folder)
    assert list(records) == ["81"] and records["81"].steps == ["fine"]


def test_startup_survives_a_failing_library_import(tmp_path, monkeypatch):
    from sqlalchemy import create_engine
    from app import main

    def boom(db):
        raise RuntimeError("broken dataset")

    monkeypatch.setattr(main, "import_library", boom)
    engine = create_engine(f"sqlite:///{tmp_path / 'x.db'}")
    main.init_database(engine)  # must not raise
    engine.dispose()


def test_import_is_skipped_above_the_record_cap(db, tmp_path, monkeypatch):
    monkeypatch.setattr(library, "MAX_RECORDS", 2)
    folder = write_dataset(tmp_path / "d", [record("90", "a"), record("91", "b"), record("92", "c")], media=False)
    assert library.import_library(db, folder)["records"] == 0 and db.query(Exercise).count() == 22


def test_coach_does_not_pick_a_library_variant_on_a_partial_match(db, tmp_path):
    folder = write_dataset(tmp_path / "d", [
        record("100", "smith incline bench press", equipment="smith machine"),
        record("101", "barbell incline bench press"),
    ], media=False)
    library.import_library(db, folder)
    with pytest.raises(coach_tools.ToolError) as error:
        coach_tools.find_exercise(db, "Développé incliné barre")
    assert error.value.close == ["Barbell incline bench press"]  # offered, not picked ("smith" must not match "barre")
    with pytest.raises(coach_tools.ToolError):
        coach_tools.find_exercise(db, "incline bench")  # two hits: ambiguous
    assert coach_tools.find_exercise(db, "développé incliné haltères").slug == "developpe-incline-halteres"


def test_equipment_words_in_french_find_the_right_variant(db, client, tmp_path):
    folder = write_dataset(tmp_path / "d", [
        record("100", "smith incline bench press", equipment="smith machine"),
        record("101", "barbell incline bench press"),
        record("102", "dumbbell incline bench press", equipment="dumbbell"),
    ], media=False)
    library.import_library(db, folder)
    names = [e["name"] for e in client.get("/exercises", params={"q": "incline halteres"}).json()]
    assert "Dumbbell incline bench press" in names and "Barbell incline bench press" not in names


def test_own_alias_beats_a_library_name(db, tmp_path):
    folder = write_dataset(tmp_path / "d", [record("110", "quads", target="quads")], media=False)
    library.import_library(db, folder)
    assert find_by_name(db, "quads").slug == "leg-extension"
    assert Library(db).find("quads") == "leg-extension"


def test_prototype_ambiguous_library_base_name_is_not_guessed(db, tmp_path):
    folder = write_dataset(tmp_path / "d", [record("120", "crunch (floor)"), record("121", "crunch (machine)")], media=False)
    library.import_library(db, folder)
    assert Library(db).find("Crunch") is None


def test_headers_nosniff_and_gzip(client, media):
    big = client.get("/exercises", headers={"accept-encoding": "gzip"})
    assert big.headers["x-content-type-options"] == "nosniff"
    assert big.headers.get("content-encoding") == "gzip"
    img = client.get("/media/ex/images/0001-abc.jpg", headers={"accept-encoding": "gzip"})
    assert img.headers["x-content-type-options"] == "nosniff" and "content-encoding" not in img.headers
    assert client.get("/health").headers["x-content-type-options"] == "nosniff"
