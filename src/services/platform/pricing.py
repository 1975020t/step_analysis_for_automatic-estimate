"""The estimate of the screens: its inputs, what is still missing for the quotation, and the amounts.

Rules (analysis/handoff_ui.md, 決まっていること):
  * A quotation can be issued when every item the amount needs is entered: the quantity, the material, the five
    shape values (from the analysis, or entered when the analysis could not give them) and a unit price for every
    material / process / surface treatment that is not in the master. Uncertain values (a drawing reading to
    check, an analysis that is an estimate) are taken as they are: they are hints, not blockers.
  * 表面処理「なし」 and 特急「なし」 are entered values.
  * Unit prices entered for something outside the master are used for this quote only (src/services/platform/
    masters.with_custom), never written to the master, never mapped to a "similar" master row.
  * The 希望納期 (due date) is recorded and printed; it never changes the price.
Money is computed by QuoteEngine and the rounding of src/quote_document.py only (no LLM, nothing on the screen).
"""
from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field

from src.master_loader import UNREGISTERED, MasterLoader
from src.models import AdditionalProcess, QuoteCondition, SheetMetalAnalysis
from src.quote_document import price_summary
from src.quote_engine import QuoteEngine
from src.services.platform.masters import CUSTOM_FINISH, CUSTOM_PROCESS_PREFIX, with_custom

SHAPE_FIELDS = {"thickness_mm": "板厚", "blank_area_mm2": "展開面積", "cut_length_mm": "切断長",
                "hole_count": "穴数", "bend_count": "曲げ数"}
STATUS_LABEL = {"確定": "読み取り済み", "要確認": "要確認", "未登録": "未登録", "記載なし": "記載なし"}


class CustomMaterial(BaseModel):
    name: str = Field(min_length=1, description="材質の名前（図面の表記など）")
    price_per_kg: float | None = Field(None, gt=0, description="kg単価（円）")
    density_kg_m3: float | None = Field(None, gt=0, description="密度（kg/m³）")


class CustomFinish(BaseModel):
    name: str = Field(min_length=1)
    unit_price: float | None = Field(None, ge=0, description="1個あたりの単価（円）")


class ProcessInput(BaseModel):
    code: str = Field(description="data/process_rates.csv のコード")
    quantity: float | None = Field(None, gt=0, description="1個あたりの箇所数")
    source: str = "user"


class CustomProcess(BaseModel):
    name: str = Field(min_length=1, description="加工の名前（図面の表記など）")
    quantity: float | None = Field(None, gt=0, description="1個あたりの箇所数（個数）")
    unit_price: float | None = Field(None, ge=0, description="1か所（1個）あたりの単価（円）")
    source: str = "user"


class ShapeInput(BaseModel):
    """Shape values entered by the user; used only for the values the analysis could not give."""
    thickness_mm: float | None = Field(None, gt=0)
    blank_area_mm2: float | None = Field(None, gt=0)
    cut_length_mm: float | None = Field(None, gt=0)
    hole_count: int | None = Field(None, ge=0)
    bend_count: int | None = Field(None, ge=0)


class QuoteInputs(BaseModel):
    customer: str = ""
    title: str = ""
    staff: str = ""
    quantity: int | None = Field(None, gt=0)
    due_date: date | None = Field(None, description="希望納期（金額には影響しない）")
    material: str | None = Field(None, description="マスターの材質コード")
    custom_material: CustomMaterial | None = Field(None, description="マスターにない材質（material が空のとき）")
    surface_treatment: str | None = Field("NONE", description="マスターのコード。NONE（なし）は入力済み")
    custom_finish: CustomFinish | None = Field(None, description="マスターにない表面処理")
    rush: bool = False
    processes: list[ProcessInput] = Field(default_factory=list)
    custom_processes: list[CustomProcess] = Field(default_factory=list)
    k_factor: float = Field(0.33, ge=0, le=1)
    k_factor_confirmed: bool = False
    thickness_mm: float | None = Field(None, gt=0, description="展開図DXFの板厚（解析の入力）")
    flat_confirmed: bool = False
    shape: ShapeInput = Field(default_factory=ShapeInput)


class Missing(BaseModel):
    field: str
    label: str
    message: str


# ---------------------------------------------------------------- reading -> draft inputs
def inputs_from_reading(reading: dict | None, masters: MasterLoader, base: QuoteInputs) -> QuoteInputs:
    """Draft inputs from a drawing reading (src/pdf_reader.py output): registered codes are taken, an
    unregistered material / finish / process becomes an item whose unit price the user enters. Values the user
    already gave in `base` (fields set explicitly) are kept."""
    if not reading:
        return base
    given = base.model_fields_set
    update: dict = {}
    texts = evidence_texts(reading)
    material = reading.get("material")
    if "material" not in given and "custom_material" not in given:
        if material in masters.materials:
            update["material"] = material
        elif material == UNREGISTERED:
            update["material"] = None
            update["custom_material"] = CustomMaterial(name=texts.get("material") or "図面の材質（マスタ未登録）")
    if "quantity" not in given and reading.get("quantity"):
        update["quantity"] = int(reading["quantity"])
    finish = reading.get("surface_treatment")
    if "surface_treatment" not in given and "custom_finish" not in given:
        if finish in masters.surface_treatments:
            update["surface_treatment"] = finish
        elif finish == UNREGISTERED:
            update["surface_treatment"] = None
            update["custom_finish"] = CustomFinish(name=texts.get("surface_treatment") or "図面の表面処理（マスタ未登録）")
        else:
            update["surface_treatment"] = "NONE"
    if "processes" not in given and "custom_processes" not in given:
        processes, custom = [], []
        unregistered_texts = list(texts.get("processes") or [])
        for p in reading.get("processes") or []:
            count = p.get("count_per_part")
            if p.get("code") in masters.process_rates:
                processes.append(ProcessInput(code=p["code"], quantity=count or None, source="drawing"))
            elif p.get("code") == UNREGISTERED:
                text = unregistered_texts.pop(0) if unregistered_texts else "図面の加工（マスタ未登録）"
                custom.append(CustomProcess(name=without_count(text), quantity=count or count_in(text), source="drawing"))
        update["processes"], update["custom_processes"] = processes, custom
    if "rush" not in given:
        update["rush"] = bool(reading.get("rush"))
    return base.model_copy(update=update)


def count_in(text: str) -> float | None:
    """The count written in a callout ("M10タップ 6ヶ所", "3X M10 TAP"), or None."""
    import re
    import unicodedata

    t = unicodedata.normalize("NFKC", text or "")
    m = re.search(r"(\d+)\s*(?:ヶ所|箇所|か所|カ所|個|点)", t) or re.search(r"[×xX]\s*(\d+)\s*$", t) or \
        re.match(r"^\s*(\d+)\s*[xX×]\s", t)
    return float(m.group(1)) if m and int(m.group(1)) > 0 else None


def without_count(text: str) -> str:
    """The callout without its count ("M10タップ 6ヶ所" -> "M10タップ"): the count is the quantity column."""
    import re

    t = (text or "").strip()
    out = re.sub(r"\s*[\d０-９]+\s*(?:ヶ所|箇所|か所|カ所|個|点)\s*$", "", t)
    out = re.sub(r"\s*[×xX]\s*[\d０-９]+\s*$", "", out)
    out = re.sub(r"^\s*[\d０-９]+\s*[xX×]\s+", "", out)
    return out.strip() or t


def evidence_texts(reading: dict) -> dict:
    """The drawing's wording of the material / finish and of the unregistered processes (for the names)."""
    out: dict = {"processes": list(reading.get("unregistered_texts") or [])}
    out.update({k: v for k, v in (reading.get("source_texts") or {}).items() if v})
    for raw in reading.get("evidence") or []:
        for key in ("material", "surface_treatment"):
            text = ((raw or {}).get(key) or {}).get("text")
            if text and key not in out:
                out[key] = str(text).strip()
        if not reading.get("unregistered_texts"):
            for item in (raw or {}).get("processes") or []:
                if item.get("code") == UNREGISTERED and item.get("text") and item["text"] not in out["processes"]:
                    out["processes"].append(item["text"].strip())
    return out


# ---------------------------------------------------------------- shape
def effective_analysis(analysis: dict | None, shape: ShapeInput, file_name: str = "") -> tuple[SheetMetalAnalysis | None, dict]:
    """The analysis that is priced: the analysis values, and the user's values for what the analysis could not
    give. Returns (analysis or None when a value is still missing, {field: {"value", "source"}})."""
    base = SheetMetalAnalysis.model_validate(analysis) if analysis else None
    usable = base is not None and base.status in ("success", "partial")
    values, sources = {}, {}
    for name in SHAPE_FIELDS:
        own = getattr(base, name) if base is not None else None
        entered = getattr(shape, name)
        if usable and own is not None:
            values[name], sources[name] = own, "analysis"
        elif entered is not None:
            values[name], sources[name] = entered, "input"
        else:
            values[name], sources[name] = None, None
    detail = {k: {"value": values[k], "source": sources[k]} for k in SHAPE_FIELDS}
    if any(v is None for v in values.values()):
        return None, detail
    if usable and all(s == "analysis" for s in sources.values()):
        return base, detail
    if usable:
        return base.model_copy(update=values), detail
    return SheetMetalAnalysis(status="partial", file_name=(base.file_name if base else file_name) or "manual",
                              assumptions=["寸法の値は担当者の入力"], **values), detail


# ---------------------------------------------------------------- missing items
def missing_items(inputs: QuoteInputs, masters: MasterLoader, shape_detail: dict, analysis_pending: bool) -> list[Missing]:
    out: list[Missing] = []
    if not inputs.quantity:
        out.append(Missing(field="quantity", label="数量", message="数量が未入力です"))
    if inputs.material:
        if inputs.material not in masters.materials:
            out.append(Missing(field="material", label="材質", message=f"材質 {inputs.material} がマスタにありません。選び直してください"))
    elif inputs.custom_material is not None:
        lacking = [x for x, v in (("kg単価", inputs.custom_material.price_per_kg), ("密度", inputs.custom_material.density_kg_m3)) if not v]
        if lacking:
            out.append(Missing(field="custom_material", label="材質（マスタ未登録）",
                               message=f"マスタにない材質「{inputs.custom_material.name}」の{'・'.join(lacking)}が未入力です"))
    else:
        out.append(Missing(field="material", label="材質", message="材質が未選択です"))
    if inputs.custom_finish is not None and inputs.surface_treatment in (None, ""):
        if inputs.custom_finish.unit_price is None:
            out.append(Missing(field="custom_finish", label="表面処理（マスタ未登録）",
                               message=f"マスタにない表面処理「{inputs.custom_finish.name}」の単価（1個あたり）が未入力です"))
    elif inputs.surface_treatment not in (None, "", "NONE") and inputs.surface_treatment not in masters.surface_treatments:
        out.append(Missing(field="surface_treatment", label="表面処理",
                           message=f"表面処理 {inputs.surface_treatment} がマスタにありません。選び直してください"))
    for i, p in enumerate(inputs.processes):
        if p.code not in masters.process_rates:
            out.append(Missing(field=f"processes.{i}", label="追加加工", message=f"追加加工 {p.code} がマスタにありません"))
        elif not p.quantity:
            name = masters.process_rates[p.code]["display_name"]
            out.append(Missing(field=f"processes.{i}", label="追加加工", message=f"追加加工「{name}」の箇所数が未入力です"))
    for i, p in enumerate(inputs.custom_processes):
        lacking = [x for x, v in (("単価（1か所あたり）", p.unit_price), ("箇所数", p.quantity)) if v is None]
        if lacking:
            out.append(Missing(field=f"custom_processes.{i}", label="追加加工（マスタ未登録）",
                               message=f"マスタにない加工「{p.name}」の{'・'.join(lacking)}が未入力です"))
    if analysis_pending:
        out.append(Missing(field="analysis", label="CADデータ", message="CADデータの解析がまだ終わっていません"))
    else:
        for name, label in SHAPE_FIELDS.items():
            if shape_detail[name]["value"] is None:
                out.append(Missing(field=f"shape.{name}", label=label, message=f"{label}が未入力です（CADデータから求められませんでした）"))
    return out


# ---------------------------------------------------------------- amounts
def custom_overlay(inputs: QuoteInputs, masters: MasterLoader) -> tuple[MasterLoader, list[AdditionalProcess], str | None]:
    """The masters with this quote's own prices, the additional processes and the finish code to price."""
    material = None
    if not inputs.material and inputs.custom_material is not None:
        material = inputs.custom_material.model_dump()
    finish = None
    finish_code = inputs.surface_treatment if inputs.surface_treatment not in (None, "", "NONE") else None
    if inputs.custom_finish is not None and inputs.surface_treatment in (None, ""):
        finish = {"name": inputs.custom_finish.name, "unit_price": inputs.custom_finish.unit_price}
        finish_code = CUSTOM_FINISH
    custom = [{"code": f"{CUSTOM_PROCESS_PREFIX}{i + 1}", "name": p.name, "unit_price": p.unit_price}
              for i, p in enumerate(inputs.custom_processes)]
    overlay = with_custom(masters, material, finish, custom)
    extra = [AdditionalProcess(process_code=p.code, quantity=p.quantity, unit=masters.process_rates[p.code]["unit"],
                               source="drawing" if p.source == "drawing" else "user") for p in inputs.processes]
    extra += [AdditionalProcess(process_code=c["code"], quantity=p.quantity, unit="piece",
                                source="drawing" if p.source == "drawing" else "user")
              for c, p in zip(custom, inputs.custom_processes)]
    return overlay, extra, finish_code


def condition_of(inputs: QuoteInputs, masters: MasterLoader) -> tuple[QuoteCondition, MasterLoader]:
    overlay, extra, finish = custom_overlay(inputs, masters)
    material = inputs.material or inputs.custom_material.name
    return QuoteCondition(material=material, quantity=inputs.quantity, surface_treatment=finish, rush=inputs.rush,
                          additional_processes=extra), overlay


def compute(inputs: QuoteInputs, masters: MasterLoader, analysis: dict | None, reading: dict | None = None,
            analysis_pending: bool = False, file_name: str = "") -> dict:
    """Everything the estimate screen shows, computed on the server. The amounts are present only when nothing
    is missing (then the quotation can be issued)."""
    effective, shape_detail = effective_analysis(analysis, inputs.shape, file_name)
    missing = missing_items(inputs, masters, shape_detail, analysis_pending)
    out: dict = {"missing": [m.model_dump() for m in missing], "issuable": not missing, "shape": shape_detail,
                 "hints": hints(inputs, analysis, reading, masters),
                 "hint_items": hint_items(inputs, analysis, reading, masters), "items": drawing_items(reading, inputs, masters, analysis),
                 "lines": [], "price": None, "condition": None}
    if missing:
        return out
    condition, overlay = condition_of(inputs, masters)
    quote = QuoteEngine(overlay).calculate(effective, condition)
    summary = price_summary(quote, condition.quantity, masters.policy("tax_rate", 0.10))
    out.update({
        "condition": condition.model_dump(mode="json"),
        "lines": [{**line.model_dump(), "group": line_group(line.code)} for line in quote.lines],
        "groups": groups(quote),
        "subtotal_cost": quote.subtotal_cost, "margin_rate": quote.margin_rate, "final_price": quote.final_price,
        "margin": quote.final_price - quote.subtotal_cost,
        "price": {"unit_price": summary.unit_price, "quantity": summary.quantity, "amount": summary.amount,
                  "subtotal": summary.subtotal, "tax_rate": summary.tax_rate, "tax": summary.tax, "total": summary.total},
    })
    return out


GROUPS = {"MATERIAL": "材料費", "LASER_CUT": "加工費", "PIERCE": "加工費", "BEND": "加工費", "SETUP": "段取り", "RUSH": "特急割増"}


def line_group(code: str) -> str:
    if code.startswith("FINISH_"):
        return "表面処理"
    return GROUPS.get(code, "追加加工")


def groups(quote) -> list[dict]:
    """The cost lines summed by group (材料費・加工費・段取り・追加加工・表面処理・特急割増) and the margin, in the
    order the screen shows them."""
    order = ["材料費", "加工費", "段取り", "追加加工", "表面処理", "特急割増"]
    sums = {name: 0.0 for name in order}
    present = set()
    for line in quote.lines:
        g = line_group(line.code)
        sums[g] += line.amount
        present.add(g)
    out = [{"name": g, "amount": sums[g]} for g in order if g in present or g in ("材料費", "加工費")]
    out.append({"name": "粗利", "amount": quote.final_price - quote.subtotal_cost, "rate": quote.margin_rate})
    return out


def priced(inputs: QuoteInputs, masters: MasterLoader, analysis: dict | None, file_name: str = ""):
    """(analysis, condition, quote result, overlay masters) for the documents; None when something is missing."""
    effective, detail = effective_analysis(analysis, inputs.shape, file_name)
    if missing_items(inputs, masters, detail, False) or effective is None:
        return None
    condition, overlay = condition_of(inputs, masters)
    return effective, condition, QuoteEngine(overlay).calculate(effective, condition), overlay


# ---------------------------------------------------------------- hints and the drawing items
def hints(inputs: QuoteInputs, analysis: dict | None, reading: dict | None, masters: MasterLoader) -> list[str]:
    """Points worth a look before issuing (they never block issuing)."""
    return [h["message"] for h in hint_items(inputs, analysis, reading, masters)]


def hint_items(inputs: QuoteInputs, analysis: dict | None, reading: dict | None, masters: MasterLoader) -> list[dict]:
    """The hints with the condition each one is about (`field`, None when nothing on the screen fixes it), so the
    screen can link each to the place to change it."""
    from src.services.platform.wording import notes, review_reason

    out = []
    review = set((reading or {}).get("needs_review") or [])
    reasons = (reading or {}).get("review_reasons") or {}
    labels = {"material": "材質", "thickness_mm": "板厚", "quantity": "数量", "surface_treatment": "表面処理",
              "processes": "追加加工", "rush": "特急"}
    for name in sorted(review):
        why = "・".join(dict.fromkeys(review_reason(r) for r in reasons.get(name) or []))
        out.append({"message": f"図面の{labels.get(name, name)}は要確認です" + (f"（{why}）" if why else ""),
                    "field": name, "label": labels.get(name, name)})
    if analysis:
        a = SheetMetalAnalysis.model_validate(analysis)
        estimated = a.status == "partial" or a.assumptions or any(
            q.confidence in {"medium", "low"} for q in a.metric_quality.values())
        if a.status in ("success", "partial") and estimated:
            why = "／".join(notes(analysis))
            kfactor = any("Kファクター" in (t or "") for t in list(a.assumptions or []))
            out.append({"message": "展開寸法は概算です" + (f"（{why}）" if why else ""),
                        "field": "k_factor" if kfactor and not inputs.k_factor_confirmed else None,
                        "label": "Kファクター（曲げの伸び）"})
        drawn = (reading or {}).get("thickness_mm")
        if drawn is not None and a.thickness_mm is not None and abs(float(drawn) - float(a.thickness_mm)) > 1e-6:
            out.append({"message": f"板厚が図面（{float(drawn):g} mm）とCADデータ（{a.thickness_mm:g} mm）で違います。"
                                   "CADデータの板厚で計算しています", "field": "shape", "label": "CADデータ"})
    flags = {"inspection": "検査成績書の指定があります", "tolerance": "厳しい公差の指定があります",
             "appearance": "外観の指定があります"}
    out += [{"message": f"図面の注記：{flags[f]}", "field": None, "label": ""}
            for f in (reading or {}).get("flags") or [] if f in flags]
    return out


def drawing_items(reading: dict | None, inputs: QuoteInputs, masters: MasterLoader, analysis: dict | None) -> list[dict]:
    """The conditions read from the drawing, one row per item, with the reading status and the value used."""
    if not reading:
        return []
    from src.pdf_quote import condition_items
    from src.services.platform.wording import review_reason

    used = {
        "material": inputs.material and masters.materials.get(inputs.material, {}).get("display_name", inputs.material)
        or (inputs.custom_material.name + "（マスタ未登録）" if inputs.custom_material else "未選択"),
        "thickness_mm": None,
        "quantity": f"{inputs.quantity} 個" if inputs.quantity else "未入力",
        "surface_treatment": (inputs.custom_finish.name + "（マスタ未登録）" if inputs.custom_finish and not inputs.surface_treatment
                              else masters.surface_treatments.get(inputs.surface_treatment or "NONE", {}).get("display_name", "なし")),
        "processes": "、".join([f"{masters.process_rates[p.code]['display_name']} ×{p.quantity:g}" if p.quantity else
                               masters.process_rates.get(p.code, {}).get("display_name", p.code)
                               for p in inputs.processes if p.code in masters.process_rates]
                              + [f"{p.name}（マスタ未登録）" for p in inputs.custom_processes]) or "なし",
        "rush": "あり" if inputs.rush else "なし",
    }
    shape_t = (analysis or {}).get("thickness_mm")
    used["thickness_mm"] = f"{shape_t:g} mm（CADデータ）" if shape_t is not None else (
        f"{inputs.shape.thickness_mm:g} mm" if inputs.shape.thickness_mm else "未入力")
    rows = []
    for item in condition_items(reading, masters):
        notice = ""
        if item.field == "thickness_mm" and item.value is not None and shape_t is not None and \
                abs(float(item.value) - float(shape_t)) > 1e-6:
            notice = f"CADデータの板厚（{shape_t:g} mm）と違います"
        rows.append({"field": item.field, "label": item.label, "read": item.display, "status": item.status,
                     "status_label": STATUS_LABEL.get(item.status, item.status), "reasons": list(dict.fromkeys(review_reason(r) for r in item.reasons)),
                     "used": used[item.field], "notice": notice})
    return rows
