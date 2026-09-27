import pytest

cq = pytest.importorskip("cadquery")

from src.sheetmetal_geometry import ToleranceContext, build_topology_graph, shape_mesh


def test_topology_graph_uses_shared_edges():
    shape = cq.Workplane("XY").box(20, 10, 2).val()
    graph = build_topology_graph(list(shape.Faces()))
    assert len(graph.nodes) == 6
    assert all(len(node.adjacent) == 4 for node in graph.nodes.values())


def test_scale_aware_tolerance_grows_with_model():
    small = ToleranceContext.from_shape(cq.Workplane("XY").box(10, 10, 1).val())
    large = ToleranceContext.from_shape(cq.Workplane("XY").box(10_000, 10_000, 10).val())
    assert large.linear_mm > small.linear_mm
    assert 1e-6 <= small.linear_mm <= 1e-2


def test_local_mesh_contains_only_numeric_geometry():
    mesh = shape_mesh(cq.Workplane("XY").box(10, 5, 2).val())
    assert len(mesh["x"]) > 0
    assert len(mesh["i"]) > 0
    assert set(mesh) == {"x", "y", "z", "i", "j", "k"}
