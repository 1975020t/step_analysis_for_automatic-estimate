"""LLM-only analyzer (Claude API) — a comparison baseline, NOT used for quotes by default.

Claude receives only data read from the STEP file (solid volume / surface area / bounding box and a
table of the B-Rep faces: type, area, normal or axis, radius, sweep angle, adjacency) and returns
every value itself: thickness, blank area, cut length, hole count and the bends (angle, radius).
The rule-based analyzer is not called, so the two can be compared side by side with the harness:

    python scripts/evaluate_golden.py --data golden_data --tolerance 0.10 \
        --analyzer src.llm_only_analyzer:LLMOnlyAnalyzer --limit 20 --workers 4

Because these numbers are not verified geometrically, the result is always `partial` (the quote is
shown as 概算) with reason LLM_ONLY_UNVERIFIED and confidence `low`. Money is never computed by
the LLM: QuoteEngine prices whatever values are returned, as for the rule-based analyzer.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

from src.claude_api import ClaudeClient, Usage
from src.models import AnalysisStage, BendEvidence, MetricQuality, SheetMetalAnalysis

SYSTEM = """あなたは板金加工の見積担当を補助する技術者です。
一定板厚の板金部品（曲げ後の3D形状）について、STEPから読み取ったソリッドの情報とB-Rep面の一覧を受け取り、
見積に使う値を求めてください。計算はすべてあなた自身が行います。
- thickness_mm: 板厚（表裏の対向する平面の距離、または同軸の曲げ円筒2枚の半径差）
- blank_area_mm2: 展開面積（展開図の外形面積から穴の面積を引いたもの）。曲げ部は中立面の長さ
  θ×(内R＋K×板厚) で展開する。Kは入力で与える
- cut_length_mm: 切断長（展開図の外周長と全穴の周長の合計）。曲げ端の長さも中立面で数える
- hole_count: 展開図で閉じた内側の穴の数。板を貫通する穴の側面がひとつながりの輪になっているものを1個と数える。
  長穴・角穴も1個。外周につながる切欠きは穴ではない
- bends: 曲げごとの角度（度）と内R。1つの曲げは同じ軸の円筒面2枚（内側と外側、半径差＝板厚）でできている。
  180°に近い折り返し（ヘム）も1つの曲げ
面の番号は1始まり。adjacent は辺を共有する面の番号。reasoning に計算の根拠を短く書いてから値を答えてください。"""

SCHEMA = {
    "type": "object",
    "properties": {
        "reasoning": {"type": "string"},
        "part_type": {"type": "string", "description": "形状の分類（例：平板、L字、U字、トレー、多段フランジ、ヘム付き）"},
        "thickness_mm": {"type": "number"},
        "blank_area_mm2": {"type": "number"},
        "cut_length_mm": {"type": "number"},
        "hole_count": {"type": "integer"},
        "bends": {
            "type": "array",
            "items": {"type": "object",
                      "properties": {"angle_deg": {"type": "number"}, "inner_radius_mm": {"type": "number"}},
                      "required": ["angle_deg", "inner_radius_mm"]},
        },
    },
    "required": ["reasoning", "part_type", "thickness_mm", "blank_area_mm2", "cut_length_mm", "hole_count", "bends"],
}

METRICS = ("thickness_mm", "blank_area_mm2", "cut_length_mm", "hole_count", "bend_count")


def read_step(path) -> tuple[dict, list[dict]]:
    """Solid summary and a compact table of the B-Rep faces (the only data sent to Claude)."""
    import cadquery as cq
    from OCP.BRepTools import BRepTools

    from src.sheetmetal_geometry import build_topology_graph

    shape = cq.importers.importStep(str(Path(path))).val()
    solids = list(shape.Solids())
    if len(solids) != 1:
        raise ValueError(f"Solidが{len(solids)}個あります。対象は単一部品のみです。")
    solid = solids[0]
    box = solid.BoundingBox()
    summary = {"volume_mm3": round(float(solid.Volume()), 2), "surface_area_mm2": round(float(solid.Area()), 2),
               "bounding_box_mm": [round(v, 2) for v in (box.xlen, box.ylen, box.zlen)]}
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
    return summary, rows


class LLMOnlyAnalyzer:
    def __init__(self, k_factor: float = 0.33, k_factor_is_default: bool = True, client: ClaudeClient | None = None):
        if not 0 <= k_factor <= 1:
            raise ValueError("Kファクターは0以上1以下で指定してください")
        self.k_factor = float(k_factor)
        self.k_factor_is_default = k_factor_is_default
        self.client = client or ClaudeClient()
        self.last_answer: dict | None = None

    @property
    def last_usage(self) -> dict:
        return self.client.usage.as_dict()

    def prompt(self, summary: dict, table: list[dict]) -> str:
        return (f"Kファクター: {self.k_factor}\n"
                f"ソリッド: {json.dumps(summary, ensure_ascii=False)}\n"
                "面の一覧（JSON、1行1面）:\n" + "\n".join(json.dumps(row, ensure_ascii=False) for row in table))

    def analyze(self, source, file_name: str | None = None) -> SheetMetalAnalysis:
        name = file_name or Path(str(source)).name
        try:
            summary, table = read_step(source)
        except Exception as exc:
            return self._failure(name, "unsupported", "STEP_READ_ERROR", f"STEPを読み込めません: {exc}")
        try:
            answer = self.client.complete_json(SYSTEM, self.prompt(summary, table), SCHEMA, max_tokens=8192)
        except Exception as exc:  # API / configuration errors: no values, no quote
            return self._failure(name, "error", "LLM_API_ERROR", f"Claude APIの呼び出しに失敗しました: {exc}")
        self.last_answer = answer
        return self.to_analysis(name, answer)

    def to_analysis(self, file_name: str, answer: dict) -> SheetMetalAnalysis:
        try:
            thickness = float(answer["thickness_mm"])
            area = float(answer["blank_area_mm2"])
            cut = float(answer["cut_length_mm"])
            holes = int(answer["hole_count"])
            bends = [(float(b["angle_deg"]), float(b["inner_radius_mm"])) for b in answer.get("bends") or []]
        except (KeyError, TypeError, ValueError):
            return self._failure(file_name, "unsupported", "LLM_OUTPUT_INVALID", "LLMの応答から値を取得できません。")
        if min(thickness, area, cut) <= 0 or holes < 0:
            return self._failure(file_name, "unsupported", "LLM_OUTPUT_INVALID", "LLMの応答に不正な値があります。")
        note = f"LLM単独解析（{answer.get('part_type', '')}）。幾何計算による検算をしていないため概算です。"
        evidence = [BendEvidence(
            bend_id=i + 1, axis_point_mm=(0.0, 0.0, 0.0), axis_direction=(0.0, 0.0, 0.0),
            inner_radius_mm=radius, outer_radius_mm=radius + thickness, angle_deg=angle,
            bend_allowance_mm=round(math.radians(angle) * (radius + self.k_factor * thickness), 6),
        ) for i, (angle, radius) in enumerate(bends)]
        assumptions = ["Kファクターはデモ既定値0.33を使用しました。"] if self.k_factor_is_default and bends else []
        return SheetMetalAnalysis(
            status="partial", file_name=file_name, thickness_mm=round(thickness, 6),
            blank_area_mm2=round(area, 6), cut_length_mm=round(cut, 6), hole_count=holes, bend_count=len(bends),
            reason_code="LLM_ONLY_UNVERIFIED", reason_codes=["LLM_ONLY_UNVERIFIED"], message=note,
            stages=[AnalysisStage(name="LLM単独解析", status="success", message=note)],
            bend_evidence=evidence, warnings=[note], assumptions=assumptions,
            metric_quality={key: MetricQuality(method="llm_only", confidence="low",
                                               evidence=[str(answer.get("reasoning", ""))[:500]] if key == "blank_area_mm2" else [])
                            for key in METRICS},
        )

    @staticmethod
    def _failure(file_name: str, status: str, reason_code: str, message: str) -> SheetMetalAnalysis:
        return SheetMetalAnalysis(
            status=status, file_name=file_name, reason_code=reason_code, reason_codes=[reason_code], message=message,
            stages=[AnalysisStage(name="LLM単独解析", status="failed", message=message)],
            metric_quality={key: MetricQuality(method="unavailable", confidence="unavailable") for key in METRICS},
        )


__all__ = ["LLMOnlyAnalyzer", "Usage", "read_step"]
