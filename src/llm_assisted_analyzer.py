"""Sample LLM-assisted analyzer (starting point for experiments, free to redesign).

Runs the rule-based SheetMetalAnalyzer, then asks Claude to review a compact summary of the
geometry evidence. Claude may only make the result MORE cautious:
  accept     -> unchanged
  downgrade  -> success becomes partial (the quote is shown as 概算)
  reject     -> unsupported (no quote)
Claude never produces or changes numbers (thickness, area, cut length, counts).

Evaluate with the golden harness:
    python scripts/evaluate_golden.py --data golden_data --tolerance 0.10 \
        --analyzer src.llm_assisted_analyzer:LLMAssistedAnalyzer --limit 10 --workers 1
"""
from __future__ import annotations

import json
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


__all__ = ["LLMAssistedAnalyzer", "Usage"]
