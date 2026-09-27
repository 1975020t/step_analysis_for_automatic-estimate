from __future__ import annotations

import math
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import BinaryIO

from src.models import (
    AnalysisStage, FlatPatternSummary, HoleEvidence, MetricQuality, SheetMetalAnalysis, SurfacePairEvidence,
)
from src.sheetmetal_geometry import (
    ToleranceContext, build_topology_graph, connected_components, validate_shape,
)
from src.sheetmetal_recognition import SheetMetalRecognition
from src.sheetmetal_unfold import SheetMetalUnfolder, UnfoldResult


class SheetMetalAnalyzer:
    """Coordinate safe, evidence-based analysis of one constant-thickness part."""

    AREA_CONSISTENCY_TOLERANCE = 0.08
    AREA_CHECK_TOLERANCE = 0.01   # developed area vs volume / thickness
    CUT_CHECK_TOLERANCE = 0.01    # developed cut length vs cut-surface area / thickness
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
        for index in cut_indices:
            topology.nodes[index].role = "cut_or_feature"
        cut_area = sum(faces[index].Area() for index in cut_indices)
        cut_components = connected_components(topology, cut_indices)
        bends = recognition.bend_evidence(faces, pairs, thickness, topology, self.k_factor)

        unfold: UnfoldResult | None = None
        unfold_error = ""
        try:
            unfold = SheetMetalUnfolder(recognition, self.k_factor).build(
                faces, pairs, thickness, topology, volume=solid.Volume(), cut_area=cut_area,
            )
        except Exception as exc:  # the estimate below is flagged, never silently exact
            unfold_error = str(exc)

        warnings = ["見積用の展開です。加工順序・金型情報は生成しません。"]
        assumptions: list[str] = []
        reason_codes: list[str] = []
        check_problems: list[str] = []
        if self.k_factor_is_default and bends:
            assumptions.append("Kファクターはデモ既定値0.33を使用しました。")
            warnings.append("Kファクターが未指定のため、曲げ展開値は概算・加工条件要確認です。")

        if unfold is not None:
            flat = unfold.flat
            checks = unfold.checks
            self._apply_bend_development(bends, unfold, thickness)
            check_problems = checks.failures(
                flat_tol=self._flatness_tolerance(thickness, tolerance.linear_mm),
                area_tol=self.AREA_CHECK_TOLERANCE, cut_tol=self.CUT_CHECK_TOLERANCE,
            )
            developed_bend_faces = {development.face_index + 1 for development in unfold.bends}
            undeveloped = [bend.bend_id for bend in bends if not developed_bend_faces & set(bend.face_indices)]
            if undeveloped:
                check_problems.append(f"展開されない曲げ {len(undeveloped)}箇所")
            if len(cut_components) != flat.boundary_count:
                check_problems.append(
                    f"切断面の連結成分 {len(cut_components)} と展開図の輪郭数 {flat.boundary_count} が不一致")
            hole_count = flat.inner_boundary_count
            hole_faces = unfold.hole_faces
            if checks.holes_on_bends or checks.cuts_on_bends:
                reason_codes.append("INTERNAL_BOUNDARY_ESTIMATED")
                warnings.append("曲げ部にかかる穴・切欠きがあります。展開値は概算・要確認です。")
            if check_problems:
                reason_codes.append("FLAT_PATTERN_UNVERIFIED")
                warnings.append("展開図の自己検算が一致しません（" + "、".join(check_problems) + "）。値は概算です。")
        else:
            k_area = sum(
                math.radians(bend.angle_deg or 0.0) * thickness * (self.k_factor - 0.5)
                * self._bend_axis_length(faces, bend) for bend in bends
            )
            flat = FlatPatternSummary(
                method="analytical_neutral_surface_estimate",
                area_mm2=round(solid.Volume() / thickness + k_area, 6),
                cut_length_mm=round(cut_area / thickness, 6), boundary_count=len(cut_components),
                outer_boundary_count=1 if cut_components else 0,
                inner_boundary_count=max(0, len(cut_components) - 1), surface_region_count=len(pairs),
            )
            hole_count = max(0, len(cut_components) - 1)
            hole_faces = sorted(cut_components, key=lambda c: -sum(faces[i].Area() for i in c))[1:]
            reason_codes.append("FLAT_PATTERN_ESTIMATED")
            warnings.append("2D展開図を構築できず、面積・切断長は体積・切断面積からの概算です。")
            check_problems.append(unfold_error or "展開失敗")

        verified = unfold is not None and not check_problems
        exact = verified and "INTERNAL_BOUNDARY_ESTIMATED" not in reason_codes
        status = "success" if exact else "partial"
        k_default = self.k_factor_is_default and bool(bends)
        confidence = "high" if exact and not k_default else "medium"
        if not verified:
            confidence = "low" if unfold is None else "medium"
        stages.extend([
            AnalysisStage(
                name="曲げ判定", status="success",
                message=f"平面―円筒―平面の隣接関係から曲げを{len(bends)}箇所と判定しました。",
            ),
            AnalysisStage(
                name="展開形状作成", status="success" if unfold is not None else "failed",
                message=("面隣接グラフをたどり、片側の表面を基準面へ展開しました。" if unfold is not None
                         else f"2D展開図を構築できませんでした: {unfold_error}"),
            ),
            AnalysisStage(
                name="自己検算", status="success" if verified else "failed",
                message=("面積（体積÷板厚）・切断長（切断面積÷板厚）・外形の単一性・経路整合がすべて一致しました。"
                         if verified else "、".join(check_problems)),
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
        ) for index, component in enumerate(hole_faces)]
        check_evidence = []
        if unfold is not None:
            if unfold.checks.area_error is not None:
                check_evidence.append(f"体積検算差 {unfold.checks.area_error:+.3%}")
            if unfold.checks.cut_error is not None:
                check_evidence.append(f"切断面積検算差 {unfold.checks.cut_error:+.3%}")
        return SheetMetalAnalysis(
            status=status, file_name=file_name, thickness_mm=round(thickness, 6),
            blank_area_mm2=round(flat.area_mm2, 6), cut_length_mm=round(flat.cut_length_mm, 6),
            hole_count=hole_count, bend_count=len(bends),
            reason_code=reason_codes[0] if reason_codes else None, reason_codes=reason_codes,
            message=("一定板厚の板金部品として幾何展開し、自己検算が一致しました。" if exact
                     else "一部項目を概算しました。担当者の確認が必要です。"),
            flat_pattern=flat, stages=stages, thickness_evidence=thickness_evidence,
            bend_evidence=bends, hole_evidence=holes, warnings=warnings, assumptions=assumptions,
            metric_quality={
                "thickness_mm": MetricQuality(
                    method="multi_evidence_cluster", confidence="high",
                    evidence=["対向平面/同軸円筒", "短辺", "面積支持", f"体積整合性 {area_error:.2%}"],
                ),
                "blank_area_mm2": MetricQuality(
                    method=flat.method, confidence=confidence, evidence=check_evidence[:1],
                ),
                "cut_length_mm": MetricQuality(
                    method="2d_boundary" if unfold is not None else "cut_surface_area_over_thickness",
                    confidence=confidence, evidence=check_evidence[1:],
                ),
                "hole_count": MetricQuality(
                    method="2d_loop_containment" if unfold is not None else "cut_face_components",
                    confidence="high" if verified else "medium",
                    evidence=[f"切断面の連結成分 {len(cut_components)}"],
                ),
                "bend_count": MetricQuality(method="plane_cylinder_plane_adjacency", confidence="high"),
            },
            thickness_candidates=recognition.candidate_models(candidates, faces, thickness),
        )

    def _apply_bend_development(self, bends, unfold: UnfoldResult, thickness: float) -> None:
        """Use the developed sweep angle (exact, from the cylinder parameter range) in bend evidence."""
        by_face = {development.face_index + 1: development for development in unfold.bends}
        for bend in bends:
            development = next((by_face[i] for i in bend.face_indices if i in by_face), None)
            if development is None:
                continue
            bend.angle_deg = round(math.degrees(development.angle_rad), 6)
            bend.bend_allowance_mm = round(
                development.angle_rad * (development.inner_radius + self.k_factor * thickness), 6)

    def _bend_axis_length(self, faces, bend) -> float:
        values = []
        for index in bend.face_indices:
            for vertex in faces[index - 1].Vertices():
                delta = [float(vertex.X) - bend.axis_point_mm[0], float(vertex.Y) - bend.axis_point_mm[1],
                         float(vertex.Z) - bend.axis_point_mm[2]]
                values.append(sum(a * b for a, b in zip(delta, bend.axis_direction)))
        return max(values) - min(values) if values else 0.0

    @staticmethod
    def _flatness_tolerance(thickness: float, linear: float) -> float:
        return max(1e-3, linear * 100, thickness * 1e-3)

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
