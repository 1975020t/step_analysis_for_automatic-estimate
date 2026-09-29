"""Request and response models of the processing layer (also the API's OpenAPI schema).

The existing models in src/models.py (SheetMetalAnalysis, QuoteCondition, QuoteResult, ...) are used as they are.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field

from src.models import QuoteCondition, QuoteResult, SheetMetalAnalysis

ItemStatus = Literal["確定", "要確認", "未登録", "記載なし"]
ItemField = Literal["material", "thickness_mm", "quantity", "surface_treatment", "processes", "rush"]


class AdditionalProcessInput(BaseModel):
    process_code: str = Field(description="data/process_rates.csv のコード")
    quantity: float = Field(1, gt=0, description="1個あたりの個数（箇所数）")
    source: Literal["chat", "drawing", "user"] = "user"


class ConditionInput(BaseModel):
    """見積の条件（手入力）。図面の条件（drawing）があるときは、材質はその読み取りが確定でないときの仮の値。
    数量・表面処理・特急は、指定したものが図面の値に代わり確定になる（指定しなければ図面の値）。
    追加加工は図面の加工に足す。図面と同じ加工は、手入力の個数で置き換える（二重に数えない）。"""
    material: str = Field(description="data/materials.csv のコード")
    quantity: int = Field(1, gt=0)
    surface_treatment: str | None = Field(None, description="data/surface_treatments.csv のコード。None/NONE は処理なし")
    additional_processes: list[AdditionalProcessInput] = Field(default_factory=list)
    rush: bool = False


class DrawingItem(BaseModel):
    """図面から読み取った価格項目1つ。status が「確定」の項目だけが金額に入る（画面で確定にしたものも含む）。"""
    field: ItemField
    label: str = ""
    value: Any = None
    display: str = ""
    status: ItemStatus
    reasons: list[str] = Field(default_factory=list)


class DrawingContext(BaseModel):
    """図面PDFの読み取り結果のうち、見積と見積書に使うもの（読み取りの結果として返り、見積の入力に戻す）。"""
    file_name: str = ""
    drawing_no: str | None = None
    revision: str | None = None
    items: list[DrawingItem] = Field(default_factory=list)
    flags: list[str] = Field(default_factory=list, description="tolerance / appearance / inspection")
    unregistered_texts: list[str] = Field(default_factory=list, description="マスターにない加工の原文")


class AnalysisSource(BaseModel):
    """解析結果：解析の受付番号（完了していること）か、解析結果そのもの。"""
    analysis_job_id: str | None = None
    analysis: SheetMetalAnalysis | None = None


class QuoteRequest(AnalysisSource):
    condition: ConditionInput
    drawing: DrawingContext | None = None


class PriceSummaryOut(BaseModel):
    unit_price: int = Field(description="単価（最終価格÷数量、1円未満切り上げ）")
    quantity: int
    amount: int = Field(description="金額（単価×数量）")
    subtotal: int
    tax_rate: float
    tax: int = Field(description="消費税（1円未満切り捨て）")
    total: int = Field(description="合計（税込）")


class QuoteResponse(BaseModel):
    condition: QuoteCondition = Field(description="金額の計算に使った条件（確定した項目だけ）")
    quote: QuoteResult = Field(description="原価の内訳と最終価格（社内用）")
    price: PriceSummaryOut = Field(description="画面・見積書と同じ金額")
    is_estimate: bool = Field(description="解析値か条件に確認が必要な点があるか（見積書はつねに確定として出る）")
    estimate_reasons: list[str] = Field(default_factory=list, description="確認が必要な点とその扱い")


class RecipientInput(BaseModel):
    company: str = Field(min_length=1, description="宛先の会社名（必須）")
    person: str = ""


class PartInput(BaseModel):
    name: str = ""
    drawing_no: str = ""
    revision: str = ""
    revision_date: str = ""


class DocumentRequest(QuoteRequest):
    recipient: RecipientInput
    part: PartInput = Field(default_factory=PartInput)
    subject: str = ""
    delivery_place: str = ""
    remarks: str = ""
    include_internal: bool = False


class DocumentFileOut(BaseModel):
    kind: Literal["quote", "internal"]
    filename: str
    url: str


class DocumentResponse(BaseModel):
    quote_no: str
    title: str
    issued_at: str
    total: int
    files: list[DocumentFileOut]


class SimilarRequest(QuoteRequest):
    customer: str = ""
    drawing_no: str = ""
    revision: str = ""


class PastQuoteOut(BaseModel):
    quote_no: str
    date: date
    customer: str
    drawing_no: str = ""
    revision: str = ""
    part_name: str = ""
    material_text: str = ""
    material_code: str = ""
    material_family: str = ""
    thickness: float | None = None
    quantity: int | None = None
    finish_text: str = ""
    finish_code: str = ""
    processes_text: str = ""
    processes: list[dict] = Field(default_factory=list)
    rush: bool = False
    unit_price: int | None = None
    amount: int | None = None
    outcome: str = "未回答"
    staff: str = ""
    remarks: str = ""
    area: float | None = None
    cut: float | None = None
    holes: int | None = None
    bends: int | None = None
    flat_size: str = ""
    shape_class: str = ""
    source: str = ""
    original: dict = Field(default_factory=dict)


class MatchOut(BaseModel):
    category: Literal["リピート", "同じ顧客", "他の顧客"]
    score: float
    reasons: list[str]
    differences: list[str]
    price_diff: float | None = Field(None, description="(過去の単価 − 今回の単価) ÷ 今回の単価")
    past_standard: float | None = Field(None, description="過去の条件を今のマスターで計算した標準単価")
    ratio: float | None = Field(None, description="過去の単価 ÷ past_standard")
    leveled_unit: float | None = Field(None, description="今回の標準単価 × ratio（過去の出し値の水準で見た今回の単価）")
    warnings: list[str] = Field(default_factory=list)
    standard_note: str = ""
    quote: PastQuoteOut


class ReferenceOut(BaseModel):
    unit: float
    basis: str


class SimilarResponse(BaseModel):
    history_size: int
    price: PriceSummaryOut
    reference: ReferenceOut | None
    matches: list[MatchOut]


class HistoryImportResponse(BaseModel):
    read: int
    added: int
    skipped: int
    problems: list[str]


class HistoryPage(BaseModel):
    total: int
    offset: int
    limit: int
    items: list[PastQuoteOut]


class OutcomeInput(BaseModel):
    outcome: Literal["受注", "失注", "未回答"]
    customer: str | None = Field(None, description="同じ見積番号が複数の顧客にあるときに指定")


class JobOut(BaseModel):
    job_id: str
    kind: Literal["analysis", "drawing"]
    status: Literal["queued", "running", "done", "failed"]
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    error: str | None = None
    result: dict | None = Field(None, description="analysis: SheetMetalAnalysis、drawing: DrawingReadingOut")


class DrawingReadingOut(BaseModel):
    reading: dict = Field(description="読み取り器の出力（根拠の原文は除く）")
    drawing: DrawingContext


class FileOut(BaseModel):
    file_id: str
    filename: str
    kind: str
    size: int


class AnalysisJobRequest(BaseModel):
    file_id: str
    thickness_mm: float | None = Field(None, gt=0, description="DXF のとき必須（DXFには板厚がない）")
    k_factor: float = Field(0.33, ge=0, le=1)
    k_factor_confirmed: bool = Field(False, description="Kファクターが指定済みの加工条件か（未指定なら曲げ部品は概算）")
    flat_confirmed: bool = Field(False, description="DXF：曲げのない平板であることを利用者が確認済みか"
                                 "（曲げ線がない展開図は、これがないと概算。曲げ線が描き漏れている可能性があるため）")


class DrawingJobRequest(BaseModel):
    file_id: str
