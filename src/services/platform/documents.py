"""Issuing documents: 見積書 (from a quote whose amount items are all entered), 納品書 and 請求書 (from an accepted
quote, with the amounts of its issued 見積書). The API refuses an issue when an item is missing (409 with the
list), whatever the screen shows. Templates switch printed items only; costs and margin are never printed.
"""
from __future__ import annotations

import io
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from src.db.models import (Case, CaseStatus, CaseStatusLog, Drawing, DrawingRevision, IssuedDocument, PastQuoteRow, Quote,
                           Template, now)
from src.past_quotes import from_document
from src.quote_document import PartInfo, QuoteDocument, Recipient, build_document
from src.quote_pdf import render_quote
from src.services.estimate import ServiceError
from src.services.platform.core import Platform, bump, iso
from src.services.platform.pricing import priced
from src.services.platform.quotes import _inputs, compute, document_out
from src.trade_pdf import TradeDocument, TradeLine, render_trade

KINDS = {"quote": "見積書", "delivery": "納品書", "invoice": "請求書"}
UNIT_LABEL = {"kg": "kg", "mm": "mm", "穴": "穴", "曲げ": "か所", "式": "式", "hole": "か所", "bend": "か所",
              "piece": "個", "point": "点", "location": "か所", "part": "個", "job": "式", "個": "個"}


class NotIssuable(ServiceError):
    def __init__(self, message: str, missing: list[dict]) -> None:
        super().__init__(message, 409, "NOT_ISSUABLE")
        self.missing = missing


def template_for(s, template_id: int | None, customer: str) -> Template | None:
    if template_id:
        t = s.get(Template, template_id)
        if t is None:
            raise ServiceError("テンプレートが見つかりません。", 404, "NOT_FOUND")
        return t
    templates = list(s.scalars(select(Template).order_by(Template.sort)))
    return next((t for t in templates if customer in (t.customers or [])), None) or \
        next((t for t in templates if t.is_default), templates[0] if templates else None)


def _quote_document(pf: Platform, s, quote: Quote, number: str, issued_at: datetime, person: str = "",
                    subject: str = "", remarks: str = "") -> tuple[QuoteDocument, object, object]:
    masters = pf.masters()
    inputs = _inputs(quote)
    revision = s.get(DrawingRevision, quote.revision_id) if quote.revision_id else None
    drawing = s.get(Drawing, quote.drawing_id) if quote.drawing_id else None
    result = compute(inputs, masters, quote.analysis, quote.reading)
    if result["missing"]:
        raise NotIssuable("未入力の項目があるため発行できません: " + "／".join(m["message"] for m in result["missing"]),
                          result["missing"])
    analysis, condition, quote_result, overlay = priced(inputs, masters, quote.analysis, revision.shape_name if revision else "")
    part = PartInfo(name=(drawing.name if drawing else "") or quote.case.title, drawing_no=drawing.drawing_no if drawing else "",
                    revision=revision.revision if revision else "",
                    shape_file=revision.shape_name if revision and revision.shape_name else "",
                    drawing_file=revision.pdf_name if revision else "")
    free = [remarks.strip()] if remarks.strip() else []
    if inputs.due_date:
        free.insert(0, f"ご希望納期：{inputs.due_date:%Y/%m/%d}")
    document = build_document(
        analysis=analysis, condition=condition, quote=quote_result, masters=overlay, company=pf.company(),
        recipient=Recipient(quote.case.customer, person), part=part, issued_at=issued_at, number=number,
        subject=subject or quote.case.title, free_remarks="\n".join(free),
        flags=(quote.reading or {}).get("flags") or [])
    return document, quote_result, overlay


def breakdown(quote_result, overlay) -> list[tuple[str, str]]:
    """The work items of the quote, names and quantities only (no costs: never printed for the customer)."""
    out = []
    for line in quote_result.lines:
        if line.code == "RUSH":
            out.append(("特急対応", ""))
            continue
        name = line.name.replace("材料費", "材料")
        unit = UNIT_LABEL.get(line.unit, line.unit)
        out.append((name, f"{line.quantity:,.4g} {unit}" if line.code != "SETUP" else ""))
    return out


def preview(pf: Platform, quote_id: int, kind: str, template_id: int | None, png: bool = True) -> bytes:
    with pf.session() as s:
        quote, template = _load(s, quote_id, kind, template_id)
        data = _render(pf, s, quote, kind, template, "（プレビュー）", now(), preview=True)[0]
    return to_png(data) if png else data


def _load(s, quote_id: int, kind: str, template_id: int | None):
    if kind not in KINDS:
        raise ServiceError("帳票の種類は quote / delivery / invoice です。")
    quote = s.get(Quote, quote_id, options=[selectinload(Quote.case).selectinload(Case.status)])
    if quote is None:
        raise ServiceError("見積が見つかりません。", 404, "NOT_FOUND")
    return quote, template_for(s, template_id, quote.case.customer)


def _render(pf: Platform, s, quote: Quote, kind: str, template: Template | None, number: str, issued_at: datetime,
            person: str = "", remarks: str = "", preview: bool = False):
    options = dict(template.options or {}) if template else {}
    if kind == "quote":
        document, quote_result, overlay = _quote_document(pf, s, quote, number, issued_at, person, remarks=remarks)
        data = render_quote(document, options, breakdown(quote_result, overlay))
        line = document.lines[0]
        snapshot = {"lines": [{"name": l.name, "drawing_no": l.drawing_no, "revision": l.revision, "spec": l.spec,
                               "quantity": l.quantity, "unit": l.unit, "unit_price": l.unit_price, "amount": l.amount}
                              for l in document.lines],
                    "subject": document.subject, "person": person, "tax_rate": document.tax_rate,
                    "valid_until": iso(document.valid_until), "remarks": document.remarks}
        amounts = {"quantity": line.quantity, "unit_price": line.unit_price, "subtotal": document.subtotal,
                   "tax_rate": document.tax_rate, "tax": document.tax, "total": document.total}
        return data, amounts, snapshot, document
    if quote.case.outcome != "受注":
        raise NotIssuable(f"{KINDS[kind]}は受注した見積から作ります（今の状態: {quote.case.status.name}）。",
                          [{"field": "outcome", "label": "受注", "message": "案件が受注になっていません"}])
    issued = s.scalars(select(IssuedDocument).where(IssuedDocument.quote_id == quote.id, IssuedDocument.kind == "quote")
                       .order_by(IssuedDocument.id.desc())).first()
    if issued is None:
        raise NotIssuable("先に見積書を発行してください（納品書・請求書は発行した見積書と同じ金額で作ります）。",
                          [{"field": "quote_document", "label": "見積書", "message": "見積書が発行されていません"}])
    snap = issued.snapshot or {}
    company = pf.company()
    lines = [TradeLine(name=l["name"], drawing_no=l.get("drawing_no", ""), revision=l.get("revision", ""),
                       quantity=l["quantity"], unit=l.get("unit", "個"), unit_price=l["unit_price"], amount=l["amount"])
             for l in snap.get("lines") or []]
    due = None
    if kind == "invoice":
        end = (issued_at.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)  # end of this month
        due = ((end + timedelta(days=1)).replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)  # next month end
    remarks_list = [f"見積番号 {issued.number}（{issued.issued_at:%Y/%m/%d}）による。"] + ([remarks.strip()] if remarks.strip() else [])
    doc = TradeDocument(kind=kind, number=number, issued_at=issued_at, recipient=issued.customer, person=person,
                        subject=snap.get("subject", ""), quote_no=issued.number, company=company, lines=lines,
                        tax_rate=issued.tax_rate, subtotal=issued.subtotal, tax=issued.tax, total=issued.total,
                        remarks=remarks_list, due_date=due.date() if due else None, options=options)
    amounts = {"quantity": issued.quantity, "unit_price": issued.unit_price, "subtotal": issued.subtotal,
               "tax_rate": issued.tax_rate, "tax": issued.tax, "total": issued.total}
    return render_trade(doc), amounts, {**snap, "quote_document": issued.number}, None


def issue(pf: Platform, quote_id: int, kind: str, template_id: int | None, actor: str, person: str = "",
          remarks: str = "") -> dict:
    with pf.session() as s:
        quote, template = _load(s, quote_id, kind, template_id)
        issued_at = now()
        if kind == "quote":
            count = len(list(s.scalars(select(IssuedDocument.id).where(IssuedDocument.quote_id == quote.id,
                                                                         IssuedDocument.kind == "quote"))))
            number = quote.number if count == 0 else f"{quote.number}-{count + 1}"
        else:
            prefix = "D" if kind == "delivery" else "I"
            number = f"{prefix}-{issued_at.year}-{bump(s, f'{kind}-{issued_at.year}'):04d}"
        data, amounts, snapshot, document = _render(pf, s, quote, kind, template, number, issued_at, person, remarks)
        drawing = s.get(Drawing, quote.drawing_id) if quote.drawing_id else None
        meta = pf.files.save(data, f"{number}_{KINDS[kind]}.pdf")
        row = IssuedDocument(kind=kind, number=number, case_id=quote.case_id, quote_id=quote.id,
                             template_id=template.id if template else None, issued_at=issued_at, issued_by=actor,
                             customer=quote.case.customer, part_name=(drawing.name if drawing else quote.case.title),
                             drawing_no=drawing.drawing_no if drawing else "", file_id=meta["file_id"],
                             filename=meta["filename"], snapshot=snapshot, **amounts)
        s.add(row)
        case = quote.case
        if kind == "quote":
            record_history(pf, s, quote, document)
            if case.first_issued_at is None:
                case.first_issued_at = issued_at
            issued_status = s.scalars(select(CaseStatus).where(CaseStatus.role == "issued")).first()
            if issued_status and case.status.group == "見積・受注" and case.status.sort < issued_status.sort:
                case.status, case.status_changed_at = issued_status, issued_at
                s.add(CaseStatusLog(case_id=case.id, status_name=issued_status.name, actor=actor))
        case.updated_at, case.updated_by = issued_at, actor
        s.commit()
        return document_out(row)


def record_history(pf: Platform, s, quote: Quote, document: QuoteDocument) -> None:
    """The issued quotation enters the history (one row per case: a re-issue replaces it) for the similar search
    and the review."""
    past = from_document(document, _overlay_for(pf, quote))
    for old in s.scalars(select(PastQuoteRow).where(PastQuoteRow.case_id == quote.case_id)):
        s.delete(old)
    s.flush()
    case = quote.case
    fields = {f: getattr(past, f) for f in past.__dataclass_fields__}
    fields.update(staff=case.staff, outcome=case.outcome, lost_reason=case.lost_reason)
    days = (document.issued_at - case.created_at).total_seconds() / 86400
    s.add(PastQuoteRow(**fields, case_id=case.id, drawing_id=quote.drawing_id, answer_days=round(max(days, 0), 2)))
    bump(s, "history_rev")


def _overlay_for(pf: Platform, quote: Quote):
    from src.services.platform.pricing import condition_of

    return condition_of(_inputs(quote), pf.masters())[1]


def issued_list(pf: Platform, kind: str = "", limit: int = 50) -> list[dict]:
    with pf.session() as s:
        query = select(IssuedDocument).order_by(IssuedDocument.id.desc()).limit(limit)
        if kind:
            query = query.where(IssuedDocument.kind == kind)
        return [document_out(d) for d in s.scalars(query)]


def issued_file(pf: Platform, doc_id: int) -> tuple[bytes, str]:
    with pf.session() as s:
        d = s.get(IssuedDocument, doc_id)
        if d is None:
            raise ServiceError("帳票が見つかりません。", 404, "NOT_FOUND")
        data, _ = pf.files.get(d.file_id)
        return data, d.filename


def to_png(pdf: bytes, width: int = 1100) -> bytes:
    import pypdfium2 as pdfium

    from src.services.platform.drawings import PDFIUM_LOCK

    with PDFIUM_LOCK:
        doc = pdfium.PdfDocument(pdf)
        page = doc[0]
        image = page.render(scale=width / page.get_width()).to_pil()
        page.close()
        doc.close()
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def template_preview(pf: Platform, template_id: int, quote_id: int | None = None) -> bytes:
    """A template applied to a quote (the latest one that can be issued when none is given)."""
    with pf.session() as s:
        template = s.get(Template, template_id)
        if template is None:
            raise ServiceError("テンプレートが見つかりません。", 404, "NOT_FOUND")
        quotes = [s.get(Quote, quote_id)] if quote_id else list(s.scalars(select(Quote).order_by(Quote.updated_at.desc()).limit(30)))
        for quote in quotes:
            if quote is None or (quote.result or {}).get("missing"):
                continue
            try:
                data = _render(pf, s, quote, "quote", template, "（見本）", now(), preview=True)[0]
                return to_png(data)
            except ServiceError:
                continue
    raise ServiceError("見本に使える見積がありません（未入力の項目がない見積を1件作ってください）。", 404, "NO_SAMPLE")
