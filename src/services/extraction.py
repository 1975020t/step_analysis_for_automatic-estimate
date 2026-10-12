"""The heavy part of a job, without the database: shape analysis, drawing-PDF reading, PDF text, bounding size.

A job of the screens runs in three steps (src/services/platform/drawings.py and quotes.py):
  prepare  (reads the database)  -> a plan: plain JSON, which files to analyse or read and with which conditions
  extract  (this module)          -> the results: plain JSON
  finish   (writes the database) -> matching, amounts, similar results, saved
In one process the three run one after another (JobQueue). On AWS each can be a step of a state machine: `extract`
needs only the files (FileStore) and the reader, never the database, so it can run without database rights.

This module must not import src.db or src.services.platform (tests/test_extraction.py checks it).
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from src.services.storage import FileStore

# plan = {"analysis": {"file_id", "thickness_mm", "k_factor", "k_factor_confirmed", "flat_confirmed",
#                      "max_dimension": bool} | None,
#         "pdf_file_id": str | None, "pdf_text": bool, "read": bool}
# result = {"analysis": dict | None, "max_dimension_mm": float | None, "pdf_text": str | None,
#           "reading": {"reading": dict, "evidence": list | None} | None}

ANALYSIS_FAILED = "CADデータを解析できませんでした。"


class Extractor:
    """What the extraction step may use: the files and, when available, the drawing reader (no database)."""

    def __init__(self, files: FileStore, read_pdf: Callable[[Path], tuple[dict, list | None]] | None = None,
                 progress: Callable[..., None] | None = None) -> None:
        self.files = files
        self.read_pdf = read_pdf          # None: no reader (no API key); the conditions are entered by hand
        self.progress = progress or (lambda **_: None)


def extract(ex: Extractor, plan: dict) -> dict:
    out: dict = {"analysis": None, "max_dimension_mm": None, "pdf_text": None, "reading": None}
    request = plan.get("analysis")
    if request:
        ex.progress(stage="CADデータの解析")
        out["analysis"], out["max_dimension_mm"] = analyse(ex.files, request)
    pdf_file_id = plan.get("pdf_file_id")
    if pdf_file_id and (plan.get("pdf_text") or (plan.get("read") and ex.read_pdf is not None)):
        ex.progress(stage="図面の読み取り", analysis=out["analysis"])
        with ex.files.local_path(pdf_file_id) as path:
            if plan.get("pdf_text"):
                out["pdf_text"] = pdf_text(path)
            if plan.get("read") and ex.read_pdf is not None:
                try:
                    reading, evidence = ex.read_pdf(path)
                    out["reading"] = {"reading": reading, "evidence": evidence}
                except Exception:  # noqa: BLE001 - the conditions are entered by hand then
                    out["reading"] = None
    return out


def analyse(files: FileStore, request: dict) -> tuple[dict, float | None]:
    """SheetMetalAnalysis (JSON) of a STEP or flat-pattern DXF, and the largest outer size when asked."""
    from src.services.estimate import EstimateService

    file_name = ""
    try:
        data, meta = files.get(request["file_id"])
        file_name = meta["filename"]
        analysis = EstimateService.analyze_bytes(
            data, file_name, request.get("thickness_mm"), request.get("k_factor", 0.33),
            request.get("k_factor_confirmed", False), request.get("flat_confirmed", False))
    except Exception:  # noqa: BLE001 - recorded as a failed analysis; the quote asks for the values
        return {"status": "error", "file_name": request.get("file_name") or file_name, "message": ANALYSIS_FAILED}, None
    size = max_dimension(files, request["file_id"], meta["kind"], analysis) if request.get("max_dimension") else None
    return analysis.model_dump(mode="json"), size


def max_dimension(files: FileStore, file_id: str, kind: str, analysis) -> float | None:
    if kind == "step":
        try:
            import cadquery as cq

            with files.local_path(file_id) as path:
                box = cq.importers.importStep(str(path)).val().BoundingBox()
            return round(max(box.xlen, box.ylen, box.zlen), 1)
        except Exception:  # noqa: BLE001
            pass
    box = analysis.flat_pattern.bounding_box_mm if analysis.flat_pattern else None
    return round(max(box), 1) if box else None


def pdf_text(path: Path) -> str:
    """The text layer of a PDF (empty for a scan: no OCR)."""
    import pypdfium2 as pdfium

    from src.services.pdfium_lock import PDFIUM_LOCK

    with PDFIUM_LOCK:
        try:
            pdf = pdfium.PdfDocument(str(path))
            texts = []
            for page in pdf:
                textpage = page.get_textpage()
                texts.append(textpage.get_text_range())
                textpage.close()
                page.close()
            pdf.close()
            return "\n".join(texts).replace("\r", "")
        except Exception:  # noqa: BLE001
            return ""

