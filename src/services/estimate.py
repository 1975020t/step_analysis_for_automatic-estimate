"""Operations of the estimating system, shared by the API and the Streamlit demo (no web framework here).

    service = EstimateService(Settings.from_env())
    analysis = service.analyze_bytes(data, "part.step")
    outcome = service.quote(analysis, ConditionInput(material="SPCC", quantity=100))
    issued = service.issue_document(analysis, condition_input, recipient=..., part=...)

Money is computed only by QuoteEngine and the rounding rules of src/quote_document.py (the LLM never computes it).
"""
from __future__ import annotations

import io
import os
import threading
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Callable

from src.master_loader import MasterLoader
from src.models import AdditionalProcess, QuoteCondition, QuoteResult, SheetMetalAnalysis
from src.past_quotes import HistoryStore, PastQuote, from_document, import_csv
from src.pdf_quote import ConditionItem, condition_items, quote_condition
from src.quote_document import (PartInfo, PriceSummary, QuoteDocument, Recipient, build_document, load_company, log_row,
                                pending_notes, price_summary, unregistered_process_texts)
from src.quote_engine import QuoteEngine
from src.quote_log import QuoteLog
from src.quote_pdf import render_internal, render_quote
from src.services.jobs import JobError, JobQueue, JobStore
from src.services.schemas import (ConditionInput, DrawingContext, DrawingItem, MatchOut, PastQuoteOut, PriceSummaryOut,
                                  ReferenceOut)
from src.services.settings import Settings
from src.services.storage import LocalFileStore
from src.similar_quotes import Match, Reference, SimilarQuoteSearch, query_for

ReaderFactory = Callable[[], object]  # returns an object with .read(path) -> dict (src.pdf_reader.PdfConditionReader)


class ServiceError(Exception):
    """An input problem whose message is safe to show (HTTP 400/404/409/503 in the API)."""

    def __init__(self, message: str, status: int = 400, code: str = "BAD_REQUEST") -> None:
        super().__init__(message)
        self.status, self.code = status, code


@dataclass
class QuoteOutcome:
    condition: QuoteCondition
    quote: QuoteResult
    summary: PriceSummary
    reasons: list[str]

    @property
    def is_estimate(self) -> bool:
        return self.quote.is_estimate

    def price_out(self) -> PriceSummaryOut:
        return PriceSummaryOut(**asdict(self.summary))


@dataclass
class IssuedDocument:
    document: QuoteDocument
    files: dict[str, tuple[str, bytes]]  # kind ("quote" | "internal") -> (file name, PDF)


def default_reader_factory():
    from src.pdf_reader import PdfConditionReader

    return PdfConditionReader()


def llm_key_configured() -> bool:
    return bool(os.getenv("ANALYSIS_ANTHROPIC_API_KEY", "").strip() or os.getenv("ANTHROPIC_API_KEY", "").strip())


_INDEX_LOCK = threading.Lock()
_INDEX_CACHE: dict[str, tuple[float, SimilarQuoteSearch]] = {}


class EstimateService:
    def __init__(self, settings: Settings | None = None, reader_factory: ReaderFactory | None = None,
                 reader_available: Callable[[], bool] | None = None) -> None:
        self.settings = settings or Settings.from_env()
        self.masters = MasterLoader(self.settings.data_dir)
        self.files = LocalFileStore(self.settings.uploads_dir)
        self.job_store = JobStore(self.settings.jobs_dir)
        self.reader_factory = reader_factory or default_reader_factory
        self._reader_available = reader_available or (llm_key_configured if reader_factory is None else (lambda: True))

    # ------------------------------------------------------------ masters
    def masters_payload(self) -> dict:
        company = load_company(self.settings.data_dir)
        return {
            "materials": list(self.masters.materials.values()),
            "processes": list(self.masters.process_rates.values()),
            "surface_treatments": list(self.masters.surface_treatments.values()),
            "pricing_policy": dict(self.masters.pricing_policy),
            "company": {k: v for k, v in asdict(company).items() if k != "source"},
        }

    # ------------------------------------------------------------ shape analysis
    @staticmethod
    def analyze_bytes(data: bytes, file_name: str, thickness_mm: float | None = None, k_factor: float = 0.33,
                      k_factor_confirmed: bool = False, flat_confirmed: bool = False) -> SheetMetalAnalysis:
        """STEP (k factor) or flat-pattern DXF (thickness required) -> SheetMetalAnalysis."""
        if file_name.lower().endswith(".dxf"):
            if not thickness_mm or thickness_mm <= 0:
                raise ServiceError("展開図DXFの解析には板厚（mm）が必要です。", code="THICKNESS_REQUIRED")
            from src.dxf_analyzer import DxfAnalyzer

            return DxfAnalyzer(thickness_mm=thickness_mm, flat_confirmed=flat_confirmed).analyze(
                io.BytesIO(data), file_name=file_name)
        from src.sheetmetal_analyzer import SheetMetalAnalyzer

        return SheetMetalAnalyzer(k_factor=k_factor, k_factor_is_default=not k_factor_confirmed).analyze(
            io.BytesIO(data), file_name=file_name)

    def run_analysis_job(self, payload: dict) -> dict:
        try:
            data, meta = self.files.get(payload["file_id"])
        except KeyError:
            raise JobError("アップロードしたファイルが見つかりません。") from None
        try:
            analysis = self.analyze_bytes(data, meta["filename"], payload.get("thickness_mm"),
                                          payload.get("k_factor", 0.33), payload.get("k_factor_confirmed", False),
                                          payload.get("flat_confirmed", False))
        except ServiceError as exc:
            raise JobError(str(exc)) from None
        return analysis.model_dump(mode="json")

    # ------------------------------------------------------------ drawing PDF
    def drawing_reader_available(self) -> bool:
        return self._reader_available()

    def read_drawing(self, path: str | Path, file_name: str = "") -> tuple[dict, DrawingContext]:
        """Read the conditions of a drawing PDF with the existing reader (LLM); the rules decide codes."""
        if not self.drawing_reader_available():
            raise ServiceError("図面PDFの読み取りには Claude APIキー（ANALYSIS_ANTHROPIC_API_KEY）の設定が必要です。"
                               "条件は手入力してください。", status=503, code="DRAWING_READER_UNAVAILABLE")
        reading = dict(self.reader_factory().read(str(path)))
        evidence = reading.pop("evidence", None)
        return reading, self.drawing_context(reading, file_name, unregistered_process_texts(evidence, self.masters))

    def drawing_context(self, reading: dict, file_name: str = "", unregistered: list[str] | None = None) -> DrawingContext:
        items = [DrawingItem(field=i.field, label=i.label, value=i.value, display=i.display, status=i.status,
                             reasons=list(i.reasons)) for i in condition_items(reading, self.masters)]
        return DrawingContext(file_name=file_name, drawing_no=reading.get("drawing_no"), revision=reading.get("revision"),
                              items=items, flags=list(reading.get("flags") or []), unregistered_texts=unregistered or [])

    def run_drawing_job(self, payload: dict) -> dict:
        try:
            meta = self.files.meta(payload["file_id"])
        except KeyError:
            raise JobError("アップロードしたファイルが見つかりません。") from None
        try:
            reading, drawing = self.read_drawing(self.files.path(payload["file_id"]), meta["filename"])
        except ServiceError as exc:
            raise JobError(str(exc)) from None
        return {"reading": reading, "drawing": drawing.model_dump(mode="json")}

    # ------------------------------------------------------------ jobs
    def job_queue(self, autostart: bool = True) -> JobQueue:
        return JobQueue(self.job_store, {"analysis": self.run_analysis_job, "drawing": self.run_drawing_job},
                        workers=self.settings.job_workers, autostart=autostart)

    def analysis_from_job(self, job_id: str) -> SheetMetalAnalysis:
        try:
            job = self.job_store.read(job_id)
        except KeyError:
            raise ServiceError("解析の受付番号が見つかりません。", 404, "NOT_FOUND") from None
        if job["kind"] != "analysis":
            raise ServiceError("解析の受付番号ではありません。")
        if job["status"] != "done":
            raise ServiceError(f"解析がまだ完了していません（状態: {job['status']}）。", 409, "NOT_READY")
        return SheetMetalAnalysis.model_validate(job["result"])

    # ------------------------------------------------------------ quote
    @staticmethod
    def condition_items(drawing: DrawingContext | None) -> list[ConditionItem] | None:
        if drawing is None:
            return None
        return [ConditionItem(i.field, i.label, i.value, i.display, i.status, list(i.reasons)) for i in drawing.items]

    def build_condition(self, analysis: SheetMetalAnalysis, condition: ConditionInput,
                        drawing: DrawingContext | None = None) -> QuoteCondition:
        """The condition that is priced. With drawing items: only 確定 items are priced, the rest become `pending`
        (src/pdf_quote.py); the manual additional processes are added."""
        for code in [condition.material]:
            if code not in self.masters.materials:
                raise ServiceError(f"材質のコードがマスターにありません: {code}")
        if condition.surface_treatment not in (None, "", "NONE") and condition.surface_treatment not in self.masters.surface_treatments:
            raise ServiceError(f"表面処理のコードがマスターにありません: {condition.surface_treatment}")
        extra = []
        for p in condition.additional_processes:
            if p.process_code not in self.masters.process_rates:
                raise ServiceError(f"追加加工のコードがマスターにありません: {p.process_code}")
            extra.append(AdditionalProcess(process_code=p.process_code, quantity=p.quantity,
                                           unit=self.masters.process_rates[p.process_code]["unit"], source=p.source))
        if drawing is not None and drawing.items:
            drawing = self.with_manual_input(drawing, condition)
            base = quote_condition(self.condition_items(drawing), self.masters, condition.material,
                                   analysis_thickness=analysis.thickness_mm)
            # a process entered by hand replaces the drawing's process of the same code (never both: M4タップ×4
            # on the drawing and in the chat is 4 holes, not 8)
            manual = {p.process_code for p in extra}
            drawn = [p for p in base.additional_processes if p.process_code not in manual]
            return base.model_copy(update={"additional_processes": drawn + extra})
        finish = condition.surface_treatment if condition.surface_treatment not in ("", "NONE") else None
        return QuoteCondition(material=condition.material, quantity=condition.quantity, surface_treatment=finish,
                              rush=condition.rush, additional_processes=extra)

    MANUAL_FIELDS = ("quantity", "surface_treatment", "rush")

    @staticmethod
    def settled(drawing: DrawingContext | None) -> DrawingContext | None:
        """The drawing items as the quotation takes them: issuing a quotation settles its conditions, so every item
        is confirmed with the value it has (a quantity must be known; the rest falls back to the input / shape)."""
        if drawing is None or not drawing.items:
            return drawing
        quantity = next((i for i in drawing.items if i.field == "quantity"), None)
        if quantity is not None and not quantity.value:
            raise ServiceError("数量が決まっていません。図面に数量がないときは condition.quantity で指定してください。",
                               code="QUANTITY_REQUIRED")
        return drawing.model_copy(update={"items": [i.model_copy(update={"status": "確定"}) for i in drawing.items]})

    def with_manual_input(self, drawing: DrawingContext | None, condition: ConditionInput) -> DrawingContext | None:
        """The drawing items with what the user entered by hand: an explicitly given quantity, surface treatment or
        rush replaces the drawing's item and counts as confirmed (as an edit on the screen does). Nothing entered
        by hand is dropped because a drawing was read."""
        given = [f for f in self.MANUAL_FIELDS if f in condition.model_fields_set]
        if drawing is None or not drawing.items or not given:
            return drawing
        items = []
        for item in drawing.items:
            if item.field not in given:
                items.append(item)
                continue
            value = getattr(condition, item.field)
            if item.field == "surface_treatment" and value in (None, ""):
                value = "NONE"  # "no treatment" entered by hand is a confirmed value, not a missing one
            display = self._display(item.field, value)
            if item.value is not None and item.value != value:
                display += f"（手入力。図面は {item.display}）"
            items.append(item.model_copy(update={"value": value, "display": display, "status": "確定",
                                                 "reasons": ["手入力"]}))
        return drawing.model_copy(update={"items": items})

    def _display(self, field: str, value) -> str:
        if field == "quantity":
            return f"{value} 個"
        if field == "rush":
            return "あり" if value else "なし"
        return self.masters.surface_treatments.get(value, {}).get("display_name", str(value))

    def price(self, analysis: SheetMetalAnalysis, condition: QuoteCondition,
              drawing: DrawingContext | None = None) -> QuoteOutcome:
        """Quote of a priced condition: the cost lines, the unit price / subtotal / tax / total shown on the screen
        and printed on the quotation, and why it is an estimate."""
        from src.quote_engine import QuoteUnavailableError

        try:
            quote = QuoteEngine(self.masters).calculate(analysis, condition)
        except QuoteUnavailableError as exc:
            raise ServiceError(str(exc), 422, "QUOTE_UNAVAILABLE") from None
        summary = price_summary(quote, condition.quantity, self.masters.policy("tax_rate", 0.10))
        reasons = pending_notes(analysis, condition, quote, self.condition_items(drawing),
                                drawing.unregistered_texts if drawing else None)
        return QuoteOutcome(condition, quote, summary, reasons)

    def quote(self, analysis: SheetMetalAnalysis, condition: ConditionInput,
              drawing: DrawingContext | None = None) -> QuoteOutcome:
        return self.price(analysis, self.build_condition(analysis, condition, drawing),
                          self.with_manual_input(drawing, condition))

    # ------------------------------------------------------------ quotation document
    def issue_document(self, analysis: SheetMetalAnalysis, condition: QuoteCondition, drawing: DrawingContext | None,
                       recipient: Recipient, part: PartInfo, subject: str = "", delivery_place: str = "",
                       remarks: str = "", include_internal: bool = False, shape_file: str = "",
                       issued_at: datetime | None = None) -> IssuedDocument:
        """Number the quotation, render it (and the internal basis), keep the files and add it to the history.
        The quotation is never an estimate: the drawing items are taken as confirmed (see settled)."""
        if not recipient.company.strip():
            raise ServiceError("宛先の会社名を入力してください。")
        drawing = self.settled(drawing)
        outcome = self.price(analysis, condition, drawing)
        issued_at = (issued_at or datetime.now()).replace(microsecond=0)
        part = PartInfo(name=part.name, drawing_no=part.drawing_no, revision=part.revision,
                        revision_date=part.revision_date, shape_file=shape_file or part.shape_file or analysis.file_name,
                        drawing_file=part.drawing_file or (drawing.file_name if drawing else ""))
        document = build_document(
            analysis=analysis, condition=outcome.condition, quote=outcome.quote, masters=self.masters,
            company=load_company(self.settings.data_dir), recipient=recipient, part=part, issued_at=issued_at,
            subject=subject, delivery_place=delivery_place, free_remarks=remarks,
            items=self.condition_items(drawing), flags=drawing.flags if drawing else None,
            unregistered_texts=drawing.unregistered_texts if drawing else None)
        history = self.history_store()
        taken = [q.quote_no for q in self.search_index().quotes]
        document.number = QuoteLog(self.settings.quote_log_path).issue(issued_at, log_row(document), taken)
        files = {"quote": (f"{document.number}_{document.title}.pdf", render_quote(document))}
        if include_internal:
            files["internal"] = (f"{document.number}_見積根拠（社内用）.pdf", render_internal(document))
        folder = self.settings.documents_dir / document.number
        folder.mkdir(parents=True, exist_ok=True)
        for kind, (_, data) in files.items():
            (folder / f"{kind}.pdf").write_bytes(data)
        history.append([from_document(document, self.masters)])
        return IssuedDocument(document, files)

    def document_file(self, quote_no: str, kind: str) -> bytes:
        if kind not in ("quote", "internal") or not quote_no.replace("-", "").isalnum():
            raise ServiceError("見積書が見つかりません。", 404, "NOT_FOUND")
        path = self.settings.documents_dir / quote_no / f"{kind}.pdf"
        if not path.exists():
            raise ServiceError("見積書が見つかりません。", 404, "NOT_FOUND")
        return path.read_bytes()

    # ------------------------------------------------------------ history and similar quotes
    def history_store(self) -> HistoryStore:
        return HistoryStore(self.settings.history_path)

    def search_index(self) -> SimilarQuoteSearch:
        """The search index of the history, rebuilt when the history file changes."""
        path = self.settings.history_path
        mtime = path.stat().st_mtime_ns if path.exists() else 0
        key = str(path.resolve())
        with _INDEX_LOCK:
            cached = _INDEX_CACHE.get(key)
            if cached and cached[0] == mtime:
                return cached[1]
            index = SimilarQuoteSearch(HistoryStore(path).load(), self.masters)
            _INDEX_CACHE[key] = (mtime, index)
            return index

    def similar(self, analysis: SheetMetalAnalysis, outcome: QuoteOutcome, customer: str, drawing_no: str = "",
                revision: str = "", today: date | None = None) -> tuple[list[Match], Reference | None, int]:
        index = self.search_index()
        query = query_for(analysis, outcome.condition, outcome.summary.unit_price,
                          outcome.quote.final_price / outcome.condition.quantity, customer, drawing_no, revision,
                          today=today or date.today())
        matches = index.search(query)
        return matches, index.reference(matches), len(index.quotes)

    @staticmethod
    def past_quote_out(quote: PastQuote) -> PastQuoteOut:
        return PastQuoteOut(**asdict(quote))

    def match_out(self, match: Match) -> MatchOut:
        return MatchOut(category=match.category, score=match.score, reasons=match.reasons, differences=match.differences,
                        price_diff=match.price_diff, past_standard=match.past_standard, ratio=match.ratio,
                        leveled_unit=match.leveled_unit, warnings=match.warnings, standard_note=match.standard_note,
                        quote=self.past_quote_out(match.quote))

    @staticmethod
    def reference_out(reference: Reference | None) -> ReferenceOut | None:
        return ReferenceOut(unit=reference.unit, basis=reference.basis) if reference else None

    def import_history(self, path: str | Path, mapping: dict[str, str] | None = None) -> tuple[int, int, int, list[str]]:
        try:
            quotes, problems = import_csv(path, self.masters, mapping)
        except ValueError as exc:
            raise ServiceError(str(exc)) from None
        added, skipped = self.history_store().append(quotes)
        return len(quotes), added, skipped, problems

    def history_page(self, customer: str = "", drawing_no: str = "", source: str = "", offset: int = 0,
                     limit: int = 50) -> tuple[int, list[PastQuote]]:
        rows = self.search_index().quotes
        if customer:
            rows = [q for q in rows if customer in q.customer]
        if drawing_no:
            rows = [q for q in rows if drawing_no.upper() in q.drawing_no.upper()]
        if source:
            rows = [q for q in rows if q.source.startswith(source)]
        rows = sorted(rows, key=lambda q: (q.date, q.quote_no), reverse=True)
        return len(rows), rows[offset:offset + limit]

    def history_detail(self, quote_no: str) -> list[PastQuote]:
        found = [q for q in self.search_index().quotes if q.quote_no == quote_no]
        if not found:
            raise ServiceError("履歴にない見積番号です。", 404, "NOT_FOUND")
        return found

    def set_outcome(self, quote_no: str, outcome: str, customer: str | None = None) -> None:
        matches = self.history_detail(quote_no)
        if customer is None and len({q.customer for q in matches}) > 1:
            raise ServiceError("同じ見積番号が複数の顧客にあります。customer を指定してください。", 409, "AMBIGUOUS")
        try:
            self.history_store().set_outcome(quote_no, outcome, customer)
        except KeyError:
            raise ServiceError("履歴にない見積番号です。", 404, "NOT_FOUND") from None

    # ------------------------------------------------------------ 3D view
    def glb(self, file_id: str) -> bytes:
        try:
            meta = self.files.meta(file_id)
        except KeyError:
            raise ServiceError("アップロードしたファイルが見つかりません。", 404, "NOT_FOUND") from None
        if meta["kind"] != "step":
            raise ServiceError("3D表示の形状は STEP ファイルから作ります。")
        cache = self.settings.viewer_dir / f"{file_id}.glb"
        if cache.exists():
            return cache.read_bytes()
        from src.services.gltf import step_to_glb

        try:
            data = step_to_glb(self.files.path(file_id))
        except Exception:  # noqa: BLE001
            raise ServiceError("STEP を3D表示の形式に変換できませんでした。", 422, "CONVERSION_FAILED") from None
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_bytes(data)
        return data
