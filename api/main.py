"""HTTP API of the estimating system (FastAPI). The work is done by src/services (shared with the Streamlit demo).

    uvicorn api.main:app --host 0.0.0.0 --port 8000        # docs at http://localhost:8000/docs

Flow: POST /api/files -> POST /api/analyses (job) -> GET /api/jobs/{id} -> POST /api/quotes -> POST /api/documents.
See analysis/api_design.md.
"""
from __future__ import annotations

import json
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.parse import quote as urlquote

from fastapi import Depends, FastAPI, File, Form, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response

from src.models import SheetMetalAnalysis
from src.quote_document import PartInfo, Recipient
from src.services.estimate import EstimateService, ReaderFactory, ServiceError
from src.services.jobs import JobQueue
from src.services.schemas import (AnalysisJobRequest, AnalysisSource, DocumentFileOut, DocumentRequest,
                                  DocumentResponse, DrawingJobRequest, FileOut, HistoryImportResponse, HistoryPage,
                                  JobOut, OutcomeInput, PastQuoteOut, QuoteRequest, QuoteResponse, SimilarRequest,
                                  SimilarResponse)
from src.services.settings import Settings

ALLOWED = {".step": "step", ".stp": "step", ".dxf": "dxf", ".pdf": "pdf"}
SIGNATURES = {"step": (b"ISO-10303-21",), "pdf": (b"%PDF-",), "dxf": (b"SECTION", b"AutoCAD Binary DXF")}


def create_app(settings: Settings | None = None, reader_factory: ReaderFactory | None = None,
               start_jobs: bool = True) -> FastAPI:
    """The API. `reader_factory` replaces the drawing-PDF reader (tests use a fake one; no LLM call).
    `start_jobs=False` accepts jobs without running them (for tests of the queue state)."""
    settings = settings or Settings.from_env()
    service = EstimateService(settings, reader_factory=reader_factory)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.queue = service.job_queue(autostart=start_jobs)
        app.state.queue.resume()  # jobs a previous run left queued or running
        yield
        app.state.queue.shutdown(wait=False)

    app = FastAPI(title="板金見積 API", version="1.0.0", lifespan=lifespan,
                  description="STEP・DXFの解析、図面PDFの読み取り、見積、見積書、類似見積、過去見積の履歴。"
                              "金額はマスターとルールだけで決まります（LLMは計算しません）。")
    app.state.service = service
    app.state.settings = settings

    # ------------------------------------------------------------ errors (no internal paths or secrets)
    @app.exception_handler(ServiceError)
    async def service_error(_: Request, exc: ServiceError):
        return JSONResponse(status_code=exc.status, content={"error": {"code": exc.code, "message": str(exc)}})

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError):
        details = [{"loc": list(e.get("loc", [])), "message": e.get("msg", "")} for e in exc.errors()]
        return JSONResponse(status_code=422, content={"error": {"code": "INVALID_INPUT", "message": "入力が正しくありません。",
                                                                "details": details}})

    @app.exception_handler(Exception)
    async def unexpected(_: Request, exc: Exception):
        return JSONResponse(status_code=500, content={"error": {"code": "INTERNAL", "message": "サーバーでエラーが発生しました。"}})

    def require_token(request: Request) -> None:
        if not settings.api_token:
            return
        header = request.headers.get("authorization", "")
        given = header[7:].strip() if header.lower().startswith("bearer ") else request.headers.get("x-api-token", "")
        if not secrets.compare_digest(given.encode(), settings.api_token.encode()):
            raise ServiceError("APIトークンが必要です。", 401, "UNAUTHORIZED")

    guarded = [Depends(require_token)]

    def queue() -> JobQueue:
        return app.state.queue

    def analysis_of(source: AnalysisSource) -> SheetMetalAnalysis:
        if source.analysis is not None:
            return source.analysis
        if source.analysis_job_id:
            return service.analysis_from_job(source.analysis_job_id)
        raise ServiceError("analysis_job_id か analysis を指定してください。")

    # ------------------------------------------------------------ health and masters
    @app.get("/api/health", tags=["system"])
    def health() -> dict:
        return {"status": "ok", "drawing_reader": service.drawing_reader_available()}

    @app.get("/api/masters", tags=["masters"], dependencies=guarded)
    def masters() -> dict:
        """材料・工程・表面処理・価格方針・自社情報。"""
        return service.masters_payload()

    # ------------------------------------------------------------ files
    @app.post("/api/files", tags=["files"], dependencies=guarded, response_model=FileOut, status_code=201)
    async def upload(file: UploadFile = File(...)) -> FileOut:
        """STEP（.step/.stp）・展開図DXF（.dxf）・図面PDF（.pdf）をアップロードする。"""
        name = file.filename or "upload"
        kind = ALLOWED.get(Path(name).suffix.lower())
        if kind is None:
            raise ServiceError("対応していないファイルの種類です（.step .stp .dxf .pdf）。", 415, "UNSUPPORTED_TYPE")
        data = await file.read(settings.max_upload_bytes + 1)
        if len(data) > settings.max_upload_bytes:
            raise ServiceError(f"ファイルが大きすぎます（上限 {settings.max_upload_bytes // (1024 * 1024)} MB）。", 413, "TOO_LARGE")
        if not data:
            raise ServiceError("ファイルが空です。")
        head = data[:4096]
        if not any(sig in head for sig in SIGNATURES[kind]):
            raise ServiceError("ファイルの中身が拡張子と一致しません。", 415, "UNSUPPORTED_TYPE")
        return FileOut(**service.files.save(data, name))

    # ------------------------------------------------------------ jobs
    @app.post("/api/analyses", tags=["analysis"], dependencies=guarded, response_model=JobOut, status_code=202)
    def submit_analysis(body: AnalysisJobRequest) -> JobOut:
        """形状の解析を受け付ける（すぐに受付番号を返す）。結果は GET /api/jobs/{job_id}。"""
        meta = _file_meta(service, body.file_id)
        if meta["kind"] not in ("step", "dxf"):
            raise ServiceError("解析できるのは STEP と DXF です。")
        if meta["kind"] == "dxf" and not body.thickness_mm:
            raise ServiceError("展開図DXFの解析には板厚（thickness_mm）が必要です。", code="THICKNESS_REQUIRED")
        return JobOut(**queue().submit("analysis", body.model_dump()))

    @app.post("/api/drawings/readings", tags=["drawing"], dependencies=guarded, response_model=JobOut, status_code=202)
    def submit_reading(body: DrawingJobRequest) -> JobOut:
        """図面PDFの加工条件の読み取りを受け付ける。APIキーがない環境では 503（DRAWING_READER_UNAVAILABLE）。"""
        meta = _file_meta(service, body.file_id)
        if meta["kind"] != "pdf":
            raise ServiceError("読み取れるのは図面PDFです。")
        if not service.drawing_reader_available():
            raise ServiceError("図面PDFの読み取りには Claude APIキー（ANALYSIS_ANTHROPIC_API_KEY）の設定が必要です。"
                               "条件は手入力してください。", 503, "DRAWING_READER_UNAVAILABLE")
        return JobOut(**queue().submit("drawing", body.model_dump()))

    @app.get("/api/jobs/{job_id}", tags=["analysis"], dependencies=guarded, response_model=JobOut)
    def job(job_id: str) -> JobOut:
        """受付の状態（queued / running / done / failed）と結果。"""
        try:
            data = service.job_store.read(job_id)
        except KeyError:
            raise ServiceError("受付番号が見つかりません。", 404, "NOT_FOUND") from None
        return JobOut(**{k: v for k, v in data.items() if k != "input"})

    # ------------------------------------------------------------ quote and document
    @app.post("/api/quotes", tags=["quote"], dependencies=guarded, response_model=QuoteResponse)
    def quote(body: QuoteRequest) -> QuoteResponse:
        """見積の計算。画面・見積書と同じ金額（単価・小計・消費税・合計）と、概算かどうかと理由。"""
        outcome = service.quote(analysis_of(body), body.condition, body.drawing)
        return QuoteResponse(condition=outcome.condition, quote=outcome.quote, price=outcome.price_out(),
                             is_estimate=outcome.is_estimate, estimate_reasons=outcome.reasons)

    @app.post("/api/documents", tags=["document"], dependencies=guarded, response_model=DocumentResponse, status_code=201)
    def document(body: DocumentRequest) -> DocumentResponse:
        """見積書PDFを作る（見積番号の採番、履歴への記録を含む）。PDFは files[].url から取得する。"""
        analysis = analysis_of(body)
        drawing = service.settled(service.with_manual_input(body.drawing, body.condition))  # issuing settles them
        condition = service.build_condition(analysis, body.condition, drawing)
        issued = service.issue_document(
            analysis, condition, drawing, Recipient(body.recipient.company, body.recipient.person),
            PartInfo(name=body.part.name, drawing_no=body.part.drawing_no, revision=body.part.revision,
                     revision_date=body.part.revision_date),
            subject=body.subject, delivery_place=body.delivery_place, remarks=body.remarks,
            include_internal=body.include_internal)
        doc = issued.document
        return DocumentResponse(
            quote_no=doc.number, title=doc.title, issued_at=doc.issued_at.isoformat(), total=doc.total,
            files=[DocumentFileOut(kind=kind, filename=name, url=f"/api/documents/{doc.number}/{kind}.pdf")
                   for kind, (name, _) in issued.files.items()])

    @app.get("/api/documents/{quote_no}/{kind}.pdf", tags=["document"], dependencies=guarded,
             response_class=Response, responses={200: {"content": {"application/pdf": {}}}})
    def document_file(quote_no: str, kind: str) -> Response:
        data = service.document_file(quote_no, kind)
        name = f"{quote_no}_{'見積根拠（社内用）' if kind == 'internal' else '見積書'}.pdf"
        return Response(data, media_type="application/pdf",
                        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{urlquote(name)}"})

    # ------------------------------------------------------------ similar quotes and history
    @app.post("/api/similar-quotes", tags=["similar"], dependencies=guarded, response_model=SimilarResponse)
    def similar(body: SimilarRequest) -> SimilarResponse:
        """今回の条件に近い過去の見積（最大5件、理由・違い・価格の比較つき）。今回の単価は変えない。"""
        analysis = analysis_of(body)
        outcome = service.quote(analysis, body.condition, body.drawing)
        matches, reference, size = service.similar(analysis, outcome, body.customer, body.drawing_no, body.revision)
        return SimilarResponse(history_size=size, price=outcome.price_out(), reference=service.reference_out(reference),
                               matches=[service.match_out(m) for m in matches])

    @app.post("/api/history/import", tags=["history"], dependencies=guarded, response_model=HistoryImportResponse)
    async def import_history(file: UploadFile = File(...),
                             mapping: str = Form("", description='列の対応づけ（JSON）。例 {"customer": "得意先"}')) -> HistoryImportResponse:
        """過去見積のCSV（Excelの書き出し、UTF-8 か Shift_JIS）を履歴に取り込む。"""
        if Path(file.filename or "").suffix.lower() != ".csv":
            raise ServiceError("CSVファイルを指定してください。", 415, "UNSUPPORTED_TYPE")
        data = await file.read(settings.max_upload_bytes + 1)
        if len(data) > settings.max_upload_bytes:
            raise ServiceError("ファイルが大きすぎます。", 413, "TOO_LARGE")
        try:
            columns = json.loads(mapping) if mapping.strip() else None
        except json.JSONDecodeError:
            raise ServiceError("mapping は JSON で指定してください。") from None
        with NamedTemporaryFile(suffix=".csv", delete=False) as handle:
            handle.write(data)
        try:
            read, added, skipped, problems = service.import_history(handle.name, columns)
        finally:
            Path(handle.name).unlink(missing_ok=True)
        return HistoryImportResponse(read=read, added=added, skipped=skipped, problems=problems)

    @app.get("/api/history", tags=["history"], dependencies=guarded, response_model=HistoryPage)
    def history(customer: str = "", drawing_no: str = "", source: str = Query("", description="app / import"),
                offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=500)) -> HistoryPage:
        total, rows = service.history_page(customer, drawing_no, source, offset, limit)
        return HistoryPage(total=total, offset=offset, limit=limit, items=[service.past_quote_out(q) for q in rows])

    @app.get("/api/history/{quote_no}", tags=["history"], dependencies=guarded, response_model=list[PastQuoteOut])
    def history_detail(quote_no: str) -> list[PastQuoteOut]:
        """見積番号の詳細（取り込んだ元の行の全項目を含む）。番号が顧客ごとに重なる場合は複数返す。"""
        return [service.past_quote_out(q) for q in service.history_detail(quote_no)]

    @app.put("/api/history/{quote_no}/outcome", tags=["history"], dependencies=guarded)
    def outcome(quote_no: str, body: OutcomeInput) -> dict:
        """受注・失注・未回答を記録する。"""
        service.set_outcome(quote_no, body.outcome, body.customer)
        return {"quote_no": quote_no, "outcome": body.outcome}

    # ------------------------------------------------------------ 3D view
    @app.get("/api/files/{file_id}/model.glb", tags=["files"], dependencies=guarded, response_class=Response,
             responses={200: {"content": {"model/gltf-binary": {}}}})
    def model_glb(file_id: str) -> Response:
        """アップロードした STEP を、ブラウザで表示できる glTF（GLB、単位 mm）にして返す。"""
        return Response(service.glb(file_id), media_type="model/gltf-binary")

    return app


def _file_meta(service: EstimateService, file_id: str) -> dict:
    try:
        return service.files.meta(file_id)
    except KeyError:
        raise ServiceError("アップロードしたファイルが見つかりません。", 404, "NOT_FOUND") from None


app = create_app()
