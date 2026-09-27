"""Stress-set generator (golden/stress.py): builds parts outside the golden_v1 recipe."""
from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("cadquery")

from golden.sheetgen import SheetPart
from golden.stress import C_LEVELS, build_a, build_c, random_pose


def test_pose_is_a_proper_rigid_motion():
    import random

    pose = random_pose(random.Random(3))
    assert np.allclose(pose[:3, :3] @ pose[:3, :3].T, np.eye(3))
    assert np.linalg.det(pose[:3, :3]) == pytest.approx(1.0)


def test_moved_part_keeps_volume_and_truth():
    _, (moved, _, truth) = build_a("A_Lv1", 0, seed=21)
    from golden.sampler import generate

    _, (original, _, reference) = generate("Lv1", 0, 21)
    assert moved.Volume() == pytest.approx(original.Volume(), rel=1e-9)
    assert truth["level"] == "A_Lv1"
    assert (truth["blank_area_mm2"], truth["cut_length_mm"], truth["bend_count"]) == (
        reference["blank_area_mm2"], reference["cut_length_mm"], reference["bend_count"])


@pytest.mark.parametrize("level", C_LEVELS)
def test_new_shape_families_are_consistent(level):
    _, built = build_c(level, 0, seed=21)
    assert SheetPart.is_consistent(built[2])
    assert built[2]["truth_version"] == "v2"
