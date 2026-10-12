"""Estimates of the screens: create one from a registered drawing (a background job with stages the screen
follows), read it (amounts, missing items, hints, similar results), change its conditions (also by chat), the
edit history. Every amount comes from src/services/platform/pricing.compute (QuoteEngine), on the server.
"""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from src.models import SheetMetalAnalysis
from src.db.models import (Case, CaseStatus, CaseStatusLog, Drawing, DrawingRevision, IssuedDocument, Quote,
                           QuoteEditLog, now)
from src.db.retry import retry_on_conflict
from src.services.estimate import ServiceError
from src.services.jobs import JobError, report_progress
from src.services.platform.cases import next_action, status_brief
from src.services.platform.core import Platform, bump, check_version, iso
from src.services.extraction import extract
from src.services.platform.drawings import reading_for_pricing, reading_with_context, sync_revision
from src.services.platform.pricing import QuoteInputs, compute, evidence_texts, inputs_from_reading
from src.services.platform.similar import similar
from src.services.platform.wording import failure, input_errors, notes

ANALYSIS_KEYS = ("k_factor", "k_factor_confirmed", "thickness_mm", "flat_confirmed")
STAGES = ["CADデータの解析", "図面の読み取り", "マスタ照合", "見積計算", "類似実績の検索"]


class EstimateCreate(BaseModel):
    drawing_id: int
    revision_id: int | None = None
    customer: str = Field(min_length=1, description="顧客（必須）")
    title: str = ""
    quantity: int = Field(gt=0, description="数量（必須）")
    due_date: date | None = None
    staff: str = ""
    material: str | None = None
    custom_material: dict | None = None
    surface_treatment: str | None = None
    custom_finish: dict | None = None
    rush: bool | None = None
    processes: list[dict] | None = None
    custom_processes: list[dict] | None = None
    k_factor: float = Field(0.33, ge=0, le=1)
    k_factor_confirmed: bool = False
    thickness_mm: float | None = Field(None, gt=0)
    flat_confirmed: bool = False


class EstimateUpdate(BaseModel):
    version: int = Field(description="開いたときの版。ほかの人が先に保存していたら 409")
    inputs: dict[str, Any] = Field(default_factory=dict, description="変える見積の条件（QuoteInputs の一部）")
    customer: str | None = None
    title: str | None = None
    staff: str | None = None
    due_date: date | None = None
    note: str = ""


def next_case_number(s, day: date | None = None) -> str:
    year = (day or date.today()).year
    return f"Q-{year}-{bump(s, f'case-{year}'):04d}"


def status_by_role(s, role: str) -> CaseStatus:
    row = s.scalars(select(CaseStatus).where(CaseStatus.role == role).order_by(CaseStatus.sort)).first()
    return row or s.scalars(select(CaseStatus).order_by(CaseStatus.sort)).first()


def create(pf: Platform, body: EstimateCreate, actor: str) -> dict:
    masters = pf.masters()
    with pf.session() as s:
        drawing = s.get(Drawing, body.drawing_id, options=[selectinload(Drawing.revisions)])
        if drawing is None:
            raise ServiceError("図面が見つかりません。見積は登録済みの図面から作成します。", 404, "NOT_FOUND")
        revision = next((r for r in drawing.revisions if r.id == (body.revision_id or drawing.current_revision_id)), None)
        if revision is None:
            raise ServiceError("図面の版が見つかりません。", 404, "NOT_FOUND")
        sync_revision(pf, s, revision)
        given = {k: v for k, v in body.model_dump(exclude_unset=True, exclude={"drawing_id", "revision_id"}).items()
                 if v is not None}
        if "material" not in given and "custom_material" not in given and revision.material in masters.materials:
            given["material"] = revision.material
        if "surface_treatment" not in given and revision.surface_treatment in masters.surface_treatments:
            given["surface_treatment"] = revision.surface_treatment
        if revision.shape_kind == "dxf" and not given.get("thickness_mm"):
            reading = reading_for_pricing(revision) or {}
            thickness = revision.thickness_mm or reading.get("thickness_mm")
            if thickness:
                given["thickness_mm"] = float(thickness)
        try:
            inputs = QuoteInputs(**given)
        except ValidationError as exc:
            raise ServiceError("見積の条件が正しくありません: " + input_errors(exc.errors())) from None
        number = next_case_number(s)
        status = status_by_role(s, "drafting")
        case = Case(number=number, customer=inputs.customer.strip(), title=inputs.title.strip(), status=status,
                    staff=inputs.staff, due_date=inputs.due_date, updated_by=actor)
        s.add(case)
        s.flush()
        s.add(CaseStatusLog(case_id=case.id, status_name=status.name, actor=actor))
        quote = Quote(case=case, number=number, drawing_id=drawing.id, revision_id=revision.id,
                      inputs=inputs.model_dump(mode="json", exclude_unset=False), updated_by=actor)
        quote.inputs["_given"] = sorted(inputs.model_fields_set)
        s.add(quote)
        s.flush()
        job = pf.submit("estimate", {"quote_id": quote.id}, start=False)
        quote.job_id = job["job_id"]
        s.commit()
    pf.start(job["job_id"])  # only after the commit: the job reads the quote
    return {"estimate_id": quote.id, "number": number, "job_id": job["job_id"]}


def _inputs(quote: Quote) -> QuoteInputs:
    """The stored inputs; `_given` keeps which fields a person set (the drawing reading fills only the others)."""
    data = dict(quote.inputs or {})
    given = data.pop("_given", None)
    inputs = QuoteInputs(**data)
    if given is not None:
        object.__setattr__(inputs, "__pydantic_fields_set__", set(given))
    return inputs


def _store(quote: Quote, inputs: QuoteInputs) -> None:
    data = inputs.model_dump(mode="json")
    data["_given"] = sorted(inputs.model_fields_set)
    quote.inputs = data


def run_estimate_job(pf: Platform, payload: dict) -> dict:
    """The stages of an estimate (shown on the progress screen with the values found so far).
    Three steps (src/services/extraction.py): prepare (database) -> extract (files only) -> finish (database)."""
    values: dict = {}

    def stage(i: int, **found) -> None:
        values.update(found)
        report_progress(step=i + 1, steps=len(STAGES), stage=STAGES[i], stages=STAGES, values=dict(values))

    stage(0)
    plan = retry_on_conflict(lambda: prepare_estimate(pf, payload))

    def progress(**p) -> None:
        if p["stage"] == STAGES[1]:  # the shape is known before the drawing is read
            stage_shape(values, p.get("analysis") if plan["analysis"] else plan["analysis_done"])
            stage(1)

    extracted = extract(pf.service.extractor(progress), plan)
    return retry_on_conflict(lambda: finish_estimate(pf, payload, plan, extracted, stage, values))


def stage_shape(values: dict, analysis: dict | None) -> None:
    a = analysis or {}
    values["shape"] = {k: a.get(k) for k in ("thickness_mm", "blank_area_mm2", "cut_length_mm", "hole_count", "bend_count")}
    values["shape_status"] = shape_state(a) if a else "CADデータなし"


def prepare_estimate(pf: Platform, payload: dict) -> dict:
    """What the estimate needs from the files: a new analysis when its conditions differ from the registration's,
    a reading when the drawing has none yet (and no draft reading is still running)."""
    with pf.session() as s:
        quote = s.get(Quote, payload["quote_id"])
        if quote is None:
            raise JobError("見積が見つかりません。")
        revision = s.get(DrawingRevision, quote.revision_id) if quote.revision_id else None
        inputs = _inputs(quote)
        request, done = analysis_request(revision, inputs)
        read = False
        if revision is not None:
            sync_revision(pf, s, revision)
            read = bool(revision.pdf_file_id and revision.reading is None and not (
                revision.reading_job_id and (pf.job(revision.reading_job_id) or {}).get("status") in ("queued", "running")))
        return {"analysis": request, "analysis_done": done, "pdf_file_id": revision.pdf_file_id if read else None,
                "pdf_text": False, "read": read}


def finish_estimate(pf: Platform, payload: dict, plan: dict, extracted: dict, stage, values: dict) -> dict:
    masters = pf.masters()
    with pf.session() as s:
        quote = s.get(Quote, payload["quote_id"])
        if quote is None:
            raise JobError("見積が見つかりません。")
        revision = s.get(DrawingRevision, quote.revision_id) if quote.revision_id else None
        inputs = _inputs(quote)
        analysis = extracted["analysis"] if plan["analysis"] else plan["analysis_done"]
        quote.analysis = analysis
        if "shape" not in values:
            stage_shape(values, analysis)
            stage(1)
        if revision is not None and plan["read"] and revision.reading is None:
            reading = reading_with_context(pf, masters, extracted["reading"], revision.pdf_name)
            if reading:
                revision.reading = reading
        reading = reading_for_pricing(revision)
        quote.reading = reading
        values["reading"] = ({"items": len([k for k in ("material", "thickness_mm", "quantity", "surface_treatment", "rush")
                                            if reading.get(k) is not None]) + len(reading.get("processes") or []),
                              "review": len(reading.get("needs_review") or [])} if reading else None)
        stage(2)
        inputs = inputs_from_reading(reading, masters, inputs)
        _store(quote, inputs)
        values["matching"] = {"material": inputs.material or (inputs.custom_material.name + "（未登録）" if inputs.custom_material else None),
                              "processes": len(inputs.processes),
                              "unregistered": len(inputs.custom_processes) + int(bool(inputs.custom_finish)) + int(bool(inputs.custom_material))}
        stage(3)
        result = compute(inputs, masters, analysis, reading, file_name=revision.shape_name if revision else "",
                         dxf=_is_dxf(revision))
        quote.result = result
        values["price"] = result["price"]["subtotal"] if result["price"] else None
        values["missing"] = len(result["missing"])
        stage(4)
        found = similar(pf, inputs.material, _metric(result, "bend_count"), _metric(result, "hole_count"), quote.drawing_id)
        values["similar"] = {"count": len(found), "top": found[0]["drawing_no"] if found else None}
        report_progress(step=len(STAGES), steps=len(STAGES), stage="完了", stages=STAGES, values=dict(values))
        quote.updated_at = now()
        s.commit()
    return {"quote_id": payload["quote_id"], "values": values}


def _is_dxf(revision: DrawingRevision | None) -> bool:
    return bool(revision and revision.shape_kind == "dxf" and revision.shape_file_id)


def _metric(result: dict, name: str):
    return (result.get("shape") or {}).get(name, {}).get("value")


def analysis_request(revision: DrawingRevision | None, inputs: QuoteInputs) -> tuple[dict | None, dict | None]:
    """(a request for a new analysis with this quote's conditions, or None; the analysis to use when no new one is
    needed: the registration's when the conditions are the same, else None)."""
    if revision is None or not revision.shape_file_id:
        return None, None
    if revision.shape_kind == "dxf" and not inputs.thickness_mm:
        return None, None
    same = (revision.analysis is not None and inputs.k_factor == 0.33 and not inputs.k_factor_confirmed
            and not inputs.flat_confirmed and (revision.shape_kind != "dxf" or inputs.thickness_mm == revision.thickness_mm))
    if same:
        return None, revision.analysis
    return {"file_id": revision.shape_file_id, "file_name": revision.shape_name, "thickness_mm": inputs.thickness_mm,
            "k_factor": inputs.k_factor, "k_factor_confirmed": inputs.k_factor_confirmed,
            "flat_confirmed": inputs.flat_confirmed}, None


# ---------------------------------------------------------------- reading
def detail(pf: Platform, quote_id: int) -> dict:
    masters = pf.masters()
    with pf.session() as s:
        quote = s.get(Quote, quote_id, options=[selectinload(Quote.case).selectinload(Case.status)])
        if quote is None:
            raise ServiceError("見積が見つかりません。", 404, "NOT_FOUND")
        case = quote.case
        drawing = s.get(Drawing, quote.drawing_id) if quote.drawing_id else None
        revision = s.get(DrawingRevision, quote.revision_id) if quote.revision_id else None
        job = pf.job(quote.job_id)
        pending = bool(job and job["status"] in ("queued", "running"))
        inputs = _inputs(quote)
        result = compute(inputs, masters, quote.analysis, quote.reading, analysis_pending=pending,
                         file_name=revision.shape_name if revision else "", dxf=_is_dxf(revision))
        log = [{"at": iso(e.at), "actor": e.actor, "summary": e.summary, "total_before": e.total_before,
                "total_after": e.total_after}
               for e in s.scalars(select(QuoteEditLog).where(QuoteEditLog.quote_id == quote.id).order_by(QuoteEditLog.id.desc()))]
        docs = [document_out(d) for d in s.scalars(select(IssuedDocument).where(IssuedDocument.case_id == case.id)
                                                   .order_by(IssuedDocument.id.desc()))]
        found = similar(pf, inputs.material, _metric(result, "bend_count"), _metric(result, "hole_count"), quote.drawing_id)
        a = quote.analysis or {}
        out = {
            "id": quote.id, "number": quote.number, "version": quote.version, "case_id": case.id,
            "case_version": case.version, "customer": case.customer, "title": case.title, "staff": case.staff,
            "due_date": iso(case.due_date), "status": status_brief(case.status), "phase": case.status.phase,
            "outcome": case.outcome, "created_at": iso(quote.created_at), "updated_at": iso(quote.updated_at),
            "inputs": inputs.model_dump(mode="json"),
            "drawing": {"id": drawing.id, "drawing_no": drawing.drawing_no, "name": drawing.name,
                        "revision": revision.revision if revision else "", "revision_id": revision.id if revision else None,
                        "pdf_file_id": revision.pdf_file_id if revision else "", "shape_file_id": revision.shape_file_id if revision else "",
                        "shape_kind": revision.shape_kind if revision else "", "shape_name": revision.shape_name if revision else "",
                        "pdf_name": revision.pdf_name if revision else ""} if drawing else None,
            "analysis": {k: a.get(k) for k in ("status", "file_name", "thickness_mm", "blank_area_mm2", "cut_length_mm",
                                               "hole_count", "bend_count", "reason_code", "reason_codes", "message",
                                               "warnings", "assumptions", "flat_pattern")}
                         | {"state": shape_state(a), "notes": notes(a),
                            "failure": failure(a) if shape_state(a) == "解析不可" else None} if a else None,
            "job": {"job_id": job["job_id"], "status": job["status"], "error": job.get("error"),
                    "progress": job.get("progress")} if job else None,
            "result": result, "edit_log": log, "documents": docs, "chat": quote.chat or [],
            "blockers": blockers(result, case, docs),
            "notices": notices(result, docs),
            "similar": found,
            "similar_compare": compare(found, result, drawing.drawing_no if drawing else ""),
            "next_action": next_action(case, quote, drawing, len(result["missing"]), pending, {d["kind"] for d in docs}),
            "flow_step": flow_step(case.status.phase, pending),
            "outcome_statuses": {role: {"id": st.id, "name": st.name} for role in ("won", "lost")
                                 if (st := status_by_role_only(s, role)) is not None},
        }
    return out


FLOW = ["図面", "条件", "解析・計算", "内容確認", "見積書発行", "受注・失注の登録"]


def flow_step(phase: str, pending: bool) -> int:
    """Where the estimate is in the six steps of FLOW (1-based; 7 = all done)."""
    if pending:
        return 3
    return {"drafting": 4, "checking": 4, "waiting": 6}.get(phase, 7)


def status_by_role_only(s, role: str) -> CaseStatus | None:
    return s.scalars(select(CaseStatus).where(CaseStatus.role == role).order_by(CaseStatus.sort)).first()


def compare(found: list[dict], result: dict, drawing_no: str) -> dict | None:
    """This quote's unit price against the closest similar result with a price (the same drawing number first):
    {item, rate} with rate = this / that - 1, and `rates`: the same for every similar item (in their order)."""
    unit = (result.get("price") or {}).get("unit_price")
    rates = [(unit / x["unit_price"] - 1) if unit and x.get("unit_price") else None for x in found]
    priced = [i for i, x in enumerate(found) if x.get("unit_price")]
    if not priced:
        return {"item": None, "same_drawing": False, "rate": None, "count": len(found), "rates": rates}
    same = [i for i in priced if drawing_no and found[i].get("drawing_no") == drawing_no]
    i = (same or priced)[0]
    return {"item": found[i], "same_drawing": bool(same), "rate": rates[i], "count": len(found), "rates": rates}


def shape_state(a: dict) -> str:
    """確定 / 概算 / 解析不可 of an analysis, by the rule of QuoteEngine.is_estimate (partial, assumptions, a value
    of medium or low confidence)."""
    if a.get("status") not in ("success", "partial"):
        return "解析不可"
    analysis = SheetMetalAnalysis.model_validate(a)
    estimated = analysis.status == "partial" or bool(analysis.assumptions) or any(
        q.confidence in {"medium", "low"} for q in analysis.metric_quality.values())
    return "概算" if estimated else "確定"


def blockers(result: dict, case: Case, docs: list[dict]) -> dict:
    """Why each document cannot be issued now (empty list: it can). The same rules refuse the issue in
    src/services/platform/documents.py."""
    quote = [m["message"] for m in result["missing"]]
    trade = []
    if case.outcome != "受注":
        trade.append(f"受注登録が必要です（ステータス：{case.status.name}）")
    if not any(d["kind"] == "quote" for d in docs):
        trade.append("先に見積書を発行してください（同じ金額で発行します）")
    return {"quote": quote, "delivery": list(trade), "invoice": list(trade)}


def notices(result: dict, docs: list[dict]) -> dict:
    """Points worth a look before issuing a document that do not block it: the delivery note and the invoice copy
    the last issued quotation, so say so when the estimate has changed since."""
    last = next((d for d in docs if d["kind"] == "quote"), None)  # docs: newest first
    price = result.get("price")
    trade = []
    if last and (price is None or (price["subtotal"], price["quantity"]) != (last["subtotal"], last["quantity"])):
        trade.append(f"見積書 {last['number']} の発行後に見積の条件が変わっています。納品書・請求書は発行済みの見積書の金額"
                     f"（{last['quantity']}個・税込 ¥{last['total']:,}）で作成します。今の条件にするには見積書を再発行してください")
    return {"quote": [], "delivery": list(trade), "invoice": list(trade)}


def document_out(d: IssuedDocument) -> dict:
    return {"id": d.id, "kind": d.kind, "number": d.number, "issued_at": iso(d.issued_at), "issued_by": d.issued_by,
            "customer": d.customer, "part_name": d.part_name, "drawing_no": d.drawing_no, "quantity": d.quantity,
            "unit_price": d.unit_price, "subtotal": d.subtotal, "tax": d.tax, "total": d.total,
            "case_id": d.case_id, "quote_id": d.quote_id, "url": f"/api/issued/{d.id}/file.pdf"}


def list_estimates(pf: Platform, limit: int = 200) -> list[dict]:
    from src.services.platform.cases import warnings_of

    with pf.session() as s:
        quotes = list(s.scalars(select(Quote).options(selectinload(Quote.case).selectinload(Case.status))
                                .order_by(Quote.updated_at.desc(), Quote.id.desc()).limit(limit)))
        drawings = {d.id: d for d in s.scalars(select(Drawing).where(Drawing.id.in_([q.drawing_id for q in quotes if q.drawing_id])))}
        revisions = {r.id: r for r in s.scalars(select(DrawingRevision).where(
            DrawingRevision.id.in_([q.revision_id for q in quotes if q.revision_id])))}
        out = []
        for q in quotes:
            d, r = drawings.get(q.drawing_id), revisions.get(q.revision_id)
            price = (q.result or {}).get("price")
            out.append({"id": q.id, "number": q.number, "case_id": q.case_id, "customer": q.case.customer,
                        "title": q.case.title, "name": d.name if d else "", "drawing_no": d.drawing_no if d else "",
                        "revision": r.revision if r else "", "revision_id": r.id if r else None,
                        "has_pdf": bool(r and r.pdf_file_id),
                        "status": {"name": q.case.status.name, "color": q.case.status.color, "phase": q.case.status.phase},
                        "quantity": (q.inputs or {}).get("quantity"), "subtotal": price["subtotal"] if price else None,
                        "due_date": iso(q.case.due_date), "staff": q.case.staff, "warnings": warnings_of(q.case),
                        "missing": len((q.result or {}).get("missing") or []), "updated_at": iso(q.updated_at)})
    return out


# ---------------------------------------------------------------- changing
def update(pf: Platform, quote_id: int, body: EstimateUpdate, actor: str) -> dict:
    masters = pf.masters()
    with pf.session() as s:
        quote = s.get(Quote, quote_id, options=[selectinload(Quote.case)])
        if quote is None:
            raise ServiceError("見積が見つかりません。", 404, "NOT_FOUND")
        check_version(quote, body.version, "見積")
        before = _inputs(quote)
        old_total = ((quote.result or {}).get("price") or {}).get("subtotal")
        data = before.model_dump(mode="json")
        data.update(body.inputs)
        for key in ("customer", "title", "staff", "due_date"):
            value = getattr(body, key)
            if value is not None:
                data[key] = value.isoformat() if isinstance(value, date) else value
        try:
            inputs = QuoteInputs(**data)
        except ValidationError as exc:
            raise ServiceError("見積の条件が正しくありません: " + input_errors(exc.errors())) from None
        object.__setattr__(inputs, "__pydantic_fields_set__", set(before.model_fields_set) | set(body.inputs))
        if not inputs.customer.strip():
            raise ServiceError("顧客は必須です。")
        revision = s.get(DrawingRevision, quote.revision_id) if quote.revision_id else None
        request = None
        if any(getattr(inputs, k) != getattr(before, k) for k in ANALYSIS_KEYS):
            # a new analysis with the new conditions runs as a job (the CAD analysis is not done in the API)
            request, done = analysis_request(revision, inputs)
            quote.analysis = None if request else done
        _store(quote, inputs)
        pending = bool(request) or _reanalysing(pf, quote)
        quote.result = compute(inputs, masters, quote.analysis, quote.reading, analysis_pending=pending,
                               file_name=revision.shape_name if revision else "", dxf=_is_dxf(revision))
        quote.updated_at, quote.updated_by = now(), actor
        case = quote.case
        case.customer, case.title, case.staff, case.due_date = inputs.customer.strip(), inputs.title.strip(), inputs.staff, inputs.due_date
        case.updated_at, case.updated_by = now(), actor
        summary = changes(before, inputs, masters) or "条件を保存"
        if body.note:
            summary += f"（{body.note}）"
        log = QuoteEditLog(quote_id=quote.id, actor=actor, summary=summary, total_before=old_total,
                           total_after=((quote.result or {}).get("price") or {}).get("subtotal"))
        s.add(log)
        job = None
        if request:
            s.flush()
            job = pf.submit("reanalysis", {"quote_id": quote.id, "log_id": log.id}, start=False)
            quote.job_id = job["job_id"]
        s.commit()
    if job:
        pf.start(job["job_id"])  # only after the commit: the job reads the new conditions
    return detail(pf, quote_id)


def _reanalysing(pf: Platform, quote: Quote) -> bool:
    job = pf.job(quote.job_id) if quote.job_id else None
    return bool(job and job["kind"] == "reanalysis" and job["status"] in ("queued", "running"))


def run_reanalysis_job(pf: Platform, payload: dict) -> dict:
    """The analysis again with a quote's changed conditions (k factor, thickness of a flat pattern, flat), in the
    same three steps as an estimate: prepare (database) -> extract (files only) -> finish (database)."""
    report_progress(step=1, steps=1, stage=STAGES[0], stages=STAGES[:1])
    plan = retry_on_conflict(lambda: prepare_reanalysis(pf, payload))
    extracted = extract(pf.service.extractor(), plan) if plan["analysis"] else {"analysis": None}
    return retry_on_conflict(lambda: finish_reanalysis(pf, payload, plan, extracted))


def prepare_reanalysis(pf: Platform, payload: dict) -> dict:
    with pf.session() as s:
        quote = s.get(Quote, payload["quote_id"])
        if quote is None:
            raise JobError("見積が見つかりません。")
        revision = s.get(DrawingRevision, quote.revision_id) if quote.revision_id else None
        request, done = analysis_request(revision, _inputs(quote))
        return {"analysis": request, "analysis_done": done, "pdf_file_id": None, "pdf_text": False, "read": False}


def finish_reanalysis(pf: Platform, payload: dict, plan: dict, extracted: dict) -> dict:
    masters = pf.masters()
    with pf.session() as s:
        quote = s.get(Quote, payload["quote_id"])
        if quote is None:
            raise JobError("見積が見つかりません。")
        revision = s.get(DrawingRevision, quote.revision_id) if quote.revision_id else None
        inputs = _inputs(quote)
        request, done = analysis_request(revision, inputs)
        if request != plan["analysis"]:
            return {"quote_id": quote.id, "superseded": True}  # the conditions changed again: a later job analyses
        quote.analysis = extracted["analysis"] if request else done
        quote.result = compute(inputs, masters, quote.analysis, quote.reading,
                               file_name=revision.shape_name if revision else "", dxf=_is_dxf(revision))
        subtotal = ((quote.result or {}).get("price") or {}).get("subtotal")
        log = s.get(QuoteEditLog, payload.get("log_id")) if payload.get("log_id") else None
        if log is not None and log.quote_id == quote.id:
            log.total_after = subtotal  # the amount after the change is known now
        quote.updated_at = now()
        s.commit()
    return {"quote_id": payload["quote_id"], "subtotal": subtotal}


LABELS = {"customer": "顧客", "title": "案件名", "staff": "担当", "quantity": "数量", "due_date": "希望納期",
          "material": "材質", "custom_material": "材質（マスタ未登録）", "surface_treatment": "表面処理",
          "custom_finish": "表面処理（マスタ未登録）", "rush": "特急", "processes": "追加加工",
          "custom_processes": "追加加工（マスタ未登録）", "k_factor": "Kファクター", "k_factor_confirmed": "Kファクター指定",
          "thickness_mm": "板厚（DXF）", "flat_confirmed": "曲げなし（平板）", "shape": "寸法の値"}


def changes(before: QuoteInputs, after: QuoteInputs, masters) -> str:
    out = []
    for key, label in LABELS.items():
        a, b = getattr(before, key), getattr(after, key)
        if a == b:
            continue
        if key in ("quantity", "customer", "title", "staff", "k_factor", "thickness_mm", "due_date"):
            out.append(f"{label} {a if a not in (None, '') else '-'}→{b if b not in (None, '') else '-'}")
        elif key == "material":
            out.append(f"{label} {a or '-'}→{b or '-'}")
        elif key == "surface_treatment":
            name = lambda c: masters.surface_treatments.get(c or "NONE", {}).get("display_name", c or "-")  # noqa: E731
            out.append(f"{label} {name(a)}→{name(b)}")
        elif key in ("rush", "k_factor_confirmed", "flat_confirmed"):
            out.append(f"{label} {'あり' if b else 'なし'}")
        else:
            out.append(f"{label}を変更")
    return "、".join(out)


def chat(pf: Platform, quote_id: int, message: str, version: int, actor: str, llm=None) -> dict:
    """Change the conditions by a sentence (the existing chat: src/chat_service.py). The LLM only interprets the
    sentence into operations on registered codes; the amounts are computed again by the rules."""
    from src.chat_service import ChatQuoteService
    from src.llm_client import build_llm_client
    from src.models import AdditionalProcess, QuoteCondition

    if not message.strip():
        raise ServiceError("メッセージを入力してください。")
    masters = pf.masters()
    with pf.session() as s:
        quote = s.get(Quote, quote_id)
        if quote is None:
            raise ServiceError("見積が見つかりません。", 404, "NOT_FOUND")
        check_version(quote, version, "見積")
        inputs = _inputs(quote)
        history = list(quote.chat or [])
    condition = QuoteCondition(material=inputs.material or next(iter(masters.materials)), quantity=inputs.quantity or 1,
                               additional_processes=[AdditionalProcess(process_code=p.code, quantity=p.quantity or 1,
                                                                       unit=masters.process_rates[p.code]["unit"])
                                                     for p in inputs.processes if p.code in masters.process_rates])
    pending = next((m.get("confirmation") for m in reversed(history) if m.get("role") == "assistant"), None)
    try:
        applied = ChatQuoteService(llm or build_llm_client(), masters).interpret_and_apply(
            message, condition, [{"role": m["role"], "content": m["content"]} for m in history[-6:]], pending)
    except Exception as exc:  # noqa: BLE001 - the condition stays as it was
        raise ServiceError(f"チャットの条件を読み取れませんでした: {exc}") from None
    updates: dict = {}
    new = applied.condition
    if new.material != condition.material and new.material in masters.materials:
        updates["material"], updates["custom_material"] = new.material, None
    if new.quantity != condition.quantity:
        updates["quantity"] = new.quantity
    procs = [{"code": p.process_code, "quantity": p.quantity, "source": "chat" if p.user_text else "user"}
             for p in new.additional_processes]
    if [(p["code"], p["quantity"]) for p in procs] != [(p.code, p.quantity or 1) for p in inputs.processes if p.code in masters.process_rates]:
        updates["processes"] = procs
    history += [{"role": "user", "content": message, "at": iso(datetime.now().replace(microsecond=0))},
                {"role": "assistant", "content": applied.message, "status": applied.interpretation.status,
                 "confirmation": applied.interpretation.confirmation_message if applied.interpretation.status != "ready" else None}]
    if updates:
        update(pf, quote_id, EstimateUpdate(version=version, inputs=updates, note="チャット"), actor)
    with pf.session() as s:
        quote = s.get(Quote, quote_id)
        quote.chat = history[-40:]
        s.commit()
    return detail(pf, quote_id)


# ---------------------------------------------------------------- the conditions screen of a new estimate
MARK_OF = {"確定": "read", "要確認": "review", "未登録": "unregistered", "記載なし": "none"}


def draft(pf: Platform, drawing_id: int | None = None, reading_job_id: str = "", file_name: str = "") -> dict:
    """The draft conditions of a new estimate (the 条件入力 step), with where each value comes from:
    mark "read" (読取), "review" (要確認, with the reasons), "unregistered" (マスタ未登録, the drawing's words),
    "none" (記載なし: left empty, never guessed), "drawing" (the registered drawing's own value) or "".
    The values are those the estimate job would take (inputs_from_reading), so a draft left as it is gives
    the same estimate."""
    from src.pdf_quote import condition_items
    from src.services.platform.drawings import reading_of_job
    from src.services.platform.wording import review_reason

    masters = pf.masters()
    reading, drawing_info, reader = None, {}, "none"
    revision_material = revision_finish = ""
    if drawing_id:
        with pf.session() as s:
            d = s.get(Drawing, drawing_id, options=[selectinload(Drawing.revisions)])
            if d is None:
                raise ServiceError("図面が見つかりません。", 404, "NOT_FOUND")
            rev = next((r for r in d.revisions if r.id == d.current_revision_id), d.revisions[-1] if d.revisions else None)
            if rev is not None:
                sync_revision(pf, s, rev)
                reading = reading_for_pricing(rev)
                revision_material, revision_finish = rev.material, rev.surface_treatment
            drawing_info = {"drawing_no": d.drawing_no, "revision": rev.revision if rev else "", "name": d.name,
                            "customer": d.customer, "shape_kind": rev.shape_kind if rev else "",
                            "thickness_mm": (rev.thickness_mm if rev else None) or (reading or {}).get("thickness_mm"),
                            "has_pdf": bool(rev and rev.pdf_file_id)}
        reader = "done" if reading else ("none" if not drawing_info["has_pdf"] else "later")
    elif reading_job_id:
        job = pf.job(reading_job_id)
        if job is None:
            raise ServiceError("図面の読み取りが見つかりません。", 404, "NOT_FOUND")
        if job["status"] in ("queued", "running"):
            reader = "pending"
        elif job["status"] == "failed":
            reader = "failed"
        done = reading_of_job(pf, reading_job_id)
        if done:
            reading = dict(done.get("reading") or {})
            reading["unregistered_texts"] = (done.get("drawing") or {}).get("unregistered_texts") or []
            reader = "done"
    inputs = inputs_from_reading(reading, masters, QuoteInputs())
    items = {i.field: i for i in condition_items(reading, masters)} if reading else {}

    def mark(field: str) -> tuple[str, str]:
        item = items.get(field)
        if item is None:
            return "", ""
        m = MARK_OF.get(item.status, "")
        reasons = "・".join(dict.fromkeys(review_reason(r) for r in item.reasons))
        return m, reasons

    fields: dict = {}
    stem = Path(file_name).stem if file_name else ""
    if drawing_id:
        for key in ("drawing_no", "revision", "name", "customer"):
            fields[key] = {"value": drawing_info[key], "mark": "drawing" if drawing_info[key] else "", "note": ""}
    else:
        r = reading or {}
        fields["drawing_no"] = ({"value": r["drawing_no"], "mark": "read", "note": ""} if r.get("drawing_no")
                                else {"value": stem, "mark": "", "note": "ファイル名" if stem else ""})
        fields["revision"] = ({"value": r["revision"], "mark": "read", "note": ""} if r.get("revision")
                              else {"value": "", "mark": "none" if reading else "", "note": "記載なし" if reading else ""})
        fields["name"] = {"value": "", "mark": "", "note": ""}
        fields["customer"] = {"value": "", "mark": "", "note": ""}
    texts = evidence_texts(reading) if reading else {}

    # material: the registered drawing's master material wins (as in create), else the reading
    m, why = mark("material")
    if revision_material in masters.materials and (not reading or revision_material != inputs.material):
        fields["material"] = {"value": revision_material, "mark": "drawing", "note": ""}
    elif m == "unregistered" or (not reading and revision_material):
        fields["material"] = {"value": None, "mark": "unregistered",
                              "note": f"マスタ未登録：{texts.get('material') or revision_material or '図面の材質'}"}
    elif m == "none":
        fields["material"] = {"value": None, "mark": "none", "note": "記載なし"}
    else:
        fields["material"] = {"value": inputs.material, "mark": m, "note": why}
    m, why = mark("surface_treatment")
    if revision_finish in masters.surface_treatments and (not reading or revision_finish != inputs.surface_treatment):
        fields["surface_treatment"] = {"value": revision_finish, "mark": "drawing", "note": ""}
    elif m == "unregistered" or (not reading and revision_finish):
        fields["surface_treatment"] = {"value": None, "mark": "unregistered",
                                       "note": f"マスタ未登録：{texts.get('surface_treatment') or revision_finish or '図面の表面処理'}"}
    elif m == "none":
        fields["surface_treatment"] = {"value": None, "mark": "none", "note": "記載なし（表面処理なしで計算）"}
    else:
        fields["surface_treatment"] = {"value": inputs.surface_treatment if reading else None, "mark": m, "note": why}
    m, why = mark("quantity")
    fields["quantity"] = {"value": inputs.quantity if m in ("read", "review") else None, "mark": m,
                          "note": "記載なし" if m == "none" else why}
    m, why = mark("rush")
    fields["rush"] = {"value": bool(inputs.rush), "mark": m, "note": why}
    m, why = mark("processes")
    if m == "read" and not inputs.processes and not inputs.custom_processes:
        m = "none"
    unregistered = [p.name for p in inputs.custom_processes]
    note = why if m == "review" else ("記載なし" if m == "none" else
                                      (f"マスタ未登録：{'、'.join(unregistered)}" if unregistered else ""))
    fields["processes"] = {"mark": m, "note": note,
                           "processes": [{"code": p.code, "quantity": p.quantity} for p in inputs.processes],
                           "custom_processes": [{"name": p.name, "quantity": p.quantity} for p in inputs.custom_processes]}
    return {"reader": reader, "fields": fields, "shape_kind": drawing_info.get("shape_kind", ""),
            "thickness_mm": drawing_info.get("thickness_mm") if drawing_id else (reading or {}).get("thickness_mm"),
            "drawing_id": drawing_id}
