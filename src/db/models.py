"""Tables of the drawing-management and estimating platform (SQLAlchemy 2.0, PostgreSQL in production, SQLite in
tests). The schema is changed only through migrations (migrations/versions/, Alembic).

Rows that people edit together (cases, quotes, drawings, partners, masters, statuses) carry a `version`: an
update that names an older version is refused (HTTP 409), so a later save never silently overwrites an earlier
one. `*_by` columns hold who did it (today the chosen staff member; a login can fill them later).
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (JSON, Boolean, Column, Date, DateTime, Float, ForeignKey, Integer, String, Table, Text,
                        UniqueConstraint)
from sqlalchemy.orm import DeclarativeBase, declared_attr, Mapped, mapped_column, relationship


def now() -> datetime:
    return datetime.now().replace(microsecond=0)


class Base(DeclarativeBase):
    type_annotation_map = {dict: JSON, list: JSON}


class Versioned:
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    @declared_attr.directive
    def __mapper_args__(cls):
        return {"version_id_col": cls.__table__.c.version}


# ---------------------------------------------------------------- settings
class Staff(Base):
    __tablename__ = "staff"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    sort: Mapped[int] = mapped_column(Integer, default=0)


class CaseStatus(Versioned, Base):
    __tablename__ = "case_statuses"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(40))
    sort: Mapped[int] = mapped_column(Integer, default=0)
    color: Mapped[str] = mapped_column(String(20), default="blue")       # light / blue / dark / grey
    # フェーズ（画面の進捗・絞り込みの単位）: drafting 見積作成中 / checking 見積確認中 / waiting 回答待ち /
    # production 製造・出荷 / closed 完了分. Held by the status so renamed or added statuses keep their phase.
    phase: Mapped[str] = mapped_column(String(20), default="drafting")
    visible: Mapped[bool] = mapped_column(Boolean, default=True)
    role: Mapped[str] = mapped_column(String(20), default="")            # "" / issued / won / lost / hold / done


class Category(Base):
    """A folder of drawings. parent_id NULL: a group (顧客別, 製品別, 部品種別 ...)."""
    __tablename__ = "categories"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id", ondelete="CASCADE"), nullable=True)
    sort: Mapped[int] = mapped_column(Integer, default=0)


class AttributeDef(Base):
    """An attribute / search item of drawings. builtin ones map to drawing columns or analysis values."""
    __tablename__ = "attribute_defs"
    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(40), unique=True)
    label: Mapped[str] = mapped_column(String(80))
    input_type: Mapped[str] = mapped_column(String(20), default="text")  # text / select / number_range / date_range
    options: Mapped[list] = mapped_column(JSON, default=list)
    unit: Mapped[str] = mapped_column(String(20), default="")
    sort: Mapped[int] = mapped_column(Integer, default=0)
    searchable: Mapped[bool] = mapped_column(Boolean, default=True)
    builtin: Mapped[bool] = mapped_column(Boolean, default=False)


class Template(Base):
    """A document template: which items are shown. Costs and margin are never printed (fixed)."""
    __tablename__ = "templates"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    customers: Mapped[list] = mapped_column(JSON, default=list)   # empty: every customer
    options: Mapped[dict] = mapped_column(JSON, default=dict)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    sort: Mapped[int] = mapped_column(Integer, default=0)


class Counter(Base):
    """Serial numbers (cases, delivery notes, invoices), taken under a row lock."""
    __tablename__ = "counters"
    name: Mapped[str] = mapped_column(String(40), primary_key=True)
    value: Mapped[int] = mapped_column(Integer, default=0)


# ---------------------------------------------------------------- masters (initial rows: data/*.csv)
class MaterialRow(Versioned, Base):
    __tablename__ = "master_materials"
    code: Mapped[str] = mapped_column("material", String(40), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(80))
    density_kg_m3: Mapped[str] = mapped_column(String(20))
    price_per_kg: Mapped[str] = mapped_column(String(20))
    waste_factor: Mapped[str] = mapped_column(String(20))
    charge_scope: Mapped[str] = mapped_column(String(20), default="per_part")
    aliases: Mapped[str] = mapped_column(Text, default="")
    sort: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class ProcessRow(Versioned, Base):
    __tablename__ = "master_processes"
    code: Mapped[str] = mapped_column("process_code", String(40), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(80))
    calculation_type: Mapped[str] = mapped_column(String(20))
    unit_price: Mapped[str] = mapped_column(String(20))
    unit: Mapped[str] = mapped_column(String(20))
    charge_scope: Mapped[str] = mapped_column(String(20), default="per_part")
    aliases: Mapped[str] = mapped_column(Text, default="")
    sort: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class FinishRow(Versioned, Base):
    __tablename__ = "master_surface_treatments"
    code: Mapped[str] = mapped_column("treatment_code", String(40), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(80))
    unit_price: Mapped[str] = mapped_column(String(20))
    charge_scope: Mapped[str] = mapped_column(String(20), default="per_part")
    aliases: Mapped[str] = mapped_column(Text, default="")
    sort: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class PolicyRow(Versioned, Base):
    __tablename__ = "master_pricing_policy"
    key: Mapped[str] = mapped_column(String(40), primary_key=True)
    value: Mapped[str] = mapped_column(String(40))
    description: Mapped[str] = mapped_column(Text, default="")
    sort: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class CompanyRow(Versioned, Base):
    __tablename__ = "company"
    key: Mapped[str] = mapped_column(String(40), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
    description: Mapped[str] = mapped_column(Text, default="")
    sort: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now)


# ---------------------------------------------------------------- drawings
drawing_categories = Table(
    "drawing_categories", Base.metadata,
    Column("drawing_id", ForeignKey("drawings.id", ondelete="CASCADE"), primary_key=True),
    Column("category_id", ForeignKey("categories.id", ondelete="CASCADE"), primary_key=True),
)


class Drawing(Versioned, Base):
    __tablename__ = "drawings"
    id: Mapped[int] = mapped_column(primary_key=True)
    drawing_no: Mapped[str] = mapped_column(String(80), index=True)
    name: Mapped[str] = mapped_column(String(120), default="")
    customer: Mapped[str] = mapped_column(String(120), default="")
    current_revision_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    attrs: Mapped[dict] = mapped_column(JSON, default=dict)          # custom attributes (AttributeDef.key -> value)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    created_by: Mapped[str] = mapped_column(String(80), default="")
    revisions: Mapped[list["DrawingRevision"]] = relationship(back_populates="drawing", cascade="all, delete-orphan",
                                                              order_by="DrawingRevision.id")
    categories: Mapped[list[Category]] = relationship(secondary=drawing_categories)


class DrawingRevision(Base):
    """One version of a drawing: a drawing PDF and/or a shape file (STEP / DXF), their analysis and reading."""
    __tablename__ = "drawing_revisions"
    id: Mapped[int] = mapped_column(primary_key=True)
    drawing_id: Mapped[int] = mapped_column(ForeignKey("drawings.id", ondelete="CASCADE"), index=True)
    revision: Mapped[str] = mapped_column(String(20), default="")
    pdf_file_id: Mapped[str] = mapped_column(String(40), default="")
    pdf_name: Mapped[str] = mapped_column(String(160), default="")
    shape_file_id: Mapped[str] = mapped_column(String(40), default="")
    shape_name: Mapped[str] = mapped_column(String(160), default="")
    shape_kind: Mapped[str] = mapped_column(String(10), default="")      # step / dxf / ""
    thickness_mm: Mapped[float | None] = mapped_column(Float, nullable=True)  # DXF: the given thickness
    material: Mapped[str] = mapped_column(String(80), default="")        # master code, or the written text
    surface_treatment: Mapped[str] = mapped_column(String(80), default="")
    analysis: Mapped[dict | None] = mapped_column(JSON, nullable=True)   # SheetMetalAnalysis (model_dump)
    analysis_job_id: Mapped[str] = mapped_column(String(40), default="")
    reading: Mapped[dict | None] = mapped_column(JSON, nullable=True)    # {"reading": ..., "drawing": DrawingContext}
    reading_job_id: Mapped[str] = mapped_column(String(40), default="")
    max_dimension_mm: Mapped[float | None] = mapped_column(Float, nullable=True)
    pdf_text: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    created_by: Mapped[str] = mapped_column(String(80), default="")
    drawing: Mapped[Drawing] = relationship(back_populates="revisions")


class DrawingNote(Base):
    """A note written on the drawing at a position (x, y: 0..1 of the preview)."""
    __tablename__ = "drawing_notes"
    id: Mapped[int] = mapped_column(primary_key=True)
    revision_id: Mapped[int] = mapped_column(ForeignKey("drawing_revisions.id", ondelete="CASCADE"), index=True)
    x: Mapped[float] = mapped_column(Float)
    y: Mapped[float] = mapped_column(Float)
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    created_by: Mapped[str] = mapped_column(String(80), default="")


class DrawingMemo(Base):
    """注意 / 不具合 memo of a drawing."""
    __tablename__ = "drawing_memos"
    id: Mapped[int] = mapped_column(primary_key=True)
    drawing_id: Mapped[int] = mapped_column(ForeignKey("drawings.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(20), default="注意")
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    created_by: Mapped[str] = mapped_column(String(80), default="")


# ---------------------------------------------------------------- cases and quotes
class Case(Versioned, Base):
    __tablename__ = "cases"
    id: Mapped[int] = mapped_column(primary_key=True)
    number: Mapped[str] = mapped_column(String(40), unique=True)
    customer: Mapped[str] = mapped_column(String(120))
    title: Mapped[str] = mapped_column(String(160), default="")
    status_id: Mapped[int] = mapped_column(ForeignKey("case_statuses.id"))
    staff: Mapped[str] = mapped_column(String(80), default="")
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)       # 希望納期 (never changes the price)
    outcome: Mapped[str] = mapped_column(String(10), default="未回答")      # 受注 / 失注 / 未回答
    lost_reason: Mapped[str] = mapped_column(String(40), default="")
    competitor_price: Mapped[int | None] = mapped_column(Integer, nullable=True)
    lost_note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    status_changed_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    first_issued_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_by: Mapped[str] = mapped_column(String(80), default="")
    status: Mapped[CaseStatus] = relationship()
    quotes: Mapped[list["Quote"]] = relationship(back_populates="case", order_by="Quote.id")


class CaseStatusLog(Base):
    """Status changes, with the status name as it was then (renaming a status keeps the old name here)."""
    __tablename__ = "case_status_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"), index=True)
    status_name: Mapped[str] = mapped_column(String(40))
    at: Mapped[datetime] = mapped_column(DateTime, default=now)
    actor: Mapped[str] = mapped_column(String(80), default="")


class Quote(Versioned, Base):
    """The estimate of one drawing revision: its inputs, the analysis used and the last computed result."""
    __tablename__ = "quotes"
    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"), index=True)
    number: Mapped[str] = mapped_column(String(40), unique=True)
    drawing_id: Mapped[int | None] = mapped_column(ForeignKey("drawings.id"), nullable=True, index=True)
    revision_id: Mapped[int | None] = mapped_column(ForeignKey("drawing_revisions.id"), nullable=True)
    inputs: Mapped[dict] = mapped_column(JSON, default=dict)      # see src/services/platform/quotes.py
    analysis: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    reading: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # the revision's reading used as draft
    job_id: Mapped[str] = mapped_column(String(40), default="")
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    chat: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_by: Mapped[str] = mapped_column(String(80), default="")
    case: Mapped[Case] = relationship(back_populates="quotes")


class QuoteEditLog(Base):
    __tablename__ = "quote_edit_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    quote_id: Mapped[int] = mapped_column(ForeignKey("quotes.id", ondelete="CASCADE"), index=True)
    at: Mapped[datetime] = mapped_column(DateTime, default=now)
    actor: Mapped[str] = mapped_column(String(80), default="")
    summary: Mapped[str] = mapped_column(Text, default="")
    total_before: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_after: Mapped[int | None] = mapped_column(Integer, nullable=True)


class IssuedDocument(Base):
    """A document issued from a quote: 見積書 / 納品書 / 請求書, with the amounts printed on it."""
    __tablename__ = "issued_documents"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(20))                 # quote / delivery / invoice
    number: Mapped[str] = mapped_column(String(40), unique=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), index=True)
    quote_id: Mapped[int] = mapped_column(ForeignKey("quotes.id"), index=True)
    template_id: Mapped[int | None] = mapped_column(ForeignKey("templates.id"), nullable=True)
    issued_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    issued_by: Mapped[str] = mapped_column(String(80), default="")
    customer: Mapped[str] = mapped_column(String(120), default="")
    part_name: Mapped[str] = mapped_column(String(160), default="")
    drawing_no: Mapped[str] = mapped_column(String(80), default="")
    quantity: Mapped[int] = mapped_column(Integer)
    unit_price: Mapped[int] = mapped_column(Integer)
    subtotal: Mapped[int] = mapped_column(Integer)
    tax_rate: Mapped[float] = mapped_column(Float)
    tax: Mapped[int] = mapped_column(Integer)
    total: Mapped[int] = mapped_column(Integer)
    file_id: Mapped[str] = mapped_column(String(40))
    filename: Mapped[str] = mapped_column(String(200))
    snapshot: Mapped[dict] = mapped_column(JSON, default=dict)


# ---------------------------------------------------------------- history (initial rows: data/past_quotes/history.csv)
class PastQuoteRow(Base):
    __tablename__ = "past_quotes"
    __table_args__ = (UniqueConstraint("quote_no", "date", "customer", name="uq_past_quote"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    quote_no: Mapped[str] = mapped_column(String(80), index=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    customer: Mapped[str] = mapped_column(String(120))
    drawing_no: Mapped[str] = mapped_column(String(80), default="")
    revision: Mapped[str] = mapped_column(String(20), default="")
    part_name: Mapped[str] = mapped_column(String(160), default="")
    material_text: Mapped[str] = mapped_column(String(80), default="")
    material_code: Mapped[str] = mapped_column(String(80), default="")
    material_family: Mapped[str] = mapped_column(String(20), default="")
    thickness: Mapped[float | None] = mapped_column(Float, nullable=True)
    quantity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    finish_text: Mapped[str] = mapped_column(String(80), default="")
    finish_code: Mapped[str] = mapped_column(String(80), default="")
    processes_text: Mapped[str] = mapped_column(Text, default="")
    processes: Mapped[list] = mapped_column(JSON, default=list)
    rush: Mapped[bool] = mapped_column(Boolean, default=False)
    unit_price: Mapped[int | None] = mapped_column(Integer, nullable=True)
    amount: Mapped[int | None] = mapped_column(Integer, nullable=True)
    outcome: Mapped[str] = mapped_column(String(10), default="未回答")
    staff: Mapped[str] = mapped_column(String(80), default="")
    remarks: Mapped[str] = mapped_column(Text, default="")
    area: Mapped[float | None] = mapped_column(Float, nullable=True)
    cut: Mapped[float | None] = mapped_column(Float, nullable=True)
    holes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bends: Mapped[int | None] = mapped_column(Integer, nullable=True)
    flat_size: Mapped[str] = mapped_column(String(40), default="")
    shape_class: Mapped[str] = mapped_column(String(80), default="")
    source: Mapped[str] = mapped_column(String(120), default="")
    original: Mapped[dict] = mapped_column(JSON, default=dict)
    case_id: Mapped[int | None] = mapped_column(ForeignKey("cases.id"), nullable=True, index=True)
    drawing_id: Mapped[int | None] = mapped_column(ForeignKey("drawings.id"), nullable=True, index=True)
    lost_reason: Mapped[str] = mapped_column(String(40), default="")
    answer_days: Mapped[float | None] = mapped_column(Float, nullable=True)


# ---------------------------------------------------------------- partners and documents
class Partner(Versioned, Base):
    __tablename__ = "partners"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(20), default="協力会社")   # 協力会社 / 顧客
    name: Mapped[str] = mapped_column(String(120))
    category: Mapped[str] = mapped_column(String(40), default="")       # 区分 (表面処理, 熱処理, 材料, 業種 ...)
    specialties: Mapped[str] = mapped_column(Text, default="")
    price_records: Mapped[str] = mapped_column(Text, default="")
    avg_lead_days: Mapped[float | None] = mapped_column(Float, nullable=True)
    deal_count: Mapped[int] = mapped_column(Integer, default=0)
    rating: Mapped[str] = mapped_column(String(10), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class LibraryDocument(Base):
    """A registered document (PDF / Excel) for the keyword search: kind and related drawings set by a person."""
    __tablename__ = "documents"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(20))
    title: Mapped[str] = mapped_column(String(200))
    file_id: Mapped[str] = mapped_column(String(40))
    filename: Mapped[str] = mapped_column(String(200))
    file_type: Mapped[str] = mapped_column(String(10))   # PDF / Excel
    text: Mapped[str] = mapped_column(Text, default="")
    drawing_ids: Mapped[list] = mapped_column(JSON, default=list)
    customer: Mapped[str] = mapped_column(String(120), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    created_by: Mapped[str] = mapped_column(String(80), default="")
