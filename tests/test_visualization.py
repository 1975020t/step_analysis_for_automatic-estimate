from pathlib import Path

import pytest

cq = pytest.importorskip("cadquery")
pytest.importorskip("plotly")

from src.models import FlatPatternSummary
from src.visualization import flat_pattern_figure, original_shape_figure


def test_original_step_is_rendered_as_local_mesh(tmp_path: Path):
    path = tmp_path / "preview.step"
    cq.exporters.export(cq.Workplane("XY").box(10, 5, 2).val(), str(path))
    figure = original_shape_figure(path.read_bytes())
    assert len(figure.data) == 1
    assert figure.data[0].type == "mesh3d"
    assert len(figure.data[0].x) > 0


def test_flat_pattern_renders_outer_hole_and_bend_line():
    pattern = FlatPatternSummary(
        method="test", area_mm2=190, cut_length_mm=72,
        boundary_count=2, outer_boundary_count=1, inner_boundary_count=1,
        surface_region_count=3,
        outer_loops=[[(0, 0), (20, 0), (20, 10), (0, 10), (0, 0)]],
        inner_loops=[[(5, 3), (8, 3), (8, 6), (5, 6), (5, 3)]],
        bend_lines=[[(10, 0), (10, 10)]],
    )
    figure = flat_pattern_figure(pattern)
    assert [trace.name for trace in figure.data] == ["外周", "穴・内周", "曲げ線"]
