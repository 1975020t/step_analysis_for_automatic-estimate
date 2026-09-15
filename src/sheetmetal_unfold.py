from __future__ import annotations

import math

from src.models import BendEvidence, FlatPatternSummary
from src.sheetmetal_geometry import (
    classify_loops, loops_bbox, plane_basis, planar_face_loops,
    polygon_area, polyline_length, rectangle_loop,
)
from src.sheetmetal_recognition import SheetMetalRecognition, SurfacePair


class SheetMetalUnfolder:
    def __init__(self, recognition: SheetMetalRecognition, k_factor: float) -> None:
        self.recognition = recognition
        self.k_factor = k_factor

    def build(self, faces, pairs, bends, thickness, cut_area, topology) -> FlatPatternSummary:
        planar = [pair for pair in pairs if pair.kind == "PLANE"]
        cylinders = [pair for pair in pairs if pair.kind == "CYLINDER"]
        neutral_area = sum((faces[p.first].Area() + faces[p.second].Area()) / 2 for p in planar)
        for pair in cylinders:
            r1 = self.recognition.cylinder(faces[pair.first])[2]
            r2 = self.recognition.cylinder(faces[pair.second])[2]
            inner_index = pair.first if r1 < r2 else pair.second
            radius = min(r1, r2)
            neutral_area += faces[inner_index].Area() * (radius + self.k_factor * thickness) / max(radius, 1e-12)

        if not bends and planar:
            reference = max((faces[i] for pair in planar for i in (pair.first, pair.second)), key=lambda f: f.Area())
            loops = planar_face_loops(reference)
            outers, inners = classify_loops(loops)
            if outers:
                area = sum(abs(polygon_area(loop)) for loop in outers) - sum(abs(polygon_area(loop)) for loop in inners)
                outer_length = sum(polyline_length(loop) for loop in outers)
                inner_length = sum(polyline_length(loop) for loop in inners)
                return FlatPatternSummary(
                    method="geometric_planar_boundary", area_mm2=round(area, 6),
                    cut_length_mm=round(outer_length + inner_length, 6), boundary_count=len(loops),
                    outer_boundary_count=len(outers), inner_boundary_count=len(inners),
                    surface_region_count=len(pairs), outer_length_mm=round(outer_length, 6),
                    inner_length_mm=round(inner_length, 6), bounding_box_mm=loops_bbox(loops),
                    outer_loops=outers, inner_loops=inners,
                )

        axes = [bend.axis_direction for bend in bends]
        parallel = bool(axes) and all(
            abs(abs(self.recognition.dot(axes[0], axis)) - 1) <= self.recognition.PARALLEL_TOLERANCE
            for axis in axes[1:]
        )
        if parallel:
            cylinder_pair = cylinders[0]
            point, direction, _ = self.recognition.cylinder(faces[cylinder_pair.first])
            projections = [
                self.recognition.dot((float(v.X) - point[0], float(v.Y) - point[1], float(v.Z) - point[2]), direction)
                for pair in cylinders for index in (pair.first, pair.second) for v in faces[index].Vertices()
            ]
            width = max(projections) - min(projections) if projections else 0.0
            if width > self.recognition.linear_tolerance:
                length = neutral_area / width
                perimeter = 2 * (length + width)
                return FlatPatternSummary(
                    method="geometric_prismatic_unfold", area_mm2=round(neutral_area, 6),
                    cut_length_mm=round(perimeter, 6), boundary_count=1, outer_boundary_count=1,
                    inner_boundary_count=0, surface_region_count=len(pairs),
                    outer_length_mm=round(perimeter, 6), inner_length_mm=0.0,
                    bounding_box_mm=(length, width), outer_loops=[rectangle_loop(length, width)],
                    bend_lines=self._bend_lines(width, planar, faces, bends),
                )

        branched = self._branched(faces, planar, cylinders, bends, topology)
        if branched:
            return branched
        return FlatPatternSummary(
            method="analytical_neutral_surface_estimate", area_mm2=round(neutral_area, 6),
            cut_length_mm=round(cut_area / thickness, 6), boundary_count=0,
            outer_boundary_count=0, inner_boundary_count=0, surface_region_count=len(pairs),
        )

    def _branched(self, faces, planar, cylinders, bends, topology):
        if len(bends) < 2 or not planar:
            return None
        try:
            from shapely.geometry import Polygon
            from shapely.ops import unary_union
        except ImportError:
            return None
        base_pair = max(planar, key=lambda p: (faces[p.first].Area() + faces[p.second].Area()) / 2)
        base_indices = {base_pair.first, base_pair.second}
        base_index = max(base_indices, key=lambda i: faces[i].Area())
        loops = planar_face_loops(faces[base_index])
        outers, inners = classify_loops(loops)
        if len(outers) != 1:
            return None
        base = Polygon(outers[0], holes=inners)
        if not base.is_valid:
            return None
        origin, x_axis, y_axis = plane_basis(faces[base_index])
        face_to_pair = {index: pair for pair in planar for index in (pair.first, pair.second)}
        polygons, bend_lines, used = [base], [], set()
        for bend in bends:
            bend_indices = {index - 1 for index in bend.face_indices}
            adjacent = {}
            for bend_index in bend_indices:
                for face_index in topology.nodes[bend_index].adjacent:
                    pair = face_to_pair.get(face_index)
                    if pair:
                        adjacent[(pair.first, pair.second)] = pair
            base_key = (base_pair.first, base_pair.second)
            flange_pairs = [pair for key, pair in adjacent.items() if key != base_key]
            if base_key not in adjacent or len(flange_pairs) != 1:
                continue
            flange = flange_pairs[0]
            flange_key = (flange.first, flange.second)
            if flange_key in used:
                continue
            used.add(flange_key)
            edge = self._shared_edge(faces, base_indices, bend_indices, topology)
            if edge is None or len(edge.Vertices()) < 2:
                continue
            endpoints = []
            for vertex in (edge.Vertices()[0], edge.Vertices()[-1]):
                delta = (float(vertex.X) - origin[0], float(vertex.Y) - origin[1], float(vertex.Z) - origin[2])
                endpoints.append((self.recognition.dot(delta, x_axis), self.recognition.dot(delta, y_axis)))
            first, second = endpoints
            edge_length = math.hypot(second[0] - first[0], second[1] - first[1])
            if edge_length <= self.recognition.linear_tolerance:
                continue
            centroid = (base.centroid.x, base.centroid.y)
            midpoint = ((first[0] + second[0]) / 2, (first[1] + second[1]) / 2)
            dx, dy = (second[0] - first[0]) / edge_length, (second[1] - first[1]) / edge_length
            normal = (-dy, dx)
            if (centroid[0] - midpoint[0]) * normal[0] + (centroid[1] - midpoint[1]) * normal[1] > 0:
                normal = (-normal[0], -normal[1])
            flange_area = (faces[flange.first].Area() + faces[flange.second].Area()) / 2
            depth = flange_area / edge_length + float(bend.bend_allowance_mm or 0.0)
            polygons.append(Polygon([
                first, second,
                (second[0] + normal[0] * depth, second[1] + normal[1] * depth),
                (first[0] + normal[0] * depth, first[1] + normal[1] * depth),
            ]))
            bend_lines.append([first, second])
        if len(used) < 2:
            return None
        tolerance = max(self.recognition.linear_tolerance * 10, 1e-6)
        unfolded = unary_union([polygon.buffer(tolerance) for polygon in polygons]).buffer(-tolerance)
        if unfolded.geom_type != "Polygon" or not unfolded.is_valid:
            return None
        outer = [[(float(x), float(y)) for x, y in unfolded.exterior.coords]]
        inner = [[(float(x), float(y)) for x, y in ring.coords] for ring in unfolded.interiors]
        outer_length = sum(polyline_length(loop) for loop in outer)
        inner_length = sum(polyline_length(loop) for loop in inner)
        return FlatPatternSummary(
            method="geometric_branched_tray_unfold", area_mm2=round(unfolded.area, 6),
            cut_length_mm=round(outer_length + inner_length, 6), boundary_count=1 + len(inner),
            outer_boundary_count=1, inner_boundary_count=len(inner),
            surface_region_count=len(planar) + len(cylinders), outer_length_mm=round(outer_length, 6),
            inner_length_mm=round(inner_length, 6),
            bounding_box_mm=(unfolded.bounds[2] - unfolded.bounds[0], unfolded.bounds[3] - unfolded.bounds[1]),
            outer_loops=outer, inner_loops=inner, bend_lines=bend_lines,
        )

    @staticmethod
    def _shared_edge(faces, base_indices, bend_indices, topology):
        bend_keys = {key for index in bend_indices for key in topology.nodes[index].shared_edges}
        for base_index in base_indices:
            for edge in faces[base_index].Edges():
                if hash(edge) in bend_keys:
                    return edge
        return None

    @staticmethod
    def _bend_lines(width, planar, faces, bends):
        lengths = sorted((faces[p.first].Area() + faces[p.second].Area()) / (2 * width) for p in planar)
        cursor, result = 0.0, []
        for index, bend in enumerate(bends):
            if index < len(lengths):
                cursor += lengths[index]
            result.append([(cursor, 0.0), (cursor, width)])
            cursor += bend.bend_allowance_mm or 0.0
        return result
