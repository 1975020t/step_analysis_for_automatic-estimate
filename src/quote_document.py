"""Quotation document (見積書): content and amounts, rules and masters only (no LLM).

Amounts (the screen shows the same values, from `price_summary`):
  unit price = QuoteEngine final price / quantity, rounded UP to the yen
  amount     = unit price x quantity;  subtotal = sum of the amounts
  tax        = subtotal x tax_rate, rounded DOWN to the yen;  total = subtotal + tax

The external document never shows the cost lines or the margin; those go to the internal basis
(見積根拠（社内用）), which is a separate PDF. Rendering is in `src/quote_pdf.py`.
"""
from __future__ import annotations

import csv
import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

from src.master_loader import UNREGISTERED, MasterLoader
from src.models import QuoteCondition, QuoteResult, SheetMetalAnalysis
from src.pdf_quote import CONFIRMED, MISSING, REVIEW, UNREG, ConditionItem

FLAG_REMARKS = {
    "inspection": "検査成績書・検査記録の提出は別途お見積りとなります。",
    "tolerance": "図面の厳しい公差指定への対応は別途ご相談とさせていただきます。",
    "appearance": "外観指定（キズ不可など）への対応は別途ご相談とさせていただきます。",
}
COUNTED_UNITS = {"hole", "point", "location", "piece", "bend"}


# ---------------------------------------------------------------- amounts
def _money(value: float) -> float:
    return round(value, 6)  # drop float noise before rounding to yen (7776.0000001 is 7776)


def unit_price(final_price: float, quantity: int) -> int:
    return int(math.ceil(_money(final_price / quantity)))


def tax_amount(subtotal: int, tax_rate: float) -> int:
    return int(math.floor(_money(subtotal * tax_rate)))


@dataclass
class PriceSummary:
    unit_price: int
    quantity: int
    amount: int
    subtotal: int
    tax_rate: float
    tax: int
    total: int


def price_summary(quote: QuoteResult, quantity: int, tax_rate: float) -> PriceSummary:
    """The one-line amounts shared by the screen and the document."""
    price = unit_price(quote.final_price, quantity)
    amount = price * quantity
    tax = tax_amount(amount, tax_rate)
    return PriceSummary(price, quantity, amount, amount, tax_rate, tax, amount + tax)


# ---------------------------------------------------------------- company
@dataclass
class Company:
    name: str
    postal_code: str = ""
    address: str = ""
    tel: str = ""
    fax: str = ""
    registration_no: str = ""
    contact: str = ""
    payment_terms: str = ""
    delivery_place: str = "貴社指定場所"
    remarks: list[str] = field(default_factory=list)
    source: str = ""


def load_company(data_dir: str | Path = "data") -> Company:
    """data/company.local.csv (real details, not in Git) when present, else data/company.csv (placeholders)."""
    data_dir = Path(data_dir)
    path = data_dir / "company.local.csv"
    if not path.exists():
        path = data_dir / "company.csv"
    with path.open(encoding="utf-8-sig", newline="") as handle:
        values = {row["key"].strip(): (row.get("value") or "").strip() for row in csv.DictReader(handle) if row.get("key")}
    remark_keys = sorted((k for k in values if k.startswith("remark_")), key=lambda k: int(k[7:]) if k[7:].isdigit() else 0)
    return Company(
        name=values.get("name", ""), postal_code=values.get("postal_code", ""), address=values.get("address", ""),
        tel=values.get("tel", ""), fax=values.get("fax", ""), registration_no=values.get("registration_no", ""),
        contact=values.get("contact", ""), payment_terms=values.get("payment_terms", ""),
        delivery_place=values.get("delivery_place") or "貴社指定場所",
        remarks=[values[k] for k in remark_keys if values[k]], source=path.name)


# ---------------------------------------------------------------- document
@dataclass
class DocumentLine:
    name: str
    drawing_no: str = ""
    revision: str = ""
    spec: list[str] = field(default_factory=list)  # 2 lines: material / thickness / finish, then the processes
    quantity: int = 1
    unit: str = "個"
    unit_price: int = 0
    amount: int = 0


@dataclass
class Recipient:
    company: str
    person: str = ""  # department and name, printed with 様


@dataclass
class PartInfo:
    name: str = ""
    drawing_no: str = ""
    revision: str = ""
    revision_date: str = ""
    shape_file: str = ""
    drawing_file: str = ""


@dataclass
class InternalBasis:
    """見積根拠（社内用）: cost lines, margin, analysis values and the drawing conditions."""
    quote: QuoteResult
    analysis: SheetMetalAnalysis
    condition: QuoteCondition
    items: list[ConditionItem] = field(default_factory=list)


@dataclass
class QuoteDocument:
    number: str
    issued_at: datetime
    valid_until: date
    validity_days: int
    recipient: Recipient
    subject: str
    lead_time_days: int
    delivery_place: str
    payment_terms: str
    company: Company
    lines: list[DocumentLine]
    tax_rate: float
    is_estimate: bool
    pending: list[str]   # 概算: every unsettled condition and how the amount treats it
    remarks: list[str]
    part: PartInfo
    internal: InternalBasis | None = None

    @property
    def title(self) -> str:
        return "概算御見積書" if self.is_estimate else "御見積書"

    @property
    def subtotal(self) -> int:
        return sum(line.amount for line in self.lines)

    @property
    def tax(self) -> int:
        return tax_amount(self.subtotal, self.tax_rate)

    @property
    def total(self) -> int:
        return self.subtotal + self.tax


def default_subject(part: PartInfo) -> str:
    head = " ".join(x for x in (part.drawing_no, part.name) if x)
    return f"{head or Path(part.shape_file).stem or '板金部品'} 製作"


def spec_lines(analysis: SheetMetalAnalysis, condition: QuoteCondition, masters: MasterLoader,
               finish_pending: bool) -> list[str]:
    first = [condition.material]
    if analysis.thickness_mm is not None:
        first.append(f"t{analysis.thickness_mm:g}")
    line1 = " ".join(first)
    if finish_pending:
        line1 += "　表面処理：未定"
    elif condition.surface_treatment and condition.surface_treatment != "NONE":
        line1 += "　" + masters.surface_treatment(condition.surface_treatment)["display_name"]
    work = ["レーザー切断"]
    if analysis.bend_count:
        work.append(f"曲げ{analysis.bend_count}箇所")
    for process in condition.additional_processes:
        if not process.confirmed or process.process_code not in masters.process_rates:
            continue
        row = masters.process_rates[process.process_code]
        work.append(f"{row['display_name']}{process.quantity:g}箇所" if row["unit"] in COUNTED_UNITS else row["display_name"])
    return [line1, "・".join(work)]


def pending_notes(analysis: SheetMetalAnalysis, condition: QuoteCondition, quote: QuoteResult,
                  items: list[ConditionItem] | None, unregistered_texts: list[str] | None = None) -> list[str]:
    """Every condition that keeps the quote an estimate, with how the amount treats it."""
    notes: list[str] = []
    for item in items or []:
        if item.status == CONFIRMED:
            continue
        notes += _item_notes(item, analysis, condition, unregistered_texts or [])
    notes += [f"{p}（形状データの板厚で計算しています）" for p in condition.pending if p.startswith("板厚：図面")]
    if not items:
        notes += [p for p in condition.pending if not p.startswith("板厚：図面")]
    shape = analysis.status == "partial" or analysis.assumptions or any(
        q.confidence in {"medium", "low"} for q in analysis.metric_quality.values())
    if shape:
        reasons = list(analysis.assumptions) or list(analysis.warnings) or ["一部の解析値が概算"]
        notes.append("形状解析：" + "／".join(reasons) + "（概算の解析値で計算しています）")
    if quote.is_estimate and not notes:
        notes.append("見積条件の一部が未確定です")
    return notes


def _item_notes(item: ConditionItem, analysis: SheetMetalAnalysis, condition: QuoteCondition,
                unregistered_texts: list[str]) -> list[str]:
    label = item.label
    handling = {
        "material": f"材質 {condition.material} で仮計算しています",
        "thickness_mm": (f"形状データの板厚 t{analysis.thickness_mm:g} で計算しています"
                         if analysis.thickness_mm is not None else "形状データの板厚で計算しています"),
        "quantity": f"数量{condition.quantity}で仮計算しています",
        "surface_treatment": "金額に含めていません",
        "processes": "金額に含めていません",
        "rush": "特急割増を含めていません",
    }[item.field]
    if item.status == MISSING:
        return [f"{label}：図面に記載なし（{handling}）"]
    if item.field == "processes":
        notes = []
        if item.status == REVIEW:
            notes.append(f"{label}：{item.display}は読み取り値の確認が必要（確認が済むまで{handling}）")
        unregistered = sum(1 for p in item.value or [] if p.get("code") == UNREGISTERED)
        texts = list(unregistered_texts)[:unregistered] if unregistered else []
        for text in texts:
            notes.append(f"{label}：{text}（マスター未登録のため別途見積）")
        if unregistered > len(texts):
            notes.append(f"{label}：マスター未登録の加工 {unregistered - len(texts)}件（別途見積）")
        return notes
    if item.status == UNREG:
        if item.field == "surface_treatment":
            return [f"{label}：マスター未登録の表面処理（別途見積）"]
        return [f"{label}：マスター未登録（{handling}）"]
    return [f"{label}：{item.display}は読み取り値の確認が必要（{handling}）"]


def basis_remark(part: PartInfo) -> str:
    sources = []
    if part.drawing_no or part.drawing_file:
        drawing = f"図面 {part.drawing_no or part.drawing_file}"
        if part.revision:
            drawing += f" Rev.{part.revision}"
        if part.revision_date:
            drawing += f"（{part.revision_date}）"
        sources.append(drawing)
    if part.shape_file:
        kind = "展開図データ" if part.shape_file.lower().endswith(".dxf") else "3Dデータ"
        sources.append(f"{kind} {part.shape_file}")
    return f"本見積は{'および'.join(sources)} に基づきます。" if sources else ""


def build_document(*, analysis: SheetMetalAnalysis, condition: QuoteCondition, quote: QuoteResult,
                   masters: MasterLoader, company: Company, recipient: Recipient, part: PartInfo,
                   issued_at: datetime, number: str = "", subject: str = "", delivery_place: str = "",
                   free_remarks: str = "", items: list[ConditionItem] | None = None, flags: list[str] | None = None,
                   unregistered_texts: list[str] | None = None) -> QuoteDocument:
    if not recipient.company.strip():
        raise ValueError("宛先の会社名を入力してください。")
    tax_rate = masters.policy("tax_rate", 0.10)
    validity = int(masters.policy("quote_validity_days", 30))
    lead = int(masters.policy("lead_time_days_rush" if condition.rush else "lead_time_days_normal", 10))
    summary = price_summary(quote, condition.quantity, tax_rate)
    finish_pending = any(i.field == "surface_treatment" and i.status != CONFIRMED for i in items or [])
    line = DocumentLine(
        name=part.name or Path(part.shape_file).stem, drawing_no=part.drawing_no, revision=part.revision,
        spec=spec_lines(analysis, condition, masters, finish_pending), quantity=condition.quantity, unit="個",
        unit_price=summary.unit_price, amount=summary.amount)
    pending = pending_notes(analysis, condition, quote, items, unregistered_texts) if quote.is_estimate else []
    remarks = [r for r in [basis_remark(part)] if r] + list(company.remarks)
    if condition.rush:
        remarks.append("特急対応（特急割増を含みます）。")
    remarks += [FLAG_REMARKS[f] for f in flags or [] if f in FLAG_REMARKS]
    remarks += [line.strip() for line in free_remarks.splitlines() if line.strip()]
    return QuoteDocument(
        number=number, issued_at=issued_at, valid_until=issued_at.date() + timedelta(days=validity),
        validity_days=validity, recipient=Recipient(recipient.company.strip(), recipient.person.strip()),
        subject=subject.strip() or default_subject(part), lead_time_days=lead,
        delivery_place=delivery_place.strip() or company.delivery_place, payment_terms=company.payment_terms,
        company=company, lines=[line], tax_rate=tax_rate, is_estimate=quote.is_estimate, pending=pending,
        remarks=remarks, part=part, internal=InternalBasis(quote, analysis, condition, list(items or [])))


def unregistered_process_texts(evidence: list[dict] | None, masters: MasterLoader) -> list[str]:
    """Source text of the process callouts the rules could not map to the master (for the 概算 remarks)."""
    if not evidence:
        return []
    from src.pdf_terms import Terms

    terms = Terms(masters)
    texts: list[str] = []
    for raw in evidence:
        for item in (raw or {}).get("processes") or []:
            text = (item.get("text") or "").strip()
            parsed = terms.process(text)
            unregistered = (any(code == UNREGISTERED for code, _ in parsed) if parsed
                            else parsed is None and item.get("code") == UNREGISTERED)
            if unregistered and text and text not in texts:
                texts.append(text)
    return texts


def log_row(document: QuoteDocument) -> dict[str, object]:
    files = [f for f in (document.part.shape_file, document.part.drawing_file) if f]
    return {"customer": document.recipient.company, "subject": document.subject,
            "drawing_no": document.part.drawing_no, "quantity": sum(line.quantity for line in document.lines),
            "total": document.total, "is_estimate": int(document.is_estimate), "input_files": " | ".join(files)}
