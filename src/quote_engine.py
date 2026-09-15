from __future__ import annotations

import math

from src.master_loader import MasterLoader
from src.models import QuoteCondition, QuoteLine, QuoteResult, SheetMetalAnalysis


class QuoteUnavailableError(ValueError):
    pass


class QuoteEngine:
    """Deterministic sheet-metal quote calculator. The LLM never calculates money."""

    MARGIN_RATE = 0.25
    REQUIRED_METRICS = ("thickness_mm", "blank_area_mm2", "cut_length_mm", "hole_count", "bend_count")

    def __init__(self, masters: MasterLoader) -> None:
        self.masters = masters

    @staticmethod
    def _round_up(value: float, unit: int = 10) -> int:
        return int(math.ceil(value / unit) * unit)

    def calculate(self, analysis: SheetMetalAnalysis, condition: QuoteCondition) -> QuoteResult:
        missing = [name for name in self.REQUIRED_METRICS if getattr(analysis, name) is None]
        if missing or analysis.status in {"unsupported", "error"}:
            raise QuoteUnavailableError(
                "見積に必要な解析値を取得できません: " + ", ".join(missing or [analysis.status])
            )

        material = self.masters.material(condition.material)
        quantity = condition.quantity
        volume_m3 = float(analysis.blank_area_mm2) * float(analysis.thickness_mm) / 1_000_000_000
        weight_per_part_kg = volume_m3 * float(material["density_kg_m3"])
        material_base = weight_per_part_kg * float(material["price_per_kg"]) * float(material["waste_factor"])
        material_amount = self._scope_amount(material_base, material["charge_scope"], quantity)
        lines = [QuoteLine(
            code="MATERIAL", name=f"材料費（{condition.material}）",
            quantity=round(weight_per_part_kg * quantity, 4),
            unit_price=float(material["price_per_kg"]), amount=material_amount,
            source="cad", unit="kg",
        )]

        for code, per_part_quantity, unit in (
            ("LASER_CUT", float(analysis.cut_length_mm), "mm"),
            ("PIERCE", float(analysis.hole_count), "穴"),
            ("BEND", float(analysis.bend_count), "曲げ"),
            ("SETUP", 1.0, "式"),
        ):
            row = self.masters.process(code)
            billable = per_part_quantity * quantity if row["charge_scope"] == "per_part" else per_part_quantity
            lines.append(QuoteLine(
                code=code, name=row["display_name"], quantity=round(billable, 4),
                unit_price=float(row["unit_price"]), amount=billable * float(row["unit_price"]),
                source="cad" if code != "SETUP" else "master", unit=unit,
            ))

        for additional in condition.additional_processes:
            if not additional.confirmed:
                continue
            row = self.masters.process(additional.process_code)
            billable = additional.quantity * (quantity if row["charge_scope"] == "per_part" else 1)
            lines.append(QuoteLine(
                code=additional.process_code, name=row["display_name"], quantity=round(billable, 4),
                unit_price=float(row["unit_price"]), amount=billable * float(row["unit_price"]),
                source="chat", unit=row["unit"],
            ))

        subtotal = sum(line.amount for line in lines)
        final_price = subtotal * (1 + self.MARGIN_RATE)
        estimated = analysis.status == "partial" or bool(analysis.assumptions) or any(
            quality.confidence in {"medium", "low"} for quality in analysis.metric_quality.values()
        )
        return QuoteResult(
            lines=lines, subtotal_cost=subtotal, margin_rate=self.MARGIN_RATE,
            final_price=final_price, rounded_final_price=self._round_up(final_price),
            estimated_minutes=0.0, is_estimate=estimated,
            warnings=list(analysis.warnings) if estimated else [],
        )

    @staticmethod
    def _scope_amount(per_part_amount: float, scope: str, quantity: int) -> float:
        return per_part_amount * quantity if scope == "per_part" else per_part_amount
