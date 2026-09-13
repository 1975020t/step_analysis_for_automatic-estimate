from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class AnalysisStage(BaseModel):
    name: str
    status: Literal["success", "failed", "skipped"]
    message: str


class SurfacePairEvidence(BaseModel):
    kind: Literal["planar", "cylindrical"]
    face_indices: tuple[int, int]
    mid_surface_area_mm2: float


class BendEvidence(BaseModel):
    bend_id: int
    axis_point_mm: tuple[float, float, float]
    axis_direction: tuple[float, float, float]
    inner_radius_mm: float
    outer_radius_mm: float
    face_indices: list[int] = Field(default_factory=list)


class HoleEvidence(BaseModel):
    hole_id: int
    face_indices: list[int] = Field(default_factory=list)
    cut_surface_area_mm2: float


class FlatPatternSummary(BaseModel):
    """展開中立面の見積用計量表現。"""

    method: str
    area_mm2: float
    cut_length_mm: float
    boundary_count: int
    outer_boundary_count: int
    inner_boundary_count: int
    surface_region_count: int


class SheetMetalAnalysis(BaseModel):
    status: Literal["success", "unsupported", "error"]
    file_name: str
    thickness_mm: float | None = None
    blank_area_mm2: float | None = None
    cut_length_mm: float | None = None
    hole_count: int | None = None
    bend_count: int | None = None
    reason_code: str | None = None
    message: str | None = None
    flat_pattern: FlatPatternSummary | None = None
    stages: list[AnalysisStage] = Field(default_factory=list)
    thickness_evidence: list[SurfacePairEvidence] = Field(default_factory=list)
    bend_evidence: list[BendEvidence] = Field(default_factory=list)
    hole_evidence: list[HoleEvidence] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class CadFeatures(BaseModel):
    file_name: str = ""
    solid_count: int = 1
    bbox_x_mm: float
    bbox_y_mm: float
    bbox_z_mm: float
    volume_mm3: float
    surface_area_mm2: float
    face_count: int
    edge_count: int
    vertex_count: int
    estimated_hole_count: int = 0
    estimated_hole_diameters_mm: list[float] = Field(default_factory=list)


class AdditionalProcess(BaseModel):
    process_code: str
    quantity: float = Field(default=1, gt=0)
    unit: str = "job"
    user_text: str | None = None
    confirmed: bool = True


class QuoteCondition(BaseModel):
    material: str
    quantity: int = Field(default=1, gt=0)
    additional_processes: list[AdditionalProcess] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class QuoteLine(BaseModel):
    code: str
    name: str
    quantity: float
    unit_price: float | None
    amount: float
    source: Literal["cad", "master", "chat", "user"]
    unit: str = ""


class QuoteResult(BaseModel):
    lines: list[QuoteLine]
    subtotal_cost: float
    margin_rate: float
    final_price: float
    rounded_final_price: int
    estimated_minutes: float


class QuoteOperation(BaseModel):
    action: Literal["add", "update", "remove"]
    field: Literal["process", "material", "quantity"] = "process"
    process_code: str | None = None
    quantity: float | None = None
    unit: str | None = None
    value: str | float | int | None = None


class ChatInterpretation(BaseModel):
    status: Literal["ready", "needs_confirmation", "unknown"]
    operations: list[QuoteOperation] = Field(default_factory=list)
    confirmation_message: str | None = None


class ChatApplyResult(BaseModel):
    condition: QuoteCondition
    interpretation: ChatInterpretation
    message: str
