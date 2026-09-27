import pytest

from app.algorithms import LoggedSet, PlannedExercise, apply_format, compute_readiness, tonnage


def test_great_day_with_time_is_complete():
    r = compute_readiness(energy=5, sleep=5, motivation=5, soreness=1, minutes=60)
    assert (r.score, r.level, r.format) == (20, "vert", "complete")


def test_green_but_short_on_time_is_minimale():
    r = compute_readiness(energy=5, sleep=5, motivation=3, soreness=1, minutes=45)
    assert (r.level, r.format) == ("vert", "minimale")


def test_orange_is_minimale_even_with_time():
    r = compute_readiness(energy=3, sleep=3, motivation=3, soreness=3, minutes=60)
    assert (r.score, r.level, r.format) == (12, "orange", "minimale")


def test_red_is_express_never_skip():
    # Anti-flemme rule: red never says "stay home", it says "do the express version".
    r = compute_readiness(energy=1, sleep=1, motivation=1, soreness=5, minutes=60)
    assert (r.score, r.level, r.format) == (4, "rouge", "express")


def test_30_minutes_forces_express_even_when_green():
    r = compute_readiness(energy=5, sleep=5, motivation=5, soreness=1, minutes=30)
    assert (r.level, r.format) == ("vert", "express")


@pytest.mark.parametrize("score_inputs, expected_level", [
    ((5, 5, 5, 1), "vert"),    # 5+5+5+5 = 20 → max
    ((5, 3, 3, 1), "vert"),    # 5+3+3+5 = 16 → lowest possible vert (scores are always even)
    ((3, 3, 3, 1), "orange"),  # 3+3+3+5 = 14 → just below vert
    ((5, 1, 1, 3), "orange"),  # 5+1+1+3 = 10 → exactly orange
    ((1, 1, 3, 3), "rouge"),   # 1+1+3+3 = 8
])
def test_level_thresholds(score_inputs, expected_level):
    e, s, m, sore = score_inputs
    assert compute_readiness(e, s, m, sore, minutes=60).level == expected_level


def test_rejects_invalid_answer():
    with pytest.raises(ValueError):
        compute_readiness(energy=2, sleep=3, motivation=3, soreness=3, minutes=60)


# --- apply_format (SPEC §5.2) ---

HAUT_A = [
    PlannedExercise("bench", "Développé couché barre", 4, "5-6"),
    PlannedExercise("row", "Rowing barre", 4, "6-8"),
    PlannedExercise("ohp", "Développé militaire", 3, "6-10"),
    PlannedExercise("pulldown", "Tirage vertical", 3, "8-10"),
    PlannedExercise("curl", "Curl haltères", 2, "10-12"),
    PlannedExercise("triceps", "Extension triceps poulie", 2, "10-12"),
]


def test_complete_keeps_everything():
    assert apply_format(HAUT_A, "complete") == HAUT_A


def test_minimale_keeps_first_three_with_max_three_sets():
    result = apply_format(HAUT_A, "minimale")
    assert [e.slug for e in result] == ["bench", "row", "ohp"]
    assert [e.sets for e in result] == [3, 3, 3]


def test_express_keeps_first_two_with_max_three_sets():
    result = apply_format(HAUT_A, "express")
    assert [e.slug for e in result] == ["bench", "row"]
    assert [e.sets for e in result] == [3, 3]


def test_sets_already_below_three_stay_as_they_are():
    arms = HAUT_A[4:]  # curl 2 sets, triceps 2 sets
    assert [e.sets for e in apply_format(arms, "express")] == [2, 2]


def test_name_and_reps_are_kept():
    first = apply_format(HAUT_A, "minimale")[0]
    assert (first.name, first.reps) == ("Développé couché barre", "5-6")


def test_session_shorter_than_format_is_fine():
    one = [HAUT_A[0]]
    assert len(apply_format(one, "minimale")) == 1


def test_original_program_is_not_modified():
    apply_format(HAUT_A, "express")
    assert HAUT_A[0].sets == 4  # the saved program must keep its 4 sets


# --- tonnage (SPEC §4.2, end-of-workout recap) ---

def test_tonnage_of_no_sets_is_zero():
    assert tonnage([]) == 0


def test_tonnage_adds_weight_times_reps():
    sets = [LoggedSet(80, 6, True), LoggedSet(80, 5, True)]
    assert tonnage(sets) == 80 * 6 + 80 * 5  # 880


def test_tonnage_ignores_sets_not_validated():
    sets = [LoggedSet(80, 6, True), LoggedSet(100, 6, False)]
    assert tonnage(sets) == 480


def test_tonnage_ignores_sets_without_weight_or_reps():
    # bodyweight exercise (no weight), or a set validated before typing reps
    sets = [LoggedSet(None, 12, True), LoggedSet(60, None, True), LoggedSet(60, 10, True)]
    assert tonnage(sets) == 600


def test_tonnage_handles_half_kilos():
    assert tonnage([LoggedSet(22.5, 10, True)]) == 225
