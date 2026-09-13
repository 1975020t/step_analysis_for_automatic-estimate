from __future__ import annotations

import math

from src.master_loader import MasterLoader
from src.models import CadFeatures, QuoteCondition, QuoteLine, QuoteResult


class QuoteEngine:
    MARGIN_RATE = 0.25

    def __init__(self, masters: MasterLoader) -> None:
        self.masters = masters

    @staticmethod
    def _round_up(value: float, unit: int = 10) -> int:
        return int(math.ceil(value / unit) * unit)

    def calculate(self, features: CadFeatures, condition: QuoteCondition) -> QuoteResult:
        material = self.masters.material(condition.material)
        quantity = condition.quantity

        volume_m3 = features.volume_mm3 / 1_000_000_000
        weight_kg = volume_m3 * float(material["density_kg_m3"])
        material_cost = (
            weight_kg
            * float(material["price_per_kg"])
            * float(material["waste_factor"])
            * quantity
        )

        complexity = self.masters.complexity_multiplier(features.face_count)
        estimated_minutes_per_part = (
            10
            + features.volume_mm3 / 10_000 * 0.5
            + features.face_count * 0.3
        ) * complexity

        milling = self.masters.process("BASE_MILLING")
        milling_cost = estimated_minutes_per_part * float(milling["unit_price"]) * quantity
        setup = self.masters.process("SETUP")

        lines = [
            QuoteLine(
                code="MATERIAL",
                name=f"材料費（{condition.material}）",
                quantity=round(weight_kg * quantity, 4),
                unit_price=float(material["price_per_kg"]),
                amount=material_cost,
                source="cad",
                unit="kg",
            ),
            QuoteLine(
                code="BASE_MILLING",
                name=milling["display_name"],
                quantity=round(estimated_minutes_per_part * quantity, 2),
                unit_price=float(milling["unit_price"]),
                amount=milling_cost,
                source="cad",
                unit="分",
            ),
            QuoteLine(
                code="SETUP",
                name=setup["display_name"],
                quantity=1,
                unit_price=float(setup["unit_price"]),
                amount=float(setup["unit_price"]),
                source="master",
                unit="式",
            ),
        ]

        for additional in condition.additional_processes:
            if not additional.confirmed:
                continue
            process = self.masters.process(additional.process_code)
            amount = additional.quantity * float(process["unit_price"])
            lines.append(
                QuoteLine(
                    code=additional.process_code,
                    name=process["display_name"],
                    quantity=additional.quantity,
                    unit_price=float(process["unit_price"]),
                    amount=amount,
                    source="chat",
                    unit=additional.unit,
                )
            )

        subtotal = sum(line.amount for line in lines)
        final_price = subtotal * (1 + self.MARGIN_RATE)
        return QuoteResult(
            lines=lines,
            subtotal_cost=subtotal,
            margin_rate=self.MARGIN_RATE,
            final_price=final_price,
            rounded_final_price=self._round_up(final_price),
            estimated_minutes=estimated_minutes_per_part * quantity,
        )
