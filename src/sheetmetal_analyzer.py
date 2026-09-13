from __future__ import annotations

import math
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import BinaryIO

from src.models import (
    AnalysisStage,
    BendEvidence,
    FlatPatternSummary,
    HoleEvidence,
    SheetMetalAnalysis,
    SurfacePairEvidence,
)


@dataclass(frozen=True)
class _Pair:
    first: int
    second: int
    kind: str
    distance: float
    score: float


class SheetMetalAnalyzer:
    """単一・一定板厚の板金だけを安全側で判定するSTEP解析器。"""

    PARALLEL_TOLERANCE = 1e-4
    THICKNESS_CLUSTER_REL_TOLERANCE = 0.02
    THICKNESS_MATCH_REL_TOLERANCE = 0.025
    AREA_CONSISTENCY_TOLERANCE = 0.08
    MAX_SHEET_SLENDERNESS = 0.20

    def analyze(
        self, source: str | Path | BinaryIO, file_name: str | None = None
    ) -> SheetMetalAnalysis:
        temporary_path: Path | None = None
        resolved_name = file_name or getattr(source, "name", None)
        try:
            if hasattr(source, "read"):
                resolved_name = resolved_name or "uploaded.step"
                if Path(resolved_name).suffix.lower() not in {".step", ".stp"}:
                    return self._failure(
                        resolved_name, "error", "INVALID_FILE_TYPE",
                        "拡張子が .step または .stp のファイルを指定してください。", "STEP読込"
                    )
                data = source.read()
                if hasattr(source, "seek"):
                    source.seek(0)
                if not data:
                    return self._failure(
                        resolved_name, "error", "STEP_READ_ERROR",
                        "STEPファイルが空です。", "STEP読込"
                    )
                with NamedTemporaryFile(suffix=Path(resolved_name).suffix, delete=False) as handle:
                    handle.write(data)
                    temporary_path = Path(handle.name)
                path = temporary_path
            else:
                path = Path(source)
                resolved_name = resolved_name or path.name
                if path.suffix.lower() not in {".step", ".stp"}:
                    return self._failure(
                        resolved_name, "error", "INVALID_FILE_TYPE",
                        "拡張子が .step または .stp のファイルを指定してください。", "STEP読込"
                    )
            return self._analyze_path(path, resolved_name)
        except Exception as exc:
            return self._failure(
                resolved_name or "unknown.step", "error", "ANALYSIS_ERROR",
                f"解析中に予期しないエラーが発生しました: {exc}", "解析"
            )
        finally:
            if temporary_path:
                temporary_path.unlink(missing_ok=True)

    def _analyze_path(self, path: Path, file_name: str) -> SheetMetalAnalysis:
        try:
            import cadquery as cq
        except ImportError:
            return self._failure(
                file_name, "error", "ANALYZER_UNAVAILABLE",
                "CadQueryが未インストールのため解析できません。", "STEP読込"
            )

        stages: list[AnalysisStage] = []
        try:
            shape = cq.importers.importStep(str(path)).val()
            solids = list(shape.Solids())
        except Exception as exc:
            return self._failure(
                file_name, "error", "STEP_READ_ERROR",
                f"STEPファイルを3D形状として読み込めませんでした: {exc}", "STEP読込"
            )

        stages.append(AnalysisStage(name="STEP読込", status="success", message="3D形状を読み込みました。"))
        if not solids:
            return self._failure(
                file_name, "unsupported", "NO_SOLID",
                "対象となるSolid形状がSTEP内にありません。", "板金適合性確認", stages
            )
        if len(solids) != 1:
            return self._failure(
                file_name, "unsupported", "MULTIPLE_SOLIDS",
                f"Solidが{len(solids)}個あります。第一版は単一部品のみ対象です。",
                "板金適合性確認", stages
            )

        solid = solids[0]
        if not solid.isValid() or solid.Volume() <= 0:
            return self._failure(
                file_name, "unsupported", "NO_SOLID",
                "有効な体積を持つSolid形状を確認できません。", "板金適合性確認", stages
            )
        faces = list(solid.Faces())
        candidates = self._planar_thickness_candidates(faces)
        if not candidates:
            return self._failure(
                file_name, "unsupported", "NON_CONSTANT_THICKNESS",
                "一定板厚を示す対向平面を確認できません。", "板厚判定", stages
            )

        thickness = self._select_thickness(candidates)
        pairs = self._match_surface_pairs(faces, thickness)
        if not pairs:
            return self._failure(
                file_name, "unsupported", "NON_CONSTANT_THICKNESS",
                "同じ板厚で対応する表裏面を確認できません。", "板厚判定", stages
            )
        paired_area = sum((faces[p.first].Area() + faces[p.second].Area()) / 2 for p in pairs)
        volume_area = solid.Volume() / thickness
        area_error = abs(paired_area - volume_area) / max(volume_area, 1e-9)
        slenderness = thickness / max(math.sqrt(volume_area), 1e-9)
        if slenderness > self.MAX_SHEET_SLENDERNESS:
            return self._failure(
                file_name, "unsupported", "NOT_SHEET_METAL",
                "板厚に対して面内寸法が小さく、板金形状として認識できません。",
                "板金適合性確認", stages
            )
        if self._has_unresolved_bend(faces, pairs, thickness):
            return self._failure(
                file_name, "unsupported", "BEND_UNDETERMINED",
                "曲げ半径を示す対応円筒面がなく、曲げ箇所を十分な確度で判定できません。",
                "曲げ判定", stages
            )
        if area_error > self.AREA_CONSISTENCY_TOLERANCE:
            return self._failure(
                file_name, "unsupported", "NON_CONSTANT_THICKNESS",
                "表裏面から得た中立面積と体積÷板厚が整合せず、一定板厚と判断できません。",
                "板厚判定", stages
            )
        stages.append(AnalysisStage(
            name="板厚判定", status="success",
            message=f"対向する表裏面と体積整合性から板厚 {thickness:.3f} mm を確認しました。"
        ))
        paired_indices = {index for pair in pairs for index in (pair.first, pair.second)}
        cut_indices = [index for index in range(len(faces)) if index not in paired_indices]
        if not cut_indices:
            return self._failure(
                file_name, "unsupported", "FLAT_PATTERN_FAILED",
                "展開形状の切断境界を特定できません。", "展開形状作成", stages
            )

        components = self._connected_face_components(faces, cut_indices)
        if not components:
            return self._failure(
                file_name, "unsupported", "FLAT_PATTERN_FAILED",
                "展開形状の境界接続関係を求められません。", "展開形状作成", stages
            )

        component_areas = [sum(faces[index].Area() for index in component) for component in components]
        outer_index = max(range(len(components)), key=lambda index: component_areas[index])
        hole_components = [component for index, component in enumerate(components) if index != outer_index]
        cut_area = sum(faces[index].Area() for index in cut_indices)
        cut_length = cut_area / thickness
        bends = self._bend_evidence(faces, pairs)

        stages.extend([
            AnalysisStage(
                name="曲げ判定", status="success",
                message=f"対になる円筒中立面を軸位置で統合し、独立した曲げを{len(bends)}箇所と判定しました。"
            ),
            AnalysisStage(
                name="展開形状作成", status="success",
                message="表裏面の中間にある中立面を平面へ展開した計量表現を作成しました。"
            ),
            AnalysisStage(
                name="展開値取得", status="success",
                message="中立面積、切断境界長、閉じた内周境界数を取得しました。"
            ),
        ])

        thickness_evidence = [SurfacePairEvidence(
            kind="cylindrical" if pair.kind == "CYLINDER" else "planar",
            face_indices=(pair.first + 1, pair.second + 1),
            mid_surface_area_mm2=round((faces[pair.first].Area() + faces[pair.second].Area()) / 2, 6),
        ) for pair in pairs]
        holes = [HoleEvidence(
            hole_id=index + 1,
            face_indices=[face_index + 1 for face_index in sorted(component)],
            cut_surface_area_mm2=round(sum(faces[face_index].Area() for face_index in component), 6),
        ) for index, component in enumerate(hole_components)]
        blank_area = volume_area
        return SheetMetalAnalysis(
            status="success", file_name=file_name,
            thickness_mm=round(thickness, 6),
            blank_area_mm2=round(blank_area, 6),
            cut_length_mm=round(cut_length, 6),
            hole_count=len(hole_components), bend_count=len(bends),
            message="単一・一定板厚の板金部品として解析しました。",
            flat_pattern=FlatPatternSummary(
                method="constant-thickness neutral-surface analytical development",
                area_mm2=round(blank_area, 6), cut_length_mm=round(cut_length, 6),
                boundary_count=len(components), outer_boundary_count=1,
                inner_boundary_count=len(hole_components), surface_region_count=len(pairs),
            ),
            stages=stages, thickness_evidence=thickness_evidence,
            bend_evidence=bends, hole_evidence=holes,
            warnings=["見積用の幾何解析です。製造用の展開図・加工順序・金型情報は生成しません。"],
        )

    def _planar_thickness_candidates(self, faces: list) -> list[_Pair]:
        candidates: list[_Pair] = []
        for first in range(len(faces)):
            if faces[first].geomType() != "PLANE":
                continue
            normal_first = self._vector(faces[first].normalAt())
            for second in range(first + 1, len(faces)):
                if faces[second].geomType() != "PLANE":
                    continue
                normal_second = self._vector(faces[second].normalAt())
                if self._dot(normal_first, normal_second) > -1 + self.PARALLEL_TOLERANCE:
                    continue
                distance = float(faces[first].distance(faces[second]))
                if distance <= 1e-5:
                    continue
                candidates.append(_Pair(
                    first, second, "PLANE", distance,
                    min(faces[first].Area(), faces[second].Area())
                ))
        return candidates

    def _select_thickness(self, candidates: list[_Pair]) -> float:
        groups: list[list[_Pair]] = []
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

    def _match_surface_pairs(self, faces: list, thickness: float) -> list[_Pair]:
        possible: list[_Pair] = []
        for first in range(len(faces)):
            for second in range(first + 1, len(faces)):
                kind = faces[first].geomType()
                if kind != faces[second].geomType() or kind not in {"PLANE", "CYLINDER"}:
                    continue
                if kind == "PLANE":
                    if self._dot(
                        self._vector(faces[first].normalAt()), self._vector(faces[second].normalAt())
                    ) > -1 + self.PARALLEL_TOLERANCE:
                        continue
                    distance = float(faces[first].distance(faces[second]))
                else:
                    try:
                        cylinder_first = self._cylinder(faces[first])
                        cylinder_second = self._cylinder(faces[second])
                    except (AttributeError, RuntimeError, ValueError):
                        continue
                    if not self._same_axis(cylinder_first, cylinder_second):
                        continue
                    distance = abs(cylinder_first[2] - cylinder_second[2])
                if not self._near(distance, thickness, self.THICKNESS_MATCH_REL_TOLERANCE):
                    continue
                possible.append(_Pair(
                    first, second, kind, distance,
                    min(faces[first].Area(), faces[second].Area())
                ))

        used: set[int] = set()
        pairs: list[_Pair] = []
        for pair in sorted(possible, key=lambda item: item.score, reverse=True):
            if pair.first in used or pair.second in used:
                continue
            used.update((pair.first, pair.second))
            pairs.append(pair)
        return sorted(pairs, key=lambda item: (item.first, item.second))

    def _connected_face_components(self, faces: list, indices: list[int]) -> list[set[int]]:
        edge_owners: dict[int, list[int]] = defaultdict(list)
        for index in indices:
            for edge in faces[index].Edges():
                edge_owners[hash(edge)].append(index)
        adjacency: dict[int, set[int]] = {index: set() for index in indices}
        for owners in edge_owners.values():
            for owner in owners:
                adjacency[owner].update(other for other in owners if other != owner)

        components: list[set[int]] = []
        unseen = set(indices)
        while unseen:
            start = unseen.pop()
            component = {start}
            queue = deque([start])
            while queue:
                current = queue.popleft()
                for neighbor in adjacency[current] & unseen:
                    unseen.remove(neighbor)
                    component.add(neighbor)
                    queue.append(neighbor)
            components.append(component)
        return components

    def _has_unresolved_bend(self, faces: list, pairs: list[_Pair], thickness: float) -> bool:
        paired_indices = {index for pair in pairs for index in (pair.first, pair.second)}
        edge_owners: dict[int, list[int]] = defaultdict(list)
        for index in paired_indices:
            for edge in faces[index].Edges():
                edge_owners[hash(edge)].append(index)

        # A sharp meeting between non-parallel skin planes has no measurable bend
        # radius, so the first version refuses to invent a bend count.
        for owners in edge_owners.values():
            for first in owners:
                for second in owners:
                    if first >= second:
                        continue
                    if faces[first].geomType() == faces[second].geomType() == "PLANE":
                        dot = abs(self._dot(
                            self._vector(faces[first].normalAt()),
                            self._vector(faces[second].normalAt()),
                        ))
                        if dot < 1 - self.PARALLEL_TOLERANCE:
                            return True

        # A cylindrical surface extending far along its axis is bend-like. If it
        # has no concentric mate at one thickness, bend identification is unsafe.
        for index, face in enumerate(faces):
            if index in paired_indices or face.geomType() != "CYLINDER":
                continue
            try:
                point, direction, _ = self._cylinder(face)
            except (AttributeError, RuntimeError, ValueError):
                return True
            projections = [
                self._dot(
                    tuple(float(value) - point[axis] for axis, value in enumerate((v.X, v.Y, v.Z))),
                    direction,
                )
                for v in face.Vertices()
            ]
            if projections and max(projections) - min(projections) > thickness * 2.5:
                return True
        return False

    def _bend_evidence(self, faces: list, pairs: list[_Pair]) -> list[BendEvidence]:
        groups: dict[tuple[float, ...], list[_Pair]] = defaultdict(list)
        metadata: dict[tuple[float, ...], tuple] = {}
        for pair in pairs:
            if pair.kind != "CYLINDER":
                continue
            first = self._cylinder(faces[pair.first])
            second = self._cylinder(faces[pair.second])
            point, direction = self._canonical_axis(first[0], first[1])
            radii = sorted((first[2], second[2]))
            key = tuple(round(value, 4) for value in (*point, *direction, *radii))
            groups[key].append(pair)
            metadata[key] = (point, direction, radii)

        result: list[BendEvidence] = []
        for bend_id, key in enumerate(sorted(groups), start=1):
            point, direction, radii = metadata[key]
            result.append(BendEvidence(
                bend_id=bend_id,
                axis_point_mm=tuple(round(value, 6) for value in point),
                axis_direction=tuple(round(value, 6) for value in direction),
                inner_radius_mm=round(radii[0], 6), outer_radius_mm=round(radii[1], 6),
                face_indices=sorted({
                    index + 1 for pair in groups[key] for index in (pair.first, pair.second)
                }),
            ))
        return result

    def _cylinder(self, face) -> tuple[tuple[float, float, float], tuple[float, float, float], float]:
        cylinder = face._geomAdaptor().Cylinder()
        axis = cylinder.Axis()
        location = axis.Location()
        direction = axis.Direction()
        return (
            (float(location.X()), float(location.Y()), float(location.Z())),
            self._unit((float(direction.X()), float(direction.Y()), float(direction.Z()))),
            float(cylinder.Radius()),
        )

    def _same_axis(self, first: tuple, second: tuple) -> bool:
        p1, d1, _ = first
        p2, d2, _ = second
        if abs(abs(self._dot(d1, d2)) - 1) > self.PARALLEL_TOLERANCE:
            return False
        delta = tuple(p2[index] - p1[index] for index in range(3))
        cross = self._cross(delta, d1)
        return math.sqrt(self._dot(cross, cross)) <= 1e-3

    def _canonical_axis(self, point: tuple, direction: tuple) -> tuple[tuple, tuple]:
        direction = self._unit(direction)
        for value in direction:
            if abs(value) > 1e-9:
                if value < 0:
                    direction = tuple(-part for part in direction)
                break
        projection = self._dot(point, direction)
        closest = tuple(point[index] - projection * direction[index] for index in range(3))
        return closest, direction

    @staticmethod
    def _vector(value) -> tuple[float, float, float]:
        return SheetMetalAnalyzer._unit((float(value.x), float(value.y), float(value.z)))

    @staticmethod
    def _unit(value: tuple[float, float, float]) -> tuple[float, float, float]:
        length = math.sqrt(sum(part * part for part in value))
        return tuple(part / length for part in value)

    @staticmethod
    def _dot(first: tuple, second: tuple) -> float:
        return sum(a * b for a, b in zip(first, second))

    @staticmethod
    def _cross(first: tuple, second: tuple) -> tuple[float, float, float]:
        return (
            first[1] * second[2] - first[2] * second[1],
            first[2] * second[0] - first[0] * second[2],
            first[0] * second[1] - first[1] * second[0],
        )

    @staticmethod
    def _near(value: float, expected: float, relative_tolerance: float) -> bool:
        return abs(value - expected) <= max(1e-4, expected * relative_tolerance)

    @staticmethod
    def _failure(
        file_name: str, status: str, reason_code: str, message: str, stage_name: str,
        previous_stages: list[AnalysisStage] | None = None,
    ) -> SheetMetalAnalysis:
        stages = list(previous_stages or [])
        stages.append(AnalysisStage(name=stage_name, status="failed", message=message))
        return SheetMetalAnalysis(
            status=status, file_name=file_name, reason_code=reason_code,
            message=message, stages=stages,
        )
