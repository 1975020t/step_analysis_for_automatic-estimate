from __future__ import annotations

import math

import pytest

pytest.importorskip("cadquery")

from golden.curated import curated_parts
from golden.sampler import LEVELS, generate
from golden.sheetgen import SheetPart, box, circle


@pytest.mark.parametrize("level", LEVELS)
def test_random_part_is_consistent_and_deterministic(level):
    _, (_, _, first) = generate(level, 0, seed=7)
    _, (_, _, second) = generate(level, 0, seed=7)
    assert SheetPart.is_consistent(first)
    assert first["blank_area_mm2"] == second["blank_area_mm2"]
    assert first["cut_length_mm"] == second["cut_length_mm"]


def test_curated_parts_are_consistent():
    for part in curated_parts():
        assert SheetPart.is_consistent(part.build()[2]), part.name


def test_truth_matches_hand_calculation_for_l_bracket_with_hole():
    t, r, k = 2.0, 3.0, 0.5
    part = SheetPart("L", t, box(0, 0, 60, 40), k_factor=k)
    part.cut_base(circle(20, 20, 10))
    part.flange(part.base, ((60, 0), (60, 40)), 30, angle=90, radius=r)
    truth = part.build()[2]

    bend_allowance = math.pi / 2 * (r + k * t)
    length = 60 + bend_allowance + 30
    assert truth["blank_area_mm2"] == pytest.approx(length * 40 - math.pi * 25, rel=1e-6)
    assert truth["cut_length_mm"] == pytest.approx(2 * (length + 40) + math.pi * 10, rel=1e-6)
    assert truth["hole_count"] == 1
    assert truth["bend_count"] == 1


def test_evaluation_harness_scores_flat_plate_as_correct(tmp_path):
    from scripts.evaluate_golden import evaluate_part

    part, built = generate("Lv0", 0, seed=3)
    truth = part.export(tmp_path, built)
    row = evaluate_part((str(tmp_path / f"{part.name}.step"), truth, 60))
    assert row["outcome"] == "CORRECT", row
