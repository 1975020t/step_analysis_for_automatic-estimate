"""API of the screens (React, web/): drawings, estimates, cases, documents, search, settings, masters.
Every amount and every "can it be issued" decision is made here (src/services/platform), never in the browser.
"""
from __future__ import annotations

import json
from datetime import date
from tempfile import NamedTemporaryFile
from pathlib import Path
from urllib.parse import quote as urlquote

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

from api.security import current_actor
from src.past_quotes import COLUMN_ALIASES, column_map, read_csv_rows
from src.services.estimate import ServiceError
from src.services.platform import admin, cases, documents, drawings, library, quotes
from src.services.platform.core import Platform
from src.services.platform.similar import similar

FIELD_LABELS = {"quote_no": "見積番号", "date": "見積日", "customer": "顧客名", "drawing_no": "図番", "revision": "改訂",
                "part_name": "品名", "material_text": "材質", "thickness": "板厚", "quantity": "数量",
                "finish_text": "表面処理", "processes_text": "追加加工", "rush": "特急", "unit_price": "単価",
                "amount": "金額", "outcome": "結果", "staff": "担当者", "remarks": "備考", "area": "展開面積",
                "cut": "切断長", "holes": "穴数", "bends": "曲げ数", "flat_size": "展開寸法", "shape_class": "形状区分"}


class RegisterBody(BaseModel):
    items: list[drawings.RegisterItem]


class IssueBody(BaseModel):
    kind: str = "quote"
    template_id: int | None = None
    person: str = ""
    remarks: str = ""


class ChatBody(BaseModel):
    message: str
    version: int


class NoteBody(BaseModel):
    x: float
    y: float
    text: str


class MemoBody(BaseModel):
    kind: str = "注意"
    text: str


class CategoryBody(BaseModel):
    name: str
    parent_id: int | None = None


class MasterBody(BaseModel):
    code: str = ""
    values: dict = Field(default_factory=dict)
    version: int | None = None


class StatusList(BaseModel):
    statuses: list[cases.StatusRow]


class AttributeList(BaseModel):
    attributes: list[admin.AttributeRow]


class StaffList(BaseModel):
    staff: list[dict]


def png(data: bytes) -> Response:
    return Response(data, media_type="image/png", headers={"Cache-Control": "no-store"})


def pdf(data: bytes, name: str, inline: bool = True) -> Response:
    disposition = "inline" if inline else "attachment"
    return Response(data, media_type="application/pdf",
                    headers={"Content-Disposition": f"{disposition}; filename*=UTF-8''{urlquote(name)}"})


def build_router(pf: Platform, guarded: list) -> APIRouter:
    r = APIRouter(prefix="/api", dependencies=guarded)
    actor = Depends(current_actor)

    # ------------------------------------------------------------ shared lists for the screens
    @r.get("/meta", tags=["screens"])
    def meta() -> dict:
        """選択肢（材質・表面処理・追加加工・担当者・ステータス・分類・属性・テンプレート）と、図面読み取りの可否。"""
        m = pf.masters()
        return {
            "materials": [{"code": k, "name": v["display_name"]} for k, v in m.materials.items()],
            "surface_treatments": [{"code": k, "name": v["display_name"]} for k, v in m.surface_treatments.items()],
            "processes": [{"code": k, "name": v["display_name"], "unit": v["unit"]} for k, v in m.process_rates.items()
                          if k not in ("LASER_CUT", "PIERCE", "BEND", "SETUP")],
            "staff": [s for s in admin.staff(pf) if s["active"]], "statuses": cases.statuses(pf),
            "categories": admin.categories(pf), "attributes": admin.attributes(pf), "templates": admin.templates(pf),
            "drawing_reader": pf.service.drawing_reader_available(), "lost_reasons": cases.LOST_REASONS,
            "document_kinds": library.KINDS, "phases": cases.PHASES, "colors": cases.COLORS,
            "policy": {k: m.policy(k, 0) for k in ("margin_rate", "rush_surcharge_rate", "tax_rate")},
        }

    @r.get("/recent", tags=["screens"])
    def recent() -> list[dict]:
        return library.recent_cases(pf)

    @r.get("/home", tags=["screens"])
    def home() -> dict:
        """ホーム：To Doリスト（理由と次のアクションつき、急ぐ順）、フェーズごとの件数と注意の件数、メニューの件数。"""
        return cases.home(pf)

    @r.get("/files/{file_id}/raw", tags=["files"])
    def raw_file(file_id: str) -> Response:
        try:
            data, meta_ = pf.files.get(file_id)
        except KeyError:
            raise ServiceError("ファイルが見つかりません。", 404, "NOT_FOUND") from None
        media = {"pdf": "application/pdf"}.get(meta_["kind"], "application/octet-stream")
        return Response(data, media_type=media,
                        headers={"Content-Disposition": f"inline; filename*=UTF-8''{urlquote(meta_['filename'])}"})

    # ------------------------------------------------------------ drawings
    @r.post("/drawings/register", tags=["drawings"], status_code=202)
    def register(body: RegisterBody, who: str = actor) -> list[dict]:
        """図面を登録する（1件 = 図面PDF＋形状ファイル。片方でもよい）。形状ファイルは登録時に解析する（受付番号）。"""
        return drawings.register(pf, body.items, who)

    @r.get("/drawings", tags=["drawings"])
    def drawing_list(request: Request) -> list[dict]:
        """条件検索（図番・品名・顧客・ステータス・材質・加工・最大寸法・登録日・分類・図面内の文字・属性）。"""
        f = dict(request.query_params)
        f["attrs"] = json.loads(f.pop("attrs", "") or "{}")
        return drawings.list_drawings(pf, f)

    @r.get("/drawings/same-number", tags=["drawings"])
    def same_number(drawing_no: str) -> list[dict]:
        return drawings.same_number(pf, drawing_no)

    @r.get("/drawings/{drawing_id}", tags=["drawings"])
    def drawing_detail(drawing_id: int) -> dict:
        return drawings.drawing_detail(pf, drawing_id)

    @r.put("/drawings/{drawing_id}", tags=["drawings"])
    def drawing_update(drawing_id: int, body: drawings.DrawingUpdate, who: str = actor) -> dict:
        return drawings.update_drawing(pf, drawing_id, body, who)

    @r.put("/drawings/{drawing_id}/current-revision", tags=["drawings"])
    def current_revision(drawing_id: int, revision_id: int = Query(...)) -> dict:
        drawings.set_current_revision(pf, drawing_id, revision_id)
        return drawings.drawing_detail(pf, drawing_id)

    @r.get("/drawings/{drawing_id}/similar", tags=["drawings", "similar"])
    def drawing_similar(drawing_id: int) -> list[dict]:
        """類似形状の図面（材料が同じ・曲げ数の差1以内・穴数の差2以内、差の小さい順）。見積の類似実績と同じ規則。"""
        d = drawings.drawing_detail(pf, drawing_id)
        m = d["metrics"]
        return similar(pf, d["material"], m["bend_count"], m["hole_count"], drawing_id)

    @r.post("/drawings/{drawing_id}/memos", tags=["drawings"])
    def add_memo(drawing_id: int, body: MemoBody, who: str = actor) -> dict:
        drawings.add_memo(pf, drawing_id, body.kind, body.text, who)
        return drawings.drawing_detail(pf, drawing_id)

    @r.post("/revisions/{revision_id}/notes", tags=["drawings"])
    def add_note(revision_id: int, body: NoteBody, who: str = actor) -> dict:
        drawings.add_note(pf, revision_id, body.x, body.y, body.text, who)
        return {"ok": True}

    @r.delete("/notes/{note_id}", tags=["drawings"])
    def delete_note(note_id: int) -> dict:
        drawings.delete_note(pf, note_id)
        return {"ok": True}

    @r.get("/revisions/{revision_id}/preview.png", tags=["drawings"], response_class=Response)
    def revision_preview(revision_id: int, width: int = Query(900, ge=200, le=2000)) -> Response:
        return png(drawings.revision_preview(pf, revision_id, width))

    # ------------------------------------------------------------ estimates
    @r.post("/estimates", tags=["estimates"], status_code=202)
    def create_estimate(body: quotes.EstimateCreate, who: str = actor) -> dict:
        """登録済みの図面から見積を作る（案件も作る）。解析・読み取り・照合・計算・類似検索は受付番号で進む。"""
        return quotes.create(pf, body, who)

    @r.get("/estimate-draft", tags=["estimates"])
    def estimate_draft(drawing_id: int | None = None, reading_job_id: str = "", file_name: str = "") -> dict:
        """新規見積の条件入力の下書き：図面の読み取り結果（読取・要確認・マスタ未登録・記載なし）と図面の値。"""
        return quotes.draft(pf, drawing_id, reading_job_id, file_name)

    @r.get("/estimates", tags=["estimates"])
    def estimate_list() -> list[dict]:
        return quotes.list_estimates(pf)

    @r.get("/estimates/{quote_id}", tags=["estimates"])
    def estimate(quote_id: int) -> dict:
        """見積の全体：条件、形状解析、金額（費目・単価・小計・消費税・合計）、未入力の項目（issuable）、要確認のヒント、類似実績。"""
        return quotes.detail(pf, quote_id)

    @r.put("/estimates/{quote_id}", tags=["estimates"])
    def estimate_update(quote_id: int, body: quotes.EstimateUpdate, who: str = actor) -> dict:
        """条件を変えて計算し直す。version が古いと 409（ほかの人の保存を上書きしない）。"""
        return quotes.update(pf, quote_id, body, who)

    @r.post("/estimates/{quote_id}/chat", tags=["estimates"])
    def estimate_chat(quote_id: int, body: ChatBody, who: str = actor) -> dict:
        return quotes.chat(pf, quote_id, body.message, body.version, who)

    @r.get("/estimates/{quote_id}/documents/preview.png", tags=["documents"], response_class=Response)
    def document_preview(quote_id: int, kind: str = "quote", template_id: int | None = None) -> Response:
        return png(documents.preview(pf, quote_id, kind, template_id))

    @r.post("/estimates/{quote_id}/documents", tags=["documents"], status_code=201)
    def issue(quote_id: int, body: IssueBody, who: str = actor) -> dict:
        """見積書・納品書・請求書を発行する。未入力の項目があれば 409（NOT_ISSUABLE、details に一覧）。"""
        return documents.issue(pf, quote_id, body.kind, body.template_id, who, body.person, body.remarks)

    @r.get("/issued", tags=["documents"])
    def issued(kind: str = "", limit: int = Query(50, ge=1, le=500)) -> list[dict]:
        return documents.issued_list(pf, kind, limit)

    @r.get("/issued/{doc_id}/file.pdf", tags=["documents"], response_class=Response)
    def issued_file(doc_id: int, download: bool = False) -> Response:
        data, name = documents.issued_file(pf, doc_id)
        return pdf(data, name, inline=not download)

    # ------------------------------------------------------------ cases and review
    @r.get("/cases", tags=["cases"])
    def case_list(q: str = "", staff: str = "", phase: str = "") -> dict:
        """案件（見積）の一覧：急ぐ順、フェーズ・次のアクション・To Doの理由つき。phase はフェーズのキーか active。"""
        return cases.list_cases(pf, q, staff, phase)

    @r.put("/cases/{case_id}/status", tags=["cases"])
    def case_status(case_id: int, body: cases.StatusMove, who: str = actor) -> dict:
        """進捗を変える。失注は理由（と他社価格）が必要。受注・失注は履歴（類似実績・実績分析）にも反映。"""
        return cases.move(pf, case_id, body, who)

    @r.get("/cases/{case_id}/history", tags=["cases"])
    def case_history(case_id: int) -> list[dict]:
        return cases.history(pf, case_id)

    @r.get("/statuses", tags=["settings"])
    def status_list() -> list[dict]:
        return cases.statuses(pf)

    @r.put("/statuses", tags=["settings"])
    def status_save(body: StatusList) -> list[dict]:
        return cases.save_statuses(pf, body.statuses)

    @r.get("/review", tags=["cases"])
    def review(months: int = Query(6, ge=1, le=36), ref: date | None = None) -> dict:
        return cases.review(pf, months, ref)

    # ------------------------------------------------------------ search and documents
    @r.get("/search", tags=["search"])
    def search(q: str = "", kind: str = "") -> dict:
        return library.search(pf, q, kind)

    @r.get("/library", tags=["search"])
    def library_list() -> list[dict]:
        return library.documents(pf)

    @r.post("/library", tags=["search"], status_code=201)
    async def library_add(file: UploadFile = File(...), kind: str = Form(...), title: str = Form(""),
                          drawing_ids: str = Form("[]"), customer: str = Form(""), who: str = actor) -> dict:
        data = await file.read(pf.settings.max_upload_bytes + 1)
        if len(data) > pf.settings.max_upload_bytes:
            raise ServiceError("ファイルが大きすぎます。", 413, "TOO_LARGE")
        try:
            ids = [int(x) for x in json.loads(drawing_ids or "[]")]
        except (ValueError, TypeError):
            raise ServiceError("drawing_ids は図面IDのJSON配列です。") from None
        return library.add_document(pf, data, file.filename or "document", kind, title, ids, customer, who)

    @r.get("/library/{doc_id}/file", tags=["search"], response_class=Response)
    def library_file(doc_id: int) -> Response:
        data, name, media = library.document_file(pf, doc_id)
        return Response(data, media_type=media, headers={"Content-Disposition": f"inline; filename*=UTF-8''{urlquote(name)}"})

    @r.delete("/library/{doc_id}", tags=["search"])
    def library_delete(doc_id: int) -> dict:
        library.delete_document(pf, doc_id)
        return {"ok": True}

    # ------------------------------------------------------------ settings
    @r.get("/staff", tags=["settings"])
    def staff_list() -> list[dict]:
        return admin.staff(pf)

    @r.put("/staff", tags=["settings"])
    def staff_save(body: StaffList) -> list[dict]:
        return admin.save_staff(pf, body.staff)

    @r.get("/categories", tags=["settings"])
    def category_list() -> list[dict]:
        return admin.categories(pf)

    @r.post("/categories", tags=["settings"])
    def category_add(body: CategoryBody) -> list[dict]:
        return admin.add_category(pf, body.name, body.parent_id)

    @r.put("/categories/{category_id}", tags=["settings"])
    def category_rename(category_id: int, body: CategoryBody) -> list[dict]:
        return admin.rename_category(pf, category_id, body.name)

    @r.delete("/categories/{category_id}", tags=["settings"])
    def category_delete(category_id: int) -> list[dict]:
        return admin.delete_category(pf, category_id)

    @r.get("/attributes", tags=["settings"])
    def attribute_list() -> list[dict]:
        return admin.attributes(pf)

    @r.put("/attributes", tags=["settings"])
    def attribute_save(body: AttributeList) -> list[dict]:
        return admin.save_attributes(pf, body.attributes)

    @r.get("/templates", tags=["settings"])
    def template_list() -> list[dict]:
        return admin.templates(pf)

    @r.post("/templates", tags=["settings"])
    def template_add(body: admin.TemplateIn) -> list[dict]:
        return admin.save_template(pf, None, body)

    @r.put("/templates/{template_id}", tags=["settings"])
    def template_save(template_id: int, body: admin.TemplateIn) -> list[dict]:
        return admin.save_template(pf, template_id, body)

    @r.delete("/templates/{template_id}", tags=["settings"])
    def template_delete(template_id: int) -> list[dict]:
        return admin.delete_template(pf, template_id)

    @r.get("/templates/{template_id}/preview.png", tags=["settings"], response_class=Response)
    def template_preview(template_id: int, quote_id: int | None = None) -> Response:
        return png(documents.template_preview(pf, template_id, quote_id))

    @r.get("/partners", tags=["partners"])
    def partner_list(kind: str = "") -> list[dict]:
        return admin.partners(pf, kind)

    @r.post("/partners", tags=["partners"], status_code=201)
    def partner_add(body: admin.PartnerIn) -> dict:
        return admin.save_partner(pf, None, body)

    @r.put("/partners/{partner_id}", tags=["partners"])
    def partner_save(partner_id: int, body: admin.PartnerIn) -> dict:
        return admin.save_partner(pf, partner_id, body)

    @r.get("/master-tables/{table}", tags=["masters"])
    def master_table(table: str) -> list[dict]:
        """材料・工程・表面処理・価格方針・自社情報の行（更新日つき）。"""
        return admin.masters_table(pf, table)

    @r.post("/master-tables/{table}", tags=["masters"], status_code=201)
    def master_add(table: str, body: MasterBody) -> list[dict]:
        return admin.save_master_row(pf, table, body.code, body.values, None, create=True)

    @r.put("/master-tables/{table}/{code}", tags=["masters"])
    def master_save(table: str, code: str, body: MasterBody) -> list[dict]:
        return admin.save_master_row(pf, table, code, body.values, body.version, create=False)

    @r.get("/logic", tags=["masters"])
    def logic() -> dict:
        """見積の計算式（表示だけ。変えられない）。"""
        return admin.logic(pf)

    @r.post("/history/columns", tags=["history"])
    async def history_columns(file: UploadFile = File(...)) -> dict:
        """過去見積CSVの列と、自動で対応づけた結果（取り込み前の確認用）。"""
        data = await file.read(pf.settings.max_upload_bytes + 1)
        with NamedTemporaryFile(suffix=".csv", delete=False) as handle:
            handle.write(data)
        try:
            header, rows = read_csv_rows(handle.name)
        except ValueError as exc:
            raise ServiceError(str(exc)) from None
        finally:
            Path(handle.name).unlink(missing_ok=True)
        return {"columns": header, "rows": len(rows), "sample": rows[:3], "mapping": column_map(header),
                "fields": [{"key": k, "label": FIELD_LABELS.get(k, k)} for k in COLUMN_ALIASES]}

    return r
