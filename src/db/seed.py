"""Initial data: the master CSVs (data/*.csv), the company details, the quote history
(data/past_quotes/history.csv) and the default settings (14 case statuses, attributes, templates, staff).

Each part is loaded only into an empty table, so starting the API again never duplicates or overwrites what
people changed on the screens. The CSV files themselves stay as they are: the evaluation harness and the
existing tests keep reading them.
"""
from __future__ import annotations

import csv
from dataclasses import asdict
from pathlib import Path

from sqlalchemy import func, insert, select
from sqlalchemy.orm import Session

from src.db.models import (AttributeDef, CaseStatus, Category, CompanyRow, FinishRow, MaterialRow, Partner,
                           PastQuoteRow, PolicyRow, ProcessRow, Staff, Template)
from src.past_quotes import HistoryStore

STATUSES = [  # name, color, group, role
    ("見積依頼受付", "light", "見積・受注", ""), ("見積前", "light", "見積・受注", ""),
    ("見積作成中", "blue", "見積・受注", "drafting"), ("見積確認中", "blue", "見積・受注", ""),
    ("見積提出済", "dark", "見積・受注", "issued"), ("受注", "dark", "見積・受注", "won"),
    ("製造準備", "light", "製造・出荷", ""), ("製造中", "blue", "製造・出荷", ""), ("検査", "blue", "製造・出荷", ""),
    ("出荷待ち", "blue", "製造・出荷", ""), ("出荷済", "dark", "製造・出荷", "shipped"), ("完了", "grey", "製造・出荷", "done"),
    ("失注", "grey", "保留・失注", "lost"), ("保留", "grey", "保留・失注", "hold"),
]
ATTRIBUTES = [  # key, label, type, unit, builtin
    ("drawing_no", "図番", "text", "", True), ("name", "品名", "text", "", True), ("customer", "顧客名", "text", "", True),
    ("status", "ステータス", "select", "", True), ("material", "材質", "select", "", True),
    ("process", "加工", "select", "", True), ("max_dimension", "最大寸法", "number_range", "mm", True),
    ("created", "登録日", "date_range", "", True), ("surface_treatment", "表面処理", "select", "", True),
    ("internal_no", "社内管理番号", "text", "", False),
]
TEMPLATE_OPTIONS = {"breakdown": False, "unit_and_quantity": True, "drawing_no": True, "validity": True,
                    "remarks": True, "seal": True}
PART_KINDS = ["ブラケット", "カバー・パネル", "筐体部品", "ベース・プレート", "その他"]
PRODUCTS = ["制御盤", "搬送装置", "検査装置"]
PARTNERS = [  # fictional subcontractors (記録と表示だけ。見積の計算には使わない)
    ("協力会社A（表面処理）", "表面処理", "三価クロメート・無電解ニッケル", "三価クロメート ¥40/個", 4, 86, "A"),
    ("協力会社B（塗装）", "表面処理", "粉体塗装・焼付塗装", "粉体塗装 ¥180/個", 5, 41, "A"),
    ("協力会社C（溶接）", "溶接", "TIG溶接・スポット溶接", "TIG溶接 ¥800/か所", 6, 57, "B"),
    ("協力会社D（タップ・圧入）", "追加加工", "タップ・圧入ナット", "—（見積依頼中）", None, 0, "新規"),
    ("鋼材商社E", "材料", "SPCC・SECC・SUS304・A5052 板材", "SPCC ¥160/kg", 2, 132, "A"),
]


def _rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _empty(session: Session, model) -> bool:
    return not session.scalar(select(func.count()).select_from(model))


def seed(session: Session, data_dir: str | Path = "data", history_path: str | Path | None = None) -> None:
    data_dir = Path(data_dir)
    if _empty(session, MaterialRow):
        for i, r in enumerate(_rows(data_dir / "materials.csv")):
            session.add(MaterialRow(code=r["material"], display_name=r["display_name"], density_kg_m3=r["density_kg_m3"],
                                    price_per_kg=r["price_per_kg"], waste_factor=r["waste_factor"],
                                    charge_scope=r["charge_scope"], aliases=r.get("aliases") or "", sort=i))
    if _empty(session, ProcessRow):
        for i, r in enumerate(_rows(data_dir / "process_rates.csv")):
            session.add(ProcessRow(code=r["process_code"], display_name=r["display_name"],
                                   calculation_type=r["calculation_type"], unit_price=r["unit_price"], unit=r["unit"],
                                   charge_scope=r["charge_scope"], aliases=r.get("aliases") or "", sort=i))
    if _empty(session, FinishRow):
        for i, r in enumerate(_rows(data_dir / "surface_treatments.csv")):
            session.add(FinishRow(code=r["treatment_code"], display_name=r["display_name"], unit_price=r["unit_price"],
                                  charge_scope=r["charge_scope"], aliases=r.get("aliases") or "", sort=i))
    if _empty(session, PolicyRow):
        for i, r in enumerate(_rows(data_dir / "pricing_policy.csv")):
            session.add(PolicyRow(key=r["key"], value=r["value"], description=r.get("description") or "", sort=i))
    if _empty(session, CompanyRow):
        local = data_dir / "company.local.csv"
        for i, r in enumerate(_rows(local if local.exists() else data_dir / "company.csv")):
            if r.get("key"):
                session.add(CompanyRow(key=r["key"].strip(), value=(r.get("value") or "").strip(),
                                       description=r.get("description") or "", sort=i))
    if _empty(session, CaseStatus):
        for i, (name, color, group, role) in enumerate(STATUSES):
            session.add(CaseStatus(name=name, sort=i, color=color, group=group, visible=True, role=role))
    if _empty(session, AttributeDef):
        for i, (key, label, kind, unit, builtin) in enumerate(ATTRIBUTES):
            session.add(AttributeDef(key=key, label=label, input_type=kind, unit=unit, sort=i, searchable=True,
                                     builtin=builtin, options=[]))
    if _empty(session, Template):
        session.add(Template(name="標準（内訳なし）", customers=[], options=dict(TEMPLATE_OPTIONS), is_default=True, sort=0))
        session.add(Template(name="内訳付き", customers=[], options={**TEMPLATE_OPTIONS, "breakdown": True}, sort=1))
    history = HistoryStore(history_path or data_dir / "past_quotes" / "history.csv").load()
    if _empty(session, Staff):
        names = sorted({q.staff for q in history if q.staff}) or ["佐藤"]
        for i, name in enumerate(names):
            session.add(Staff(name=name, sort=i))
    if _empty(session, PastQuoteRow) and history:
        session.execute(insert(PastQuoteRow), [asdict(q) for q in history])
    if _empty(session, Category):
        customers = [c for c, _ in _top_customers(history, 5)]
        for i, (group, names) in enumerate((("顧客別", customers), ("製品別", PRODUCTS), ("部品種別", PART_KINDS))):
            parent = Category(name=group, sort=i)
            session.add(parent)
            session.flush()
            for j, name in enumerate(names):
                session.add(Category(name=name, parent_id=parent.id, sort=j))
    if _empty(session, Partner):
        for name, category, special, price, lead, deals, rating in PARTNERS:
            session.add(Partner(kind="協力会社", name=name, category=category, specialties=special, price_records=price,
                                avg_lead_days=lead, deal_count=deals, rating=rating))
        for name, count in _top_customers(history, 8):
            won = sum(1 for q in history if q.customer == name and q.outcome == "受注")
            session.add(Partner(kind="顧客", name=name, category="顧客", specialties="板金部品", deal_count=won,
                                price_records=f"見積 {count}件", rating=""))
    session.commit()


def _top_customers(history, n: int) -> list[tuple[str, int]]:
    counts: dict[str, int] = {}
    for q in history:
        counts[q.customer] = counts.get(q.customer, 0) + 1
    return sorted(counts.items(), key=lambda kv: -kv[1])[:n]
