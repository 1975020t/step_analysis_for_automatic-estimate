from __future__ import annotations

import math
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import BinaryIO

from src.models import (
    AnalysisStage, HoleEvidence, MetricQuality, SheetMetalAnalysis, SurfacePairEvidence,
)
from src.sheetmetal_geometry import (
    ToleranceContext, build_topology_graph, connected_components, polyline_length, validate_shape,
)
from src.sheetmetal_recognition import SheetMetalRecognition
from src.sheetmetal_unfold import SheetMetalUnfolder


class SheetMetalAnalyzer:
    """Coordinate safe, evidence-based analysis of one constant-thickness part."""

    AREA_CONSISTENCY_TOLERANCE = 0.08
    MAX_SHEET_SLENDERNESS = 0.20

    def __init__(self, k_factor: float = 0.33, k_factor_is_default: bool = True) -> None:
        if not 0 <= k_factor <= 1:
            raise ValueError("Kファクターは0以上1以下で指定してください")
        self.k_factor = float(k_factor)
        self.k_factor_is_default = k_factor_is_default

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
                        "拡張子が .step または .stp のファイルを指定してください。", "STEP読込",
                    )
                data = source.read()
                if hasattr(source, "seek"):
                    source.seek(0)
                if not data:
                    return self._failure(
                        resolved_name, "error", "STEP_READ_ERROR", "STEPファイルが空です。", "STEP読込",
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
                        "拡張子が .step または .stp のファイルを指定してください。", "STEP読込",
                    )
            return self._analyze_path(path, resolved_name)
        except Exception as exc:
            return self._failure(
                resolved_name or "unknown.step", "error", "ANALYSIS_ERROR",
                f"解析中に予期しないエラーが発生しました: {exc}", "解析",
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
                "CadQueryが未インストールのため解析できません。", "STEP読込",
            )
        stages: list[AnalysisStage] = []
        try:
            shape = cq.importers.importStep(str(path)).val()
            solids = list(shape.Solids())
        except Exception as exc:
            return self._failure(
                file_name, "error", "STEP_READ_ERROR",
                f"STEPファイルを3D形状として読み込めませんでした: {exc}", "STEP読込",
            )
        stages.append(AnalysisStage(name="STEP読込", status="success", message="3D形状を読み込みました。"))
        if not solids:
            return self._failure(
                file_name, "unsupported", "NO_SOLID", "対象となるSolid形状がSTEP内にありません。",
                "板金適合性確認", stages,
            )
        if len(solids) != 1:
            return self._failure(
                file_name, "unsupported", "MULTIPLE_SOLIDS",
                f"Solidが{len(solids)}個あります。対象は単一部品のみです。", "板金適合性確認", stages,
            )
        solid = solids[0]
        if not solid.isValid() or solid.Volume() <= 0:
            return self._failure(
                file_name, "unsupported", "NO_SOLID", "有効な体積を持つSolid形状を確認できません。",
                "板金適合性確認", stages,
            )
        valid, validation_reason = validate_shape(solid)
        if not valid:
            return self._failure(
                file_name, "unsupported", validation_reason or "BREP_INVALID",
                "B-Repが不正です。形状を自動変更せず解析を停止しました。", "B-Rep妥当性確認", stages,
            )

        tolerance = ToleranceContext.from_shape(solid)
        recognition = SheetMetalRecognition(tolerance.linear_mm)
        faces = list(solid.Faces())
        topology = build_topology_graph(faces)
        stages.extend([
            AnalysisStage(
                name="B-Rep妥当性確認", status="success",
                message=(f"有効な閉じたSolidです。モデル対角 {tolerance.diagonal_mm:.3f} mm、"
                         f"線形公差 {tolerance.linear_mm:.6g} mmを使用します。"),
            ),
            AnalysisStage(
                name="面隣接グラフ", status="success",
                message=f"Face {len(faces)}面を共有Edgeで接続したB-Rep隣接グラフを構築しました。",
            ),
        ])
        candidates = recognition.surface_thickness_candidates(faces)
        if not candidates:
            return self._failure(
                file_name, "unsupported", "NON_CONSTANT_THICKNESS",
                "一定板厚を示す対向平面を確認できません。", "板厚判定", stages,
            )
        thickness = recognition.select_thickness(candidates)
        pairs = recognition.match_surface_pairs(faces, thickness)
        if not pairs:
            return self._failure(
                file_name, "unsupported", "NON_CONSTANT_THICKNESS",
                "同じ板厚で対応する表裏面を確認できません。", "板厚判定", stages,
            )
        paired_area = sum((faces[p.first].Area() + faces[p.second].Area()) / 2 for p in pairs)
        volume_area = solid.Volume() / thickness
        area_error = abs(paired_area - volume_area) / max(volume_area, 1e-9)
        if thickness / max(math.sqrt(volume_area), 1e-9) > self.MAX_SHEET_SLENDERNESS:
            return self._failure(
                file_name, "unsupported", "NOT_SHEET_METAL",
                "板厚に対して面内寸法が小さく、板金形状として認識できません。",
                "板金適合性確認", stages,
            )
        if recognition.has_unresolved_bend(faces, pairs, thickness):
            return self._failure(
                file_name, "unsupported", "BEND_UNDETERMINED",
                "曲げ半径を示す対応円筒面がなく、曲げ箇所を十分な確度で判定できません。",
                "曲げ判定", stages,
            )
        if area_error > self.AREA_CONSISTENCY_TOLERANCE:
            return self._failure(
                file_name, "unsupported", "NON_CONSTANT_THICKNESS",
                "表裏面の中立面積と体積÷板厚が整合せず、一定板厚と判断できません。",
                "板厚判定", stages,
            )
        stages.append(AnalysisStage(
            name="板厚判定", status="success",
            message=f"複数根拠と体積整合性から板厚 {thickness:.3f} mm を確認しました。",
        ))

        paired_indices = {index for pair in pairs for index in (pair.first, pair.second)}
        for pair in pairs:
            role = "bend_surface" if pair.kind == "CYLINDER" else "skin"
            topology.nodes[pair.first].role = role
            topology.nodes[pair.second].role = role
        cut_indices = [index for index in range(len(faces)) if index not in paired_indices]
        if not cut_indices:
            return self._failure(
                file_name, "unsupported", "FLAT_PATTERN_FAILED",
                "展開形状の切断境界を特定できません。", "展開形状作成", stages,
            )
        components = connected_components(topology, cut_indices)
        if not components:
            return self._failure(
                file_name, "unsupported", "FLAT_PATTERN_FAILED",
                "展開形状の境界接続関係を求められません。", "展開形状作成", stages,
            )
        for index in cut_indices:
            topology.nodes[index].role = "cut_or_feature"
        cut_area = sum(faces[index].Area() for index in cut_indices)
        bends = recognition.bend_evidence(faces, pairs, thickness, topology, self.k_factor)
        flat = SheetMetalUnfolder(recognition, self.k_factor).build(
            faces, pairs, bends, thickness, cut_area, topology
        )
        hole_components = recognition.hole_components(faces, components, thickness, bends)
        hole_count = len(flat.inner_loops) if flat.inner_loops else len(hole_components)

        has_estimated_internal = False
        if flat.method == "geometric_prismatic_unfold":
            residual = max(0.0, cut_area / thickness - float(flat.outer_length_mm or flat.cut_length_mm))
            threshold = max(2.0, float(flat.outer_length_mm or 0.0) * 0.02)
            if residual > threshold:
                flat.inner_length_mm = round(residual, 6)
                flat.cut_length_mm = round(float(flat.outer_length_mm or flat.cut_length_mm) + residual, 6)
                flat.inner_boundary_count = len(hole_components)
                flat.boundary_count = 1 + len(hole_components)
                flat.method = "geometric_prismatic_unfold_with_estimated_internal_boundaries"
                has_estimated_internal = True

        is_geometric = flat.method.startswith("geometric")
        status = "success" if is_geometric and not has_estimated_internal else "partial"
        confidence = "medium" if (self.k_factor_is_default and bends) or not is_geometric or has_estimated_internal else "high"
        warnings = ["見積用の展開です。加工順序・金型情報は生成しません。"]
        assumptions: list[str] = []
        reason_codes: list[str] = []
        if self.k_factor_is_default and bends:
            assumptions.append("Kファクターはデモ既定値0.33を使用しました。")
            warnings.append("Kファクターが未指定のため、曲げ展開値は概算・加工条件要確認です。")
        if not is_geometric:
            reason_codes.append("FLAT_PATTERN_ESTIMATED")
            warnings.append("2D輪郭を一意に構築できず、面積・切断長は解析曲面による概算です。")
        if has_estimated_internal:
            reason_codes.append("INTERNAL_BOUNDARY_ESTIMATED")
            warnings.append("曲げ部品の穴・切欠き位置は表示せず、切断側面から追加境界長だけを概算しました。")
        stages.extend([
            AnalysisStage(
                name="曲げ判定", status="success",
                message=f"平面―円筒―平面の隣接関係から曲げを{len(bends)}箇所と判定しました。",
            ),
            AnalysisStage(
                name="展開形状作成", status="success",
                message="2D閉ループを構築しました。" if is_geometric else "解析曲面による概算へ切り替えました。",
            ),
            AnalysisStage(
                name="展開値取得", status="success",
                message="展開面積、外周・内周、穴数、曲げ数を項目別品質付きで取得しました。",
            ),
        ])

        thickness_evidence = [SurfacePairEvidence(
            kind="cylindrical" if pair.kind == "CYLINDER" else "planar",
            face_indices=(pair.first + 1, pair.second + 1),
            mid_surface_area_mm2=round((faces[pair.first].Area() + faces[pair.second].Area()) / 2, 6),
        ) for pair in pairs]
        holes = [HoleEvidence(
            hole_id=index + 1, face_indices=[face_index + 1 for face_index in sorted(component)],
            cut_surface_area_mm2=round(sum(faces[face_index].Area() for face_index in component), 6),
        ) for index, component in enumerate(hole_components)]
        while len(holes) < hole_count:
            loop = flat.inner_loops[len(holes)]
            holes.append(HoleEvidence(
                hole_id=len(holes) + 1, face_indices=[],
                cut_surface_area_mm2=round(polyline_length(loop) * thickness, 6),
            ))
        return SheetMetalAnalysis(
            status=status, file_name=file_name, thickness_mm=round(thickness, 6),
            blank_area_mm2=round(flat.area_mm2, 6), cut_length_mm=round(flat.cut_length_mm, 6),
            hole_count=hole_count, bend_count=len(bends),
            reason_code=reason_codes[0] if reason_codes else None, reason_codes=reason_codes,
            message="一定板厚の板金部品として幾何展開しました。" if is_geometric else "一部項目を概算しました。",
            flat_pattern=flat, stages=stages, thickness_evidence=thickness_evidence,
            bend_evidence=bends, hole_evidence=holes, warnings=warnings, assumptions=assumptions,
            metric_quality={
                "thickness_mm": MetricQuality(
                    method="multi_evidence_cluster", confidence="high",
                    evidence=["対向平面/同軸円筒", "短辺", "面積支持", "体積整合性"],
                ),
                "blank_area_mm2": MetricQuality(
                    method=flat.method, confidence=confidence,
                    evidence=[f"体積検算差 {area_error:.2%}"],
                ),
                "cut_length_mm": MetricQuality(
                    method="2d_boundary" if is_geometric and not has_estimated_internal else "cut_surface_area_over_thickness",
                    confidence=confidence,
                ),
                "hole_count": MetricQuality(
                    method="2d_loop_containment" if flat.inner_loops else "topology_feature_components",
                    confidence="high" if flat.inner_loops else "medium",
                ),
                "bend_count": MetricQuality(method="plane_cylinder_plane_adjacency", confidence="high"),
            },
            thickness_candidates=recognition.candidate_models(candidates, faces, thickness),
        )

    @staticmethod
    def _failure(
        file_name: str, status: str, reason_code: str, message: str, stage_name: str,
        previous_stages: list[AnalysisStage] | None = None,
    ) -> SheetMetalAnalysis:
        stages = list(previous_stages or [])
        stages.append(AnalysisStage(name=stage_name, status="failed", message=message))
        return SheetMetalAnalysis(
            status=status, file_name=file_name, reason_code=reason_code,
            reason_codes=[reason_code], message=message, stages=stages,
            metric_quality={
                key: MetricQuality(method="unavailable", confidence="unavailable")
                for key in ("thickness_mm", "blank_area_mm2", "cut_length_mm", "hole_count", "bend_count")
            },
        )
