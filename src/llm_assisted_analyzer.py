"""LLM-assisted analyzers (Claude API), evaluated against the rule-based analyzer.

Two ways of using Claude, both on top of the rule-based SheetMetalAnalyzer. Neither lets Claude
produce or change a number; Claude can only make a result MORE cautious.

LLMAssistedAnalyzer (review): Claude reviews a compact summary of the rule-based result.
  accept     -> unchanged
  downgrade  -> success becomes partial (the quote is shown as 概算)
  reject     -> unsupported (no quote)
Claude never produces or changes numbers (thickness, area, cut length, counts).

LLMCrossCheckAnalyzer (independent cross-check): Claude gets a table of the B-Rep faces
(type, area, normal / axis, radius, sweep angle, adjacency) but NOT the rule-based result, and
counts bends, hems and holes on its own. If any count disagrees with the rule-based analysis,
the result is downgraded to partial (概算) with reason LLM_CROSSCHECK_MISMATCH.

Evaluate with the golden harness:
    python scripts/evaluate_golden.py --data golden_data --tolerance 0.10 \
        --analyzer src.llm_assisted_analyzer:LLMCrossCheckAnalyzer --limit 20 --workers 4
"""
from __future__ import annotations

import json
import math
from collections import Counter
from pathlib import Path

from src.claude_api import ClaudeClient, Usage
from src.models import SheetMetalAnalysis
from src.sheetmetal_analyzer import SheetMetalAnalyzer

SYSTEM = """あなたは板金加工の見積担当を補助するレビュアーです。
STEPから幾何計算で得た解析結果の要約を受け取り、その結果を見積に使ってよいかを判断します。
数値の計算や修正は行いません。判断は次の3つから選びます。
- accept: 根拠に矛盾がなく、そのまま使える
- downgrade: 値は出ているが、根拠に不整合や不確かな点があり、担当者の確認が必要（概算扱い）
- reject: 対象外の形状、または値が使えない
迷った場合は downgrade を選んでください。"""

SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["accept", "downgrade", "reject"]},
        "part_type": {"type": "string", "description": "形状の分類（例：平板、L字、トレー、多段フランジ、ヘム付き）"},
        "concerns": {"type": "array", "items": {"type": "string"}},
        "reason": {"type": "string"},
    },
    "required": ["verdict", "part_type", "concerns", "reason"],
}


def summarize(result: SheetMetalAnalysis, face_types: dict[str, int]) -> dict:
    flat = result.flat_pattern
    return {
        "status": result.status,
        "reason_codes": result.reason_codes,
        "face_types": face_types,
        "thickness_mm": result.thickness_mm,
        "blank_area_mm2": result.blank_area_mm2,
        "cut_length_mm": result.cut_length_mm,
        "hole_count": result.hole_count,
        "bend_count": result.bend_count,
        "bends": [{"angle_deg": b.angle_deg, "inner_radius_mm": b.inner_radius_mm,
                   "axis_direction": b.axis_direction} for b in result.bend_evidence],
        "flat_pattern": None if flat is None else {
            "method": flat.method, "outer_boundaries": flat.outer_boundary_count,
            "inner_boundaries": flat.inner_boundary_count, "bounding_box_mm": flat.bounding_box_mm},
        "metric_quality": {k: {"method": v.method, "confidence": v.confidence, "evidence": v.evidence}
                           for k, v in result.metric_quality.items()},
        "warnings": result.warnings,
    }


class LLMAssistedAnalyzer:
    def __init__(self, k_factor: float = 0.33, k_factor_is_default: bool = True, client: ClaudeClient | None = None):
        self.rule = SheetMetalAnalyzer(k_factor=k_factor, k_factor_is_default=k_factor_is_default)
        self.client = client or ClaudeClient()
        self.last_review: dict | None = None

    @property
    def last_usage(self) -> dict:
        return self.client.usage.as_dict()

    def analyze(self, source, file_name: str | None = None) -> SheetMetalAnalysis:
        result = self.rule.analyze(source, file_name=file_name)
        if result.status not in {"success", "partial"}:
            return result  # nothing to review: already rejected
        face_types = self._face_types(source)
        review = self.client.complete_json(
            SYSTEM, "解析結果の要約:\n" + json.dumps(summarize(result, face_types), ensure_ascii=False), SCHEMA)
        self.last_review = review
        return self.apply(result, review)

    @staticmethod
    def apply(result: SheetMetalAnalysis, review: dict) -> SheetMetalAnalysis:
        verdict = review.get("verdict")
        note = f"LLMレビュー（{review.get('part_type', '')}）: {review.get('reason', '')}"
        if verdict == "reject":
            return result.model_copy(update={
                "status": "unsupported", "reason_code": "LLM_REVIEW_REJECTED",
                "reason_codes": [*result.reason_codes, "LLM_REVIEW_REJECTED"],
                "message": note, "warnings": [*result.warnings, note]})
        if verdict == "downgrade" and result.status == "success":
            return result.model_copy(update={
                "status": "partial", "reason_codes": [*result.reason_codes, "LLM_REVIEW_FLAGGED"],
                "warnings": [*result.warnings, note]})
        return result  # accept (or downgrade of an already partial result): never upgrade

    @staticmethod
    def _face_types(source) -> dict[str, int]:
        try:
            import cadquery as cq
            shape = cq.importers.importStep(str(Path(source))).val()
            return dict(Counter(face.geomType() for face in shape.Faces()))
        except Exception:
            return {}


CROSSCHECK_SYSTEM = """あなたは板金部品のCAD形状を読み解く技術者です。
一定板厚の板金部品（曲げ後の3D形状）のB-Rep面の一覧を受け取り、次を数えてください。
- bend_count: 曲げの数。1つの曲げは、同じ軸の円筒面2枚（内側と外側、半径差＝板厚）で、両側の平面をつなぐ
- hem_count: そのうち曲げ角度が170°以上のもの（ヘム、折り返し）
- hole_count: 展開図で閉じた内側の穴の数。板を貫通する穴の側面（円筒・平面）がひとつながりの輪になっているものを1個と数える。
  長穴（両端が半円筒2面＋平面2面）や角穴（平面4面）も1個。外周につながる切欠きは穴ではない
面の番号は1始まり。adjacent は辺を共有する面の番号。与えられた情報だけで判断し、数えた根拠を短く書いてください。"""

CROSSCHECK_SCHEMA = {
    "type": "object",
    "properties": {
        "part_type": {"type": "string", "description": "形状の分類（例：平板、L字、U字、トレー、多段フランジ、ヘム付き）"},
        "thickness_mm": {"type": "number"},
        "bend_count": {"type": "integer"},
        "hem_count": {"type": "integer"},
        "hole_count": {"type": "integer"},
        "reasoning": {"type": "string"},
    },
    "required": ["part_type", "thickness_mm", "bend_count", "hem_count", "hole_count", "reasoning"],
}


def face_table(path) -> list[dict]:
    """Compact, analyzer-independent description of the B-Rep faces (sent to Claude)."""
    import cadquery as cq
    from OCP.BRepTools import BRepTools

    from src.sheetmetal_geometry import build_topology_graph

    solid = cq.importers.importStep(str(Path(path))).val()
    faces = list(solid.Faces())
    graph = build_topology_graph(faces)
    rows = []
    for index, face in enumerate(faces):
        kind = face.geomType()
        row = {"id": index + 1, "type": kind, "area": round(float(face.Area()), 1)}
        if kind == "PLANE":
            row["normal"] = [round(v, 3) for v in face.normalAt().toTuple()]
            row["center"] = [round(v, 1) for v in face.Center().toTuple()]
        elif kind == "CYLINDER":
            cylinder = face._geomAdaptor().Cylinder()
            direction = cylinder.Axis().Direction()
            location = cylinder.Axis().Location()
            u_min, u_max, _, _ = BRepTools.UVBounds_s(face.wrapped)
            row["radius"] = round(float(cylinder.Radius()), 3)
            row["axis_dir"] = [round(v, 3) for v in (direction.X(), direction.Y(), direction.Z())]
            row["axis_point"] = [round(v, 1) for v in (location.X(), location.Y(), location.Z())]
            row["sweep_deg"] = round(math.degrees(u_max - u_min), 1)
        row["adjacent"] = sorted(i + 1 for i in graph.nodes[index].adjacent)
        rows.append(row)
    return rows


class LLMCrossCheckAnalyzer:
    """Rule-based analysis + an independent count by Claude; any disagreement -> 概算 (never a new number)."""

    def __init__(self, k_factor: float = 0.33, k_factor_is_default: bool = True, client: ClaudeClient | None = None):
        self.rule = SheetMetalAnalyzer(k_factor=k_factor, k_factor_is_default=k_factor_is_default)
        self.client = client or ClaudeClient()
        self.last_answer: dict | None = None

    @property
    def last_usage(self) -> dict:
        usage = self.client.usage.as_dict()
        if self.last_answer is not None:
            usage.update({f"llm_{key}": self.last_answer.get(key) for key in ("bend_count", "hem_count", "hole_count")})
        return usage

    def analyze(self, source, file_name: str | None = None) -> SheetMetalAnalysis:
        result = self.rule.analyze(source, file_name=file_name)
        if result.status not in {"success", "partial"}:
            return result
        table = face_table(source)
        answer = self.client.complete_json(
            CROSSCHECK_SYSTEM,
            "面の一覧（JSON、1行1面）:\n" + "\n".join(json.dumps(row, ensure_ascii=False) for row in table),
            CROSSCHECK_SCHEMA, max_tokens=4096)
        self.last_answer = answer
        return self.apply(result, answer)

    @staticmethod
    def apply(result: SheetMetalAnalysis, answer: dict) -> SheetMetalAnalysis:
        hems = sum((bend.angle_deg or 0) >= 170 for bend in result.bend_evidence)
        mismatches = [
            f"{label}: 解析 {ours} / LLM {answer.get(key)}"
            for label, key, ours in (("曲げ数", "bend_count", result.bend_count),
                                     ("ヘム数", "hem_count", hems), ("穴数", "hole_count", result.hole_count))
            if answer.get(key) != ours
        ]
        if not mismatches:
            return result
        note = "LLMの独立カウントと不一致（" + "、".join(mismatches) + "）。担当者の確認が必要です。"
        return result.model_copy(update={
            "status": "partial", "reason_codes": [*result.reason_codes, "LLM_CROSSCHECK_MISMATCH"],
            "reason_code": result.reason_code or "LLM_CROSSCHECK_MISMATCH",
            "warnings": [*result.warnings, note]})


__all__ = ["LLMAssistedAnalyzer", "LLMCrossCheckAnalyzer", "Usage", "face_table"]
