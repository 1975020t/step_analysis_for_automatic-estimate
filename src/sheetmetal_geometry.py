from __future__ import annotations

import math
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Iterable


@dataclass(frozen=True)
class ToleranceContext:
    diagonal_mm: float
    linear_mm: float
    angular: float = 1e-4

    @classmethod
    def from_shape(cls, shape) -> "ToleranceContext":
        box = shape.BoundingBox()
        diagonal = math.sqrt(box.xlen**2 + box.ylen**2 + box.zlen**2)
        return cls(diagonal_mm=diagonal, linear_mm=min(1e-2, max(1e-6, diagonal * 1e-7)))


@dataclass
class FaceNode:
    index: int
    geom_type: str
    area_mm2: float
    orientation: str
    adjacent: set[int] = field(default_factory=set)
    shared_edges: dict[int, list[int]] = field(default_factory=dict)
    role: str = "unclassified"


@dataclass
class TopologyGraph:
    nodes: dict[int, FaceNode]
    edge_owners: dict[int, list[int]]

    @property
    def unclassified_ratio(self) -> float:
        if not self.nodes:
            return 1.0
        return sum(node.role == "unclassified" for node in self.nodes.values()) / len(self.nodes)


def build_topology_graph(faces: list) -> TopologyGraph:
    edge_owners: dict[int, list[int]] = defaultdict(list)
    face_edges: dict[int, list[int]] = {}
    nodes: dict[int, FaceNode] = {}
    for index, face in enumerate(faces):
        keys = [hash(edge) for edge in face.Edges()]
        face_edges[index] = keys
        for key in keys:
            edge_owners[key].append(index)
        nodes[index] = FaceNode(
            index=index,
            geom_type=face.geomType(),
            area_mm2=float(face.Area()),
            orientation=str(face.wrapped.Orientation()),
        )
    for key, owners in edge_owners.items():
        for owner in owners:
            others = [item for item in owners if item != owner]
            nodes[owner].adjacent.update(others)
            if others:
                nodes[owner].shared_edges[key] = others
    return TopologyGraph(nodes=nodes, edge_owners=dict(edge_owners))


def validate_shape(shape) -> tuple[bool, str | None]:
    """Validate the imported B-Rep without silently changing customer geometry."""
    try:
        from OCP.BRepCheck import BRepCheck_Analyzer

        analyzer = BRepCheck_Analyzer(shape.wrapped)
        if analyzer.IsValid():
            return True, None
        return False, "BREP_INVALID"
    except Exception:
        return False, "BREP_VALIDATION_FAILED"


def shape_mesh(shape, tolerance: float = 0.2) -> dict[str, list]:
    vertices, triangles = shape.tessellate(tolerance)
    return {
        "x": [float(vertex.x) for vertex in vertices],
        "y": [float(vertex.y) for vertex in vertices],
        "z": [float(vertex.z) for vertex in vertices],
        "i": [int(triangle[0]) for triangle in triangles],
        "j": [int(triangle[1]) for triangle in triangles],
        "k": [int(triangle[2]) for triangle in triangles],
    }


def plane_basis(face) -> tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]]:
    center = face.Center()
    normal = _unit(_vector(face.normalAt()))
    reference = (1.0, 0.0, 0.0) if abs(normal[0]) < 0.9 else (0.0, 1.0, 0.0)
    x_axis = _unit(_cross(reference, normal))
    y_axis = _unit(_cross(normal, x_axis))
    return _vector(center), x_axis, y_axis


def planar_face_loops(face, samples_per_curve: int = 48) -> list[list[tuple[float, float]]]:
    origin, x_axis, y_axis = plane_basis(face)
    loops: list[list[tuple[float, float]]] = []
    for wire in face.Wires():
        edge_points: list[list[tuple[float, float, float]]] = []
        for edge in wire.Edges():
            count = 2 if edge.geomType() == "LINE" else samples_per_curve
            try:
                points = [_vector(point) for point in edge.sample(count)[0]]
            except Exception:
                points = [_vector(edge.startPoint()), _vector(edge.endPoint())]
            edge_points.append(points)
        ordered = _connect_edge_points(edge_points)
        projected = [
            (_dot(_sub(point, origin), x_axis), _dot(_sub(point, origin), y_axis))
            for point in ordered
        ]
        if projected and _distance_2d(projected[0], projected[-1]) > 1e-7:
            projected.append(projected[0])
        if len(projected) >= 4:
            loops.append(projected)
    return loops


def classify_loops(
    loops: list[list[tuple[float, float]]],
) -> tuple[list[list[tuple[float, float]]], list[list[tuple[float, float]]]]:
    """Classify closed loops using containment depth, not face/component rank."""
    outers: list[list[tuple[float, float]]] = []
    inners: list[list[tuple[float, float]]] = []
    for index, loop in enumerate(loops):
        point = _interior_probe(loop)
        depth = sum(
            _point_in_polygon(point, other)
            for other_index, other in enumerate(loops)
            if other_index != index and abs(polygon_area(other)) > abs(polygon_area(loop))
        )
        (outers if depth % 2 == 0 else inners).append(loop)
    return outers, inners


def polygon_area(loop: list[tuple[float, float]]) -> float:
    return 0.5 * sum(
        first[0] * second[1] - second[0] * first[1]
        for first, second in zip(loop, loop[1:])
    )


def polyline_length(loop: list[tuple[float, float]]) -> float:
    return sum(_distance_2d(first, second) for first, second in zip(loop, loop[1:]))


def loops_bbox(loops: list[list[tuple[float, float]]]) -> tuple[float, float] | None:
    points = [point for loop in loops for point in loop]
    if not points:
        return None
    xs, ys = zip(*points)
    return max(xs) - min(xs), max(ys) - min(ys)


def rectangle_loop(length: float, width: float) -> list[tuple[float, float]]:
    return [(0.0, 0.0), (length, 0.0), (length, width), (0.0, width), (0.0, 0.0)]


def connected_components(graph: TopologyGraph, indices: Iterable[int]) -> list[set[int]]:
    remaining = set(indices)
    components: list[set[int]] = []
    while remaining:
        start = remaining.pop()
        component = {start}
        queue = deque([start])
        while queue:
            current = queue.popleft()
            for other in graph.nodes[current].adjacent & remaining:
                remaining.remove(other)
                component.add(other)
                queue.append(other)
        components.append(component)
    return components


def _connect_edge_points(edges: list[list[tuple[float, float, float]]]) -> list[tuple[float, float, float]]:
    if not edges:
        return []
    result = edges.pop(0)[:]
    while edges:
        end = result[-1]
        best_index, reverse = min(
            ((index, False) for index in range(len(edges))),
            key=lambda item: min(_distance_3d(end, edges[item[0]][0]), _distance_3d(end, edges[item[0]][-1])),
        )
        points = edges.pop(best_index)
        reverse = _distance_3d(end, points[-1]) < _distance_3d(end, points[0])
        if reverse:
            points.reverse()
        result.extend(points[1:] if _distance_3d(end, points[0]) < 1e-6 else points)
    return result


def _interior_probe(loop: list[tuple[float, float]]) -> tuple[float, float]:
    points = loop[:-1] if loop and loop[0] == loop[-1] else loop
    return sum(p[0] for p in points) / len(points), sum(p[1] for p in points) / len(points)


def _point_in_polygon(point: tuple[float, float], polygon: list[tuple[float, float]]) -> bool:
    x, y = point
    inside = False
    for first, second in zip(polygon, polygon[1:]):
        x1, y1 = first
        x2, y2 = second
        if (y1 > y) != (y2 > y):
            crossing = (x2 - x1) * (y - y1) / ((y2 - y1) or 1e-30) + x1
            if x < crossing:
                inside = not inside
    return inside


def _vector(value) -> tuple[float, float, float]:
    return float(value.x), float(value.y), float(value.z)


def _unit(value: tuple[float, float, float]) -> tuple[float, float, float]:
    length = math.sqrt(_dot(value, value))
    return tuple(item / length for item in value)


def _dot(first, second) -> float:
    return sum(a * b for a, b in zip(first, second))


def _cross(first, second) -> tuple[float, float, float]:
    return (
        first[1] * second[2] - first[2] * second[1],
        first[2] * second[0] - first[0] * second[2],
        first[0] * second[1] - first[1] * second[0],
    )


def _sub(first, second) -> tuple[float, float, float]:
    return tuple(a - b for a, b in zip(first, second))


def _distance_2d(first, second) -> float:
    return math.hypot(first[0] - second[0], first[1] - second[1])


def _distance_3d(first, second) -> float:
    return math.sqrt(sum((a - b) ** 2 for a, b in zip(first, second)))
