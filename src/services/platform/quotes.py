"""Estimates of the screens: create one from a registered drawing (a background job with stages the screen
follows), read it (amounts, missing items, hints, similar results), change its conditions (also by chat), the
edit history. Every amount comes from src/services/platform/pricing.compute (QuoteEngine), on the server.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from src.db.models import (Case, CaseStatus, CaseStatusLog, Drawing, DrawingRevision, IssuedDocument, Quote,
                           QuoteEditLog, now)
from src.services.estimate import ServiceError
from src.services.jobs import JobError, report_progress
from src.services.platform.core import Platform, bump, check_version, iso
from src.services.platform.drawings import reading_for_pricing, sync_revision
from src.services.platform.pricing import QuoteInputs, compute, inputs_from_reading
from src.services.platform.similar import similar

ANALYSIS_KEYS = ("k_factor", "k_factor_confirmed", "thickness_mm", "flat_confirmed")
STAGES = ["形状解析", "図面の読み取り", "マスタ照合", "見積計算", "類似実績の検索"]


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
        given = body.model_dump(exclude_unset=True, exclude={"drawing_id", "revision_id"})
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
            raise ServiceError("見積の条件が正しくありません: " + "; ".join(e["msg"] for e in exc.errors())) from None
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
        job = pf.submit("estimate", {"quote_id": quote.id})
        quote.job_id = job["job_id"]
        s.commit()
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
    """The stages of an estimate (shown on the progress screen with the values found so far)."""
    masters = pf.masters()
    values: dict = {}

    def stage(i: int, **found) -> None:
        values.update(found)
        report_progress(step=i + 1, steps=len(STAGES), stage=STAGES[i], stages=STAGES, values=dict(values))

    with pf.session() as s:
        quote = s.get(Quote, payload["quote_id"])
        if quote is None:
            raise JobError("見積が見つかりません。")
        revision = s.get(DrawingRevision, quote.revision_id) if quote.revision_id else None
        inputs = _inputs(quote)
        stage(0)
        analysis = analyse(pf, revision, inputs)
        quote.analysis = analysis
        a = analysis or {}
        values["shape"] = {k: a.get(k) for k in ("thickness_mm", "blank_area_mm2", "cut_length_mm", "hole_count", "bend_count")}
        values["shape_status"] = a.get("status") or "形状ファイルなし"
        stage(1)
        if revision is not None:
            sync_revision(pf, s, revision)
            if revision.pdf_file_id and revision.reading is None and pf.service.drawing_reader_available() and \
                    not (revision.reading_job_id and (pf.job(revision.reading_job_id) or {}).get("status") in ("queued", "running")):
                try:
                    r, ctx = pf.service.read_drawing(pf.files.path(revision.pdf_file_id), revision.pdf_name)
                    revision.reading = {"reading": r, "drawing": ctx.model_dump(mode="json")}
                except Exception:  # noqa: BLE001 - the conditions are entered by hand then
                    pass
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
        result = compute(inputs, masters, analysis, reading, file_name=revision.shape_name if revision else "")
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


def _metric(result: dict, name: str):
    return (result.get("shape") or {}).get(name, {}).get("value")


def analyse(pf: Platform, revision: DrawingRevision | None, inputs: QuoteInputs) -> dict | None:
    """The shape analysis with this quote's conditions (the registration analysis when they are the same)."""
    if revision is None or not revision.shape_file_id:
        return None
    if revision.shape_kind == "dxf" and not inputs.thickness_mm:
        return None
    same = (revision.analysis is not None and inputs.k_factor == 0.33 and not inputs.k_factor_confirmed
            and not inputs.flat_confirmed and (revision.shape_kind != "dxf" or inputs.thickness_mm == revision.thickness_mm))
    if same:
        return revision.analysis
    data, meta = pf.files.get(revision.shape_file_id)
    try:
        return pf.service.analyze_bytes(data, meta["filename"], inputs.thickness_mm, inputs.k_factor,
                                        inputs.k_factor_confirmed, inputs.flat_confirmed).model_dump(mode="json")
    except Exception:  # noqa: BLE001
        return {"status": "error", "file_name": meta["filename"], "message": "形状ファイルを解析できませんでした。"}


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
                         file_name=revision.shape_name if revision else "")
        log = [{"at": iso(e.at), "actor": e.actor, "summary": e.summary, "total_before": e.total_before,
                "total_after": e.total_after}
               for e in s.scalars(select(QuoteEditLog).where(QuoteEditLog.quote_id == quote.id).order_by(QuoteEditLog.id.desc()))]
        docs = [document_out(d) for d in s.scalars(select(IssuedDocument).where(IssuedDocument.case_id == case.id)
                                                   .order_by(IssuedDocument.id.desc()))]
        a = quote.analysis or {}
        out = {
            "id": quote.id, "number": quote.number, "version": quote.version, "case_id": case.id,
            "case_version": case.version, "customer": case.customer, "title": case.title, "staff": case.staff,
            "due_date": iso(case.due_date), "status": {"id": case.status.id, "name": case.status.name,
                                                       "color": case.status.color, "group": case.status.group,
                                                       "role": case.status.role},
            "outcome": case.outcome, "created_at": iso(quote.created_at), "updated_at": iso(quote.updated_at),
            "inputs": inputs.model_dump(mode="json"),
            "drawing": {"id": drawing.id, "drawing_no": drawing.drawing_no, "name": drawing.name,
                        "revision": revision.revision if revision else "", "revision_id": revision.id if revision else None,
                        "pdf_file_id": revision.pdf_file_id if revision else "", "shape_file_id": revision.shape_file_id if revision else "",
                        "shape_kind": revision.shape_kind if revision else "", "shape_name": revision.shape_name if revision else "",
                        "pdf_name": revision.pdf_name if revision else ""} if drawing else None,
            "analysis": {k: a.get(k) for k in ("status", "file_name", "thickness_mm", "blank_area_mm2", "cut_length_mm",
                                               "hole_count", "bend_count", "reason_code", "reason_codes", "message",
                                               "warnings", "assumptions", "flat_pattern")} if a else None,
            "job": {"job_id": job["job_id"], "status": job["status"], "error": job.get("error"),
                    "progress": job.get("progress")} if job else None,
            "result": result, "edit_log": log, "documents": docs, "chat": quote.chat or [],
            "blockers": blockers(result, case, docs),
            "similar": similar(pf, inputs.material, _metric(result, "bend_count"), _metric(result, "hole_count"),
                               quote.drawing_id),
        }
    return out


def blockers(result: dict, case: Case, docs: list[dict]) -> dict:
    """Why each document cannot be issued now (empty list: it can). The same rules refuse the issue in
    src/services/platform/documents.py."""
    quote = [m["message"] for m in result["missing"]]
    trade = []
    if case.outcome != "受注":
        trade.append(f"受注した見積から作ります（今の状態：{case.status.name}）")
    if not any(d["kind"] == "quote" for d in docs):
        trade.append("先に見積書を発行してください（同じ金額で作ります）")
    return {"quote": quote, "delivery": list(trade), "invoice": list(trade)}


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
                        "status": {"name": q.case.status.name, "color": q.case.status.color},
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
            raise ServiceError("見積の条件が正しくありません: " + "; ".join(
                f"{'.'.join(str(x) for x in e['loc'])}: {e['msg']}" for e in exc.errors())) from None
        object.__setattr__(inputs, "__pydantic_fields_set__", set(before.model_fields_set) | set(body.inputs))
        if not inputs.customer.strip():
            raise ServiceError("顧客は必須です。")
        revision = s.get(DrawingRevision, quote.revision_id) if quote.revision_id else None
        if any(getattr(inputs, k) != getattr(before, k) for k in ANALYSIS_KEYS):
            quote.analysis = analyse(pf, revision, inputs)
        _store(quote, inputs)
        quote.result = compute(inputs, masters, quote.analysis, quote.reading, file_name=revision.shape_name if revision else "")
        quote.updated_at, quote.updated_by = now(), actor
        case = quote.case
        case.customer, case.title, case.staff, case.due_date = inputs.customer.strip(), inputs.title.strip(), inputs.staff, inputs.due_date
        case.updated_at, case.updated_by = now(), actor
        summary = changes(before, inputs, masters) or "条件を保存"
        if body.note:
            summary += f"（{body.note}）"
        s.add(QuoteEditLog(quote_id=quote.id, actor=actor, summary=summary, total_before=old_total,
                           total_after=((quote.result or {}).get("price") or {}).get("subtotal")))
        s.commit()
    return detail(pf, quote_id)


LABELS = {"customer": "顧客", "title": "案件名", "staff": "担当", "quantity": "数量", "due_date": "希望納期",
          "material": "材質", "custom_material": "材質（マスター未登録）", "surface_treatment": "表面処理",
          "custom_finish": "表面処理（マスター未登録）", "rush": "特急", "processes": "追加加工",
          "custom_processes": "追加加工（マスター未登録）", "k_factor": "Kファクター", "k_factor_confirmed": "Kファクター指定",
          "thickness_mm": "板厚（DXF）", "flat_confirmed": "曲げなし（平板）", "shape": "形状の値"}


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
