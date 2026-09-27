"""General flat-pattern development by walking the face-adjacency graph.

One side of the sheet (the skin faces reachable from the largest planar face without crossing
the thickness) is developed into the plane of that face. Every time the walk crosses a bend
(plane -> cylinder -> plane) the child side is rotated back about the bend axis by the bend
angle and shifted by the bend allowance, i.e. the exact inverse of folding a flat sheet:

    U_child = Translate(n * BA) o U_parent o Rotate(axis, -theta),   BA = theta * (r_in + K * t)

Points on a bend (cylinder) face are developed by arc angle, so the bend ends become straight
segments of length BA. The flat pattern's loops are then chained from the side's boundary edges
through shared B-Rep vertices (topologically, no polygon union, so no slivers):

    area       = outer loop area - inner loop areas
    cut length = sum of boundary edge lengths (planar edges are moved rigidly, so their exact
                 lengths are used; bend-end edges are measured after development)
    holes      = number of inner loops

The result carries self-checks (coverage, flatness, multi-path closure, single simple outline,
area vs volume/thickness, cut length vs cut-surface area/thickness) that the analyzer uses to
decide status and confidence.
"""
from __future__ import annotations

import math
from collections import defaultdict, deque
from dataclasses import dataclass, field

import numpy as np

from src.models import FlatPatternSummary

TWO_PI = 2 * math.pi


@dataclass
class BendDevelopment:
    """Development data of one bend face on the walked side."""

    face_index: int
    parent_index: int
    angle_rad: float
    inner_radius: float
    face_radius: float
    axis_length: float
    end_arc_count: int = 0


@dataclass
class UnfoldChecks:
    side_faces: int = 0
    missing_pairs: int = 0            # skin pairs with no face on the walked side
    both_sides_pairs: int = 0         # skin pairs with both faces on the walked side (walk crossed the sheet)
    flatness_mm: float = 0.0          # max out-of-plane distance of developed planar geometry
    closure_mm: float = 0.0           # max disagreement between alternative walk paths (closed loops)
    open_chains: int = 0              # boundary edges that could not be chained into closed loops
    outer_loops: int = 0
    outline_valid: bool = False       # one simple outer loop, holes inside and disjoint
    area_error: float | None = None   # developed area vs volume/thickness (+K correction)
    cut_error: float | None = None    # developed cut length vs cut-surface area/thickness (+K correction)
    holes_on_bends: int = 0           # inner loops touching a bend face (hole crossing a bend)
    cuts_on_bends: int = 0            # boundary edges on a bend face other than its two end arcs
    notes: list[str] = field(default_factory=list)

    def failures(self, flat_tol: float, area_tol: float, cut_tol: float) -> list[str]:
        problems = []
        if self.missing_pairs:
            problems.append(f"展開に含まれない表裏ペア {self.missing_pairs}組")
        if self.both_sides_pairs:
            problems.append(f"表裏の両面を展開した面ペア {self.both_sides_pairs}組")
        if self.flatness_mm > flat_tol:
            problems.append(f"展開後の平面度 {self.flatness_mm:.3g} mm")
        if self.closure_mm > flat_tol:
            problems.append(f"経路間の位置ずれ {self.closure_mm:.3g} mm")
        if self.open_chains:
            problems.append(f"閉じない境界 {self.open_chains}本")
        if self.outer_loops != 1 or not self.outline_valid:
            problems.append("展開図が単一の単純な外形になりません")
        if self.area_error is None or abs(self.area_error) > area_tol:
            problems.append("面積検算不一致" if self.area_error is None else f"面積検算差 {self.area_error:+.2%}")
        if self.cut_error is None or abs(self.cut_error) > cut_tol:
            problems.append("切断長検算不一致" if self.cut_error is None else f"切断長検算差 {self.cut_error:+.2%}")
        return problems


@dataclass
class UnfoldResult:
    flat: FlatPatternSummary
    checks: UnfoldChecks
    bends: list[BendDevelopment]
    side: set[int]
    hole_faces: list[set[int]]


class UnfoldError(RuntimeError):
    pass


class SheetMetalUnfolder:
    """Develop one side of a constant-thickness sheet into the plane of its largest face."""

    ARC_SEGMENT_RAD = TWO_PI / 180  # sampling of curved edges for the 2D polygon (lengths stay exact)

    def __init__(self, recognition, k_factor: float) -> None:
        self.recognition = recognition
        self.k_factor = k_factor

    # ------------------------------------------------------------------ public
    def build(self, faces, pairs, thickness, topology, volume: float, cut_area: float) -> UnfoldResult:
        t, k = float(thickness), self.k_factor
        partner: dict[int, int] = {}
        for pair in pairs:
            partner[pair.first], partner[pair.second] = pair.second, pair.first
        skin = set(partner)
        inner_radius: dict[int, float] = {}
        for pair in pairs:
            if pair.kind == "CYLINDER":
                radius = min(self.recognition.cylinder(faces[pair.first])[2],
                             self.recognition.cylinder(faces[pair.second])[2])
                inner_radius[pair.first] = inner_radius[pair.second] = radius
        planar = [pair for pair in pairs if pair.kind == "PLANE"]
        if not planar:
            raise UnfoldError("基準となる平面の表裏ペアがありません")
        start = max((index for pair in planar for index in (pair.first, pair.second)),
                    key=lambda index: faces[index].Area())
        n0 = _normal(faces[start])
        c0 = _center(faces[start])

        # ---- walk one side of the sheet
        transform: dict[int, np.ndarray] = {start: np.eye(4)}
        bend_maps: dict[int, _BendMap] = {}
        developments: list[BendDevelopment] = []
        closure = 0.0
        queue = deque([start])
        while queue:
            current = queue.popleft()
            current_type = faces[current].geomType()
            for other in sorted(topology.nodes[current].adjacent):
                if other not in skin or other == partner.get(current):
                    continue
                other_type = faces[other].geomType()
                if current_type == "PLANE" and other_type == "PLANE":
                    candidate = transform[current]
                elif current_type == "PLANE" and other_type == "CYLINDER":
                    if other in bend_maps:
                        continue
                    shared = self._shared_edges(faces, topology, current, other)
                    if not shared:
                        continue
                    bend_map = _BendMap.create(
                        faces[other], shared, transform[current], inner_radius.get(other), t, k)
                    if bend_map is None:
                        continue
                    bend_maps[other] = bend_map
                    developments.append(BendDevelopment(
                        face_index=other, parent_index=current, angle_rad=bend_map.theta,
                        inner_radius=bend_map.inner_radius, face_radius=bend_map.radius,
                        axis_length=_axis_extent(faces[other], bend_map.origin, bend_map.axis)))
                    transform[other] = transform[current]  # placeholder: cylinder uses bend_map
                    queue.append(other)
                    continue
                elif current_type == "CYLINDER" and other_type == "PLANE":
                    bend_map = bend_maps.get(current)
                    if bend_map is None:
                        continue
                    shared = self._shared_edges(faces, topology, current, other)
                    if not shared:
                        continue
                    candidate = bend_map.transform_for(shared)
                else:
                    continue
                if other in transform:
                    probe = _center(faces[other])
                    closure = max(closure, float(np.linalg.norm(
                        _apply(candidate, probe) - _apply(transform[other], probe))))
                    continue
                transform[other] = candidate
                queue.append(other)

        side = set(transform)
        checks = UnfoldChecks(side_faces=len(side))
        for pair in pairs:
            members = (pair.first in side) + (pair.second in side)
            checks.missing_pairs += members == 0
            checks.both_sides_pairs += members == 2
        checks.closure_mm = closure

        x_axis, y_axis = _plane_axes(n0)

        def to_2d(point3: np.ndarray) -> tuple[float, float]:
            delta = point3 - c0
            return float(delta @ x_axis), float(delta @ y_axis)

        flatness = 0.0

        def develop(face_index: int, points: np.ndarray) -> list[tuple[float, float]]:
            nonlocal flatness
            if face_index in bend_maps:
                moved = bend_maps[face_index].develop(points)
            else:
                moved = (transform[face_index][:3, :3] @ points.T).T + transform[face_index][:3, 3]
            if len(moved):
                flatness = max(flatness, float(np.max(np.abs((moved - c0) @ n0))))
            return [to_2d(point) for point in moved]

        # ---- boundary edges of the walked side
        boundary: list[_BoundaryEdge] = []
        for face_index in sorted(side):
            face = faces[face_index]
            for edge in _face_edges(face):
                key = hash(edge)
                owners = topology.edge_owners.get(key, [])
                others = [owner for owner in owners if owner != face_index]
                if not others or all(owner in side for owner in others):
                    continue  # internal (tangent line between side faces) or seam
                points, first_vertex, last_vertex = _sample_edge(edge, self.ARC_SEGMENT_RAD)
                if points is None:
                    continue
                developed = develop(face_index, points)
                if face_index in bend_maps:
                    length = _polyline_length(developed)
                    if _is_end_arc(edge, bend_maps[face_index].axis):
                        for development in developments:
                            if development.face_index == face_index:
                                development.end_arc_count += 1
                    else:
                        checks.cuts_on_bends += 1
                else:
                    length = float(edge.Length())
                boundary.append(_BoundaryEdge(
                    face_index=face_index, cut_faces={owner for owner in others if owner not in side},
                    points=developed, first=first_vertex, last=last_vertex, length=length))
        checks.flatness_mm = flatness

        loops, open_chains = _chain_loops(boundary)
        checks.open_chains = open_chains
        if not loops:
            raise UnfoldError("展開図の境界ループを構成できません")

        # ---- classify loops: outer = largest, others must be holes inside it
        polygons = [_ring_points(loop) for loop in loops]
        areas = [abs(_shoelace(ring)) for ring in polygons]
        order = sorted(range(len(loops)), key=lambda i: -areas[i])
        outer_index = order[0]
        inner_indices = order[1:]
        outer_ring = polygons[outer_index]
        valid = True
        try:
            from shapely.geometry import Polygon
            outer_polygon = Polygon(outer_ring)
            inner_polygons = [Polygon(polygons[i]) for i in inner_indices]
            valid = outer_polygon.is_valid and all(p.is_valid for p in inner_polygons)
            if valid:
                valid = Polygon(outer_ring, holes=[polygons[i] for i in inner_indices]).is_valid
            outer_count = 1 + sum(not outer_polygon.contains(p.representative_point()) for p in inner_polygons)
        except ImportError:  # pragma: no cover - shapely is a hard dependency
            outer_count = 1
        checks.outer_loops = outer_count
        checks.outline_valid = bool(valid)

        area = areas[outer_index] - sum(areas[i] for i in inner_indices)
        outer_length = sum(edge.length for edge in loops[outer_index])
        inner_length = sum(edge.length for i in inner_indices for edge in loops[i])
        cut_length = outer_length + inner_length

        # ---- independent checks from the 3D solid
        k_area = sum(d.angle_rad * d.axis_length * t * (k - 0.5) for d in developments)
        expected_area = volume / t + k_area
        checks.area_error = (area - expected_area) / max(expected_area, 1e-9)
        k_cut = sum(d.end_arc_count * d.angle_rad * t * (k - 0.5) for d in developments)
        expected_cut = cut_area / t + k_cut
        checks.cut_error = (cut_length - expected_cut) / max(expected_cut, 1e-9)
        bend_faces = set(bend_maps)
        checks.holes_on_bends = sum(
            any(edge.face_index in bend_faces for edge in loops[i]) for i in inner_indices)

        hole_faces = [set().union(*(edge.cut_faces for edge in loops[i])) for i in inner_indices]
        bend_lines = [bend_maps[d.face_index].bend_line(faces[d.face_index], to_2d) for d in developments]
        outer_loops = [outer_ring]
        inner_loops = [polygons[i] for i in inner_indices]
        xs = [p[0] for p in outer_ring]
        ys = [p[1] for p in outer_ring]
        flat = FlatPatternSummary(
            method="geometric_graph_unfold" if developments else "geometric_planar_boundary",
            area_mm2=round(area, 6), cut_length_mm=round(cut_length, 6),
            boundary_count=len(loops), outer_boundary_count=1, inner_boundary_count=len(inner_indices),
            surface_region_count=len(side), outer_length_mm=round(outer_length, 6),
            inner_length_mm=round(inner_length, 6),
            bounding_box_mm=(round(max(xs) - min(xs), 6), round(max(ys) - min(ys), 6)),
            outer_loops=outer_loops, inner_loops=inner_loops,
            bend_lines=[line for line in bend_lines if line],
        )
        return UnfoldResult(flat=flat, checks=checks, bends=developments, side=side, hole_faces=hole_faces)

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _shared_edges(faces, topology, first: int, second: int) -> list:
        keys = {key for key, owners in topology.nodes[first].shared_edges.items() if second in owners}
        return [edge for edge in _face_edges(faces[first]) if hash(edge) in keys]


# ====================================================================== bend development
@dataclass
class _BendMap:
    origin: np.ndarray        # point on the cylinder axis
    axis: np.ndarray          # A = X x Y (u increases counter-clockwise about A)
    x_dir: np.ndarray
    y_dir: np.ndarray
    radius: float             # radius of this face
    inner_radius: float
    thickness: float
    k_factor: float
    u_min: float
    u_max: float
    u_parent: float           # u of the parent tangent line
    sign: float               # +1 if the walk goes towards increasing u
    parent: np.ndarray        # 4x4 transform of the parent plane
    theta: float

    @classmethod
    def create(cls, face, shared_edges, parent_transform, inner_radius, thickness, k_factor):
        from OCP.BRepTools import BRepTools

        cylinder = face._geomAdaptor().Cylinder()
        position = cylinder.Position()
        origin = _gp(position.Location())
        x_dir, y_dir = _gp(position.XDirection()), _gp(position.YDirection())
        axis = np.cross(x_dir, y_dir)
        u_min, u_max, _, _ = BRepTools.UVBounds_s(face.wrapped)
        theta = u_max - u_min
        if theta <= 1e-9 or theta >= TWO_PI - 1e-6:
            return None
        radius = float(cylinder.Radius())
        probe = np.mean([_edge_midpoint(edge) for edge in shared_edges], axis=0)
        u_probe = _unwrap(_angle(probe - origin, x_dir, y_dir), u_min, u_max)
        if abs(u_probe - u_min) <= abs(u_probe - u_max):
            u_parent, sign = u_min, 1.0
        else:
            u_parent, sign = u_max, -1.0
        return cls(origin, axis, x_dir, y_dir, radius,
                   float(inner_radius if inner_radius is not None else radius), float(thickness),
                   float(k_factor), u_min, u_max, u_parent, sign, parent_transform, theta)

    @property
    def allowance(self) -> float:
        return self.theta * (self.inner_radius + self.k_factor * self.thickness)

    def _radial(self, u: float) -> np.ndarray:
        return math.cos(u) * self.x_dir + math.sin(u) * self.y_dir

    def _direction(self) -> np.ndarray:
        """Unit direction (folded frame) in which the developed bend extends from the parent line."""
        return self.sign * np.cross(self.axis, self._radial(self.u_parent))

    def develop(self, points: np.ndarray) -> np.ndarray:
        rotation, offset = self.parent[:3, :3], self.parent[:3, 3]
        radial_parent = self._radial(self.u_parent)
        direction = rotation @ self._direction()
        scale = self.inner_radius + self.k_factor * self.thickness
        result = []
        for point in points:
            delta = point - self.origin
            axial = float(delta @ self.axis)
            u = _unwrap(_angle(delta, self.x_dir, self.y_dir), self.u_min, self.u_max)
            phi = min(max(self.sign * (u - self.u_parent), 0.0), self.theta)
            on_parent_line = self.origin + axial * self.axis + self.radius * radial_parent
            result.append(rotation @ on_parent_line + offset + direction * phi * scale)
        return np.array(result).reshape(-1, 3)

    def transform_for(self, shared_edges) -> np.ndarray:
        """Transform of the plane on the far side of this bend (or of the parent side)."""
        probe = np.mean([_edge_midpoint(edge) for edge in shared_edges], axis=0)
        u_probe = _unwrap(_angle(probe - self.origin, self.x_dir, self.y_dir), self.u_min, self.u_max)
        if abs(u_probe - self.u_parent) <= abs(u_probe - (self.u_parent + self.sign * self.theta)):
            return self.parent
        rotate = _rotation(self.origin, self.axis, -self.sign * self.theta)
        shift = np.eye(4)
        shift[:3, 3] = (self.parent[:3, :3] @ self._direction()) * self.allowance
        return shift @ self.parent @ rotate

    def bend_line(self, face, to_2d) -> list[tuple[float, float]]:
        """Center line of the developed bend strip (for display)."""
        values = [float((np.array(v.toTuple()) - self.origin) @ self.axis) for v in face.Vertices()]
        if not values:
            return []
        radial = self._radial(self.u_parent)
        rotation, offset = self.parent[:3, :3], self.parent[:3, 3]
        shift = (rotation @ self._direction()) * self.allowance / 2
        line = []
        for axial in (min(values), max(values)):
            point = self.origin + axial * self.axis + self.radius * radial
            line.append(to_2d(rotation @ point + offset + shift))
        return line


# ====================================================================== geometry helpers
@dataclass
class _BoundaryEdge:
    face_index: int
    cut_faces: set[int]
    points: list[tuple[float, float]]
    first: int
    last: int
    length: float


def _chain_loops(edges: list[_BoundaryEdge]) -> tuple[list[list[_BoundaryEdge]], int]:
    """Chain boundary edges into closed loops through shared B-Rep vertices."""
    at_vertex: dict[int, list[int]] = defaultdict(list)
    for index, edge in enumerate(edges):
        at_vertex[edge.first].append(index)
        at_vertex[edge.last].append(index)
    used = [False] * len(edges)
    loops: list[list[_BoundaryEdge]] = []
    open_chains = 0
    for start in range(len(edges)):
        if used[start]:
            continue
        used[start] = True
        first = edges[start]
        loop = [first]
        begin, cursor = first.first, first.last
        if first.first == first.last:  # a single closed edge (full circle)
            loops.append(loop)
            continue
        closed = False
        while True:
            candidates = [i for i in at_vertex[cursor] if not used[i]]
            if not candidates:
                break
            nxt = candidates[0]
            if len(candidates) > 1:  # touching loops: keep the straightest continuation
                nxt = min(candidates, key=lambda i: _turn(loop[-1], edges[i], cursor))
            used[nxt] = True
            edge = edges[nxt]
            if edge.first != cursor:
                edge = _BoundaryEdge(edge.face_index, edge.cut_faces, edge.points[::-1],
                                     edge.last, edge.first, edge.length)
            loop.append(edge)
            cursor = edge.last
            if cursor == begin:
                closed = True
                break
        if closed:
            loops.append(loop)
        else:
            open_chains += len(loop)
    return loops, open_chains


def _turn(previous: _BoundaryEdge, candidate: _BoundaryEdge, vertex: int) -> float:
    a = np.subtract(previous.points[-1], previous.points[-2]) if len(previous.points) > 1 else np.zeros(2)
    points = candidate.points if candidate.first == vertex else candidate.points[::-1]
    b = np.subtract(points[1], points[0]) if len(points) > 1 else np.zeros(2)
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    if na < 1e-12 or nb < 1e-12:
        return math.pi
    return math.acos(max(-1.0, min(1.0, float(a @ b) / (na * nb))))


def _ring_points(loop: list[_BoundaryEdge], merge: float = 1e-7) -> list[tuple[float, float]]:
    """Closed ring of developed points; shared vertices are emitted once and the ring closes exactly."""
    ring: list[tuple[float, float]] = []
    for edge in loop:
        for point in edge.points:
            if not ring or math.dist(ring[-1], point) > merge:
                ring.append(point)
    while len(ring) > 1 and math.dist(ring[0], ring[-1]) <= merge:
        ring.pop()
    if ring:
        ring.append(ring[0])
    return ring


def _shoelace(ring: list[tuple[float, float]]) -> float:
    return 0.5 * sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(ring, ring[1:]))


def _polyline_length(points) -> float:
    return sum(math.dist(a, b) for a, b in zip(points, points[1:]))


def _face_edges(face) -> list:
    """Edges of a face, without seam duplicates or degenerated edges."""
    from OCP.BRep import BRep_Tool

    result, seen = [], set()
    for edge in face.Edges():
        key = hash(edge)
        if key in seen or BRep_Tool.Degenerated_s(edge.wrapped):
            continue
        seen.add(key)
        result.append(edge)
    return result


def _sample_edge(edge, arc_step: float):
    """Points along an edge (parameter order) with the hashes of its first/last vertices."""
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.TopExp import TopExp

    curve = BRepAdaptor_Curve(edge.wrapped)
    first_param, last_param = curve.FirstParameter(), curve.LastParameter()
    kind = edge.geomType()
    if kind == "LINE":
        count = 2
    elif kind == "CIRCLE":
        count = max(3, int(math.ceil(abs(last_param - first_param) / arc_step)) + 1)
    else:
        count = 64
    params = np.linspace(first_param, last_param, count)
    points = np.array([_gp(curve.Value(float(p))) for p in params])
    import cadquery as cq

    first_vertex = TopExp.FirstVertex_s(edge.wrapped)
    last_vertex = TopExp.LastVertex_s(edge.wrapped)
    if first_vertex.IsNull() or last_vertex.IsNull():
        return None, 0, 0
    first_key, last_key = hash(cq.Vertex(first_vertex)), hash(cq.Vertex(last_vertex))
    if first_key == last_key and count > 2 and np.linalg.norm(points[0] - points[-1]) > 1e-6:
        return None, 0, 0
    return points, first_key, last_key


def _is_end_arc(edge, axis: np.ndarray) -> bool:
    """True for the arc that closes a bend at its end (a circle about the bend axis)."""
    if edge.geomType() != "CIRCLE":
        return False
    from OCP.BRepAdaptor import BRepAdaptor_Curve

    circle_axis = _gp(BRepAdaptor_Curve(edge.wrapped).Circle().Axis().Direction())
    return abs(abs(float(circle_axis @ axis)) - 1.0) < 1e-6


def _edge_midpoint(edge) -> np.ndarray:
    from OCP.BRepAdaptor import BRepAdaptor_Curve

    curve = BRepAdaptor_Curve(edge.wrapped)
    return _gp(curve.Value((curve.FirstParameter() + curve.LastParameter()) / 2))


def _axis_extent(face, origin: np.ndarray, axis: np.ndarray) -> float:
    values = [float((np.array(v.toTuple()) - origin) @ axis) for v in face.Vertices()]
    return max(values) - min(values) if values else 0.0


def _gp(value) -> np.ndarray:
    return np.array([value.X(), value.Y(), value.Z()], dtype=float)


def _normal(face) -> np.ndarray:
    normal = np.array(face.normalAt().toTuple(), dtype=float)
    return normal / np.linalg.norm(normal)


def _center(face) -> np.ndarray:
    return np.array(face.Center().toTuple(), dtype=float)


def _plane_axes(normal: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    reference = np.array([1.0, 0.0, 0.0]) if abs(normal[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
    x_axis = np.cross(reference, normal)
    x_axis /= np.linalg.norm(x_axis)
    return x_axis, np.cross(normal, x_axis)


def _angle(delta: np.ndarray, x_dir: np.ndarray, y_dir: np.ndarray) -> float:
    return math.atan2(float(delta @ y_dir), float(delta @ x_dir))


def _unwrap(u: float, u_min: float, u_max: float) -> float:
    """Shift an angle by 2*pi*k so it lies in (or nearest to) [u_min, u_max]."""
    center = (u_min + u_max) / 2
    return u + TWO_PI * round((center - u) / TWO_PI)


def _rotation(point: np.ndarray, axis: np.ndarray, angle: float) -> np.ndarray:
    d = axis / np.linalg.norm(axis)
    k = np.array([[0, -d[2], d[1]], [d[2], 0, -d[0]], [-d[1], d[0], 0]])
    r = np.eye(3) + math.sin(angle) * k + (1 - math.cos(angle)) * (k @ k)
    m = np.eye(4)
    m[:3, :3] = r
    m[:3, 3] = point - r @ point
    return m


def _apply(matrix: np.ndarray, point: np.ndarray) -> np.ndarray:
    return matrix[:3, :3] @ point + matrix[:3, 3]
