"""Settings and reference data edited on the screens: staff, categories, drawing attributes, document templates,
partners, the masters (rows and aliases; never changed automatically), the company details, and the read-only
calculation formulas (見積ロジック).
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import func, select

from src.db.models import (AttributeDef, Category, CompanyRow, FinishRow, MaterialRow, Partner, PolicyRow, ProcessRow,
                           Staff, Template, drawing_categories, now)
from src.services.estimate import ServiceError
from src.services.platform.core import Platform, bump, check_version, iso

MASTER_TABLES = {
    "materials": (MaterialRow, ["display_name", "density_kg_m3", "price_per_kg", "waste_factor", "charge_scope", "aliases"],
                  ["density_kg_m3", "price_per_kg", "waste_factor"]),
    "processes": (ProcessRow, ["display_name", "calculation_type", "unit_price", "unit", "charge_scope", "aliases"],
                  ["unit_price"]),
    "surface_treatments": (FinishRow, ["display_name", "unit_price", "charge_scope", "aliases"], ["unit_price"]),
    "pricing_policy": (PolicyRow, ["value", "description"], ["value"]),
    "company": (CompanyRow, ["value", "description"], []),
}
FIELD_LABELS = {"density_kg_m3": "密度", "price_per_kg": "kg単価", "waste_factor": "歩留まり係数", "unit_price": "単価",
                "value": "値"}
INPUT_TYPES = ["text", "select", "number_range", "date_range"]


# ---------------------------------------------------------------- staff
def staff(pf: Platform) -> list[dict]:
    with pf.session() as s:
        return [{"id": r.id, "name": r.name, "active": r.active} for r in s.scalars(select(Staff).order_by(Staff.sort, Staff.id))]


def save_staff(pf: Platform, rows: list[dict]) -> list[dict]:
    names = [str(r.get("name", "")).strip() for r in rows]
    if any(not n for n in names) or len(set(names)) != len(names):
        raise ServiceError("担当者の名前は空にできず、重複もできません。")
    with pf.session() as s:
        existing = {r.id: r for r in s.scalars(select(Staff))}
        keep = {r.get("id") for r in rows if r.get("id")}
        for sid, row in existing.items():
            if sid not in keep:
                row.active = False  # kept for the history (cases keep the name)
        for i, r in enumerate(rows):
            row = existing.get(r.get("id")) if r.get("id") else None
            if row is None:
                row = s.scalars(select(Staff).where(Staff.name == r["name"].strip())).first() or Staff(name=r["name"].strip())
                s.add(row)
            row.name, row.active, row.sort = r["name"].strip(), bool(r.get("active", True)), i
        s.commit()
    return staff(pf)


# ---------------------------------------------------------------- categories and attributes
def categories(pf: Platform) -> list[dict]:
    with pf.session() as s:
        counts = dict(s.execute(select(drawing_categories.c.category_id, func.count()).group_by(drawing_categories.c.category_id)).all())
        rows = list(s.scalars(select(Category).order_by(Category.sort, Category.id)))
        groups = [r for r in rows if r.parent_id is None]
        return [{"id": g.id, "name": g.name, "children": [{"id": c.id, "name": c.name, "count": counts.get(c.id, 0)}
                                                          for c in rows if c.parent_id == g.id]} for g in groups]


def add_category(pf: Platform, name: str, parent_id: int | None) -> list[dict]:
    if not name.strip():
        raise ServiceError("分類の名前を入力してください。")
    with pf.session() as s:
        if parent_id is not None and s.get(Category, parent_id) is None:
            raise ServiceError("親の分類が見つかりません。", 404, "NOT_FOUND")
        sort = s.scalar(select(func.count()).select_from(Category).where(Category.parent_id == parent_id)) or 0
        s.add(Category(name=name.strip(), parent_id=parent_id, sort=sort))
        s.commit()
    return categories(pf)


def rename_category(pf: Platform, category_id: int, name: str) -> list[dict]:
    with pf.session() as s:
        row = s.get(Category, category_id)
        if row is None:
            raise ServiceError("分類が見つかりません。", 404, "NOT_FOUND")
        if not name.strip():
            raise ServiceError("分類の名前を入力してください。")
        row.name = name.strip()
        s.commit()
    return categories(pf)


def delete_category(pf: Platform, category_id: int) -> list[dict]:
    with pf.session() as s:
        row = s.get(Category, category_id)
        if row is not None:
            for child in s.scalars(select(Category).where(Category.parent_id == row.id)):
                s.delete(child)
            s.delete(row)
            s.commit()
    return categories(pf)


def attributes(pf: Platform) -> list[dict]:
    masters = pf.masters()
    with pf.session() as s:
        from src.db.models import CaseStatus

        statuses = [st.name for st in s.scalars(select(CaseStatus).order_by(CaseStatus.sort))]
        out = []
        for a in s.scalars(select(AttributeDef).order_by(AttributeDef.sort, AttributeDef.id)):
            options = list(a.options or [])
            if a.key == "status":
                options = ["未作成"] + statuses
            elif a.key == "material":
                options = [{"value": k, "label": v["display_name"]} for k, v in masters.materials.items()]
            elif a.key == "surface_treatment":
                options = [{"value": k, "label": v["display_name"]} for k, v in masters.surface_treatments.items()]
            elif a.key == "process":
                options = ["レーザー切断", "曲げ"] + [v["display_name"] for k, v in masters.process_rates.items()
                                                 if k not in ("LASER_CUT", "PIERCE", "BEND", "SETUP")]
            out.append({"id": a.id, "key": a.key, "label": a.label, "input_type": a.input_type, "options": options,
                        "unit": a.unit, "searchable": a.searchable, "builtin": a.builtin})
        return out


class AttributeRow(BaseModel):
    id: int | None = None
    label: str = Field(min_length=1)
    input_type: str = "text"
    options: list[str] = Field(default_factory=list)
    unit: str = ""
    searchable: bool = True


def save_attributes(pf: Platform, rows: list[AttributeRow]) -> list[dict]:
    for r in rows:
        if r.input_type not in INPUT_TYPES:
            raise ServiceError("入力の種類は テキスト・選択肢・数値の範囲・日付の範囲 のどれかです。")
    with pf.session() as s:
        existing = {a.id: a for a in s.scalars(select(AttributeDef))}
        keep = {r.id for r in rows if r.id}
        for aid, a in existing.items():
            if aid not in keep:
                if a.builtin:
                    raise ServiceError(f"標準の項目「{a.label}」は削除できません（検索に使わない設定はできます）。")
                s.delete(a)
        for i, r in enumerate(rows):
            a = existing.get(r.id) if r.id else None
            if a is None:
                a = AttributeDef(key=f"attr_{bump(s, 'attribute')}", builtin=False)
                s.add(a)
            a.label, a.sort, a.searchable = r.label.strip(), i, r.searchable
            if not a.builtin:
                a.input_type, a.options, a.unit = r.input_type, [o.strip() for o in r.options if o.strip()], r.unit
        s.commit()
    return attributes(pf)


# ---------------------------------------------------------------- templates
def templates(pf: Platform) -> list[dict]:
    with pf.session() as s:
        return [{"id": t.id, "name": t.name, "customers": t.customers or [], "options": t.options or {},
                 "is_default": t.is_default} for t in s.scalars(select(Template).order_by(Template.sort, Template.id))]


class TemplateIn(BaseModel):
    name: str = Field(min_length=1)
    customers: list[str] = Field(default_factory=list)
    options: dict[str, bool] = Field(default_factory=dict)
    is_default: bool = False


TEMPLATE_KEYS = {"breakdown", "unit_and_quantity", "drawing_no", "validity", "remarks", "seal"}


def save_template(pf: Platform, template_id: int | None, body: TemplateIn) -> list[dict]:
    unknown = set(body.options) - TEMPLATE_KEYS
    if unknown:
        raise ServiceError(f"表示項目の名前が正しくありません: {sorted(unknown)}")
    with pf.session() as s:
        t = s.get(Template, template_id) if template_id else None
        if template_id and t is None:
            raise ServiceError("テンプレートが見つかりません。", 404, "NOT_FOUND")
        if t is None:
            t = Template(sort=s.scalar(select(func.count()).select_from(Template)) or 0)
            s.add(t)
        t.name, t.customers = body.name.strip(), [c.strip() for c in body.customers if c.strip()]
        t.options = {k: bool(body.options.get(k, (t.options or {}).get(k, k != "breakdown"))) for k in TEMPLATE_KEYS}
        if body.is_default:
            for other in s.scalars(select(Template)):
                other.is_default = False
        t.is_default = body.is_default or t.is_default
        s.commit()
    return templates(pf)


def delete_template(pf: Platform, template_id: int) -> list[dict]:
    with pf.session() as s:
        t = s.get(Template, template_id)
        if t is not None:
            if t.is_default:
                raise ServiceError("既定のテンプレートは削除できません。")
            s.delete(t)
            s.commit()
    return templates(pf)


# ---------------------------------------------------------------- partners
class PartnerIn(BaseModel):
    version: int | None = None
    kind: str = "協力会社"
    name: str = Field(min_length=1)
    category: str = ""
    specialties: str = ""
    price_records: str = ""
    avg_lead_days: float | None = None
    deal_count: int = 0
    rating: str = ""
    notes: str = ""


def partners(pf: Platform, kind: str = "") -> list[dict]:
    with pf.session() as s:
        query = select(Partner).order_by(Partner.id)
        if kind:
            query = query.where(Partner.kind == kind)
        return [{"id": p.id, "kind": p.kind, "name": p.name, "category": p.category, "specialties": p.specialties,
                 "price_records": p.price_records, "avg_lead_days": p.avg_lead_days, "deal_count": p.deal_count,
                 "rating": p.rating, "notes": p.notes, "version": p.version, "updated_at": iso(p.updated_at)}
                for p in s.scalars(query)]


def save_partner(pf: Platform, partner_id: int | None, body: PartnerIn) -> dict:
    if body.kind not in ("協力会社", "顧客"):
        raise ServiceError("区分は 協力会社 か 顧客 です。")
    with pf.session() as s:
        p = s.get(Partner, partner_id) if partner_id else None
        if partner_id and p is None:
            raise ServiceError("取引先が見つかりません。", 404, "NOT_FOUND")
        if p is not None:
            check_version(p, body.version, "取引先")
        else:
            p = Partner()
            s.add(p)
        for field in ("kind", "name", "category", "specialties", "price_records", "avg_lead_days", "deal_count", "rating", "notes"):
            setattr(p, field, getattr(body, field))
        p.updated_at = now()
        s.commit()
        return {"id": p.id}


# ---------------------------------------------------------------- masters
def masters_table(pf: Platform, table: str) -> list[dict]:
    if table not in MASTER_TABLES:
        raise ServiceError("マスタの種類が正しくありません。", 404, "NOT_FOUND")
    model, fields, _ = MASTER_TABLES[table]
    with pf.session() as s:
        rows = s.scalars(select(model).order_by(model.sort))
        return [{"code": getattr(r, "code", None) or getattr(r, "key"), **{f: getattr(r, f) for f in fields},
                 "version": r.version, "updated_at": iso(r.updated_at)} for r in rows]


def save_master_row(pf: Platform, table: str, code: str, values: dict[str, Any], version: int | None, create: bool) -> list[dict]:
    """Add or change one master row. Values are checked (numbers, charge scope); a change applies to quotes
    computed from now on (issued documents keep their amounts)."""
    if table not in MASTER_TABLES:
        raise ServiceError("マスタの種類が正しくありません。", 404, "NOT_FOUND")
    model, fields, numeric = MASTER_TABLES[table]
    code = (code or "").strip()
    if not code:
        raise ServiceError("コード（キー）を入力してください。")
    clean = {}
    for f in fields:
        if f in values:
            v = "" if values[f] is None else str(values[f]).strip()
            if f in numeric and table != "company":
                try:
                    if float(v) < 0:
                        raise ValueError
                except ValueError:
                    raise ServiceError(f"{FIELD_LABELS.get(f, f)}は0以上の数値で入力してください。") from None
            if f == "charge_scope" and v not in ("per_part", "per_order"):
                raise ServiceError("課金の範囲は「1個ごと」か「1注文に1回」です。")
            if f == "aliases":
                v = "|".join(a.strip() for a in v.replace("、", "|").split("|") if a.strip())
            clean[f] = v
    with pf.session() as s:
        row = s.get(model, code)
        if create:
            if row is not None:
                raise ServiceError(f"コード {code} はすでにあります。", 409, "DUPLICATE")
            missing = [f for f in fields if f not in clean and f not in ("aliases", "description")]
            if missing:
                raise ServiceError(f"未入力の列があります: {missing}")
            row = model(**{("key" if table in ("pricing_policy", "company") else "code"): code}, **clean,
                        sort=s.scalar(select(func.count()).select_from(model)) or 0)
            s.add(row)
        else:
            if row is None:
                raise ServiceError("マスタの行が見つかりません。", 404, "NOT_FOUND")
            check_version(row, version, "マスタの行")
            for f, v in clean.items():
                setattr(row, f, v)
        row.updated_at = now()
        bump(s, "masters_rev")
        s.commit()
    pf.masters()  # validate the whole master once more (e.g. charge scopes)
    return masters_table(pf, table)


# ---------------------------------------------------------------- 見積ロジック (display only)
def logic(pf: Platform) -> dict:
    """The formulas of src/quote_engine.py and src/quote_document.py with the current policy values (display
    only; nothing here can change them). Same flow as docs/08_マスターデータの解説.md 「計算の順番」."""
    m = pf.masters()
    margin = m.policy("margin_rate", 0.25)
    rush = m.policy("rush_surcharge_rate", 0.0)
    tax = m.policy("tax_rate", 0.10)
    return {
        "summary": "見積金額 ＝（材料費 ＋ レーザー切断 ＋ ピアス加工 ＋ 曲げ加工 ＋ 段取り ＋ 追加加工 ＋ 表面処理）"
                   f"×（1 ＋ 特急割増 {rush:.0%}：特急のときだけ）×（1 ＋ 粗利率 {margin:.0%}）",
        "items": [
            {"name": "材料費", "rows": [
                {"item": "1個の重量", "formula": "展開面積(mm²) × 板厚(mm) ÷ 10⁹ × 密度(kg/m³)", "note": "CADデータの値と材料マスタ"},
                {"item": "材料費", "formula": "1個の重量 × kg単価 × 歩留まり係数 × 数量", "note": "材料マスタ（課金の範囲が1個ごと）"}]},
            {"name": "レーザー切断", "rows": [
                {"item": "レーザー切断", "formula": "切断長(mm) × 単価(円/mm) × 数量", "note": "工程マスタ"}]},
            {"name": "ピアス加工", "rows": [
                {"item": "ピアス加工", "formula": "穴数 × 単価(円/穴) × 数量", "note": "工程マスタ"}]},
            {"name": "曲げ加工", "rows": [
                {"item": "曲げ加工", "formula": "曲げ数 × 単価(円/曲げ) × 数量", "note": "工程マスタ"}]},
            {"name": "段取り", "rows": [
                {"item": "段取り", "formula": "単価(円/式) × 1", "note": "工程マスタ（1注文に1回）"}]},
            {"name": "追加加工", "rows": [
                {"item": "追加加工", "formula": "1個あたりの箇所数 × 単価 × 数量", "note": "工程マスタ（タップ・皿穴・溶接など）。図面の読み取りか担当者の入力"},
                {"item": "マスタにない加工", "formula": "1個あたりの箇所数 × 担当者が入力した単価 × 数量", "note": "その見積だけに使う（マスタには登録しない）"}]},
            {"name": "表面処理", "rows": [
                {"item": "表面処理", "formula": "単価(円/個) × 数量", "note": "表面処理マスタ。「なし」は0円"},
                {"item": "マスタにない表面処理", "formula": "担当者が入力した単価(円/個) × 数量", "note": "その見積だけに使う"}]},
            {"name": "特急割増", "rows": [
                {"item": "特急割増", "formula": f"小計 × {rush:.0%}", "note": "特急を指定したときだけ（価格方針の特急割増率）"}]},
            {"name": "粗利", "rows": [
                {"item": "見積金額", "formula": f"（小計 ＋ 特急割増）× (1 ＋ {margin:.0%})", "note": "価格方針の粗利率"}]},
            {"name": "単価・消費税", "rows": [
                {"item": "単価", "formula": "見積金額 ÷ 数量 を1円未満切り上げ", "note": "画面・見積書・納品書・請求書で同じ"},
                {"item": "金額・小計", "formula": "単価 × 数量", "note": ""},
                {"item": "消費税", "formula": f"小計 × {tax:.0%} を1円未満切り捨て", "note": "価格方針の消費税率"},
                {"item": "合計", "formula": "小計 ＋ 消費税", "note": ""}]},
        ],
        "notes": [                  "希望納期は記録と表示に使い、金額には影響しません（特急は担当者が指定します）。",
                  "マスタにない材料は、入力したkg単価と密度に、歩留まり係数1.15を掛けて計算します。",
                  "単価や率はマスタの画面で変えます。"],
        "values": {"margin_rate": margin, "rush_surcharge_rate": rush, "tax_rate": tax},
    }
