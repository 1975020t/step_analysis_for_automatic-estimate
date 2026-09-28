"""Build the seed "past quotes" data from the golden datasets (there is no real quote history yet).

    python scripts/build_past_quotes.py            # -> data/past_quotes/past_quotes_seed.csv

Every part of the development / holdout sets whose evaluation is finished becomes a past quote of a
fictional customer: golden_v2 (STEP dev, 508), holdout_v1 (STEP holdout, 200), dxf_v1 / dxf_holdout_v1
(one row per part, 200 + 100), pdf_v1 / pdf_holdout_v1 (300 + 200; their drawing conditions are used).
NOT used: pdf_holdout_s33_v1 and the blind vocabulary-shift set (still reserved for final judgements).

The file imitates an export of a shop's quote spreadsheet: Japanese column names, conditions as free text
(material / finish / processes as a person typed them), prices rounded by hand, and about a third of the
rows without CAD values (quotes made from paper drawings). Some parts were quoted repeatedly (same
customer and drawing number, later revisions, other quantities).

Prices: the current QuoteEngine price of the recorded conditions, times a customer factor, a per-part
factor (how the estimator judged the part), a price-level factor for the quote date (material prices
rose), and a small noise; unit prices rounded to 10 yen. Outcome (受注/失注/未回答) depends on how high
the price was against the standard. Everything is deterministic (seed 2026).
"""
from __future__ import annotations

import csv
import json
import math
import random
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.master_loader import UNREGISTERED, MasterLoader  # noqa: E402
from src.models import AdditionalProcess, MetricQuality, QuoteCondition, SheetMetalAnalysis  # noqa: E402
from src.quote_engine import QuoteEngine  # noqa: E402

OUT = ROOT / "data" / "past_quotes" / "past_quotes_seed.csv"
SOURCES = [("golden_v2", "parts"), ("holdout_v1", "parts"), ("dxf_v1", "parts"), ("dxf_holdout_v1", "parts"),
           ("pdf_v1", "drawings"), ("pdf_holdout_v1", "drawings")]
START, END = date(2023, 1, 5), date(2026, 8, 31)

# fictional customers: name, drawing-number style, price factor, material mix
CUSTOMERS = [
    ("サンプル電機株式会社", "SE-{y}-{n:04d}", 1.00, {"SECC": 4, "SPCC": 4, "AL5052": 1, "SUS304": 1}),
    ("株式会社サンプル機工", "KB-{y}-{n:04d}", 0.94, {"SPCC": 5, "SS400": 2, "SPHC": 2, "SUS304": 1}),
    ("サンプル精機株式会社", "SP{n:05d}-{r:02d}", 1.05, {"SUS304": 3, "AL5052": 3, "SPCC": 2}),
    ("有限会社サンプル製作所", "D{y4}-{n:04d}", 0.90, {"SPCC": 4, "SS400": 3, "SECC": 2}),
    ("サンプル医療機器株式会社", "MD-{n:05d}", 1.08, {"SUS304": 6, "AL5052": 2}),
    ("サンプルロボティクス株式会社", "RB{y}{n:04d}", 0.97, {"AL5052": 4, "SPCC": 3, "SECC": 2}),
    ("サンプル産業株式会社", "SS-{n:04d}-{r:02d}", 0.92, {"SS400": 4, "SPHC": 3, "SPCC": 2}),
    ("株式会社サンプル計測", "SK{n:05d}", 1.03, {"SECC": 3, "AL5052": 3, "SUS304": 2}),
    ("サンプル通信株式会社", "TC-{y}-{n:05d}", 0.99, {"SECC": 5, "SPCC": 2, "AL5052": 2}),
    ("サンプル住設株式会社", "HS{n:04d}", 0.95, {"SPCC": 4, "SUS304": 3, "SECC": 2}),
]
STAFF = ["佐藤", "鈴木", "高橋"]
PART_NAMES = ["ブラケット", "カバー", "ベースプレート", "取付板", "シャーシ", "パネル", "ステー", "アングル", "トレー",
              "ダクトカバー", "フレーム", "ケース", "蓋", "補強板", "端子台取付板"]
MATERIAL_TEXT = {"SPCC": ["SPCC", "SPCC", "SPCC-SD", "冷延"], "SPHC": ["SPHC", "SPHC-P", "酸洗"],
                 "SECC": ["SECC", "SECC", "ボンデ"], "SUS304": ["SUS304", "SUS304", "SUS304-2B", "SUS"],
                 "AL5052": ["A5052P", "A5052P", "A5052", "AL"], "SS400": ["SS400", "SS400", "SS"]}
UNREG_MATERIALS = {"SUS430": ("SUS304", 0.85), "SGCC": ("SECC", 1.05), "SUS316L": ("SUS304", 1.6),
                   "A1050P": ("AL5052", 0.9), "C1100P": ("AL5052", 3.5)}
FINISH_TEXT = {"NONE": ["なし", "生地", ""], "ZINC_CLEAR": ["ユニクロ", "三価クロメート（光沢）", "三価クリア"],
               "ZINC_YELLOW": ["有色クロメート", "三価クロメート（有色）"], "ZINC_BLACK": ["黒クロメート", "三価黒"],
               "POWDER_COAT": ["粉体塗装", "粉体塗装 N7", "粉体 黒"], "BAKING_PAINT": ["焼付塗装", "メラミン焼付"],
               "ELECTROLESS_NI": ["無電解Ni", "無電解ニッケル"], "ANODIZE_CLEAR": ["白アルマイト", "アルマイト"],
               "ANODIZE_BLACK": ["黒アルマイト"]}
FINISH_BY_MATERIAL = {"SPCC": ["ZINC_CLEAR", "ZINC_YELLOW", "POWDER_COAT", "BAKING_PAINT", "NONE"],
                      "SPHC": ["POWDER_COAT", "BAKING_PAINT", "ZINC_CLEAR", "NONE"],
                      "SS400": ["POWDER_COAT", "BAKING_PAINT", "NONE"], "SECC": ["NONE", "NONE", "POWDER_COAT"],
                      "SUS304": ["NONE", "NONE", "NONE", "ELECTROLESS_NI"],
                      "AL5052": ["ANODIZE_CLEAR", "ANODIZE_BLACK", "NONE"]}
PROCESS_TEXT = {"TAP_M3": "M3タップ", "TAP_M4": "M4タップ", "TAP_M5": "M5タップ", "TAP_M6": "M6タップ", "TAP_M8": "M8タップ",
                "COUNTERSINK": "皿もみ", "PRESS_NUT": "圧入ナット", "PRESS_STUD": "圧入スタッド", "SPOT_WELD": "スポット溶接",
                "TIG_WELD": "TIG溶接"}
QTYS = [1, 2, 3, 5, 10, 10, 20, 30, 50, 50, 100, 100, 200, 300, 500, 1000]


def load_parts() -> list[dict]:
    parts = []
    for name, key in SOURCES:
        data = json.loads((ROOT / "golden" / "datasets" / f"{name}.json").read_text(encoding="utf-8"))
        for rec in data[key]:
            if name.startswith("dxf"):
                if rec["variant"] != "D0":
                    continue
                geo, pid = rec, rec["part"]
            elif name.startswith("pdf"):
                geo, pid = rec["part_truth"], rec["name"]
            else:
                geo, pid = rec, rec["name"]
            parts.append({"source": f"{name}:{pid}", "level": rec.get("level", ""), "note": rec.get("note", ""),
                          "thickness": geo["thickness_mm"], "area": geo["blank_area_mm2"], "cut": geo["cut_length_mm"],
                          "holes": geo["hole_count"], "bends": geo["bend_count"],
                          "bbox": rec.get("flat_bbox_mm"), "pdf": rec.get("truth") if name.startswith("pdf") else None})
    return parts


class Builder:
    def __init__(self, seed: int = 2026):
        self.rng = random.Random(seed)
        self.masters = MasterLoader(ROOT / "data")
        self.engine = QuoteEngine(self.masters)
        self.serial = {}
        self.used_numbers = set()

    # ---------------------------------------------------------------- conditions
    def drawing_no(self, customer: int, first: date) -> str:
        fmt = CUSTOMERS[customer][1]
        while True:
            no = fmt.format(y=first.year % 100, y4=first.year, n=self.rng.randint(1, 99999 if "05d" in fmt else 9999),
                            r=self.rng.randint(1, 20))
            if (customer, no) not in self.used_numbers:
                self.used_numbers.add((customer, no))
                return no

    def conditions(self, part: dict, customer: int) -> dict:
        rng, pdf = self.rng, part["pdf"]
        if pdf:  # the conditions the drawing actually carries (what was written, after revisions)
            material = pdf["material"]
            material_text = pdf["material_text"] if material == UNREGISTERED else None
            if material is None:
                material = rng.choices(*zip(*CUSTOMERS[customer][3].items()))[0]
            finish = pdf["surface_treatment"]
            finish_text = pdf["surface_treatment_text"] if finish == UNREGISTERED else None
            if finish is None:
                finish = rng.choice(FINISH_BY_MATERIAL.get(material, ["NONE"]))
            procs = [(p["code"], p["count_per_part"], p.get("text")) for p in pdf["processes"]]
            qty = pdf["quantity"] or rng.choice(QTYS)
            rush = pdf["rush"]
            drawing = pdf["drawing_no"]
        else:
            material = rng.choices(*zip(*CUSTOMERS[customer][3].items()))[0]
            material_text = finish_text = None
            if rng.random() < 0.04:
                material_text = rng.choice(list(UNREG_MATERIALS))
                material = UNREGISTERED
            base = UNREG_MATERIALS[material_text][0] if material == UNREGISTERED else material
            finish = rng.choice(FINISH_BY_MATERIAL[base])
            procs = []
            for _ in range(rng.choice([0, 0, 1, 1, 2])):
                code = rng.choice(list(PROCESS_TEXT))
                if code in ("SPOT_WELD", "TIG_WELD") and part["bends"] == 0:
                    continue
                if code not in [p[0] for p in procs]:
                    procs.append((code, rng.randint(1, 8), None))
            qty = rng.choice(QTYS)
            rush = rng.random() < 0.12
            drawing = None
        return {"material": material, "material_text": material_text, "finish": finish, "finish_text": finish_text,
                "procs": procs, "qty": int(qty), "rush": bool(rush), "drawing": drawing}

    # ---------------------------------------------------------------- price
    def standard_unit_price(self, part: dict, cond: dict) -> float:
        analysis = SheetMetalAnalysis(
            status="success", file_name=part["source"], thickness_mm=part["thickness"], blank_area_mm2=part["area"],
            cut_length_mm=part["cut"], hole_count=part["holes"], bend_count=part["bends"],
            metric_quality={"blank_area_mm2": MetricQuality(method="golden", confidence="high")})
        extra, factor = 0.0, 1.0
        material = cond["material"]
        if material == UNREGISTERED:
            material, factor = UNREG_MATERIALS.get(cond["material_text"], ("SPCC", 1.2))
        finish = cond["finish"] if cond["finish"] not in (UNREGISTERED, None) else None
        if cond["finish"] == UNREGISTERED:
            extra += 150
        processes = []
        for code, count, _ in cond["procs"]:
            if code == UNREGISTERED:
                extra += 100 * (count or 1)
            else:
                processes.append(AdditionalProcess(process_code=code, quantity=count,
                                                   unit=self.masters.process(code)["unit"], source="user"))
        q = self.engine.calculate(analysis, QuoteCondition(material=material, quantity=cond["qty"], surface_treatment=finish,
                                                           rush=cond["rush"], additional_processes=processes))
        material_cost = q.lines[0].amount / cond["qty"]
        return q.final_price / cond["qty"] + material_cost * (factor - 1) * (1 + q.margin_rate) + extra

    @staticmethod
    def price_level(day: date) -> float:
        """Material and labour prices rose: quotes from early 2023 are about 10 % cheaper than today."""
        span = (END - START).days
        return 0.90 + 0.10 * (day - START).days / span

    @staticmethod
    def round_price(v: float) -> int:
        return int(round(v)) if v < 100 else int(round(v / 10) * 10)

    # ---------------------------------------------------------------- rows
    def rows(self, parts: list[dict]) -> list[dict]:
        rng = self.rng
        rows = []
        for part in parts:
            customer = rng.randrange(len(CUSTOMERS))
            cond = self.conditions(part, customer)
            n_quotes = rng.choices([1, 2, 3, 4], weights=[75, 15, 7, 3])[0]
            span = (END - START).days
            first = START + timedelta(days=rng.randrange(span - 60 * n_quotes if span > 60 * n_quotes else span))
            drawing = cond["drawing"] or self.drawing_no(customer, first)
            name = rng.choice(PART_NAMES)
            part_factor = math.exp(rng.gauss(0, 0.08))   # how the estimator judged this part, kept for repeats
            legacy = rng.random() < 0.35                  # quoted from a paper drawing: no CAD values recorded
            revision = ""
            day = first
            for k in range(n_quotes):
                if k:
                    day = min(END, day + timedelta(days=rng.randint(40, 400)))
                    change = rng.random()
                    if change < 0.5:
                        cond = dict(cond, qty=rng.choice([q for q in QTYS if q != cond["qty"]]))
                    elif change < 0.7:
                        revision = chr(ord(revision) + 1) if revision else "A"
                        base = cond["material"] if cond["material"] != UNREGISTERED else "SPCC"
                        cond = dict(cond, finish=rng.choice(FINISH_BY_MATERIAL.get(base, ["NONE"])), finish_text=None)
                    elif change < 0.85:
                        revision = chr(ord(revision) + 1) if revision else "A"
                        code = rng.choice(["TAP_M4", "TAP_M3", "PRESS_NUT"])
                        procs = [p for p in cond["procs"] if p[0] != code] + [(code, rng.randint(1, 6), None)]
                        cond = dict(cond, procs=procs)
                    cond = dict(cond, rush=rng.random() < 0.12)
                std = self.standard_unit_price(part, cond)
                noise = math.exp(rng.gauss(0, 0.04))
                unit = self.round_price(std * CUSTOMERS[customer][2] * part_factor * self.price_level(day) * noise)
                ratio = unit / (std * self.price_level(day))
                recent = (END - day).days < 60
                if recent and rng.random() < 0.5:
                    outcome = "未回答"
                else:
                    p_win = min(0.92, max(0.08, 0.62 - 2.8 * (ratio - 1.0)))
                    outcome = "受注" if rng.random() < p_win else "失注"
                remark = rng.choice(["", "", "", "", "前回同等", "値引き対応", "図面改訂による再見積", "短納期のため割増"]
                                    if k else ["", "", "", "", "", "新規", "値引き対応"])
                if cond["rush"] and not remark:
                    remark = "特急対応"
                rows.append(self.row(part, cond, customer, drawing, revision, name, day, unit, outcome, remark, legacy))
        rows.sort(key=lambda r: (r["見積日"], r["顧客名"], r["図番"]))
        per_month = {}
        for r in rows:  # legacy quote numbers, serial per month
            ym = r["見積日"][:7]
            per_month[ym] = per_month.get(ym, 0) + 1
            r["見積番号"] = f"M-{ym[2:4]}{ym[5:7]}-{per_month[ym]:04d}"
        return rows

    def row(self, part, cond, customer, drawing, revision, name, day, unit, outcome, remark, legacy) -> dict:
        rng = self.rng
        if cond["material"] == UNREGISTERED:
            material = cond["material_text"]
        else:
            material = rng.choice(MATERIAL_TEXT[cond["material"]])
        if cond["finish"] == UNREGISTERED:
            finish = cond["finish_text"] or "その他処理"
        else:
            finish = rng.choice(FINISH_TEXT.get(cond["finish"], [""]))
        procs = []
        for code, count, text in cond["procs"]:
            if code == UNREGISTERED:
                procs.append(text or "その他加工")
            else:
                procs.append(f"{PROCESS_TEXT[code]}×{count}")
        bbox = part["bbox"]
        return {
            "見積番号": "", "見積日": day.strftime("%Y/%m/%d"), "顧客名": CUSTOMERS[customer][0], "図番": drawing,
            "改訂": revision, "品名": name, "材質": material, "板厚": f"{part['thickness']:g}", "数量": cond["qty"],
            "表面処理": finish, "追加加工": "、".join(procs), "特急": "○" if cond["rush"] else "",
            "単価": unit, "金額": unit * cond["qty"], "結果": outcome, "担当者": rng.choice(STAFF), "備考": remark,
            "展開面積_mm2": "" if legacy else round(part["area"], 1),
            "切断長_mm": "" if legacy else round(part["cut"], 1),
            "穴数": "" if legacy else part["holes"], "曲げ数": "" if legacy else part["bends"],
            "展開寸法_mm": "" if legacy or not bbox else f"{bbox[0]:.0f}×{bbox[1]:.0f}",
            "形状区分": "" if legacy else part["note"],
            "出典": part["source"],
        }


def main() -> int:
    parts = load_parts()
    rows = Builder().rows(parts)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    repeats = len(rows) - len({(r["顧客名"], r["図番"]) for r in rows})
    print(f"{len(parts)} parts -> {len(rows)} past quotes ({repeats} repeat quotes) -> {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
