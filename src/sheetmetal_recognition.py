from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass

from src.models import BendEvidence, ThicknessCandidate


@dataclass(frozen=True)
class SurfacePair:
    first: int
    second: int
    kind: str
    distance: float
    score: float


class SheetMetalRecognition:
    PARALLEL_TOLERANCE = 1e-4
    THICKNESS_CLUSTER_REL_TOLERANCE = 0.02
    THICKNESS_MATCH_REL_TOLERANCE = 0.025

    def __init__(self, linear_tolerance: float = 1e-4) -> None:
        self.linear_tolerance = linear_tolerance

    def surface_thickness_candidates(self, faces: list) -> list[SurfacePair]:
        candidates: list[SurfacePair] = []
        for first in range(len(faces)):
            if faces[first].geomType() != "PLANE":
                continue
            normal_first = self.vector(faces[first].normalAt())
            for second in range(first + 1, len(faces)):
                if faces[second].geomType() != "PLANE":
                    continue
                normal_second = self.vector(faces[second].normalAt())
                if self.dot(normal_first, normal_second) > -1 + self.PARALLEL_TOLERANCE:
                    continue
                distance = float(faces[first].distance(faces[second]))
                if distance <= self.linear_tolerance or not self.locally_offset_planes(
                    faces[first], faces[second], normal_first, distance
                ):
                    continue
                candidates.append(SurfacePair(
                    first, second, "PLANE", distance,
                    min(faces[first].Area(), faces[second].Area()),
                ))
        for first in range(len(faces)):
            if faces[first].geomType() != "CYLINDER":
                continue
            for second in range(first + 1, len(faces)):
                if faces[second].geomType() != "CYLINDER":
                    continue
                try:
                    first_cylinder = self.cylinder(faces[first])
                    second_cylinder = self.cylinder(faces[second])
                except Exception:
                    continue
                if not self.same_axis(first_cylinder, second_cylinder):
                    continue
                if not self.cylinders_overlap_along_axis(faces[first], faces[second], first_cylinder):
                    continue
                distance = abs(first_cylinder[2] - second_cylinder[2])
                if distance > self.linear_tolerance:
                    candidates.append(SurfacePair(
                        first, second, "CYLINDER", distance,
                        min(faces[first].Area(), faces[second].Area()),
                    ))
        return candidates

    def select_thickness(self, candidates: list[SurfacePair]) -> float:
        groups: list[list[SurfacePair]] = []
        for candidate in sorted(candidates, key=lambda item: item.distance):
            for group in groups:
                center = sum(item.distance * item.score for item in group) / sum(item.score for item in group)
                if abs(candidate.distance - center) <= max(
                    1e-4, center * self.THICKNESS_CLUSTER_REL_TOLERANCE
                ):
                    group.append(candidate)
                    break
            else:
                groups.append([candidate])
        best = max(groups, key=lambda group: sum(item.score for item in group))
        return sum(item.distance * item.score for item in best) / sum(item.score for item in best)

    def match_surface_pairs(self, faces: list, thickness: float) -> list[SurfacePair]:
        possible: list[SurfacePair] = []
        for first in range(len(faces)):
            for second in range(first + 1, len(faces)):
                kind = faces[first].geomType()
                if kind != faces[second].geomType() or kind not in {"PLANE", "CYLINDER"}:
                    continue
                if kind == "PLANE":
                    normal = self.vector(faces[first].normalAt())
                    if self.dot(normal, self.vector(faces[second].normalAt())) > -1 + self.PARALLEL_TOLERANCE:
                        continue
                    distance = float(faces[first].distance(faces[second]))
                    if not self.locally_offset_planes(faces[first], faces[second], normal, distance):
                        continue
                else:
                    try:
                        first_cylinder = self.cylinder(faces[first])
                        second_cylinder = self.cylinder(faces[second])
                    except (AttributeError, RuntimeError, ValueError):
                        continue
                    if not self.same_axis(first_cylinder, second_cylinder):
                        continue
                    if not self.cylinders_overlap_along_axis(faces[first], faces[second], first_cylinder):
                        continue
                    distance = abs(first_cylinder[2] - second_cylinder[2])
                if self.near(distance, thickness, self.THICKNESS_MATCH_REL_TOLERANCE):
                    possible.append(SurfacePair(
                        first, second, kind, distance,
                        min(faces[first].Area(), faces[second].Area()),
                    ))
        used: set[int] = set()
        pairs: list[SurfacePair] = []
        for pair in sorted(possible, key=lambda item: item.score, reverse=True):
            if pair.first in used or pair.second in used:
                continue
            used.update((pair.first, pair.second))
            pairs.append(pair)
        return sorted(pairs, key=lambda item: (item.first, item.second))

    def has_unresolved_bend(self, faces: list, pairs: list[SurfacePair], thickness: float) -> bool:
        paired_indices = {index for pair in pairs for index in (pair.first, pair.second)}
        edge_owners: dict[int, list[int]] = defaultdict(list)
        for index in paired_indices:
            for edge in faces[index].Edges():
                edge_owners[hash(edge)].append(index)
        for owners in edge_owners.values():
            for first in owners:
                for second in owners:
                    if first >= second:
                        continue
                    if faces[first].geomType() == faces[second].geomType() == "PLANE":
                        if abs(self.dot(
                            self.vector(faces[first].normalAt()), self.vector(faces[second].normalAt())
                        )) < 1 - self.PARALLEL_TOLERANCE:
                            return True
        for index, face in enumerate(faces):
            if index in paired_indices or face.geomType() != "CYLINDER":
                continue
            try:
                point, direction, _ = self.cylinder(face)
            except (AttributeError, RuntimeError, ValueError):
                return True
            projections = [
                self.dot((float(v.X) - point[0], float(v.Y) - point[1], float(v.Z) - point[2]), direction)
                for v in face.Vertices()
            ]
            if projections and max(projections) - min(projections) > thickness * 2.5:
                return True
        return False

    def bend_evidence(
        self, faces: list, pairs: list[SurfacePair], thickness: float, topology,
        k_factor: float,
    ) -> list[BendEvidence]:
        groups: dict[tuple[float, ...], list[SurfacePair]] = defaultdict(list)
        metadata: dict[tuple[float, ...], tuple] = {}
        for pair in pairs:
            if pair.kind != "CYLINDER":
                continue
            adjacent_skin_planes = {
                adjacent for index in (pair.first, pair.second)
                for adjacent in topology.nodes[index].adjacent
                if topology.nodes[adjacent].geom_type == "PLANE" and topology.nodes[adjacent].role == "skin"
            }
            if len(adjacent_skin_planes) < 2:
                continue
            first = self.cylinder(faces[pair.first])
            second = self.cylinder(faces[pair.second])
            point, direction = self.canonical_axis(first[0], first[1])
            radii = sorted((first[2], second[2]))
            key = tuple(round(value, 4) for value in (*point, *direction, *radii))
            groups[key].append(pair)
            metadata[key] = (point, direction, radii)
        result: list[BendEvidence] = []
        for bend_id, key in enumerate(sorted(groups), start=1):
            point, direction, radii = metadata[key]
            group = groups[key]
            inner_face = min(
                (faces[index] for pair in group for index in (pair.first, pair.second)),
                key=lambda face: self.cylinder(face)[2],
            )
            projections = [
                self.dot((float(v.X) - point[0], float(v.Y) - point[1], float(v.Z) - point[2]), direction)
                for v in inner_face.Vertices()
            ]
            axis_length = max(projections) - min(projections) if projections else 0.0
            angle_rad = float(inner_face.Area()) / max(radii[0] * axis_length, 1e-12) if axis_length > self.linear_tolerance else 0.0
            result.append(BendEvidence(
                bend_id=bend_id,
                axis_point_mm=tuple(round(value, 6) for value in point),
                axis_direction=tuple(round(value, 6) for value in direction),
                inner_radius_mm=round(radii[0], 6), outer_radius_mm=round(radii[1], 6),
                angle_deg=round(math.degrees(angle_rad), 6),
                bend_allowance_mm=round(angle_rad * (radii[0] + k_factor * thickness), 6),
                face_indices=sorted({index + 1 for pair in group for index in (pair.first, pair.second)}),
            ))
        return result

    def hole_components(self, faces, components, thickness, bends) -> list[set[int]]:
        if bends:
            direction = bends[0].axis_direction
            bend_indices = {index - 1 for bend in bends for index in bend.face_indices}
            feature_cylinders = {
                index for component in components for index in component
                if index not in bend_indices and faces[index].geomType() == "CYLINDER"
                and self.cylinder(faces[index])[2] > thickness
            }
            all_values = [self.dot((float(v.X), float(v.Y), float(v.Z)), direction) for face in faces for v in face.Vertices()]
            model_min, model_max = min(all_values), max(all_values)
            result = [{index} for index in sorted(feature_cylinders)]
            for component in components:
                values = [self.dot((float(v.X), float(v.Y), float(v.Z)), direction) for index in component for v in faces[index].Vertices()]
                spans = values and min(values) <= model_min + self.linear_tolerance * 10 and max(values) >= model_max - self.linear_tolerance * 10
                residual = component - feature_cylinders
                if not spans and residual:
                    result.append(residual)
            return result
        result = []
        for component in components:
            if {faces[index].geomType() for index in component} == {"CYLINDER"}:
                radii = [self.cylinder(faces[index])[2] for index in component]
                if radii and max(radii) > thickness:
                    result.append(component)
        return result

    def candidate_models(self, candidates, faces, thickness) -> list[ThicknessCandidate]:
        methods = {"PLANE": "opposing_planes", "CYLINDER": "coaxial_cylinders"}
        result = [ThicknessCandidate(
            method=methods.get(item.kind, item.kind.lower()), value_mm=round(item.distance, 6),
            support=round(item.score, 6),
        ) for item in sorted(candidates, key=lambda item: item.score, reverse=True)[:50]]
        lengths, seen = [], set()
        for face in faces:
            for edge in face.Edges():
                key = hash(edge)
                if key in seen or edge.geomType() != "LINE":
                    continue
                seen.add(key)
                if self.near(float(edge.Length()), thickness, self.THICKNESS_MATCH_REL_TOLERANCE):
                    lengths.append(float(edge.Length()))
        if lengths:
            result.append(ThicknessCandidate(
                method="short_linear_edges", value_mm=round(sum(lengths) / len(lengths), 6),
                support=float(len(lengths)),
            ))
        return result

    def cylinder(self, face):
        cylinder = face._geomAdaptor().Cylinder()
        axis = cylinder.Axis()
        location, direction = axis.Location(), axis.Direction()
        return (
            (float(location.X()), float(location.Y()), float(location.Z())),
            self.unit((float(direction.X()), float(direction.Y()), float(direction.Z()))),
            float(cylinder.Radius()),
        )

    def same_axis(self, first, second) -> bool:
        p1, d1, _ = first
        p2, d2, _ = second
        if abs(abs(self.dot(d1, d2)) - 1) > self.PARALLEL_TOLERANCE:
            return False
        cross = self.cross(tuple(p2[i] - p1[i] for i in range(3)), d1)
        return math.sqrt(self.dot(cross, cross)) <= max(self.linear_tolerance * 10, 1e-5)

    def locally_offset_planes(self, first, second, normal, distance) -> bool:
        ratio = min(first.Area(), second.Area()) / max(first.Area(), second.Area(), 1e-12)
        if ratio < 0.5:
            return False
        a, b = first.Center(), second.Center()
        delta = (float(b.x - a.x), float(b.y - a.y), float(b.z - a.z))
        normal_distance = abs(self.dot(delta, normal))
        tangential = math.sqrt(max(0.0, self.dot(delta, delta) - normal_distance**2))
        return abs(normal_distance - distance) <= max(self.linear_tolerance * 10, distance * 0.02) and tangential <= max(self.linear_tolerance * 10, math.sqrt(min(first.Area(), second.Area())) * 0.25)

    def cylinders_overlap_along_axis(self, first_face, second_face, cylinder) -> bool:
        point, direction, _ = cylinder
        def interval(face):
            values = [self.dot((float(v.X) - point[0], float(v.Y) - point[1], float(v.Z) - point[2]), direction) for v in face.Vertices()]
            return min(values), max(values)
        a1, a2 = interval(first_face)
        b1, b2 = interval(second_face)
        overlap, shorter = min(a2, b2) - max(a1, b1), min(a2 - a1, b2 - b1)
        return overlap >= max(self.linear_tolerance, shorter * 0.5)

    def canonical_axis(self, point, direction):
        direction = self.unit(direction)
        for value in direction:
            if abs(value) > 1e-9:
                if value < 0:
                    direction = tuple(-part for part in direction)
                break
        projection = self.dot(point, direction)
        return tuple(point[i] - projection * direction[i] for i in range(3)), direction

    @staticmethod
    def vector(value):
        return SheetMetalRecognition.unit((float(value.x), float(value.y), float(value.z)))

    @staticmethod
    def unit(value):
        length = math.sqrt(sum(part * part for part in value))
        return tuple(part / length for part in value)

    @staticmethod
    def dot(first, second):
        return sum(a * b for a, b in zip(first, second))

    @staticmethod
    def cross(first, second):
        return (first[1] * second[2] - first[2] * second[1], first[2] * second[0] - first[0] * second[2], first[0] * second[1] - first[1] * second[0])

    @staticmethod
    def near(value, expected, relative_tolerance):
        return abs(value - expected) <= max(1e-4, expected * relative_tolerance)
