"""Pure functions: streak, cardio minutes, records, progression tips, auto protein, search."""

import datetime as dt

import pytest

from app.algorithms import (
    HistorySet, LoggedSet, auto_protein_goal, cardio_minutes, format_kg, matches_query,
    normalize_text, progression_tip, slugify, top_of_range, weeks_held, weight_records,
)

D = dt.date
WED = D(2026, 10, 7)  # a Wednesday; its Monday is 2026-10-05


def days(*dates):
    return list(dates)


# --- weeks_held ---

def test_no_workouts_no_streak():
    assert weeks_held([], WED, 4) == 0


def test_current_week_not_met_does_not_break_the_streak():
    # goal 4 → threshold 3. Two previous weeks met, current week has only 1 workout so far.
    last = [D(2026, 9, 28), D(2026, 9, 29), D(2026, 9, 30)]
    before = [D(2026, 9, 21), D(2026, 9, 22), D(2026, 9, 24)]
    assert weeks_held(last + before + [D(2026, 10, 5)], WED, 4) == 2


def test_current_week_counts_once_met():
    current = [D(2026, 10, 5), D(2026, 10, 6), D(2026, 10, 7)]
    last = [D(2026, 9, 28), D(2026, 9, 29), D(2026, 9, 30)]
    assert weeks_held(current + last, WED, 4) == 2


def test_a_missed_week_breaks_the_streak():
    last = [D(2026, 9, 28), D(2026, 9, 29), D(2026, 9, 30)]
    old = [D(2026, 9, 14), D(2026, 9, 15), D(2026, 9, 16)]  # week of 9/21 is empty
    assert weeks_held(last + old, WED, 4) == 1


def test_threshold_is_goal_minus_one_but_at_least_two():
    two_a_week = [D(2026, 9, 28), D(2026, 9, 30)]
    assert weeks_held(two_a_week, WED, 3) == 1   # 3 − 1 = 2
    assert weeks_held(two_a_week, WED, 1) == 1   # max(2, 0) = 2
    assert weeks_held(two_a_week, WED, 4) == 0   # needs 3
    assert weeks_held([D(2026, 9, 28)], WED, 2) == 0  # one workout is never enough


def test_only_current_week_met():
    assert weeks_held([D(2026, 10, 5), D(2026, 10, 6)], WED, 3) == 1


def test_sunday_belongs_to_the_week_that_started_on_monday():
    sunday = D(2026, 10, 4)  # week of Monday 9/28
    assert weeks_held([D(2026, 9, 28), sunday], WED, 3) == 1


# --- cardio_minutes ---

def test_cardio_minutes_sums_validated_reps_only():
    sets = [LoggedSet(None, 20, True), LoggedSet(None, 10, True), LoggedSet(None, 15, False),
            LoggedSet(None, None, True)]
    assert cardio_minutes(sets) == 30
    assert cardio_minutes([]) == 0


# --- weight_records ---

def hs(slug, weight, reps, date=D(2026, 9, 1), cardio=False, done=True):
    return HistorySet(slug, weight, reps, date, cardio, done)


def test_record_is_heaviest_then_most_reps():
    records = weight_records([
        hs("squat", 80, 8, D(2026, 9, 1)), hs("squat", 90, 3, D(2026, 9, 8)),
        hs("squat", 90, 5, D(2026, 9, 15)), hs("squat", 85, 12, D(2026, 9, 22)),
    ])
    assert (records["squat"].weight, records["squat"].reps, records["squat"].date) == (90, 5, D(2026, 9, 15))


def test_exact_tie_keeps_the_first_date():
    records = weight_records([hs("squat", 90, 5, D(2026, 9, 15)), hs("squat", 90, 5, D(2026, 9, 1))])
    assert records["squat"].date == D(2026, 9, 1)


def test_records_ignore_cardio_undone_and_weightless_sets():
    records = weight_records([
        hs("tapis", 10, 30, cardio=True), hs("squat", 100, 5, done=False),
        hs("pompes", None, 20), hs("gainage", 0, 60),
    ])
    assert records == {}


# --- progression_tip ---

def sets(*pairs, done=True):
    return [LoggedSet(w, r, done) for w, r in pairs]


def tip(last, target="8-12", iso=False, lower=False, cardio=False):
    return progression_tip(last, target, iso, lower, cardio)


def test_tip_never_done():
    expected = "Prends une charge où il te reste 2 à 3 reps en réserve"
    assert tip([]) == expected
    assert tip(sets((50, 10), done=False)) == expected  # nothing validated = never done


def test_tip_all_sets_at_top_upper_body():
    assert tip(sets((80, 12), (80, 12), (80, 12))) == (
        "Tout était au haut de la fourchette : passe à 82,5 kg")


def test_tip_at_top_isolation_and_lower():
    assert tip(sets((20, 12), (20, 12)), iso=True).endswith("passe à 21 kg")
    assert tip(sets((80, 12), (80, 12)), lower=True).endswith("passe à 85 kg")
    assert tip(sets((30, 12)), iso=True, lower=True).endswith("passe à 31 kg")  # isolation wins


def test_tip_not_at_top_keeps_the_weight():
    assert tip(sets((80, 12), (80, 10))) == "Garde 80 kg et vise une répétition de plus par série"
    assert tip(sets((80, 8), (82.5, 6))) == "Garde 82,5 kg et vise une répétition de plus par série"


def test_tip_needs_every_set_done_and_at_the_top():
    # SPEC 5.3 "toutes les series": one skipped set means no weight increase
    last = sets((80, 12), (80, 12)) + sets((80, 3), done=False)
    assert tip(last) == "Garde 80 kg et vise une répétition de plus par série"
    assert "passe à 82,5 kg" in tip(sets((80, 12), (80, 12)))


def test_tip_with_unreadable_target_never_raises_the_weight():
    assert tip(sets((80, 30)), target="AMRAP").startswith("Garde 80 kg")


def test_tip_bodyweight():
    assert tip(sets((None, 12), (None, 12))).startswith("Tout est au haut")
    assert tip(sets((None, 8))) == "Vise une répétition de plus par série"


def test_tip_cardio():
    assert "10 à 15 min" in tip([], cardio=True)
    assert tip(sets((None, 20), (None, 10)), cardio=True) == (
        "La dernière fois : 30 min. Ajoute 2 à 5 min ou monte un peu le niveau")


def test_top_of_range_and_format_kg():
    assert [top_of_range(t) for t in ("5-6", "10", "15-20", "AMRAP", "")] == [6, 10, 20, None, None]
    assert (format_kg(85.0), format_kg(82.5), format_kg(21)) == ("85", "82,5", "21")


# --- auto_protein_goal ---

@pytest.mark.parametrize("weight,objective,expected", [
    (88.4, "recompo", 177), (88.4, "perte", 177), (88.4, "muscle", 159), (88.4, "forme", 141),
    (20, "recompo", 60), (200, "recompo", 300), (250, "muscle", 300),
])
def test_auto_protein_goal(weight, objective, expected):
    assert auto_protein_goal(weight, objective) == expected


# --- search helpers ---

def test_normalize_and_match():
    assert normalize_text("Épaules / Élévations-latérales œuf") == "epaules elevations laterales oeuf"
    assert matches_query("epaule", ["Élévations latérales", "Épaules"])
    assert matches_query("AVANT bras", ["Avant-bras"])
    assert matches_query("lat pull", ["Tirage vertical", "lat pulldown"])
    assert not matches_query("squat", ["Curl"])
    assert matches_query("", ["x"])


def test_slugify():
    assert slugify("Ma machine à dos !") == "ma-machine-a-dos"
    assert slugify("???") == "exercice"
