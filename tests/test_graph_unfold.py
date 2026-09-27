"""General graph-walk unfold and its self-checks, on parts built with the golden generator."""
from __future__ import annotations

from pathlib import Path

import pytest

cq = pytest.importorskip("cadquery")

from golden.sampler import generate
from golden.sheetgen import SheetPart, box, circle, slot
from golden.truth_v2 import apply_v2
from src.sheetmetal_analyzer import SheetMetalAnalyzer
from src.sheetmetal_recognition import SheetMetalRecognition
from src.sheetmetal_unfold import _BendMap


def _analyze(part_or_built, tmp_path: Path, k_factor=None):
    folded, _, truth = part_or_built if isinstance(part_or_built, tuple) else part_or_built.build()
    path = tmp_path / f"{truth['name']}.step"
    cq.exporters.export(folded, str(path))
    analyzer = SheetMetalAnalyzer(k_factor=truth["k_factor"] if k_factor is None else k_factor,
                                  k_factor_is_default=False)
    return analyzer.analyze(path), truth


def _assert_matches(result, truth, rel=1e-3):
    assert result.status == "success", (result.reason_codes, [s.message for s in result.stages])
    assert result.blank_area_mm2 == pytest.approx(truth["blank_area_mm2"], rel=rel)
    assert result.cut_length_mm == pytest.approx(truth["cut_length_mm"], rel=rel)
    assert result.hole_count == truth["hole_count"]
    assert result.bend_count == truth["bend_count"]
    assert all(q.confidence == "high" for q in result.metric_quality.values())


@pytest.mark.parametrize("level,index", [("Lv1", 3), ("Lv2", 1), ("Lv3", 0), ("Lv3", 2)])
def test_generated_parts_unfold_exactly(tmp_path, level, index):
    _, built = generate(level, index, seed=7)
    result, truth = _analyze(built, tmp_path)
    _assert_matches(result, truth)
    assert result.flat_pattern.method == "geometric_graph_unfold"
    assert len(result.flat_pattern.bend_lines) == truth["bend_count"]


def test_slot_on_a_flange_is_one_hole(tmp_path):
    part = SheetPart("L_slot", 1.6, box(0, 0, 60, 40))
    part.cut_base(slot(20, 20, 20, 6))
    f = part.flange(part.base, ((60, 0), (60, 40)), 30)
    f.cutouts += [slot(20, f.ba + 15, 18, 5, angle=90), circle(8, f.ba + 20, 4)]
    result, truth = _analyze(part, tmp_path)
    _assert_matches(result, truth)
    assert result.hole_count == 3


def test_open_hem_is_unfolded_and_reported_as_180_degrees(tmp_path):
    # Inner radius 0.5t leaves a gap of exactly one thickness between the base and the hem;
    # those opposed faces must not be taken as the two skins of the sheet.
    t = 2.0
    part = SheetPart("hem", t, box(0, 0, 60, 50))
    part.cut_base(circle(25, 25, 24))  # base skin smaller than the hem skin: the gap pair ranks first
    part.flange(part.base, ((60, 0), (60, 50)), 57, angle=180.0, radius=0.5 * t)
    result, truth = _analyze(part, tmp_path)
    _assert_matches(result, truth)
    assert [round(b.angle_deg) for b in result.bend_evidence] == [180]

    faces = list(part.build()[0].Faces())
    planes = [f for f in faces if f.geomType() == "PLANE" and abs(f.normalAt().z) > 0.9]
    base_top = next(f for f in planes if abs(f.Center().z - t) < 1e-6)
    base_bottom = next(f for f in planes if abs(f.Center().z) < 1e-6)
    hem_inner = next(f for f in planes if abs(f.Center().z - 2 * t) < 1e-6)
    recognition = SheetMetalRecognition()
    normal = recognition.vector(base_top.normalAt())
    assert recognition.material_between(base_top, base_bottom, normal)
    assert recognition.plane_gap(base_top, hem_inner, normal) == pytest.approx(t)
    assert not recognition.material_between(base_top, hem_inner, normal)


def test_k_factor_other_than_half_is_developed_with_that_k(tmp_path):
    part = SheetPart("L_k033", 2.0, box(0, 0, 60, 40), k_factor=0.33)
    part.cut_base(circle(20, 20, 6))
    part.flange(part.base, ((60, 0), (60, 40)), 30, angle=120, radius=3.0)
    result, truth = _analyze(part, tmp_path)
    _assert_matches(result, truth, rel=1e-4)


def test_self_check_flags_a_wrong_development(tmp_path, monkeypatch):
    """A development error must not be reported as an exact result (it becomes 概算)."""
    _, built = generate("Lv2", 1, seed=7)
    original = _BendMap.transform_for

    def skewed(self, shared_edges):
        matrix = original(self, shared_edges).copy()
        matrix[:3, 3] += self.parent[:3, :3] @ self._direction() * 5.0  # 5 mm too long per bend
        return matrix

    monkeypatch.setattr(_BendMap, "transform_for", skewed)
    result, _ = _analyze(built, tmp_path)
    assert result.status == "partial"
    assert "FLAT_PATTERN_UNVERIFIED" in result.reason_codes
    assert result.metric_quality["blank_area_mm2"].confidence != "high"


def test_golden_v2_counts_relief_notch_as_outline_not_hole(tmp_path):
    """golden_v1 bug: a bend relief ending ~1e-14 mm short of the edge was counted as a hole."""
    _, built = generate("Lv2", 18, seed=3)
    v1_holes = built[2]["hole_count"]
    apply_v2(built)
    assert (v1_holes, built[2]["hole_count"]) == (9, 7)
    result, truth = _analyze(built, tmp_path)
    _assert_matches(result, truth)
