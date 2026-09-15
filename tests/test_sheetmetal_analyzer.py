from __future__ import annotations

import io
import math
from pathlib import Path

import pytest

cq = pytest.importorskip("cadquery")

from src.sheetmetal_analyzer import SheetMetalAnalyzer


def export_step(shape, path: Path) -> Path:
    cq.exporters.export(shape, str(path))
    return path


def test_flat_plate_with_round_and_rectangular_holes(tmp_path: Path):
    thickness = 2.0
    shape = (
        cq.Workplane("XY")
        .box(100, 50, thickness)
        .faces(">Z")
        .workplane()
        .pushPoints([(-25, 0)])
        .hole(10)
        .moveTo(20, -5)
        .rect(12, 10)
        .cutThruAll()
        .val()
    )
    result = SheetMetalAnalyzer().analyze(export_step(shape, tmp_path / "plate.step"))

    expected_area = 100 * 50 - math.pi * 5**2 - 12 * 10
    expected_cut = 2 * (100 + 50) + math.pi * 10 + 2 * (12 + 10)
    assert result.status == "success", result.model_dump()
    assert result.thickness_mm == pytest.approx(thickness, rel=0.01)
    assert result.blank_area_mm2 == pytest.approx(expected_area, rel=0.01)
    assert result.cut_length_mm == pytest.approx(expected_cut, rel=0.01)
    assert result.hole_count == 2
    assert result.bend_count == 0
    assert result.flat_pattern is not None
    assert result.flat_pattern.inner_boundary_count == 2
    assert len(result.hole_evidence) == 2
    assert {item.method for item in result.thickness_candidates} >= {
        "opposing_planes", "short_linear_edges"
    }


def test_one_linear_bend_is_unfolded_and_counted(tmp_path: Path):
    thickness = 2.0
    width = 20.0
    inner_radius = 5.0
    profile = (
        cq.Workplane("XZ")
        .moveTo(-40, 0)
        .lineTo(0, 0)
        .threePointArc((5 / math.sqrt(2), 5 - 5 / math.sqrt(2)), (5, 5))
        .lineTo(5, 35)
        .lineTo(7, 35)
        .lineTo(7, 5)
        .threePointArc((7 / math.sqrt(2), 5 - 7 / math.sqrt(2)), (0, -2))
        .lineTo(-40, -2)
        .close()
    )
    shape = profile.extrude(width).val()
    result = SheetMetalAnalyzer().analyze(
        export_step(shape, tmp_path / "one_bend.step"), file_name="one_bend.stp"
    )

    developed_length = 40 + 30 + (inner_radius + thickness / 2) * math.pi / 2
    expected_area = width * developed_length
    expected_cut = 2 * (width + developed_length)
    assert result.status == "success", result.model_dump()
    assert result.thickness_mm == pytest.approx(thickness, rel=0.01)
    assert result.blank_area_mm2 == pytest.approx(expected_area, rel=0.01)
    assert result.cut_length_mm == pytest.approx(expected_cut, rel=0.01)
    assert result.hole_count == 0
    assert result.bend_count == 1
    assert len(result.bend_evidence) == 1


def test_multiple_solids_are_rejected_with_reason(tmp_path: Path):
    first = cq.Workplane("XY").box(40, 30, 2).val()
    second = cq.Workplane("XY").transformed(offset=(60, 0, 0)).box(40, 30, 2).val()
    compound = cq.Compound.makeCompound([first, second])
    result = SheetMetalAnalyzer().analyze(export_step(compound, tmp_path / "assembly.step"))

    assert result.status == "unsupported"
    assert result.reason_code == "MULTIPLE_SOLIDS"
    assert result.thickness_mm is None
    assert result.stages[-1].status == "failed"


def test_non_sheet_machined_block_is_rejected(tmp_path: Path):
    cube = cq.Workplane("XY").box(20, 20, 20).val()
    result = SheetMetalAnalyzer().analyze(export_step(cube, tmp_path / "cube.step"))

    assert result.status == "unsupported"
    assert result.reason_code == "NOT_SHEET_METAL"
    assert result.thickness_mm is None


def test_invalid_step_is_reported_as_read_error():
    result = SheetMetalAnalyzer().analyze(io.BytesIO(b"not a STEP file"), "broken.step")

    assert result.status == "error"
    assert result.reason_code == "STEP_READ_ERROR"
    assert result.stages[-1].status == "failed"


def test_wrong_extension_is_rejected_before_geometry_read():
    result = SheetMetalAnalyzer().analyze(io.BytesIO(b"anything"), "part.obj")

    assert result.status == "error"
    assert result.reason_code == "INVALID_FILE_TYPE"


def test_stepped_thickness_is_not_forced_into_a_result(tmp_path: Path):
    thin = cq.Workplane("XY").box(80, 40, 2).val()
    thick_patch = cq.Workplane("XY").transformed(offset=(20, 0, 2)).box(20, 20, 4).val()
    shape = thin.fuse(thick_patch)
    result = SheetMetalAnalyzer().analyze(export_step(shape, tmp_path / "variable.step"))

    assert result.status == "unsupported"
    assert result.reason_code == "NON_CONSTANT_THICKNESS"
    assert result.thickness_mm is None


def test_sharp_fold_without_bend_radius_is_rejected(tmp_path: Path):
    profile = (
        cq.Workplane("XZ")
        .moveTo(-40, 0)
        .lineTo(5, 0)
        .lineTo(5, 35)
        .lineTo(7, 35)
        .lineTo(7, -2)
        .lineTo(-40, -2)
        .close()
    )
    shape = profile.extrude(20).val()
    result = SheetMetalAnalyzer().analyze(export_step(shape, tmp_path / "sharp.step"))

    assert result.status == "unsupported"
    assert result.reason_code == "BEND_UNDETERMINED"
    assert result.bend_count is None


def test_two_independent_linear_bends_are_counted_once_each(tmp_path: Path):
    thickness = 2.0
    profile = (
        cq.Workplane("XZ")
        .moveTo(-40, 0)
        .lineTo(0, 0)
        .threePointArc((5 / math.sqrt(2), 5 - 5 / math.sqrt(2)), (5, 5))
        .lineTo(5, 25)
        .threePointArc((10 - 5 / math.sqrt(2), 25 + 5 / math.sqrt(2)), (10, 30))
        .lineTo(40, 30)
        .lineTo(40, 28)
        .lineTo(10, 28)
        .threePointArc((10 - 3 / math.sqrt(2), 25 + 3 / math.sqrt(2)), (7, 25))
        .lineTo(7, 5)
        .threePointArc((7 / math.sqrt(2), 5 - 7 / math.sqrt(2)), (0, -2))
        .lineTo(-40, -2)
        .close()
    )
    shape = profile.extrude(20).val()
    result = SheetMetalAnalyzer().analyze(export_step(shape, tmp_path / "two_bends.step"))

    expected_length = 40 + 20 + 30 + (5 + 0.33 * thickness + 3 + 0.33 * thickness) * math.pi / 2
    assert result.status == "success", result.model_dump()
    assert result.bend_count == 2
    assert result.blank_area_mm2 == pytest.approx(20 * expected_length, rel=0.01)
    assert result.cut_length_mm == pytest.approx(2 * (20 + expected_length), rel=0.01)


def test_step_without_a_solid_has_specific_reason(tmp_path: Path):
    wire = cq.Workplane("XY").rect(20, 10).val()
    result = SheetMetalAnalyzer().analyze(export_step(wire, tmp_path / "wire.step"))

    assert result.status == "unsupported"
    assert result.reason_code == "NO_SOLID"


@pytest.mark.parametrize("angle_deg", [45.0, 60.0, 120.0])
def test_arbitrary_linear_bend_angle_uses_k_factor(tmp_path: Path, angle_deg: float):
    thickness = 2.0
    radius = 5.0
    width = 20.0
    first_length = 40.0
    second_length = 30.0
    angle = math.radians(angle_deg)
    half = angle / 2
    direction = (math.cos(angle), math.sin(angle))

    inner_end = (radius * math.sin(angle), radius * (1 - math.cos(angle)))
    inner_far = (
        inner_end[0] + second_length * direction[0],
        inner_end[1] + second_length * direction[1],
    )
    outer_radius = radius + thickness
    outer_end = (
        outer_radius * math.sin(angle),
        radius - outer_radius * math.cos(angle),
    )
    outer_far = (
        outer_end[0] + second_length * direction[0],
        outer_end[1] + second_length * direction[1],
    )
    profile = (
        cq.Workplane("XZ")
        .moveTo(-first_length, 0)
        .lineTo(0, 0)
        .threePointArc(
            (radius * math.sin(half), radius * (1 - math.cos(half))), inner_end
        )
        .lineTo(*inner_far)
        .lineTo(*outer_far)
        .lineTo(*outer_end)
        .threePointArc(
            (outer_radius * math.sin(half), radius - outer_radius * math.cos(half)),
            (0, -thickness),
        )
        .lineTo(-first_length, -thickness)
        .close()
    )
    shape = profile.extrude(width).val()
    result = SheetMetalAnalyzer().analyze(
        export_step(shape, tmp_path / f"bend_{angle_deg:g}.step")
    )

    expected_length = first_length + second_length + angle * (radius + 0.33 * thickness)
    assert result.status == "success", result.model_dump()
    assert result.bend_count == 1
    assert result.bend_evidence[0].angle_deg == pytest.approx(angle_deg, rel=0.01)
    assert result.blank_area_mm2 == pytest.approx(width * expected_length, rel=0.01)
    assert result.metric_quality["blank_area_mm2"].confidence == "medium"
    assert result.assumptions


def test_open_tray_with_corner_reliefs_uses_branched_unfold(tmp_path: Path):
    thickness = 2.0
    radius = 5.0
    flange_height = 20.0

    def flange(tangent: float, half_span: float):
        profile = (
            cq.Workplane("XZ")
            .moveTo(tangent, 0)
            .threePointArc(
                (tangent + radius / math.sqrt(2), radius - radius / math.sqrt(2)),
                (tangent + radius, radius),
            )
            .lineTo(tangent + radius, radius + flange_height)
            .lineTo(tangent + radius + thickness, radius + flange_height)
            .lineTo(tangent + radius + thickness, radius)
            .threePointArc(
                (
                    tangent + (radius + thickness) / math.sqrt(2),
                    radius - (radius + thickness) / math.sqrt(2),
                ),
                (tangent, -thickness),
            )
            .close()
        )
        return profile.extrude(half_span, both=True).val()

    base = cq.Workplane("XY").box(80, 60, thickness).translate((0, 0, -1)).val()
    x_flange = flange(40, 25)
    y_flange = flange(30, 35).rotate((0, 0, 0), (0, 0, 1), 90)
    tray = (
        base.fuse(x_flange)
        .fuse(x_flange.mirror("YZ"))
        .fuse(y_flange)
        .fuse(y_flange.mirror("XZ"))
    )
    result = SheetMetalAnalyzer().analyze(export_step(tray, tmp_path / "open_tray.step"))

    bend_allowance = math.pi / 2 * (radius + 0.33 * thickness)
    expected_area = 80 * 60 + 2 * 50 * flange_height + 2 * 70 * flange_height
    expected_area += 2 * 50 * bend_allowance + 2 * 70 * bend_allowance
    expected_perimeter = 2 * (80 + 60) + 8 * (flange_height + bend_allowance)
    assert result.status == "success", result.model_dump()
    assert result.bend_count == 4
    assert result.flat_pattern.method == "geometric_branched_tray_unfold"
    assert result.blank_area_mm2 == pytest.approx(expected_area, rel=0.01)
    assert result.cut_length_mm == pytest.approx(expected_perimeter, rel=0.01)
    assert len(result.flat_pattern.bend_lines) == 4


def test_hole_crossing_bend_is_not_reported_as_high_confidence_exact(tmp_path: Path):
    profile = (
        cq.Workplane("XZ")
        .moveTo(-40, 0)
        .lineTo(0, 0)
        .threePointArc((5 / math.sqrt(2), 5 - 5 / math.sqrt(2)), (5, 5))
        .lineTo(5, 35)
        .lineTo(7, 35)
        .lineTo(7, 5)
        .threePointArc((7 / math.sqrt(2), 5 - 7 / math.sqrt(2)), (0, -2))
        .lineTo(-40, -2)
        .close()
    )
    # CenterOfBoundBox places the cylindrical cut across the planar/bend transition.
    shape = profile.extrude(20).faces(">Z").workplane().hole(6).val()
    result = SheetMetalAnalyzer().analyze(
        export_step(shape, tmp_path / "bend_crossing_hole.step")
    )

    assert result.status == "partial"
    assert result.hole_count == 1
    assert result.reason_code == "INTERNAL_BOUNDARY_ESTIMATED"
    assert "INTERNAL_BOUNDARY_ESTIMATED" in result.reason_codes
    assert result.metric_quality["cut_length_mm"].confidence == "medium"
    assert result.flat_pattern.inner_loops == []
