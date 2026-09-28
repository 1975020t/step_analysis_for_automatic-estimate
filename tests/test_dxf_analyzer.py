"""Flat-pattern DXF analyzer: hand-built drawings for each rule, plus golden-generator parts."""
from __future__ import annotations

import io
import math

import pytest

ezdxf = pytest.importorskip("ezdxf")

from src.dxf_analyzer import DxfAnalyzer

W, H, R = 120.0, 80.0, 5.0
AREA = W * H - math.pi * R * R
CUT = 2 * (W + H) + 2 * math.pi * R


def new_doc(units=4):
    doc = ezdxf.new("R2010", setup=True)
    doc.header["$INSUNITS"] = units
    return doc, doc.modelspace()


def rectangle(msp, x0, y0, x1, y1, pieces=1, s=1.0, **attrs):
    corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    for a, b in zip(corners, corners[1:] + corners[:1]):
        for k in range(pieces):
            p = (a[0] + (b[0] - a[0]) * k / pieces, a[1] + (b[1] - a[1]) * k / pieces)
            q = (a[0] + (b[0] - a[0]) * (k + 1) / pieces, a[1] + (b[1] - a[1]) * (k + 1) / pieces)
            msp.add_line((p[0] * s, p[1] * s), (q[0] * s, q[1] * s), dxfattribs=attrs)


def plate(msp, s=1.0, bend=True, **cut):
    rectangle(msp, 0, 0, W, H, s=s, **cut)
    msp.add_circle((30 * s, 40 * s), R * s, dxfattribs=cut)
    if bend:
        msp.add_line((90 * s, 0), (90 * s, H * s), dxfattribs={"linetype": "DASHED", "color": 3})


def frame_and_title(msp):
    rectangle(msp, -40, -60, 260, 120)
    for y in (-51, -42, -33):
        msp.add_line((170, y), (260, y))
    msp.add_line((170, -60), (170, -24))
    msp.add_line((170, -24), (260, -24))
    msp.add_text("板厚 t2", height=4).set_placement((173, -58))


def analyze(tmp_path, doc, thickness=2.0, name="part.dxf"):
    path = tmp_path / name
    doc.saveas(path)
    return DxfAnalyzer(thickness_mm=thickness, k_factor=0.5, k_factor_is_default=False).analyze(path)


def assert_exact(result, holes=1, bends=1, area=AREA, cut=CUT):
    assert result.status == "success", (result.reason_codes, result.warnings)
    assert result.blank_area_mm2 == pytest.approx(area, rel=1e-4)
    assert result.cut_length_mm == pytest.approx(cut, rel=1e-4)
    assert (result.hole_count, result.bend_count) == (holes, bends)
    assert all(q.confidence == "high" for q in result.metric_quality.values())


def test_clean_drawing_on_named_layers(tmp_path):
    doc, msp = new_doc()
    doc.layers.add("CUT")
    doc.layers.add("BEND")
    rectangle(msp, 0, 0, W, H, layer="CUT")
    msp.add_circle((30, 40), R, dxfattribs={"layer": "CUT"})
    msp.add_line((90, 0), (90, H), dxfattribs={"layer": "BEND"})  # continuous, but on a bend layer
    assert_exact(analyze(tmp_path, doc))


def test_frame_title_block_centre_marks_and_dimension_are_not_cut(tmp_path):
    doc, msp = new_doc()
    plate(msp)
    frame_and_title(msp)
    for a, b in [((23, 40), (37, 40)), ((30, 33), (30, 47))]:
        msp.add_line(a, b, dxfattribs={"linetype": "CENTER", "color": 1})
    msp.add_linear_dim(base=(0, -10), p1=(0, 0), p2=(W, 0)).render()
    assert_exact(analyze(tmp_path, doc))


def test_messy_split_lines_tiny_gaps_duplicates_polyline_and_inches(tmp_path):
    doc, msp = new_doc(units=1)
    s = 1 / 25.4
    rectangle(msp, 0, 0, W, H, pieces=3, s=s)
    msp.add_line((0, 0), (W * s, 0))  # duplicate of a split edge
    msp.add_arc((30 * s, 40 * s), R * s, 0, 180)  # hole as two half arcs ...
    msp.add_arc((30 * s, 40 * s), R * s, 180, 360)
    msp.add_circle((30 * s, 40 * s), R * s)  # ... and drawn again as a circle
    gap = 0.004 * s  # 0.004 mm drawing gap
    msp.add_lwpolyline([(60 * s, 20 * s, 0), (70 * s + gap, 20 * s, 1), (70 * s, 30 * s, 0), (60 * s, 30 * s, 0)],
                       format="xyb", close=True)  # slot-like hole with a bulge (half circle, r=5)
    for a, b in [((90, 0), (90, 40)), ((90, 40), (90, H))]:  # bend line in two pieces
        msp.add_line((a[0] * s, a[1] * s), (b[0] * s, b[1] * s), dxfattribs={"linetype": "PHANTOM", "color": 4})
    slot_area = 10 * 10 + math.pi * 25 / 2
    slot_len = 10 + 10 + 10 + math.pi * 5
    result = analyze(tmp_path, doc)
    # the values are right, but a unit other than mm is never confirmed (a mm drawing saved as inch looks the same)
    assert result.status == "partial" and result.reason_codes == ["UNIT_NOT_MM"]
    assert result.hole_count == 2 and result.bend_count == 1
    assert result.blank_area_mm2 == pytest.approx(AREA - slot_area, rel=1e-4)
    assert result.cut_length_mm == pytest.approx(CUT + slot_len, rel=1e-4)


def test_hole_block_insert_inherits_the_insert_style(tmp_path):
    doc, msp = new_doc()
    rectangle(msp, 0, 0, W, H)
    block = doc.blocks.new("HOLE")
    block.add_circle((0, 0), R, dxfattribs={"layer": "0"})
    msp.add_blockref("HOLE", (30, 40))
    msp.add_line((90, 0), (90, H), dxfattribs={"linetype": "DASHED"})
    assert_exact(analyze(tmp_path, doc))


def test_open_outline_is_not_confirmed(tmp_path):
    doc, msp = new_doc()
    for a, b in [((0, 0), (W, 0)), ((W, 0), (W, H)), ((W, H), (0, H)), ((0, H), (0, 0.5))]:  # 0.5 mm gap
        msp.add_line(a, b)
    msp.add_circle((30, 40), R)
    result = analyze(tmp_path, doc)
    assert (result.status, result.reason_code) == ("unsupported", "OPEN_CONTOUR")
    assert result.blank_area_mm2 is None


def test_two_parts_in_one_file_are_not_confirmed(tmp_path):
    doc, msp = new_doc()
    plate(msp)
    rectangle(msp, 150, 0, 150 + W, H)
    result = analyze(tmp_path, doc)
    assert (result.status, result.reason_code) == ("unsupported", "MULTIPLE_PARTS")


def test_small_gap_is_closed_but_flagged(tmp_path):
    doc, msp = new_doc()
    for a, b in [((0, 0), (W, 0)), ((W, 0), (W, H)), ((W, H), (0, H)), ((0, H), (0, 0.05))]:  # 0.05 mm gap
        msp.add_line(a, b)
    msp.add_circle((30, 40), R)
    result = analyze(tmp_path, doc)
    assert result.status == "partial"
    assert "SMALL_GAP_CLOSED" in result.reason_codes
    assert result.hole_count == 1


def test_marking_line_of_another_colour_is_ignored_but_same_colour_is_flagged(tmp_path):
    doc, msp = new_doc()
    plate(msp)
    msp.add_line((40, 60), (60, 60), dxfattribs={"color": 5})
    assert_exact(analyze(tmp_path, doc, name="mark.dxf"))

    doc, msp = new_doc()
    plate(msp)
    msp.add_line((40, 60), (60, 60))  # same colour as the outline: slit or note? -> 概算
    result = analyze(tmp_path, doc, name="slit.dxf")
    assert result.status == "partial" and "UNEXPLAINED_GEOMETRY" in result.reason_codes


def test_dashed_line_that_is_not_a_bend_is_flagged(tmp_path):
    doc, msp = new_doc()
    plate(msp)
    msp.add_line((50, 10), (70, 10), dxfattribs={"linetype": "DASHED"})  # ends are not on the outline
    result = analyze(tmp_path, doc)
    assert result.status == "partial" and "UNEXPLAINED_LINE" in result.reason_codes
    assert result.bend_count == 1


def test_title_block_thickness_and_dimensions_are_cross_checked(tmp_path):
    doc, msp = new_doc()
    plate(msp)
    frame_and_title(msp)  # says t2
    result = analyze(tmp_path, doc, thickness=1.6, name="t.dxf")
    assert result.status == "partial" and "THICKNESS_MISMATCH" in result.reason_codes

    doc, msp = new_doc()
    plate(msp)
    msp.add_linear_dim(base=(0, -10), p1=(0, 0), p2=(W + 30, 0)).render()  # says 150, part is 120
    result = analyze(tmp_path, doc, name="dim.dxf")
    assert result.status == "partial" and "DIMENSION_MISMATCH" in result.reason_codes


def test_unknown_units_are_assumed_mm_and_flagged(tmp_path):
    doc, msp = new_doc(units=0)
    plate(msp)
    result = analyze(tmp_path, doc)
    assert result.status == "partial" and result.assumptions
    assert result.blank_area_mm2 == pytest.approx(AREA, rel=1e-4)


def test_dimension_text_is_compared_not_the_geometry_with_itself(tmp_path):
    doc, msp = new_doc()
    plate(msp)
    msp.add_linear_dim(base=(0, -10), p1=(0, 0), p2=(W, 0), text="150").render()  # the drawing says 150
    result = analyze(tmp_path, doc, name="text.dxf")
    assert result.status == "partial" and "DIMENSION_MISMATCH" in result.reason_codes


def test_part_split_by_a_solid_bend_line_is_not_taken_for_a_frame(tmp_path):
    doc, msp = new_doc()
    rectangle(msp, 0, 0, W, H)
    msp.add_line((90, 0), (90, H))  # bend line drawn as a solid line
    msp.add_circle((30, 40), R)
    result = analyze(tmp_path, doc)
    assert result.status == "partial"  # before: the hole alone was the part (78.5 mm², confirmed)
    assert result.blank_area_mm2 == pytest.approx(AREA, rel=1e-4) and result.hole_count == 1


def test_mm_drawing_saved_as_inch_is_not_confirmed(tmp_path):
    doc, msp = new_doc(units=1)
    plate(msp)
    msp.add_linear_dim(base=(0, -10), p1=(0, 0), p2=(W, 0)).render()
    result = analyze(tmp_path, doc)
    assert result.status == "partial" and "UNIT_NOT_MM" in result.reason_codes
    assert any("25.4倍" in w for w in result.warnings)


def test_closed_lines_of_another_colour_or_layer_are_not_counted_silently(tmp_path):
    doc, msp = new_doc()
    plate(msp)
    msp.add_circle((60, 40), 3, dxfattribs={"color": 5})  # a marking circle
    result = analyze(tmp_path, doc)
    assert result.status == "partial" and result.hole_count == 1
    assert result.blank_area_mm2 == pytest.approx(AREA, rel=1e-4)
    doc, msp = new_doc()
    doc.layers.add("HOLES")
    plate(msp)
    msp.add_circle((60, 40), 3, dxfattribs={"layer": "HOLES"})  # a hole on its own layer (or a mark)
    result = analyze(tmp_path, doc, name="layer.dxf")
    assert result.status == "partial" and result.hole_count == 2


def test_no_bend_line_is_not_confirmed_as_flat_unless_the_user_says_so(tmp_path):
    doc, msp = new_doc()
    plate(msp, bend=False)
    path = tmp_path / "flat.dxf"
    doc.saveas(path)
    result = DxfAnalyzer(thickness_mm=2.0).analyze(path)
    assert result.status == "partial" and "NO_BEND_LINES" in result.reason_codes and result.bend_count == 0
    flat = DxfAnalyzer(thickness_mm=2.0, flat_confirmed=True).analyze(path)
    assert flat.status == "success" and flat.bend_count == 0


def test_bad_inputs(tmp_path):
    analyzer = DxfAnalyzer(thickness_mm=2.0)
    assert analyzer.analyze(io.BytesIO(b"x"), "part.step").reason_code == "INVALID_FILE_TYPE"
    assert analyzer.analyze(io.BytesIO(b"not a dxf"), "broken.dxf").reason_code == "DXF_READ_ERROR"
    doc, msp = new_doc()
    plate(msp)
    doc.saveas(tmp_path / "p.dxf")
    assert DxfAnalyzer(thickness_mm=0).analyze(tmp_path / "p.dxf").reason_code == "THICKNESS_REQUIRED"
    buffer = io.StringIO()
    doc.write(buffer)
    uploaded = DxfAnalyzer(thickness_mm=2.0).analyze(io.BytesIO(buffer.getvalue().encode("utf-8")), "upload.dxf")
    assert uploaded.status == "success" and uploaded.file_name == "upload.dxf"


@pytest.mark.parametrize("variant", ["D1", "D2"])
def test_golden_generator_parts(tmp_path, variant):
    pytest.importorskip("cadquery")
    from golden.dxf_golden import build_dxf

    truth = build_dxf("Lv3", 0, 7, variant, tmp_path)
    result = DxfAnalyzer(thickness_mm=truth["thickness_mm"], k_factor=0.5, k_factor_is_default=False).analyze(
        tmp_path / f"{truth['name']}.dxf")
    assert_exact(result, holes=truth["hole_count"], bends=truth["bend_count"],
                 area=truth["blank_area_mm2"], cut=truth["cut_length_mm"])
