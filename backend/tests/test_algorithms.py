import pytest

from app.algorithms import compute_readiness


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
