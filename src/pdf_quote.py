"""Turn the conditions read from a drawing PDF into a quote condition, item by item.

Every price item gets one status:
  確定     read with confidence -> priced
  要確認   read, but a person must confirm it (needs_review)      -> not priced, the quote is 概算
  未登録   written on the drawing, but not in the master          -> not priced, the quote is 概算
  記載なし not written on the drawing                              -> the user enters it, the quote is 概算
The user can edit any item in the UI; an edited item becomes 確定 (confirmed by the user).
Money is always computed by QuoteEngine from the master.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from src.master_loader import UNREGISTERED, MasterLoader
from src.models import AdditionalProcess, QuoteCondition

CONFIRMED, REVIEW, UNREG, MISSING = "確定", "要確認", "未登録", "記載なし"
LABELS = {"material": "材質", "thickness_mm": "板厚", "quantity": "数量", "surface_treatment": "表面処理",
          "processes": "追加加工", "rush": "特急"}


@dataclass
class ConditionItem:
    field: str
    label: str
    value: object
    display: str
    status: str
    reasons: list[str] = field(default_factory=list)


def condition_items(reading: dict, masters: MasterLoader) -> list[ConditionItem]:
    review = set(reading.get("needs_review") or [])
    reasons = reading.get("review_reasons") or {}
    items: list[ConditionItem] = []

    def status_of(name, value, unregistered=False):
        if value is None:
            return MISSING
        if unregistered:
            return UNREG
        return REVIEW if name in review else CONFIRMED

    material = reading.get("material")
    items.append(ConditionItem("material", LABELS["material"], material,
                               _name(masters.materials, material), status_of("material", material, material == UNREGISTERED),
                               reasons.get("material", [])))
    for name, unit in (("thickness_mm", "mm"), ("quantity", "個")):
        value = reading.get(name)
        items.append(ConditionItem(name, LABELS[name], value, "-" if value is None else f"{value:g} {unit}",
                                   status_of(name, value), reasons.get(name, [])))
    finish = reading.get("surface_treatment")
    items.append(ConditionItem("surface_treatment", LABELS["surface_treatment"], finish,
                               _name(masters.surface_treatments, finish),
                               status_of("surface_treatment", finish, finish == UNREGISTERED), reasons.get("surface_treatment", [])))
    procs = reading.get("processes") or []
    registered = [p for p in procs if p.get("code") != UNREGISTERED]
    display = "、".join(f"{masters.process_rates[p['code']]['display_name']} ×{p.get('count_per_part')}" for p in registered
                        if p["code"] in masters.process_rates) or "なし"
    unregistered = len(procs) - len(registered)
    if unregistered:
        display += f"（ほかに未登録の加工 {unregistered}件）"
    status = REVIEW if "processes" in review else (UNREG if unregistered else CONFIRMED)
    items.append(ConditionItem("processes", LABELS["processes"], procs, display, status, reasons.get("processes", [])))
    rush = bool(reading.get("rush"))
    items.append(ConditionItem("rush", LABELS["rush"], rush, "あり" if rush else "なし",
                               REVIEW if "rush" in review else CONFIRMED, reasons.get("rush", [])))
    return items


def quote_condition(items: list[ConditionItem], masters: MasterLoader, material_fallback: str,
                    analysis_thickness: float | None = None) -> QuoteCondition:
    """QuoteCondition from the items. Only 確定 items are priced; the rest become `pending` reasons."""
    by = {item.field: item for item in items}
    pending: list[str] = []

    def note(item: ConditionItem, extra: str = "") -> None:
        text = f"{item.label}：{item.status}"
        if item.status != MISSING and item.display not in ("-", ""):
            text += f"（{item.display}）"
        if extra:
            text += f" {extra}"
        pending.append(text)

    material = by["material"]
    code = material_fallback
    if material.status == CONFIRMED and material.value in masters.materials:
        code = material.value
    else:
        note(material, f"→ 材質 {material_fallback} で仮計算")
    quantity = by["quantity"]
    qty = 1
    if quantity.status == CONFIRMED and quantity.value:
        qty = int(quantity.value)
    else:
        note(quantity, f"→ 数量 {int(quantity.value) if quantity.value else 1} で仮計算")
        qty = int(quantity.value) if quantity.value else 1
    thickness = by["thickness_mm"]
    if thickness.status != CONFIRMED:
        note(thickness)
    elif analysis_thickness is not None and abs(float(thickness.value) - float(analysis_thickness)) > 1e-6:
        pending.append(f"板厚：図面 {thickness.value:g} mm と形状 {analysis_thickness:g} mm が異なる")
    finish = by["surface_treatment"]
    surface = None
    if finish.status == CONFIRMED and finish.value in masters.surface_treatments:
        surface = finish.value
    else:
        note(finish, "→ 金額に含めない")
    processes: list[AdditionalProcess] = []
    proc = by["processes"]
    for p in proc.value or []:
        if p.get("code") in masters.process_rates and p.get("count_per_part"):
            processes.append(AdditionalProcess(
                process_code=p["code"], quantity=p["count_per_part"], unit=masters.process_rates[p["code"]]["unit"],
                confirmed=proc.status in (CONFIRMED, UNREG), source="drawing"))
    if proc.status != CONFIRMED:
        note(proc, "→ 未確認の加工は金額に含めない")
    rush = by["rush"]
    if rush.status != CONFIRMED:
        note(rush, "→ 特急割増を含めない")
    return QuoteCondition(material=code, quantity=max(1, qty), additional_processes=processes,
                          surface_treatment=surface, rush=bool(rush.value) and rush.status == CONFIRMED,
                          pending=pending)


def _name(rows: dict, code) -> str:
    if code is None:
        return "-"
    if code == UNREGISTERED:
        return "マスター未登録"
    row = rows.get(code)
    return row["display_name"] if row else str(code)
