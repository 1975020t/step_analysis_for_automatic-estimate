"""Drawing-PDF golden data: price-relevant conditions written on sheet-metal part drawings.

Each drawing shows a part from golden/sampler.py (the same generator as the STEP / DXF sets, projected
with OpenCascade hidden-line removal) and carries ordering conditions: material, thickness, quantity,
surface treatment, additional processes (taps, countersinks, press-fit hardware, welding), rush, plus
drawing number / revision and special requirements (tolerance, appearance, inspection).

The conditions are sampled FIRST and become the truth; rendering only decides how they look:
  - which fields are written at all (about a third of the drawings carry every price field, the others
    leave some out - an omitted field has truth null, never a guess)
  - where each one is written (title block, notes, parts list, free text, revision table, callouts on
    the views, stamps, handwriting)
  - notation variants (full-width, JIS / trade names, English), unregistered items (truth UNREGISTERED:
    written on the drawing, but not in the master - must not be mapped to a similar registered code)
  - revisions (truth = the latest value), handwritten corrections on scans (truth = the corrected value)
  - distractors: plain holes next to tapped ones, hardware rows in the parts list, part-count forecasts,
    "急ぎません", mating-part materials, general tolerances that are NOT special requirements, ...

PDF kinds (exact 50/20/20/10 mix per level and block of 10 drawings):
  vector       CAD export, text is extractable
  scan         office scan: grey JPEG, skew, noise, stamps
  fax          G3 FAX: 1-bit, half vertical resolution, speckles, transmission header
  handwritten  colour scan with handwritten additions / corrections and seals over the text

Codes in the truth are master codes (data/*.csv). This module does not read the master: the notation
tables below deliberately contain variants that are NOT master aliases.
"""
from __future__ import annotations

import io
import random
import zlib
from pathlib import Path

from reportlab.lib.pagesizes import A3, A4, landscape, portrait
from reportlab.pdfgen import canvas

from golden import pdf_render as R
from golden.sampler import generate
from golden.truth_v2 import apply_v2

UNREG = "UNREGISTERED"
KINDS = ("vector", "scan", "fax", "handwritten")
KIND_BLOCK = ["vector"] * 5 + ["scan"] * 2 + ["fax"] * 2 + ["handwritten"]
STYLES = {"jis_cad": 0.34, "iso_en": 0.20, "jis_bom": 0.16, "bilingual": 0.15, "memo": 0.15}
CAPS = {
    "jis_cad": {"title", "notes", "rev", "callouts"},
    "bilingual": {"title", "notes", "rev", "callouts"},
    "iso_en": {"title", "notes", "rev", "callouts"},
    "jis_bom": {"title", "notes", "rev", "callouts", "bom"},
    "memo": {"free", "bom", "callouts"},
}
PRICE_FIELDS = ("material", "thickness", "quantity", "finish")

# ------------------------------------------------------------------ notation tables
MAT = {
    "ja": {
        "SPCC": ["SPCC", "SPCC-SD", "SPCC（JIS G3141）", "冷間圧延鋼板 SPCC", "ＳＰＣＣ", "SPCC材", "冷延鋼板（SPCC）", "spcc"],
        "SPHC": ["SPHC", "SPHC-P", "SPHC（酸洗）", "熱間圧延鋼板 SPHC", "ＳＰＨＣ", "SPHC-PO"],
        "SECC": ["SECC", "SECC（ボンデ鋼板）", "ボンデ鋼板", "SECC-C", "電気亜鉛めっき鋼板 SECC", "ＳＥＣＣ"],
        "SUS304": ["SUS304", "SUS304-2B", "SUS304 2B", "ステンレス SUS304", "SUS 304", "ＳＵＳ３０４"],
        "AL5052": ["A5052P", "A5052P-H32", "A5052-H32", "アルミ A5052P", "A5052", "Ａ５０５２Ｐ"],
        "SS400": ["SS400", "SS400（黒皮）", "一般構造用圧延鋼材 SS400", "ＳＳ４００"],
    },
    "en": {
        "SPCC": ["SPCC", "COLD ROLLED STEEL (SPCC)", "CRS, JIS G3141 SPCC", "SPCC-SD", "STEEL, COLD ROLLED, SPCC"],
        "SPHC": ["SPHC", "HOT ROLLED STEEL SPHC-P", "SPHC (PICKLED & OILED)"],
        "SECC": ["SECC", "ELECTROGALVANIZED STEEL (SECC)", "SECC-C"],
        "SUS304": ["SUS304", "STAINLESS STEEL 304 (2B)", "AISI 304 / SUS304", "SUS304-2B"],
        "AL5052": ["A5052P-H32", "ALUMINUM 5052-H32", "AL 5052-H32", "A5052P"],
        "SS400": ["SS400", "STRUCTURAL STEEL SS400"],
    },
}
MAT_UNREG = {"ja": ["SUS430", "SUS430-2B", "SGCC", "C1100P", "A1050P", "SUS316L"],
             "en": ["SUS430", "SGCC (HOT-DIP GALVANIZED)", "C1100P COPPER", "A1050P", "SUS316L"]}
MAT_WEIGHTS = [("SPCC", 30), ("SECC", 15), ("SUS304", 15), ("AL5052", 12), ("SPHC", 10), ("SS400", 10), (UNREG, 8)]

FIN = {
    "ja": {
        "NONE": ["なし", "無し", "生地", "生地のまま", "無処理"],
        "ZINC_CLEAR": ["三価クロメート（光沢）", "ユニクロ（三価）", "電気亜鉛めっき 三価クリア", "Znめっき 三価クロメート クリア"],
        "ZINC_YELLOW": ["三価クロメート（有色）", "有色クロメート（三価）", "亜鉛めっき 三価有色"],
        "ZINC_BLACK": ["三価黒クロメート", "亜鉛めっき 三価黒", "黒クロメート（三価）"],
        "POWDER_COAT": ["粉体塗装 マンセルN7 半艶", "粉体塗装（黒 3分艶）", "静電粉体塗装 アイボリー", "紛体塗装 N7"],
        "BAKING_PAINT": ["メラミン焼付塗装 5Y7/1", "焼付塗装（アイボリー）", "焼付け塗装 黒"],
        "ELECTROLESS_NI": ["無電解ニッケルめっき 5μm", "無電解Niめっき", "カニゼンめっき"],
        "ANODIZE_CLEAR": ["白アルマイト", "アルマイト（白）", "クリアアルマイト 10μm", "陽極酸化処理（白）"],
        "ANODIZE_BLACK": ["黒アルマイト", "アルマイト（黒）"],
    },
    "en": {
        "NONE": ["NONE", "NO FINISH", "UNFINISHED"],
        "ZINC_CLEAR": ["ZINC PLATED, TRIVALENT CLEAR", "CLEAR ZINC (TRIVALENT)", "ZN PLATE, CLEAR CHROMATE (CR3+)"],
        "ZINC_YELLOW": ["ZINC PLATED, TRIVALENT YELLOW", "YELLOW ZINC (TRIVALENT)"],
        "ZINC_BLACK": ["BLACK ZINC (TRIVALENT)", "ZINC PLATED, TRIVALENT BLACK"],
        "POWDER_COAT": ["POWDER COAT, RAL 7035", "POWDER COATED BLACK, SEMI-GLOSS"],
        "BAKING_PAINT": ["BAKED ENAMEL, BLACK", "BAKED PAINT, RAL 9005"],
        "ELECTROLESS_NI": ["ELECTROLESS NICKEL 5um", "ELECTROLESS NI PLATE"],
        "ANODIZE_CLEAR": ["CLEAR ANODIZE", "ANODIZE, CLEAR, 10um"],
        "ANODIZE_BLACK": ["BLACK ANODIZE", "ANODIZE, BLACK"],
    },
}
FIN_UNREG = {"ja": ["クロムめっき", "パーカー処理（リン酸亜鉛）", "カチオン電着塗装", "錫めっき"],
             "en": ["CHROME PLATE", "ZINC PHOSPHATE", "E-COAT", "TIN PLATE"]}
FIN_UNREG_AL = {"ja": ["化成処理（アロジン）"], "en": ["CHEM FILM (ALODINE)"]}
FIN_BY_MAT = {
    "SPCC": [("ZINC_CLEAR", 3), ("ZINC_YELLOW", 2), ("ZINC_BLACK", 1), ("POWDER_COAT", 3), ("BAKING_PAINT", 2), ("ELECTROLESS_NI", 1), ("NONE", 1)],
    "SPHC": [("ZINC_CLEAR", 2), ("ZINC_BLACK", 1), ("POWDER_COAT", 3), ("BAKING_PAINT", 2), ("NONE", 1)],
    "SS400": [("ZINC_CLEAR", 1), ("ZINC_YELLOW", 1), ("POWDER_COAT", 3), ("BAKING_PAINT", 2), ("NONE", 2)],
    "SECC": [("NONE", 5), ("POWDER_COAT", 2), ("BAKING_PAINT", 2)],
    "SUS304": [("NONE", 6), ("POWDER_COAT", 1)],
    "AL5052": [("ANODIZE_CLEAR", 3), ("ANODIZE_BLACK", 2), ("NONE", 2), ("POWDER_COAT", 1)],
    UNREG: [("NONE", 3), ("POWDER_COAT", 1), ("ZINC_CLEAR", 1)],
}
QTYS = [1, 2, 3, 5, 10, 10, 20, 20, 30, 50, 50, 100, 100, 150, 200, 300, 500, 1000, 2000]

CS_DIA = {3: (3.4, 6.4), 4: (4.5, 8.4), 5: (5.5, 10.4)}
PITCH = {3: "0.5", 4: "0.7", 5: "0.8", 6: "1.0", 8: "1.25"}
PROC = {  # code -> {lang: [templates]} ; {n} count per callout, {d} nominal size, {p} pitch, {h}/{c} countersink dias
    "TAP": {"ja": ["{n}-M{d}", "M{d}タップ {n}ヶ所", "M{d}×{p} タップ {n}箇所", "{n}-M{d}×{p}", "M{d}タップ（{n}ヶ所）", "{n}×M{d} タップ"],
            "en": ["{n}X M{d}x{p} TAP THRU", "M{d} TAP, {n} PLCS", "{n}X M{d}-{p} THD"]},
    "COUNTERSINK": {"ja": ["{n}-φ{h} 皿もみ 90°", "皿もみ M{d}用 {n}ヶ所", "{n}-φ{h} 皿 φ{c}×90°"],
                    "en": ["{n}X Ø{h} CSK Ø{c} x 90°", "CSK FOR M{d} FHS, {n} PLCS"]},
    "PRESS_NUT": {"ja": ["PEMナット CLS-M{d}-1 {n}ヶ所 圧入", "圧入ナット M{d} ×{n}", "クリンチナット M{d}（{n}個）", "{n}-M{d} 圧入ナット"],
                  "en": ["{n}X PEM S-M{d}-1 NUT", "SELF-CLINCHING NUT M{d}, {n} PLCS"]},
    "PRESS_STUD": {"ja": ["圧入スタッド M{d}×10 {n}ヶ所", "PEMスタッド FH-M{d}-10 ×{n}"],
                   "en": ["{n}X PEM FH-M{d}-10 STUD", "SELF-CLINCHING STUD M{d}x10, {n} PLCS"]},
    "SPOT_WELD": {"ja": ["スポット溶接 {n}点", "スポット溶接（{n}ヶ所）", "SPOT {n}点"],
                  "en": ["SPOT WELD {n} PLCS", "{n}X SPOT WELD"]},
    "TIG_WELD": {"ja": ["TIG溶接 {n}箇所", "角部 TIG溶接 {n}ヶ所", "コーナー{n}ヶ所 TIG溶接"],
                 "en": ["TIG WELD {n} CORNERS", "TIG WELD, {n} PLCS"]},
}
PROC_UNREG = {"ja": ["バーリングタップ M4 {n}ヶ所", "M10タップ {n}ヶ所", "圧入スペーサ SO-M3-10 {n}ヶ所", "ブラインドリベット {n}ヶ所"],
              "en": ["{n}X BLIND RIVET", "{n}X PEM SO-M3-10 STANDOFF", "{n}X M10 TAP"]}
HOLE_DISTRACTORS = {"ja": ["{n}-φ{a}", "{n}-φ4.5 キリ（M4用）", "φ{a} キリ", "{n}-φ3.4（M3用キリ穴）", "□{b}×{c} 抜き"],
                    "en": ["{n}X Ø{a} THRU", "Ø4.5 THRU (FOR M4)", "{n}X Ø3.4 THRU", "RECT. {b} x {c} THRU"]}
FLAGS = {
    "tolerance": {"ja": ["穴位置公差 ±0.05", "※印寸法 ±0.1 厳守", "平面度 0.2 以下のこと", "指示穴ピッチ ±0.03"],
                  "en": ["HOLE POSITION TOLERANCE ±0.05", "FLATNESS 0.2 MAX.", "DIMENSIONS MARKED * ±0.1"]},
    "appearance": {"ja": ["外観面（A面）キズ・打痕不可", "化粧面：底面外側 キズなきこと", "外観重要部品につき取扱注意"],
                   "en": ["COSMETIC SURFACE: NO SCRATCHES OR DENTS", "CLASS A COSMETIC SURFACE (SIDE A)"]},
    "inspection": {"ja": ["検査成績書提出のこと", "全数寸法検査のこと", "ミルシート添付のこと", "初品検査成績書要"],
                   "en": ["INSPECTION REPORT REQUIRED", "100% DIMENSIONAL INSPECTION", "MATERIAL CERTIFICATE REQUIRED"]},
}
STANDARD_NOTES = {"ja": ["バリ・カエリなきこと", "指示なき曲げ内R＝板厚", "図示なき角部 C0.5", "寸法は仕上がり寸法とする", "尺度に従わず寸法で読むこと"],
                  "en": ["REMOVE ALL BURRS AND SHARP EDGES.", "INSIDE BEND RADIUS = T UNLESS OTHERWISE SPECIFIED.",
                         "DO NOT SCALE DRAWING.", "DIMENSIONS APPLY AFTER FINISH."]}
GENERAL_TOL = {"ja": ["普通公差 JIS B 0405-m", "普通公差 JIS B 0405 中級", "普通寸法公差 ±0.2"],
               "en": ["GENERAL TOLERANCES: ISO 2768-mK", "UNLESS OTHERWISE SPECIFIED ±0.2"]}
RUSH = {"ja": ["特急", "至急", "短納期品", "急ぎ（{m}/{d}納品希望）", "特急対応をお願いします"],
        "en": ["URGENT", "RUSH ORDER", "EXPEDITE - DUE {m}/{d}"]}
RUSH_NEG = {"ja": ["納期：別途打合せ", "納期 通常", "急ぎません（通常納期で可）"], "en": ["DELIVERY: STANDARD LEAD TIME", "NOT URGENT"]}
DISTRACT_NOTES = {"ja": ["月産 {f} 個予定（参考）", "相手部品：A5052（別手配）", "旧図番 {o}（廃止）", "板取り方向：図示の矢印方向"],
                  "en": ["FORECAST {f} PCS/MONTH (REFERENCE ONLY)", "MATING PART: A5052 (SUPPLIED SEPARATELY)", "SUPERSEDES DWG {o}"]}
PART_NAMES = {"ja": ["取付トレー", "カバー", "ブラケット", "ベースプレート", "シャーシ", "パネル", "ステー", "アングル", "ダクトカバー", "端子台取付板"],
              "en": ["MOUNTING BRACKET", "COVER", "BASE PLATE", "CHASSIS", "SIDE PANEL", "STAY", "TRAY"]}
COMPANIES = {"ja": ["株式会社サンプル機工", "サンプル電機株式会社", "（有）サンプル製作所", "サンプル精機株式会社"],
             "en": ["SAMPLE ROBOTICS INC.", "EXAMPLE DEVICES LTD.", "SAMPLE AUTOMATION CO."]}
PEOPLE = ["山田", "佐藤", "鈴木", "高橋", "田中", "伊藤", "渡辺"]
LABELS = {
    "ja": {"drawing_no": ["図番", "図面番号"], "part_name": ["品名", "名称", "部品名"], "material": ["材質", "材料"],
           "thickness": ["板厚", "厚さ"], "mat_thk": ["材質・板厚", "材料／板厚"], "finish": ["表面処理", "処理", "表面仕上"],
           "quantity": ["数量", "個数", "製作数", "手配数"], "revision": ["改訂", "版"], "due": ["納期"], "scale": ["尺度"], "date": ["作成日", "日付"]},
    "en": {"drawing_no": ["DRAWING NO.", "DWG NO."], "part_name": ["TITLE", "PART NAME", "DESCRIPTION"], "material": ["MATERIAL"],
           "thickness": ["THICKNESS", "THK"], "mat_thk": ["MATERIAL / THK"], "finish": ["FINISH", "SURFACE TREATMENT"],
           "quantity": ["QTY", "QUANTITY", "Q'TY"], "revision": ["REV", "REVISION"], "due": ["DUE DATE"], "scale": ["SCALE"], "date": ["DATE"]},
}
BILINGUAL = {"drawing_no": "図番 DWG NO.", "part_name": "品名 TITLE", "material": "材質 MATERIAL", "thickness": "板厚 THK",
             "mat_thk": "材質/板厚 MAT./THK", "finish": "表面処理 FINISH", "quantity": "数量 QTY", "revision": "改訂 REV",
             "due": "納期 DUE", "scale": "尺度 SCALE", "date": "日付 DATE"}
NOTE_FORMS = {
    "ja": {"material": ["材質：{v}", "材料は{v}とする"], "thickness": ["板厚：{v}", "板厚 {v}"], "quantity": ["製作数量：{v}", "数量 {v}（今回手配分）"],
           "finish": ["表面処理：{v}", "{v}のこと"], "mat_thk": ["材質：{m}　{t}"], "see": ["注記参照", "注記による"]},
    "en": {"material": ["MATERIAL: {v}"], "thickness": ["MATERIAL THICKNESS: {v}"], "quantity": ["QUANTITY: {v}"],
           "finish": ["FINISH: {v}"], "mat_thk": ["MATERIAL: {m}, {t}"], "see": ["SEE NOTES", "PER NOTE"]},
}
FREE_FORMS = {"material": ["材料：{v}", "材質 {v} でお願いします"], "thickness": ["板厚 {v}", "厚み {v}"],
              "quantity": ["数量：{v}", "{v} 必要です", "製作数 {v}"], "finish": ["表面処理：{v}", "処理は{v}で"],
              "mat_thk": ["材料：{m} {t}", "{m} {t} でお願いします"]}
REV_FORMS = {
    "ja": {"quantity": ["数量変更 {o}→{n}", "手配数 {o}→{n} に変更"], "finish": ["表面処理変更 {o}→{n}", "処理変更（{o} → {n}）"],
           "material": ["材質変更 {o}→{n}"], "thickness": ["板厚変更 {o}→{n}"],
           "other": ["外形寸法変更 {a}→{b}", "穴位置変更", "注記{k}追加", "板取り方向追記", "曲げR変更 R{r}→R{s}"]},
    "en": {"quantity": ["QTY CHANGED FROM {o} TO {n}"], "finish": ["FINISH CHANGED: {o} TO {n}"],
           "material": ["MATERIAL CHANGED FROM {o} TO {n}"], "thickness": ["THICKNESS CHANGED {o} TO {n}"],
           "other": ["HOLE LOCATION REVISED", "NOTE {k} ADDED", "DIM {b} WAS {a}", "BEND RELIEF ADDED"]},
}
HAND_RUSH = ["急ぎ {m}/{d}納品希望", "至急！", "特急でお願いします", "大至急"]
HAND_NOISE = ["寸法確認済 {m}/{d}", "OK 田中", "図面受領 {m}/{d}", "要確認→済"]


# ------------------------------------------------------------------ helpers
def _pick(rng, weighted):
    items, weights = zip(*weighted)
    return rng.choices(items, weights=weights)[0]


def fmt_t(t: float, lang: str, rng: random.Random) -> str:
    v = f"{t:.1f}" if rng.random() < 0.7 else f"{t:g}"
    if lang == "en":
        return rng.choice([f"{v} mm", f"{v}mm", f"THK {v}", f"T={v}", f"{v} THK"])
    return rng.choice([f"t{v}", f"t={v}", f"{v}t", f"{v}mm", f"t{v}mm", f"{v}"])


def fmt_q(q: int, lang: str, rng: random.Random, bare=False) -> str:
    v = f"{q:,}" if q >= 1000 and rng.random() < 0.6 else str(q)
    if bare:
        return v
    if lang == "en":
        return rng.choice([v, f"{v} PCS", f"{v} EA", f"{v} pcs"])
    return rng.choice([v, f"{v}個", f"{v}ヶ", f"{v}枚", f"{v} pcs", f"N={v}"])


def mat_text(code, lang, rng):
    return rng.choice(MAT_UNREG[lang]) if code == UNREG else rng.choice(MAT[lang][code])


def fin_text(code, lang, rng, material):
    if code == UNREG:
        return rng.choice(FIN_UNREG_AL[lang] if material == "AL5052" else FIN_UNREG[lang])
    return rng.choice(FIN[lang][code])


def kind_for(seed: int, level: str, index: int) -> str:
    block = list(KIND_BLOCK)
    random.Random(zlib.crc32(f"pdfkind:{seed}:{level}:{index // 10}".encode())).shuffle(block)
    return block[index % 10]


def _date(rng):
    return rng.randint(1, 12), rng.randint(1, 28)


# ------------------------------------------------------------------ the plan: truth + what goes where
class Plan:
    """Everything that will be written on the drawing, decided before drawing. Text items carry tags so
    handwriting can later strike them on the scanned image."""

    def __init__(self, rng: random.Random, style: str, lang: str, kind: str, level: str, thickness: float,
                 unseen: bool = False, labels=None):
        self.rng, self.style, self.lang, self.kind, self.level, self.t = rng, style, lang, kind, level, thickness
        self.caps = CAPS.get(style, {"title", "notes", "rev", "callouts"})
        self.unseen = unseen
        self.labels = labels or LABELS["en" if lang == "en" else "ja"]
        self.title: list[tuple[str, str, str | None]] = []  # (field key, value text, tag)
        self.notes: list[tuple[str, str | None]] = []
        self.free: list[tuple[str, str | None]] = []
        self.callouts: list[tuple[str, str | None]] = []
        self.revisions: list[tuple[str, str, str]] = []
        self.rev_order = "asc"
        self.bom = None  # (header, rows, tags)
        self.marks: list[dict] = []  # raster-only: handwriting and stamps
        self.meta = {"sources": {}, "revision_changes": [], "handwriting": [], "stamps": [], "distractors": []}
        self.truth = {"drawing_no": None, "revision": None, "material": None, "material_text": None, "thickness_mm": None,
                      "quantity": None, "surface_treatment": None, "surface_treatment_text": None, "processes": [],
                      "rush": False, "flags": []}

    # -------------------------------------------------------------- sampling
    def build(self):
        rng, lang = self.rng, self.lang
        raster = self.kind != "vector"
        m, d = _date(rng)
        self.date = f"2026.{m:02d}.{d:02d}" if lang == "ja" else f"2026-{m:02d}-{d:02d}"
        self.company = rng.choice(COMPANIES[lang])
        self.part_name = rng.choice(PART_NAMES[lang])
        self.people = rng.sample(PEOPLE, 3)
        self.general_tol = rng.choice(GENERAL_TOL[lang])

        # ---- values
        material = _pick(rng, MAT_WEIGHTS)
        finish = _pick(rng, FIN_BY_MAT[material])
        if rng.random() < 0.08:
            finish = UNREG
        values = {
            "material": (material, mat_text(material, lang, rng)),
            "thickness": (self.t, fmt_t(self.t, lang, rng)),
            "quantity": ((q := rng.choice(QTYS)), fmt_q(q, lang, rng)),
            "finish": (finish, fin_text(finish, lang, rng, material)),
        }
        dno = self._drawing_no()

        # ---- which price fields are written
        present = set(PRICE_FIELDS)
        full = rng.random() < 0.35
        if not full:
            drop = {f for f, p in (("material", 0.15), ("thickness", 0.35), ("quantity", 0.45), ("finish", 0.45)) if rng.random() < p}
            if not drop:
                drop = {rng.choice(PRICE_FIELDS)}
            present -= drop
        self.meta["all_price_fields_written"] = present == set(PRICE_FIELDS)

        # ---- where each one is written
        loc = {f: self._location(f) for f in sorted(present)}
        combine = ("material" in loc and "thickness" in loc and loc["material"] == loc["thickness"]
                   and loc["material"] in ("title", "notes", "free") and rng.random() < 0.45)

        # ---- revisions (change values that are written; truth = latest)
        shown = {f: values[f][1] for f in present}
        final = {f: values[f] for f in present}
        if "rev" in self.caps and rng.random() < 0.38:
            shown = self._revisions(present, loc, values, shown)

        # ---- truth for the price fields
        for f in present:
            code, text = final[f]
            self.meta["sources"][f] = loc[f]
            if f == "material":
                self.truth["material"], self.truth["material_text"] = code, text
            elif f == "thickness":
                self.truth["thickness_mm"] = float(code)
            elif f == "quantity":
                self.truth["quantity"] = int(code)
            else:
                self.truth["surface_treatment"], self.truth["surface_treatment_text"] = code, text

        # ---- drawing number
        dno_written = self.style != "memo" or rng.random() < 0.7
        if dno_written:
            self.truth["drawing_no"] = dno
        self.dno = dno if dno_written else None

        # ---- title block cells
        if "title" in self.caps:
            self._title_cells(present, loc, shown, combine)
        # ---- notes / free text for fields placed there
        for f in sorted(present):
            if loc[f] == "notes" and not (combine and f == "thickness"):
                self._field_note(f, shown, combine)
            elif loc[f] == "free" and not (combine and f == "thickness"):
                self._field_free(f, shown, combine)
        if self.style == "memo" and self.dno and rng.random() < 0.5:
            self.free.insert(0, (rng.choice(["品番 {v}", "図番：{v}", "{v}"]).format(v=self.dno), "val:drawing_no"))

        # ---- processes, rush, flags, distractors
        procs = self._processes()
        self._place_processes(procs, present, values)
        if self.style == "memo" and self.dno and self.bom is None and not any(t == "val:drawing_no" for _, t in self.free):
            self.free.insert(0, (rng.choice(["品番 {v}", "図番：{v}"]).format(v=self.dno), "val:drawing_no"))
        self._rush(raster)
        self._flags()
        self._distractors(present, values)
        self._standard_notes()

        # ---- raster marks
        if raster:
            self._stamps()
        if self.kind == "handwritten":
            self._handwriting(present, loc, values)
        self.truth["processes"] = self._merge(self.truth["processes"])
        self.truth["flags"] = sorted(self.truth["flags"])
        rng.shuffle(self.notes)
        return self

    def _drawing_no(self):
        rng = self.rng
        a, b = rng.choice("ABCDKMST"), rng.choice("ABCKPRSX")
        return rng.choice([f"{a}{b}-{rng.randint(20, 26)}-{rng.randint(1, 9999):04d}", f"D{rng.randint(2019, 2026)}-{rng.randint(1, 9999):04d}",
                           f"{a}{rng.randint(10000, 99999)}-{rng.randint(1, 20):02d}", f"{a}{b}{rng.randint(100, 999)}-{rng.randint(1000, 9999)}"])

    def _location(self, field):
        rng, caps = self.rng, self.caps
        options = []
        if "title" in caps:
            options.append(("title", {"material": 7, "thickness": 6, "quantity": 6, "finish": 6}[field]))
        if "notes" in caps:
            options.append(("notes", {"material": 2, "thickness": 2, "quantity": 2, "finish": 4}[field]))
        if "bom" in caps and field in ("material", "quantity"):
            options.append(("bom", 4 if self.style == "jis_bom" else 3))
        if "free" in caps:
            options.append(("free", 8))
        return _pick(rng, options)

    def _revisions(self, present, loc, values, shown):
        rng, lang = self.rng, self.lang
        n = rng.choice([1, 1, 2, 2, 3])
        style = rng.choice(["letter", "letter", "number", "triangle"])
        syms = [{"letter": "ABC"[i], "number": str(i + 1), "triangle": f"△{i + 1}"}[style] for i in range(n)]
        dates = sorted(f"2026.{rng.randint(1, 9):02d}.{rng.randint(1, 28):02d}" for _ in range(n))
        changeable = [f for f in ("quantity", "finish", "material", "thickness") if f in present and loc[f] in ("title", "notes")]
        weights = {"quantity": 5, "finish": 4, "material": 1, "thickness": 1}
        entries = []
        for i in range(n):
            f = None
            if changeable and rng.random() < 0.6:
                f = rng.choices(changeable, weights=[weights[c] for c in changeable])[0]
            entries.append(f)
        history = {}
        for i, f in reversed(list(enumerate(entries))):  # newest first: build the chain backwards
            if f is None:
                continue
            newer_text = history[f][0][1] if f in history else values[f][1]
            newer_code = history[f][0][0] if f in history else values[f][0]
            old_code, old_text = self._other_value(f, newer_code)
            history.setdefault(f, []).insert(0, (old_code, old_text))
            entries[i] = (f, old_text, newer_text)
        rows = []
        for i, e in enumerate(entries):
            if e is None:
                tmpl = rng.choice(REV_FORMS[lang]["other"])
                desc = tmpl.format(a=rng.randint(50, 300), b=rng.randint(50, 300), k=rng.randint(2, 7), r=0.5, s=1.0)
            else:
                f, o, nw = e
                if f == "quantity":
                    o, nw = o.rstrip("個ヶ枚 pcsPCSEA").replace("N=", ""), nw.rstrip("個ヶ枚 pcsPCSEA").replace("N=", "")
                desc = rng.choice(REV_FORMS[lang][f]).format(o=o, n=nw)
                self.meta["revision_changes"].append({"rev": syms[i], "field": f, "from": e[1], "to": e[2]})
            rows.append((syms[i], desc, dates[i]))
        self.revisions = rows
        self.rev_order = rng.choice(["asc", "asc", "desc"])
        self.truth["revision"] = syms[-1].lstrip("△")
        self.meta["revision_style"] = style
        mode = {}
        for f in history:
            m = "stale" if rng.random() < 0.2 else "updated"
            mode[f] = m
            if m == "stale":  # the drafter updated the revision table but not the value itself
                shown[f] = history[f][-1][1]
        self.meta["revision_mode"] = mode
        return shown

    def _other_value(self, field, current):
        rng, lang = self.rng, self.lang
        if field == "quantity":
            q = rng.choice([x for x in QTYS if x != current])
            return q, fmt_q(q, lang, rng)
        if field == "thickness":
            from golden.sampler import THICKNESSES
            t = rng.choice([x for x in THICKNESSES if x != current])
            return t, fmt_t(t, lang, rng)
        if field == "material":
            c = rng.choice([k for k in MAT[lang] if k != current])
            return c, mat_text(c, lang, rng)
        c = rng.choice([k for k in FIN[lang] if k not in (current, "NONE")])
        return c, fin_text(c, lang, rng, None)

    def _title_cells(self, present, loc, shown, combine):
        rng = self.rng
        cells = []
        if self.dno:
            cells.append(("drawing_no", self.dno, "val:drawing_no"))
        cells.append(("part_name", self.part_name, None))
        see = rng.choice(NOTE_FORMS[self.lang]["see"])
        for f in ("material", "thickness", "finish", "quantity"):
            if f == "thickness" and combine:
                continue
            key = "mat_thk" if (f == "material" and combine) else f
            if f in present and loc[f] == "title":
                val = f"{shown['material']} {shown['thickness']}" if key == "mat_thk" else shown[f]
                cells.append((key, val, f"val:{f}"))
            elif f in present and loc[f] == "notes" and rng.random() < 0.35:
                cells.append((key, see, None))
            elif f not in present and f != "finish" and rng.random() < 0.5:
                # label printed, value left blank = not specified. Not for the finish: a blank finish cell is
                # read as "no treatment" by many people, so an omitted finish has no cell at all.
                cells.append((key, "", None))
        if self.revisions or rng.random() < 0.3:
            cells.append(("revision", self.truth["revision"] or "", "val:revision"))
        self.title = cells

    def _field_note(self, f, shown, combine):
        forms = NOTE_FORMS[self.lang]
        if f == "material" and combine:
            text = self.rng.choice(forms["mat_thk"]).format(m=shown["material"], t=shown["thickness"])
        else:
            text = self.rng.choice(forms[f]).format(v=shown[f])
        self.notes.append((text, f"val:{f}"))

    def _field_free(self, f, shown, combine):
        if f == "material" and combine:
            text = self.rng.choice(FREE_FORMS["mat_thk"]).format(m=shown["material"], t=shown["thickness"])
        else:
            text = self.rng.choice(FREE_FORMS[f]).format(v=shown[f])
        self.free.append((text, f"val:{f}"))

    # -------------------------------------------------------------- processes
    def _processes(self):
        rng, t, lv = self.rng, self.t, self.level
        k = _pick(rng, [(0, 35), (1, 35), (2, 20), (3, 10)])
        pool = ["TAP", "TAP", "COUNTERSINK", "PRESS_NUT", "PRESS_STUD"]
        if lv != "Lv0":
            pool.append("SPOT_WELD")
        if lv in ("Lv2", "Lv3"):
            pool.append("TIG_WELD")
        if t < 1.2:
            pool = [p for p in pool if p != "COUNTERSINK"]
        out, used = [], set()
        for _ in range(k):
            kind = rng.choice(pool)
            if kind == "TAP":
                d = rng.choice([3, 4] if t <= 1.0 else [3, 4, 5, 6] if t <= 2.0 else [4, 5, 6, 8])
                code, n = f"TAP_M{d}", rng.randint(1, 8)
            elif kind == "COUNTERSINK":
                d, code, n = rng.choice([3, 4, 5]), "COUNTERSINK", rng.randint(2, 6)
            elif kind in ("PRESS_NUT", "PRESS_STUD"):
                d, code, n = rng.choice([3, 4, 5, 6]), kind, rng.randint(2, 6)
            elif kind == "SPOT_WELD":
                d, code, n = 0, kind, rng.randint(4, 12)
            else:
                d, code, n = 0, kind, rng.randint(2, 4)
            if code in used:
                continue
            used.add(code)
            out.append({"code": code, "family": kind, "d": d, "n": n})
        if rng.random() < 0.12:
            out.append({"code": UNREG, "family": UNREG, "d": 0, "n": rng.randint(2, 6)})
        return out

    def _proc_text(self, p, n, lang=None, tmpl=None):
        lang = lang or self.lang
        if p["code"] == UNREG:
            return (tmpl or self.rng.choice(PROC_UNREG[lang])).format(n=n)
        tmpl = tmpl or self.rng.choice(PROC[p["family"]][lang])
        h, c = CS_DIA.get(p["d"], (0, 0))
        return tmpl.format(n=n, d=p["d"], p=PITCH.get(p["d"], ""), h=h, c=c)

    def _place_processes(self, procs, present, values):
        rng = self.rng
        bom_rows = []
        for p in procs:
            n = p["n"]
            truth = {"code": p["code"], "count_per_part": n}
            if p["family"] in ("PRESS_NUT", "PRESS_STUD") and "bom" in self.caps and rng.random() < 0.6:
                bom_rows.append(p)
                truth["text"] = self._hardware_name(p)
                self.meta["sources"].setdefault("processes", []).append("bom")
                self.truth["processes"].append(truth)
                continue
            where = "callouts" if rng.random() < 0.7 else ("notes" if "notes" in self.caps else "free")
            parts = [n]
            if where == "callouts" and n >= 2 and p["family"] in ("TAP", "COUNTERSINK", "PRESS_NUT") and rng.random() < 0.3:
                a = rng.randint(1, n - 1)
                parts = [a, n - a]  # the same process called out at two places: the reader must add them up
            tmpl = self.rng.choice(PROC_UNREG[self.lang] if p["code"] == UNREG else PROC[p["family"]][self.lang])
            texts = [self._proc_text(p, k, tmpl=tmpl) for k in parts]  # same notation at both places
            truth["text"] = " + ".join(texts)
            for tx in texts:
                if where == "callouts":
                    self.callouts.append((tx, None))
                elif where == "notes":
                    self.notes.append((rng.choice(["{v}", "図示位置 {v}", "{v}（図示）"] if self.lang == "ja" else ["{v}", "{v} AS SHOWN"]).format(v=tx), None))
                else:
                    self.free.append((tx + rng.choice(["", " お願いします", "（図示）"]), None))
            self.meta["sources"].setdefault("processes", []).append(where)
            self.truth["processes"].append(truth)
        # plain holes etc. next to the real callouts
        for _ in range(rng.randint(1, 3)):
            tmpl = rng.choice(HOLE_DISTRACTORS[self.lang])
            self.callouts.append((tmpl.format(n=rng.randint(2, 6), a=rng.choice([5, 6, 8, 10, 12, 16]),
                                              b=rng.choice([20, 30, 40]), c=rng.choice([10, 15, 20])), None))
        rng.shuffle(self.callouts)
        if "bom" in self.caps and (bom_rows or self.style == "jis_bom" or self.meta["sources"].get("material") == "bom"
                                   or self.meta["sources"].get("quantity") == "bom"):
            self._bom(bom_rows, present, values)

    def _hardware_name(self, p):
        d = p["d"]
        if p["code"] == "PRESS_NUT":
            return self.rng.choice([f"PEMナット CLS-M{d}-1", f"圧入ナット M{d}", f"クリンチナット M{d}"] if self.lang == "ja"
                                   else [f"PEM S-M{d}-1", f"SELF-CLINCHING NUT M{d}"])
        return self.rng.choice([f"PEMスタッド FH-M{d}-10", f"圧入スタッド M{d}×10"] if self.lang == "ja" else [f"PEM FH-M{d}-10"])

    def _bom(self, hw, present, values):
        rng = self.rng
        qty_in_bom = self.meta["sources"].get("quantity") == "bom"
        mat_in_bom = self.meta["sources"].get("material") == "bom"
        q = self.truth["quantity"]
        if qty_in_bom:  # counts are totals for the order; hardware per part = total / quantity
            header = ["No", "品番", "品名", "材質", rng.choice([f"数量（{q}台分）", "手配数（合計）"])]
            count = lambda n: n * q
            part_count = fmt_q(q, "ja", rng, bare=True)
            self.meta["bom_mode"] = "total"
        else:
            header = ["No", rng.choice(["品番", "部品番号"]), rng.choice(["品名", "名称"]), "材質", rng.choice(["員数", "数量/台"])]
            count = lambda n: n
            part_count = "1"
            self.meta["bom_mode"] = "per_part"
        mat_cell = self._shown_value("material") if mat_in_bom else ""
        rows = [["1", self.dno or "", self.part_name, mat_cell, part_count]]
        tags = {(1, 3): "val:material" if mat_in_bom else None, (1, 4): "val:quantity" if qty_in_bom else None}
        for i, p in enumerate(hw):
            name = next(t["text"] for t in self.truth["processes"] if t["code"] == p["code"])
            rows.append([str(i + 2), f"{'CLS' if p['code'] == 'PRESS_NUT' else 'FH'}-M{p['d']}", name,
                         rng.choice(["SS（三価）", "SUS", "SWCH"]), str(count(p["n"]))])
        self.bom = (header, rows, {k: v for k, v in tags.items() if v})

    def _shown_value(self, f):
        code, text = self.truth.get(f), self.truth.get(f + "_text")
        for ch in self.meta["revision_changes"][::-1]:
            if ch["field"] == f and self.meta.get("revision_mode", {}).get(f) == "stale":
                return ch["from"]
        return text

    # -------------------------------------------------------------- rush, flags, distractors
    def _rush(self, raster):
        rng = self.rng
        m, d = _date(rng)
        if rng.random() < 0.2:
            self.truth["rush"] = True
            opts = []
            if "notes" in self.caps:
                opts.append(("notes", 4))
            if "title" in self.caps:
                opts.append(("title", 2))
            if "free" in self.caps:
                opts.append(("free", 4))
            if raster:
                opts.append(("stamp", 3))
            where = _pick(rng, opts)
            self.meta["sources"]["rush"] = where
            text = rng.choice(RUSH[self.lang]).format(m=m, d=d)
            if where == "notes":
                self.notes.append((text, None))
            elif where == "free":
                self.free.append((rng.choice(["特急でお願いします", "至急", "急ぎ：{m}/{d} 納品希望"]).format(m=m, d=d), None))
            elif where == "title":
                self.title.append(("due", text if self.lang == "en" else rng.choice(["特急", "至急", f"{m}/{d}（特急）"]), None))
            else:
                self.marks.append({"type": "stamp_rush", "text": "特急" if self.lang == "ja" else "URGENT"})
        elif rng.random() < 0.12:
            text = rng.choice(RUSH_NEG[self.lang])
            self.meta["distractors"].append(text)
            (self.notes if "notes" in self.caps else self.free).append((text, None))
        elif "title" in self.caps and rng.random() < 0.25:
            self.title.append(("due", f"{m}/{d}" if self.lang == "ja" else f"{m:02d}/{d:02d}/2026", None))  # a date alone is not rush

    def _flags(self):
        rng = self.rng
        for cat in ("tolerance", "appearance", "inspection"):
            if rng.random() < 0.15:
                self.truth["flags"].append(cat)
                (self.notes if "notes" in self.caps else self.free).append((rng.choice(FLAGS[cat][self.lang]), None))

    def _distractors(self, present, values):
        rng = self.rng
        if rng.random() < 0.18:
            tmpl = rng.choice(DISTRACT_NOTES[self.lang])
            if "{f}" in tmpl and "quantity" not in present:
                return
            text = tmpl.format(f=rng.choice([300, 500, 1000, 2000]), o=self._drawing_no())
            self.meta["distractors"].append(text)
            (self.notes if "notes" in self.caps else self.free).append((text, None))

    def _standard_notes(self):
        rng = self.rng
        if "notes" in self.caps:
            for s in rng.sample(STANDARD_NOTES[self.lang], rng.randint(1, 3)):
                self.notes.append((s, None))

    # -------------------------------------------------------------- scans: stamps and handwriting
    def _stamps(self):
        rng = self.rng
        self.marks.append({"type": "seal", "text": rng.choice(self.people)})
        if rng.random() < 0.5:
            self.marks.append({"type": "seal", "text": rng.choice(["承認", "検図", rng.choice(self.people)])})
        if rng.random() < 0.5:
            m, d = _date(rng)
            self.marks.append({"type": "date_stamp", "text": "受付", "sub": f"26.{m:02d}.{d:02d}"})
        self.meta["stamps"] = [mk["type"] for mk in self.marks]

    def _handwriting(self, present, loc, values):
        rng = self.rng
        options = []
        changed = {c["field"] for c in self.meta["revision_changes"]}
        on_first_page = ("title", "free") if self.meta["pages"] == 2 else ("title", "notes", "free")
        for f in ("quantity", "finish"):
            if f in present and loc[f] in on_first_page and f not in changed:
                options.append(f)
        if not self.truth["rush"]:
            options.append("rush")
        options.append("process")
        if rng.random() < 0.8:
            what = rng.choices(options, weights=[{"quantity": 3, "finish": 2}.get(o, 1.5) for o in options])[0]
            m, d = _date(rng)
            if what == "quantity":
                q = rng.choice([x for x in QTYS if x != self.truth["quantity"]])
                self.marks.append({"type": "correct", "tag": "val:quantity", "text": str(q)})
                self.truth["quantity"] = q
            elif what == "finish":
                c = rng.choice([k for k in ("ZINC_CLEAR", "ZINC_YELLOW", "POWDER_COAT") if k != self.truth["surface_treatment"]])
                text = rng.choice({"ZINC_CLEAR": ["ユニクロ", "三価クリア"], "ZINC_YELLOW": ["有色クロメート", "三価有色"],
                                   "POWDER_COAT": ["粉体塗装", "粉体 N7"]}[c])
                self.marks.append({"type": "correct", "tag": "val:finish", "text": text})
                self.truth["surface_treatment"], self.truth["surface_treatment_text"] = c, text
            elif what == "rush":
                self.marks.append({"type": "write", "text": rng.choice(HAND_RUSH).format(m=m, d=d), "color": "red"})
                self.truth["rush"] = True
                self.meta["sources"]["rush"] = "handwriting"
            else:
                d_ = rng.choice([3, 4, 5])
                code = f"TAP_M{d_}"
                n = rng.randint(1, 4)
                if any(p["code"] == code for p in self.truth["processes"]):
                    self.marks.append({"type": "write", "text": f"M{d_}タップ {n}ヶ所追加", "color": "blue"})
                else:
                    self.marks.append({"type": "write", "text": rng.choice([f"M{d_}タップ追加 {n}ヶ所", f"+ M{d_}タップ {n}ヶ所"]), "color": "blue"})
                self.truth["processes"].append({"code": code, "count_per_part": n, "text": self.marks[-1]["text"]})
            self.meta["handwriting"].append({"affects": what, "text": self.marks[-1]["text"]})
        if rng.random() < 0.6 or not self.meta["handwriting"]:
            m, d = _date(rng)
            self.marks.append({"type": "write", "text": rng.choice(HAND_NOISE).format(m=m, d=d), "color": "blue"})
            self.meta["handwriting"].append({"affects": None, "text": self.marks[-1]["text"]})

    @staticmethod
    def _merge(procs):
        merged, unreg = {}, []
        for p in procs:
            if p["code"] == UNREG:
                unreg.append(p)
            elif p["code"] in merged:
                merged[p["code"]]["count_per_part"] += p["count_per_part"]
                merged[p["code"]]["text"] += " + " + p["text"]
            else:
                merged[p["code"]] = dict(p)
        return sorted(merged.values(), key=lambda p: p["code"]) + unreg


# ------------------------------------------------------------------ layouts
def _nice_scale(s):
    for v in (5, 2, 1.5, 1, 0.5, 0.4, 0.25, 0.2, 0.1, 0.05):
        if v <= s:
            return v
    return 0.05


def _scale_label(s):
    return f"{s:g}:1" if s >= 1 else f"1:{1 / s:g}"


def draw_views(sh, views, region, third=True, iso=False, font=None):
    """Three orthographic views fitted into region (x, y, w, h). Returns (top view origin, scale, box)."""
    x, y, w, h = region
    side = "right" if third else "left"
    fw, fh = views.size("front", 1)
    tw, th = views.size("top", 1)
    sw, _ = views.size(side, 1)
    iso_w = 0
    if iso:
        iw, ih = views.size("iso", 1)
        iso_w = iw
    gap = 14
    s = _nice_scale(min((w - 2 * gap - 8) / max(fw + sw + iso_w * 0.6, 1), (h - 2 * gap) / max(fh + th, 1)))
    fx, fy = x + 8, (y + gap if third else y + h - gap - fh * s)
    if third:
        ty = fy + fh * s + gap
        views.draw(sh, "front", fx, fy, s)
        views.draw(sh, "top", fx, ty, s)
        views.draw(sh, side, fx + fw * s + gap, fy, s)
    else:
        ty = fy - gap - th * s
        views.draw(sh, "front", fx, fy, s)
        views.draw(sh, "top", fx, ty, s)
        views.draw(sh, side, fx + fw * s + gap, fy, s)
    W, D, H = views.bbox
    fmt = (lambda v: f"{v:.1f}") if sh.font != R.JA else (lambda v: f"{v:g}" if abs(v - round(v)) < 0.05 else f"{v:.1f}")
    lo_y = min(fy, ty)
    sh.hdim(fx, fx + fw * s, lo_y, lo_y - 7, fmt(round(W, 1)), font=font)
    sh.vdim(ty, ty + th * s, fx, fx - 6, fmt(round(D, 1)), font=font)
    if fh * s > 3:
        sh.vdim(fy, fy + fh * s, fx + fw * s + gap + sw * s, fx + fw * s + gap + sw * s + 6, fmt(round(H, 1)), font=font)
    right = fx + (fw + sw) * s + gap + 12
    if iso:
        iw, ih = views.size("iso", 1)
        si = min(s * 0.7, (w - (fw + sw) * s - 3 * gap) / max(iw, 1))
        if si > 0.05:
            views.draw(sh, "iso", fx + (fw + sw) * s + 2 * gap + 8, y + h - gap - ih * si, si, hidden=False)
            right = fx + (fw + sw) * s + 2 * gap + 8 + iw * si + 4
    return (fx, ty), s, right


def callout_column(sh, views, rng, items, top_origin, s, x, y_top, step=6.5, font=None):
    tips = views.edge_points("top", top_origin[0], top_origin[1], s, rng, len(items))
    for k, ((text, tag), tip) in enumerate(zip(items, tips)):
        sh.leader(tip, (x, y_top - k * step), text, size=2.5, font=font, tag=tag)
    return y_top - len(items) * step


def notes_block(sh, plan, x, y_top, width, lang, size=2.7, heading=True):
    font = R.EN if lang == "en" else R.JA
    y = y_top
    if heading:
        sh.text("NOTES:" if lang == "en" else plan.rng.choice(["注記", "注記事項", "注）"]), x, y, 3.2, R.EN_BOLD if lang == "en" else R.JA)
        y -= 5.2
    for k, (text, tag) in enumerate(plan.notes):
        sh.text(f"{k + 1}. {text}", x + 1.5, y, size, font, tag=tag, max_w=width)
        y -= 4.8
    return y


def revision_table(sh, plan, x, y_top, width, lang):
    if not plan.revisions:
        return y_top
    font = R.EN if lang == "en" else R.JA
    header = ["REV", "DESCRIPTION", "DATE", "BY"] if lang == "en" else ["記号", "変更内容", "日付", "担当"]
    rows = list(plan.revisions) if plan.rev_order == "asc" else list(reversed(plan.revisions))
    body = [[sym, desc, date, plan.rng.choice(PEOPLE) if lang == "ja" else plan.rng.choice(["TK", "HS", "MY"])] for sym, desc, date in rows]
    widths = [10, width - 10 - 22 - 12, 22, 12]
    return sh.table(x, y_top, widths, [header] + body, row_h=5.4, size=2.5, font=font)


def title_block(sh, plan, x, y, width, grid=False, bilingual=False, scale="1:1", third=True):
    """Bottom-right title block. Returns its top y."""
    lang = plan.lang
    font = R.EN if lang == "en" else R.JA
    cells = [(k, v, tag) for k, v, tag in plan.title]
    cells += [("scale", scale, None), ("date", plan.date, None)]

    def label(k):
        if bilingual and not plan.unseen:
            return BILINGUAL[k]
        return plan.labels_chosen.setdefault(k, plan.rng.choice(plan.labels[k]))

    if grid:  # 2-column grid, label small top-left of every cell
        ncol, ch = 2, 8.5
        nrow = (len(cells) + 1) // 2
        cw = width / ncol
        for i, (k, v, tag) in enumerate(cells):
            col, row = i % ncol, nrow - 1 - i // ncol
            cx, cy = x + col * cw, y + 8 + row * ch
            sh.rect(cx, cy, cw, ch, 0.2)
            sh.text(label(k), cx + 1, cy + ch - 2.6, 1.9, font)
            sh.text(v, cx + 2, cy + 1.6, 3.0, R.EN_BOLD if k in ("drawing_no", "part_name") and lang == "en" else font, tag=tag, max_w=cw - 3)
        sh.rect(x, y, width, 8, 0.2)
        sh.text(plan.company, x + 2, y + 2.5, 3.0, R.EN_BOLD if lang == "en" else R.JA, max_w=width - 30)
        sh.rect(x, y, width, 8 + nrow * ch, 0.5)
        top = y + 8 + nrow * ch
    else:  # label | value rows, people row at the bottom
        rh, lw = 6.2, 24 if not bilingual else 30
        rows = list(reversed(cells))
        for i, (k, v, tag) in enumerate(rows):
            ry = y + rh * (i + 1)
            sh.line(x, ry, x + width, ry, 0.2)
            sh.text(label(k), x + 1.5, ry + 1.8, 2.6 if not bilingual else 2.2, font, max_w=lw - 2)
            if v:
                sh.text(v, x + lw + 2, ry + 1.8, 3.2, font, tag=tag, max_w=width - lw - 4)
        sh.line(x + lw, y + rh, x + lw, y + rh * (len(rows) + 1), 0.2)
        roles = ["設計", "検図", "承認"] if lang == "ja" else ["DRAWN", "CHECKED", "APPROVED"]
        for k, role in enumerate(roles):
            sh.text(f"{role} {plan.people[k]}", x + 1.5 + k * width / 3, y + 1.8, 2.4, font)
            if k:
                sh.line(x + k * width / 3, y, x + k * width / 3, y + rh, 0.2)
        top = y + rh * (len(rows) + 1)
        sh.rect(x, y, width, top - y, 0.5)
        sh.text(plan.company, x, top + 2, 3.0, font)
    plan.regions["seal"] = (x + width * 0.45, y + 2.5, x + width - 6, y + 3.5)
    return top


def layout_sheet(sh, plan, views, page_size, style):
    """Title-block drawings (jis_cad, bilingual, iso_en, jis_bom) on an A4-landscape layout (297 x 210)."""
    rng, lang = plan.rng, plan.lang
    font = R.EN if lang == "en" else R.JA
    third = style != "iso_en"
    sh.rect(10, 10, 277, 190, 0.5)
    two_pages = plan.meta["pages"] == 2
    # left: notes on top, views below
    notes_bottom = 198 if two_pages else notes_block(sh, plan, 14, 196, 158, lang)
    top_origin, s, views_right = draw_views(sh, views, (14, 24, 160, notes_bottom - 26), third=third,
                               iso=(style == "iso_en" and len(plan.callouts) <= 2), font=font)
    plan.scale_label = _scale_label(s)
    # right column: revision table (top), callouts, title block (+ parts list) at the bottom
    y_after_rev = revision_table(sh, plan, 177, 198, 108, lang) if not two_pages else 198
    tb_top = title_block(sh, plan, 177, 12, 108, grid=style == "iso_en", bilingual=style == "bilingual",
                         scale=plan.scale_label, third=third)
    if plan.bom:
        header, rows, tags = plan.bom
        table_top = tb_top + 6 + 6 * (len(rows) + 1)
        sh.table(177, table_top, [8, 24, 38, 18, 20], [header] + rows, row_h=6, size=2.5, tags=tags, font=font)
        sh.text("部品表" if lang == "ja" else "PARTS LIST", 177, table_top + 1.5, 2.8, font)
        tb_top = table_top + 6
    cx = min(181, views_right + 4)
    cy = y_after_rev - 8 if cx >= 175 else min(y_after_rev - 8, notes_bottom - 6)
    if cx < 175 and cx + 50 > 175 and cy - 6.5 * len(plan.callouts) < tb_top + 4:
        cx, cy = 181, y_after_rev - 8  # labels would run into the title block: use the right column
    col_bottom = callout_column(sh, views, rng, plan.callouts, top_origin, s, cx, cy, font=font)
    labels_right = cx + max([sh.width(t, 2.5, font) for t, _ in plan.callouts] or [0]) + 2
    if cx < 175 and labels_right < 180:
        col_bottom = y_after_rev - 8  # the labels stay left of the right column: it is free for the flat pattern
    # flat pattern in the remaining space on the right
    free_h = col_bottom - tb_top - 12
    fw, fh = views.flat_size(1)
    if free_h > 28 and not two_pages:
        fs = _nice_scale(min(100 / max(fw, 1), (free_h - 8) / max(fh, 1)))
        if fs >= 0.1:
            sh.text("展開図（参考）" if lang == "ja" else "FLAT PATTERN (REF.)", 182, tb_top + 6 + fh * fs + 3, 2.6, font)
            views.draw_flat(sh, 182, tb_top + 6, fs)
    sh.text(plan.general_tol, 14, 14, 2.7, font)
    if third:
        sh.text("第三角法" if lang == "ja" else "THIRD ANGLE", 120, 14, 2.6, font)
        sh.projection_symbol(145, 12.5, True)
    else:
        sh.text("FIRST ANGLE PROJECTION", 110, 14, 2.6, font)
        sh.projection_symbol(160, 12.5, False)
    if two_pages:
        sh.text("1/2", 280, 203, 2.6, font, anchor="r")
        sh.new_page()
        sh.rect(10, 10, 277, 190, 0.5)
        sh.text(f"{plan.dno or ''}  2/2", 280, 203, 2.6, font, anchor="r")
        y = notes_block(sh, plan, 16, 190, 160, lang, size=3.0)
        revision_table(sh, plan, 177, 198, 108, lang)
        fs = _nice_scale(min(150 / max(fw, 1), (y - 30) / max(fh, 1)))
        views.draw_flat(sh, 20, 20, fs)


def layout_memo(sh, plan, views, page_size):
    """Simple sketch without a title block: conditions as free text and a parts list (A4 portrait, 210 x 297)."""
    rng = plan.rng
    y = 282
    for text, tag in plan.free:
        sh.text(text, 16, y, 4.0, R.JA, tag=tag, max_w=178)
        y -= 7.5
    bom_top = 72
    if plan.bom:
        header, rows, tags = plan.bom
        sh.text("部品表", 16, bom_top + 2, 3.2)
        sh.table(16, bom_top, [10, 34, 60, 30, 26], [header] + rows, row_h=7, size=2.9, tags=tags)
    region = (14, bom_top + 8, 130, y - bom_top - 14)
    top_origin, s, views_right = draw_views(sh, views, region, third=True, font=R.JA)
    callout_column(sh, views, rng, plan.callouts, top_origin, s, min(150, views_right + 4), y - 10)
    sh.text(f"{plan.company}　{plan.date}", 16, 14, 3.0)
    plan.regions["seal"] = (150, 10, 200, 20)


# ------------------------------------------------------------------ building one drawing
PAGE = {"A4L": (landscape(A4), (297, 210)), "A3L": (landscape(A3), (297, 210)), "A4P": (portrait(A4), (210, 297))}


def choose_style(rng, unseen):
    if unseen:
        from golden import pdf_unseen
        return pdf_unseen.choose_style(rng)
    return _pick(rng, list(STYLES.items()))


def build_pdf(level: str, index: int, seed: int, out_dir: Path, unseen_ratio: float = 0.0, with_step: bool = True) -> dict:
    """Write <out_dir>/pdf/<name>.pdf (and step/<name>.step) and return the truth record."""
    part, built = generate(level, index, seed)
    part.level = level
    apply_v2(built)
    folded, flat, base_truth = built
    from golden.dxf_golden import bend_lines, flat_geometry
    outer, inners = flat_geometry(flat)
    name = f"{level}_{index:04d}"
    rng = random.Random(zlib.crc32(f"pdf:{seed}:{level}:{index}".encode()))
    kind = kind_for(seed, level, index)
    unseen = rng.random() < unseen_ratio
    style = choose_style(rng, unseen)
    lang = "en" if style == "iso_en" else "ja"
    labels = None
    if unseen:
        from golden import pdf_unseen
        labels = pdf_unseen.labels(rng, lang)
    plan = Plan(rng, style, lang, kind, level, base_truth["thickness_mm"], unseen, labels)
    plan.labels_chosen, plan.regions = {}, {}
    plan.meta["pages"] = 2 if (style not in ("memo", "spec_sheet") and rng.random() < 0.1) else 1
    plan.build()

    page_key = "A4P" if style in ("memo", "spec_sheet") else ("A3L" if rng.random() < 0.3 else "A4L")
    page_pt, layout_size = PAGE[page_key]
    names = ("front", "top", "right" if style != "iso_en" else "left", "iso")
    views = R.Views(folded, outer, inners, bend_lines(part), names=names)
    plan.scale_label = "1:1"

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=page_pt, invariant=1)
    c.setTitle(plan.dno or name)
    c.scale(page_pt[0] / (layout_size[0] * R.mm), page_pt[1] / (layout_size[1] * R.mm))
    sh = R.Sheet(c, layout_size, font=R.EN if lang == "en" else R.JA)
    if style == "memo":
        layout_memo(sh, plan, views, layout_size)
    elif style == "spec_sheet":
        from golden import pdf_unseen
        pdf_unseen.layout_spec_sheet(sh, plan, views, layout_size)
    else:
        layout_sheet(sh, plan, views, layout_size, style)
    c.showPage()
    c.save()
    pdf = buf.getvalue()
    if kind != "vector":
        pdf = rasterize(pdf, plan, sh, page_pt, layout_size, kind, rng)

    out_dir = Path(out_dir)
    (out_dir / "pdf").mkdir(parents=True, exist_ok=True)
    (out_dir / "pdf" / f"{name}.pdf").write_bytes(pdf)
    if with_step:
        import cadquery as cq
        (out_dir / "step").mkdir(parents=True, exist_ok=True)
        step = out_dir / "step" / f"{name}.step"
        cq.exporters.export(folded, str(step))
        _normalize_step_header(step, name)

    meta = dict(plan.meta)
    meta.update(unseen=unseen, page_size=page_key, lang=lang)
    return {"name": name, "file": f"pdf/{name}.pdf", "step_file": f"step/{name}.step", "level": level, "seed": [seed, index],
            "kind": kind, "style": style, "unseen": unseen,
            "part_truth": {k: base_truth[k] for k in ("thickness_mm", "blank_area_mm2", "cut_length_mm", "hole_count", "bend_count")},
            "truth": plan.truth, "meta": meta}


def _normalize_step_header(path: Path, name: str) -> None:
    """OpenCascade writes the export time and a per-process counter into the header; fix both so that
    regenerating the data gives byte-identical STEP files (the geometry is already deterministic)."""
    import re
    text = path.read_text(encoding="utf-8", errors="replace")
    text = re.sub(r"(FILE_NAME\('[^']*',)'[^']*'", r"\1'2026-01-01T00:00:00'", text, count=1)
    text = re.sub(r"Open CASCADE STEP translator ([\d.]+) \d+", rf"{name}", text)
    path.write_text(text, encoding="utf-8")


def rasterize(pdf: bytes, plan: Plan, sh: R.Sheet, page_pt, layout_size, kind: str, rng: random.Random) -> bytes:
    dpi = {"scan": rng.choice([150, 200]), "fax": 200, "handwritten": rng.choice([150, 200])}[kind]
    pages = R.render_pages(pdf, dpi)
    img = pages[0]
    W, H = img.size
    ppm = W / layout_size[0]  # pixels per layout mm
    ink = R.InkMap(img)
    seal = plan.regions.get("seal")
    for mk in plan.marks:
        if mk["type"] == "seal" and seal:  # over the people row of the title block, next to the values
            x0, y0, x1, y1 = R.to_px(seal, layout_size, (W, H))
            R.stamp(img, (rng.uniform(x0, x1), rng.uniform(y0, y1)), mk["text"], 3.2 * ppm, rng)
        elif mk["type"] in ("date_stamp", "stamp_rush"):
            px = (3.0 if mk["type"] == "date_stamp" else 5.0) * ppm
            w, h = px * (len(mk["text"]) * 1.25 + 2.5), px * 2.8
            l, t = ink.blank(w, h, rng)
            R.stamp(img, (l + w / 2, t + h / 2), mk["text"], px, rng, shape="rect", sub=mk.get("sub"))
            ink.occupy(l - px, t - px, w + 2 * px, h + 2 * px)
        elif mk["type"] == "correct":
            page, *box = sh.boxes[mk["tag"]]
            l, t, r, b = R.to_px(box, layout_size, (W, H))
            color = (35, 55, 170)
            R.hand_strike(img, (l, t, r, b), color, rng)
            px = 4.2 * ppm
            nl, nt = r + px * 0.5, t - px * 0.3  # right next to the struck value, on the same line
            R.hand_text(img, (nl, nt + px * 0.3), mk["text"], px, color, rng)
            ink.occupy(nl, nt, px * (len(mk["text"]) + 1.5), px * 1.6)
        elif mk["type"] == "write":
            px = rng.uniform(4.2, 5.2) * ppm
            w, h = px * (len(mk["text"]) * 1.05 + 1), px * 1.8
            l, t = ink.blank(w, h, rng)
            color = (200, 30, 30) if mk.get("color") == "red" else (35, 55, 170)
            R.hand_text(img, (l + px * 0.3, t + px * 0.3), mk["text"], px, color, rng, angle=rng.uniform(-3, 3))
            ink.occupy(l, t, w, h)
    out = []
    for i, page in enumerate(pages):
        if kind == "fax":
            header = f"2026/{rng.randint(1, 12):02d}/{rng.randint(1, 28):02d} {rng.randint(8, 18):02d}:{rng.randint(0, 59):02d}  FROM: " \
                     f"{plan.company}  FAX 03-5555-{rng.randint(1000, 9999)}  P.{i + 1:02d}/{len(pages):02d}"
            out.append(R.degrade_fax(page, rng, header))
        else:
            out.append(R.degrade_scan(page, rng, color=(kind == "handwritten")))
    return R.images_to_pdf(out, page_pt, fmt="PNG" if kind == "fax" else "JPEG", quality=rng.choice([65, 75, 85]))
