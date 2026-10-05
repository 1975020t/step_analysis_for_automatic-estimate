"""Documents (書類) and the keyword search over them, the registered drawings and the quotes. Keyword search only
(no AI question answering); text comes from the PDF text layer and the Excel cell values (no OCR).
"""
from __future__ import annotations

import io
import re
import unicodedata

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from src.db.models import Case, Drawing, IssuedDocument, LibraryDocument, Quote
from src.services.estimate import ServiceError
from src.services.platform.core import Platform, iso
from src.services.platform.drawings import pdf_text

KINDS = ["図面", "見積書", "発注書", "ミルシート", "作業日報", "社内規格", "仕様書", "その他"]


def excel_text(data: bytes) -> str:
    from openpyxl import load_workbook

    try:
        book = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception:  # noqa: BLE001
        raise ServiceError("Excelファイルを読めませんでした（.xlsx を指定してください）。", 415, "UNSUPPORTED_TYPE") from None
    lines = []
    for sheet in book.worksheets:
        for row in sheet.iter_rows(values_only=True):
            cells = [str(v) for v in row if v not in (None, "")]
            if cells:
                lines.append(" ".join(cells))
    return "\n".join(lines)


def add_document(pf: Platform, data: bytes, filename: str, kind: str, title: str, drawing_ids: list[int],
                 customer: str, actor: str) -> dict:
    if kind not in KINDS:
        raise ServiceError(f"書類の種類は {'・'.join(KINDS)} のどれかです。")
    lower = filename.lower()
    if lower.endswith(".pdf"):
        if not data.startswith(b"%PDF-"):
            raise ServiceError("ファイルの中身がPDFではありません。", 415, "UNSUPPORTED_TYPE")
        file_type = "PDF"
    elif lower.endswith(".xlsx"):
        file_type = "Excel"
    else:
        raise ServiceError("登録できる書類は PDF と Excel（.xlsx）です。", 415, "UNSUPPORTED_TYPE")
    meta = pf.files.save(data, filename)
    text = pdf_text(pf.files.path(meta["file_id"])) if file_type == "PDF" else excel_text(data)
    with pf.session() as s:
        valid = [d for d in drawing_ids if s.get(Drawing, d) is not None]
        doc = LibraryDocument(kind=kind, title=title.strip() or filename, file_id=meta["file_id"], filename=meta["filename"],
                              file_type=file_type, text=text, drawing_ids=valid, customer=customer.strip(), created_by=actor)
        s.add(doc)
        s.commit()
        return {"id": doc.id, "title": doc.title, "has_text": bool(text.strip())}


def document_file(pf: Platform, doc_id: int) -> tuple[bytes, str, str]:
    with pf.session() as s:
        doc = s.get(LibraryDocument, doc_id)
        if doc is None:
            raise ServiceError("書類が見つかりません。", 404, "NOT_FOUND")
        data, _ = pf.files.get(doc.file_id)
        media = "application/pdf" if doc.file_type == "PDF" else \
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        return data, doc.filename, media


def _norm(text: str) -> str:
    return unicodedata.normalize("NFKC", text or "").upper()


def snippet(text: str, query: str, width: int = 50) -> str:
    flat = re.sub(r"\s+", " ", text or "")
    i = _norm(flat).find(_norm(query))
    if i < 0:
        return flat[:width * 2]
    start = max(0, i - width)
    return ("…" if start else "") + flat[start:i + len(query) + width] + ("…" if i + len(query) + width < len(flat) else "")


def search(pf: Platform, query: str, kind: str = "") -> dict:
    """Every hit for the keyword: registered documents, drawings (number, name, customer, drawing text) and
    quotes (number, customer, title, drawing number), with a snippet around the hit."""
    q = query.strip()
    results = []
    if q:
        terms = [t for t in re.split(r"\s+", q) if t]
        hit = lambda *texts: all(any(_norm(t) in _norm(x or "") for x in texts) for t in terms)  # noqa: E731
        with pf.session() as s:
            for doc in s.scalars(select(LibraryDocument).order_by(LibraryDocument.id.desc())):
                if hit(doc.title, doc.text, doc.filename, doc.customer):
                    results.append({"type": "document", "kind": doc.kind, "id": doc.id, "title": doc.title,
                                    "snippet": snippet(doc.text or doc.title, terms[0]),
                                    "meta": " | ".join(x for x in (doc.customer, iso(doc.created_at)[:10], doc.file_type) if x),
                                    "url": f"/api/library/{doc.id}/file", "drawing_ids": doc.drawing_ids})
            for d in s.scalars(select(Drawing).options(selectinload(Drawing.revisions)).order_by(Drawing.id.desc())):
                rev = next((r for r in d.revisions if r.id == d.current_revision_id), None)
                text = rev.pdf_text if rev else ""
                if hit(d.drawing_no, d.name, d.customer, text, rev.material if rev else ""):
                    results.append({"type": "drawing", "kind": "図面", "id": d.id,
                                    "title": f"{d.drawing_no} {d.name}" + (f" Rev.{rev.revision}" if rev and rev.revision else ""),
                                    "snippet": snippet(text, terms[0]) if text and _norm(terms[0]) in _norm(text) else
                                    " ".join(x for x in (d.customer, rev.material if rev else "") if x),
                                    "meta": " | ".join(x for x in (d.customer, rev.shape_kind.upper() if rev and rev.shape_kind else "",
                                                                   "PDF" if rev and rev.pdf_file_id else "") if x),
                                    "url": f"/drawings/{d.id}"})
            drawings = {d.id: d for d in s.scalars(select(Drawing))}
            for quote in s.scalars(select(Quote).options(selectinload(Quote.case)).order_by(Quote.id.desc())):
                d = drawings.get(quote.drawing_id)
                if hit(quote.number, quote.case.customer, quote.case.title, d.drawing_no if d else "", d.name if d else ""):
                    price = (quote.result or {}).get("price")
                    results.append({"type": "quote", "kind": "見積書", "id": quote.id,
                                    "title": f"見積 {quote.number} {d.name if d else quote.case.title}",
                                    "snippet": " ".join(x for x in (d.drawing_no if d else "",
                                                                    f"単価 ¥{price['unit_price']:,} ×{price['quantity']}個" if price else "未入力あり") if x),
                                    "meta": " | ".join(x for x in (quote.case.customer, iso(quote.updated_at)[:10]) if x),
                                    "url": f"/estimates/{quote.id}"})
            for doc in s.scalars(select(IssuedDocument).order_by(IssuedDocument.id.desc())):
                if hit(doc.number, doc.customer, doc.part_name, doc.drawing_no):
                    label = {"quote": "見積書", "delivery": "納品書", "invoice": "請求書"}[doc.kind]
                    results.append({"type": "issued", "kind": "見積書" if doc.kind == "quote" else "その他", "id": doc.id,
                                    "title": f"{label} {doc.number} {doc.part_name}",
                                    "snippet": f"{doc.drawing_no} 合計 ¥{doc.total:,}（税込）",
                                    "meta": " | ".join(x for x in (doc.customer, iso(doc.issued_at)[:10], "PDF") if x),
                                    "url": f"/api/issued/{doc.id}/file.pdf"})
    counts: dict[str, int] = {}
    for r in results:
        counts[r["kind"]] = counts.get(r["kind"], 0) + 1
    if kind:
        results = [r for r in results if r["kind"] == kind]
    return {"query": q, "counts": counts, "total": sum(counts.values()), "results": results[:200], "kinds": KINDS}


def documents(pf: Platform) -> list[dict]:
    with pf.session() as s:
        return [{"id": d.id, "kind": d.kind, "title": d.title, "filename": d.filename, "file_type": d.file_type,
                 "customer": d.customer, "drawing_ids": d.drawing_ids, "created_at": iso(d.created_at),
                 "has_text": bool((d.text or "").strip())}
                for d in s.scalars(select(LibraryDocument).order_by(LibraryDocument.id.desc()))]


def delete_document(pf: Platform, doc_id: int) -> None:
    with pf.session() as s:
        doc = s.get(LibraryDocument, doc_id)
        if doc is not None:
            s.delete(doc)
            s.commit()


def recent_cases(pf: Platform, limit: int = 3) -> list[dict]:
    with pf.session() as s:
        rows = s.scalars(select(Case).options(selectinload(Case.status), selectinload(Case.quotes))
                         .order_by(Case.updated_at.desc(), Case.id.desc()).limit(limit))
        drawings = {d.id: d for d in s.scalars(select(Drawing))}
        out = []
        for c in rows:
            q = c.quotes[-1] if c.quotes else None
            d = drawings.get(q.drawing_id) if q else None
            out.append({"case_id": c.id, "quote_id": q.id if q else None, "drawing_no": d.drawing_no if d else "",
                        "name": d.name if d else c.title, "status": c.status.name, "number": c.number})
        return out
