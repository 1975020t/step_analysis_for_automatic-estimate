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
