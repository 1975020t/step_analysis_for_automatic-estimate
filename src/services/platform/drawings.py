"""Drawings: registration (a drawing PDF and/or a shape file per drawing), revisions, the analysis and reading
done at registration, attributes, notes, the list search and the previews.

The drawing PDF is read with the existing reader only (src/pdf_reader.py through EstimateService.read_drawing);
the result is a draft that a person confirms. No AI sorting of files.
"""
from __future__ import annotations

import io
from datetime import date, datetime
from pathlib import Path

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from src.db.models import (Case, Category, Drawing, DrawingMemo, DrawingNote, DrawingRevision, LibraryDocument, Quote,
                           now)
from src.master_loader import UNREGISTERED
from src.services.estimate import ServiceError
from src.services.jobs import JobError, report_progress
from src.services.pdfium_lock import PDFIUM_LOCK
from src.services.platform.core import Platform, check_version, iso

METRICS = ("thickness_mm", "blank_area_mm2", "cut_length_mm", "hole_count", "bend_count")


class RegisterItem(BaseModel):
    pdf_file_id: str = ""
    shape_file_id: str = ""
    drawing_no: str = Field(min_length=1)
    name: str = ""
    customer: str = ""
    revision: str = ""
    category_ids: list[int] = Field(default_factory=list)
    thickness_mm: float | None = Field(None, gt=0, description="DXF の板厚（なければ形状の値は見積のときに入力）")
    new_revision_of: int | None = Field(None, description="同じ図番の既存の図面に、新しい版として登録する")
    reading_job_id: str = Field("", description="下書きに使った図面PDFの読み取りの受付番号")
    material: str = ""
    surface_treatment: str = ""


class DrawingUpdate(BaseModel):
    version: int
    drawing_no: str | None = None
    name: str | None = None
    customer: str | None = None
    category_ids: list[int] | None = None
    attrs: dict | None = None
    material: str | None = None
    surface_treatment: str | None = None
    revision: str | None = None


# ---------------------------------------------------------------- registration
def register(pf: Platform, items: list[RegisterItem], actor: str) -> list[dict]:
    if not items:
        raise ServiceError("登録する図面がありません。")
    created = []
    with pf.session() as s:
        for item in items:
            pdf = _meta(pf, item.pdf_file_id, "pdf") if item.pdf_file_id else None
            shape = _meta(pf, item.shape_file_id, ("step", "dxf")) if item.shape_file_id else None
            if not pdf and not shape:
                raise ServiceError(f"図番 {item.drawing_no}：図面PDFか形状ファイルのどちらかが必要です。")
            if item.new_revision_of:
                drawing = s.get(Drawing, item.new_revision_of)
                if drawing is None:
                    raise ServiceError("新しい版を登録する図面が見つかりません。", 404, "NOT_FOUND")
                if item.name:
                    drawing.name = item.name
                if item.customer:
                    drawing.customer = item.customer
            else:
                drawing = Drawing(drawing_no=item.drawing_no.strip(), name=item.name.strip(), customer=item.customer.strip(),
                                  created_by=actor, attrs={})
                s.add(drawing)
            if item.category_ids:
                drawing.categories = list(s.scalars(select(Category).where(Category.id.in_(item.category_ids))))
            revision = DrawingRevision(
                drawing=drawing, revision=item.revision.strip(), pdf_file_id=item.pdf_file_id, pdf_name=pdf["filename"] if pdf else "",
                shape_file_id=item.shape_file_id, shape_name=shape["filename"] if shape else "",
                shape_kind=shape["kind"] if shape else "", thickness_mm=item.thickness_mm, created_by=actor,
                material=item.material.strip(), surface_treatment=item.surface_treatment.strip(),
                reading_job_id=item.reading_job_id)
            s.add(revision)
            s.flush()
            drawing.current_revision_id = revision.id
            drawing.updated_at = now()
            created.append((drawing, revision))
        s.commit()
        out = []
        for drawing, revision in created:
            job = pf.submit("register", {"revision_id": revision.id})
            revision.analysis_job_id = job["job_id"]
            out.append({"drawing_id": drawing.id, "revision_id": revision.id, "job_id": job["job_id"]})
        s.commit()
    return out


def _meta(pf: Platform, file_id: str, kinds) -> dict:
    try:
        meta = pf.files.meta(file_id)
    except KeyError:
        raise ServiceError("アップロードしたファイルが見つかりません。", 404, "NOT_FOUND") from None
    kinds = (kinds,) if isinstance(kinds, str) else kinds
    if meta["kind"] not in kinds:
        raise ServiceError(f"{meta['filename']} は {'・'.join(kinds)} ではありません。")
    return meta


def run_register_job(pf: Platform, payload: dict) -> dict:
    """At registration: analyse the shape file, take the drawing reading (the draft read before registering, or a
    new reading when none was made and the reader is available), extract the PDF text for the search."""
    with pf.session() as s:
        revision = s.get(DrawingRevision, payload["revision_id"])
        if revision is None:
            raise JobError("図面の版が見つかりません。")
        result: dict = {}
        if revision.shape_file_id:
            report_progress(stage="形状解析")
            if revision.shape_kind == "dxf" and not revision.thickness_mm:
                result["analysis"] = "板厚がないため未解析（見積のときに板厚を入力）"
            else:
                try:
                    data, meta = pf.files.get(revision.shape_file_id)
                    analysis = pf.service.analyze_bytes(data, meta["filename"], revision.thickness_mm)
                    revision.analysis = analysis.model_dump(mode="json")
                    revision.max_dimension_mm = max_dimension(pf, revision, analysis)
                    result["analysis"] = analysis.status
                except Exception:  # noqa: BLE001 - recorded as a failed analysis; the quote asks for the values
                    revision.analysis = {"status": "error", "file_name": revision.shape_name,
                                         "message": "形状ファイルを解析できませんでした。"}
                    result["analysis"] = "error"
        if revision.pdf_file_id:
            report_progress(stage="図面の読み取り")
            revision.pdf_text = pdf_text(pf.files.path(revision.pdf_file_id))
            reading = reading_of_job(pf, revision.reading_job_id)
            if reading is None and not revision.reading_job_id and pf.service.drawing_reader_available():
                try:
                    r, ctx = pf.service.read_drawing(pf.files.path(revision.pdf_file_id), revision.pdf_name)
                    reading = {"reading": r, "drawing": ctx.model_dump(mode="json")}
                except Exception:  # noqa: BLE001
                    reading = None
            if reading:
                revision.reading = reading
                apply_reading(revision, reading["reading"])
            result["reading"] = bool(reading)
        s.commit()
    return result


def reading_of_job(pf: Platform, job_id: str) -> dict | None:
    job = pf.job(job_id)
    if job and job.get("status") == "done" and job.get("kind") == "drawing":
        return job["result"]
    return None


def apply_reading(revision: DrawingRevision, reading: dict) -> None:
    """Fill the drawing's material / finish from the reading when the person left them empty."""
    texts = reading.get("source_texts") or {}
    if not revision.material and reading.get("material"):
        revision.material = texts.get("material", "") if reading["material"] == UNREGISTERED else reading["material"]
    if not revision.surface_treatment and reading.get("surface_treatment"):
        finish = reading["surface_treatment"]
        revision.surface_treatment = texts.get("surface_treatment", "") if finish == UNREGISTERED else finish


def sync_revision(pf: Platform, s: Session, revision: DrawingRevision) -> None:
    """Take a reading that finished after registration (the draft job) into the revision."""
    if revision.reading is None and revision.reading_job_id:
        reading = reading_of_job(pf, revision.reading_job_id)
        if reading:
            revision.reading = reading
            apply_reading(revision, reading["reading"])
            s.commit()


def max_dimension(pf: Platform, revision: DrawingRevision, analysis) -> float | None:
    if revision.shape_kind == "step":
        try:
            import cadquery as cq

            box = cq.importers.importStep(str(pf.files.path(revision.shape_file_id))).val().BoundingBox()
            return round(max(box.xlen, box.ylen, box.zlen), 1)
        except Exception:  # noqa: BLE001
            pass
    box = analysis.flat_pattern.bounding_box_mm if analysis.flat_pattern else None
    return round(max(box), 1) if box else None


def pdf_text(path: Path) -> str:
    """The text layer of a PDF (empty for a scan: no OCR)."""
    import pypdfium2 as pdfium

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


def render_png(path: Path, cache: Path, width: int = 900) -> bytes:
    """The first page of a PDF as PNG (cached)."""
    if cache.exists():
        return cache.read_bytes()
    import pypdfium2 as pdfium

    with PDFIUM_LOCK:
        pdf = pdfium.PdfDocument(str(path))
        page = pdf[0]
        scale = width / page.get_width()
        image = page.render(scale=scale).to_pil()
        page.close()
        pdf.close()
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_bytes(buffer.getvalue())
    return buffer.getvalue()


def revision_preview(pf: Platform, revision_id: int, width: int = 900) -> bytes:
    with pf.session() as s:
        revision = s.get(DrawingRevision, revision_id)
        if revision is None or not revision.pdf_file_id:
            raise ServiceError("図面PDFがありません。", 404, "NOT_FOUND")
        path = pf.files.path(revision.pdf_file_id)
    return render_png(path, pf.settings.viewer_dir / f"rev{revision_id}_{width}.png", width)


# ---------------------------------------------------------------- reading the drawing objects
def metrics_of(revision: DrawingRevision | None) -> dict:
    a = (revision.analysis or {}) if revision else {}
    ok = a.get("status") in ("success", "partial")
    return {m: (a.get(m) if ok else None) for m in METRICS}


def latest_quotes(s: Session, drawing_ids: list[int]) -> dict[int, Quote]:
    rows = s.scalars(select(Quote).options(selectinload(Quote.case).selectinload(Case.status))
                     .where(Quote.drawing_id.in_(drawing_ids)).order_by(Quote.id)) if drawing_ids else []
    latest: dict[int, Quote] = {}
    for q in rows:
        latest[q.drawing_id] = q
    return latest


def card(drawing: Drawing, revision: DrawingRevision | None, quote: Quote | None, masters) -> dict:
    metrics = metrics_of(revision)
    material = revision.material if revision else ""
    finish = revision.surface_treatment if revision else ""
    price = (quote.result or {}).get("price") if quote else None
    processes = processes_of(revision, masters)
    return {
        "id": drawing.id, "drawing_no": drawing.drawing_no, "name": drawing.name, "customer": drawing.customer,
        "revision": revision.revision if revision else "", "revision_id": revision.id if revision else None,
        "material": material, "material_name": masters.materials.get(material, {}).get("display_name", material),
        "surface_treatment": finish,
        "surface_treatment_name": masters.surface_treatments.get(finish, {}).get("display_name", finish),
        "processes": processes, "max_dimension_mm": revision.max_dimension_mm if revision else None,
        "has_pdf": bool(revision and revision.pdf_file_id), "has_shape": bool(revision and revision.shape_file_id),
        "shape_kind": revision.shape_kind if revision else "", "shape_file_id": revision.shape_file_id if revision else "",
        "metrics": metrics, "analysis_status": (revision.analysis or {}).get("status") if revision else None,
        "flat_pattern": ((revision.analysis or {}).get("flat_pattern") if revision else None),
        "created_at": iso(drawing.created_at), "attrs": drawing.attrs or {},
        "category_ids": [c.id for c in drawing.categories], "version": drawing.version,
        "status": _status(quote), "quote_id": quote.id if quote else None, "quote_number": quote.number if quote else None,
        "unit_price": price["unit_price"] if price else None, "quantity": (quote.inputs or {}).get("quantity") if quote else None,
        "quote_date": iso(quote.updated_at.date()) if quote else None,
    }


def _status(quote: Quote | None) -> dict | None:
    if quote is None:
        return None
    st = quote.case.status
    return {"id": st.id, "name": st.name, "color": st.color, "group": st.group}


def processes_of(revision: DrawingRevision | None, masters) -> list[str]:
    """Process names of the drawing: cutting, bending when it bends, and the processes read from the drawing."""
    if revision is None:
        return []
    names = []
    if revision.shape_file_id or revision.analysis:
        names.append("レーザー切断")
    if (metrics_of(revision).get("bend_count") or 0) > 0:
        names.append("曲げ")
    for p in ((revision.reading or {}).get("reading") or {}).get("processes") or []:
        row = masters.process_rates.get(p.get("code"))
        if row and row["display_name"] not in names:
            names.append(row["display_name"])
    return names


def list_drawings(pf: Platform, f: dict) -> list[dict]:
    masters = pf.masters()
    with pf.session() as s:
        drawings = list(s.scalars(select(Drawing).options(selectinload(Drawing.revisions), selectinload(Drawing.categories))
                                  .order_by(Drawing.created_at.desc(), Drawing.id.desc())))
        latest = latest_quotes(s, [d.id for d in drawings])
        cards = []
        for d in drawings:
            revision = next((r for r in d.revisions if r.id == d.current_revision_id), d.revisions[-1] if d.revisions else None)
            c = card(d, revision, latest.get(d.id), masters)
            c["_text"] = revision.pdf_text if revision else ""
            cards.append(c)
    out = [c for c in cards if matches(c, f)]
    for c in out:
        c.pop("_text", None)
    return out


def _has(value: str, needle: str) -> bool:
    import unicodedata

    norm = lambda t: unicodedata.normalize("NFKC", t or "").upper().replace(" ", "")  # noqa: E731
    return norm(needle) in norm(value)


def matches(c: dict, f: dict) -> bool:
    if f.get("drawing_no") and not _has(c["drawing_no"], f["drawing_no"]):
        return False
    if f.get("name") and not _has(c["name"], f["name"]):
        return False
    if f.get("customer") and not _has(c["customer"], f["customer"]):
        return False
    if f.get("status"):
        name = c["status"]["name"] if c["status"] else "未作成"
        if name != f["status"]:
            return False
    if f.get("material") and c["material"] != f["material"]:
        return False
    if f.get("process") and f["process"] not in c["processes"]:
        return False
    if f.get("surface_treatment") and c["surface_treatment"] != f["surface_treatment"]:
        return False
    dim = c["max_dimension_mm"]
    if f.get("dim_min") not in (None, "") and (dim is None or dim < float(f["dim_min"])):
        return False
    if f.get("dim_max") not in (None, "") and (dim is None or dim > float(f["dim_max"])):
        return False
    created = (c["created_at"] or "")[:10]
    if f.get("date_from") and created < f["date_from"]:
        return False
    if f.get("date_to") and created > f["date_to"]:
        return False
    if f.get("category_id") and int(f["category_id"]) not in c["category_ids"]:
        return False
    if f.get("text"):
        if not (_has(c.get("_text", ""), f["text"]) or _has(c["drawing_no"], f["text"]) or _has(c["name"], f["text"])):
            return False
    for key, value in (f.get("attrs") or {}).items():
        if value in (None, ""):
            continue
        have = c["attrs"].get(key)
        if isinstance(value, dict):  # ranges: {"min", "max"} / {"from", "to"}
            low, high = value.get("min", value.get("from")), value.get("max", value.get("to"))
            if have in (None, ""):
                return False
            try:
                comparable = float(have) if "min" in value or "max" in value else str(have)
                if low not in (None, "") and comparable < (float(low) if isinstance(comparable, float) else low):
                    return False
                if high not in (None, "") and comparable > (float(high) if isinstance(comparable, float) else high):
                    return False
            except ValueError:
                return False
        elif not _has(str(have or ""), str(value)):
            return False
    return True


def drawing_detail(pf: Platform, drawing_id: int) -> dict:
    masters = pf.masters()
    with pf.session() as s:
        d = s.get(Drawing, drawing_id, options=[selectinload(Drawing.revisions), selectinload(Drawing.categories)])
        if d is None:
            raise ServiceError("図面が見つかりません。", 404, "NOT_FOUND")
        for r in d.revisions:
            sync_revision(pf, s, r)
        latest = latest_quotes(s, [d.id])
        current = next((r for r in d.revisions if r.id == d.current_revision_id), d.revisions[-1] if d.revisions else None)
        out = card(d, current, latest.get(d.id), masters)
        out["revisions"] = [revision_out(pf, r, masters) for r in reversed(d.revisions)]
        quotes = list(s.scalars(select(Quote).options(selectinload(Quote.case).selectinload(Case.status))
                                .where(Quote.drawing_id == d.id).order_by(Quote.id.desc())))
        out["usage"] = [{"quote_id": q.id, "number": q.number, "customer": q.case.customer, "title": q.case.title,
                         "status": q.case.status.name, "status_color": q.case.status.color,
                         "revision": next((r.revision for r in d.revisions if r.id == q.revision_id), ""),
                         "quantity": (q.inputs or {}).get("quantity"),
                         "total": ((q.result or {}).get("price") or {}).get("subtotal"), "updated_at": iso(q.updated_at)}
                        for q in quotes]
        docs = [doc for doc in s.scalars(select(LibraryDocument).order_by(LibraryDocument.id.desc()))
                if d.id in (doc.drawing_ids or [])]
        out["documents"] = [{"id": doc.id, "kind": doc.kind, "title": doc.title, "filename": doc.filename,
                             "file_type": doc.file_type, "created_at": iso(doc.created_at)} for doc in docs]
        out["memos"] = [{"id": m.id, "kind": m.kind, "text": m.text, "created_at": iso(m.created_at), "created_by": m.created_by}
                        for m in s.scalars(select(DrawingMemo).where(DrawingMemo.drawing_id == d.id).order_by(DrawingMemo.id.desc()))]
    return out


def revision_out(pf: Platform, r: DrawingRevision, masters) -> dict:
    from src.services.platform.pricing import drawing_items, QuoteInputs, inputs_from_reading

    reading = reading_for_pricing(r)
    items = drawing_items(reading, inputs_from_reading(reading, masters, QuoteInputs()), masters, r.analysis) if reading else []
    job = pf.job(r.analysis_job_id)
    with pf.session() as s:
        notes = [{"id": n.id, "x": n.x, "y": n.y, "text": n.text, "created_at": iso(n.created_at), "created_by": n.created_by}
                 for n in s.scalars(select(DrawingNote).where(DrawingNote.revision_id == r.id).order_by(DrawingNote.id))]
    return {"id": r.id, "revision": r.revision, "pdf_file_id": r.pdf_file_id, "pdf_name": r.pdf_name,
            "shape_file_id": r.shape_file_id, "shape_name": r.shape_name, "shape_kind": r.shape_kind,
            "thickness_mm": r.thickness_mm, "material": r.material, "surface_treatment": r.surface_treatment,
            "analysis": r.analysis, "metrics": metrics_of(r), "max_dimension_mm": r.max_dimension_mm,
            "reading_items": items, "flags": ((r.reading or {}).get("reading") or {}).get("flags") or [],
            "processing": bool(job and job["status"] in ("queued", "running")),
            "created_at": iso(r.created_at), "created_by": r.created_by, "notes": notes}


def reading_for_pricing(r: DrawingRevision | None) -> dict | None:
    """The reading dict as src/services/platform/pricing.py takes it (with the unregistered process texts)."""
    if r is None or not r.reading:
        return None
    reading = dict(r.reading.get("reading") or {})
    reading["unregistered_texts"] = (r.reading.get("drawing") or {}).get("unregistered_texts") or []
    return reading


def update_drawing(pf: Platform, drawing_id: int, body: DrawingUpdate, actor: str) -> dict:
    with pf.session() as s:
        d = s.get(Drawing, drawing_id, options=[selectinload(Drawing.revisions)])
        if d is None:
            raise ServiceError("図面が見つかりません。", 404, "NOT_FOUND")
        check_version(d, body.version, "図面")
        for field in ("drawing_no", "name", "customer"):
            value = getattr(body, field)
            if value is not None:
                if field == "drawing_no" and not value.strip():
                    raise ServiceError("図番は必須です。")
                setattr(d, field, value.strip())
        if body.attrs is not None:
            d.attrs = dict(body.attrs)
        if body.category_ids is not None:
            d.categories = list(s.scalars(select(Category).where(Category.id.in_(body.category_ids))))
        current = next((r for r in d.revisions if r.id == d.current_revision_id), None)
        if current is not None:
            if body.material is not None:
                current.material = body.material.strip()
            if body.surface_treatment is not None:
                current.surface_treatment = body.surface_treatment.strip()
            if body.revision is not None:
                current.revision = body.revision.strip()
        d.updated_at = now()
        s.commit()
    return drawing_detail(pf, drawing_id)


def set_current_revision(pf: Platform, drawing_id: int, revision_id: int) -> None:
    with pf.session() as s:
        d = s.get(Drawing, drawing_id)
        r = s.get(DrawingRevision, revision_id)
        if d is None or r is None or r.drawing_id != d.id:
            raise ServiceError("図面の版が見つかりません。", 404, "NOT_FOUND")
        d.current_revision_id = r.id
        s.commit()


def add_note(pf: Platform, revision_id: int, x: float, y: float, text: str, actor: str) -> None:
    if not text.strip():
        raise ServiceError("メモを入力してください。")
    with pf.session() as s:
        if s.get(DrawingRevision, revision_id) is None:
            raise ServiceError("図面の版が見つかりません。", 404, "NOT_FOUND")
        s.add(DrawingNote(revision_id=revision_id, x=min(max(x, 0), 1), y=min(max(y, 0), 1), text=text.strip(), created_by=actor))
        s.commit()


def delete_note(pf: Platform, note_id: int) -> None:
    with pf.session() as s:
        note = s.get(DrawingNote, note_id)
        if note:
            s.delete(note)
            s.commit()


def add_memo(pf: Platform, drawing_id: int, kind: str, text: str, actor: str) -> None:
    if not text.strip():
        raise ServiceError("メモを入力してください。")
    with pf.session() as s:
        if s.get(Drawing, drawing_id) is None:
            raise ServiceError("図面が見つかりません。", 404, "NOT_FOUND")
        s.add(DrawingMemo(drawing_id=drawing_id, kind=kind if kind in ("注意", "不具合") else "注意", text=text.strip(), created_by=actor))
        s.commit()


def same_number(pf: Platform, drawing_no: str) -> list[dict]:
    """Registered drawings with this drawing number (to offer 「新しい版として登録」)."""
    import unicodedata

    key = unicodedata.normalize("NFKC", drawing_no or "").strip().upper()
    if not key:
        return []
    with pf.session() as s:
        rows = s.scalars(select(Drawing).options(selectinload(Drawing.revisions)))
        return [{"id": d.id, "drawing_no": d.drawing_no, "name": d.name, "customer": d.customer,
                 "revisions": [r.revision for r in d.revisions]}
                for d in rows if unicodedata.normalize("NFKC", d.drawing_no).strip().upper() == key]


def today() -> date:
    return datetime.now().date()
