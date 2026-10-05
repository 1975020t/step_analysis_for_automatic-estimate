"""Similar past quotes (類似実績) and similar drawings: one rule for every screen (rules only, no LLM).

Similar means all three:
  * the same material (master code; an unknown or unregistered material is never similar to anything)
  * the number of bends differs by at most 1 (MAX_BEND_DIFF)
  * the number of holes differs by at most 2 (MAX_HOLE_DIFF)
A part whose bends or holes are unknown (no shape file / no CAD values) is left out. Results are ordered by the
difference, smallest first: bends + holes, then bends, then holes, then the newest. `is_similar` / `difference`
are the rule itself; the drawing list and the estimate screen both use them (src/services/platform/similar.py).

Each past quote found comes with why it is similar, what differs and how it was priced: the past unit price
against today's; when the past quote has CAD values and conditions the current master can price, its standard
unit price is recomputed with today's master, and the ratio actual / standard is applied to today's standard
("the past pricing level applied to this quote"). Nothing here changes the quote: it is shown for the estimator.
"""
from __future__ import annotations

import math
import statistics
import unicodedata
from dataclasses import dataclass, field
from datetime import date

from src.master_loader import UNREGISTERED, MasterLoader
from src.models import AdditionalProcess, QuoteCondition, SheetMetalAnalysis
from src.past_quotes import FAMILY_LABEL, PastQuote
from src.quote_engine import QuoteEngine

MAX_RESULTS = 10
MAX_BEND_DIFF = 1
MAX_HOLE_DIFF = 2
REPEAT, SAME_CUSTOMER, OTHER_CUSTOMER = "リピート", "同じ顧客", "他の顧客"
CATEGORY_ORDER = {REPEAT: 0, SAME_CUSTOMER: 1, OTHER_CUSTOMER: 2}
# sheet thickness series used for "adjacent" (a value between two steps is adjacent to both)
THICKNESS_SERIES = (0.3, 0.4, 0.5, 0.6, 0.8, 1.0, 1.2, 1.6, 2.0, 2.3, 3.2, 4.5, 6.0, 9.0, 12.0, 16.0)
QUANTITY_BANDS = ((1, 9), (10, 99), (100, 999), (1000, None))
AREA_TOLERANCE = 0.30
REPEAT_WARNING = 0.20
OLD_YEARS = 2


# ---------------------------------------------------------------- normalisation and rules
def norm_customer(name: str) -> str:
    t = unicodedata.normalize("NFKC", name or "")
    for word in ("株式会社", "有限会社", "合同会社", "(株)", "(有)", "㈱", "㈲"):
        t = t.replace(unicodedata.normalize("NFKC", word), "")
    return "".join(t.split()).upper()


def norm_drawing(drawing_no: str) -> str:
    return "".join(unicodedata.normalize("NFKC", drawing_no or "").split()).upper()


def thickness_position(value: float) -> float:
    s = THICKNESS_SERIES
    if value <= s[0]:
        return 0.0
    for i in range(1, len(s)):
        if value <= s[i] + 1e-9:
            return i - 1 + (value - s[i - 1]) / (s[i] - s[i - 1])
    return float(len(s) - 1)


def thickness_relation(a: float | None, b: float | None) -> str | None:
    """"same", "adjacent" or None (too far / unknown)."""
    if a is None or b is None:
        return None
    if abs(a - b) < 1e-6:
        return "same"
    return "adjacent" if abs(thickness_position(a) - thickness_position(b)) <= 1.0 + 1e-9 else None


def quantity_band(quantity: int | None) -> int | None:
    if not quantity:
        return None
    for i, (low, high) in enumerate(QUANTITY_BANDS):
        if quantity >= low and (high is None or quantity <= high):
            return i
    return None


def band_label(band: int) -> str:
    low, high = QUANTITY_BANDS[band]
    return f"{low}〜{high}" if high else f"{low}〜"


def same_material(a: str | None, b: str | None) -> bool:
    a, b = (a or "").strip().upper(), (b or "").strip().upper()
    return bool(a) and a == b and a != UNREGISTERED


def is_similar(material: str | None, bends: int | None, holes: int | None,
               other_material: str | None, other_bends: int | None, other_holes: int | None) -> bool:
    """The similarity rule: same material, bends within 1, holes within 2 (unknown counts are never similar)."""
    if None in (bends, holes, other_bends, other_holes) or not same_material(material, other_material):
        return False
    return abs(bends - other_bends) <= MAX_BEND_DIFF and abs(holes - other_holes) <= MAX_HOLE_DIFF


def difference(bends: int, holes: int, other_bends: int, other_holes: int) -> tuple[int, int, int]:
    """Sort key of a similar part, smallest first: (bends + holes, bends, holes) differences."""
    db, dh = abs(bends - other_bends), abs(holes - other_holes)
    return db + dh, db, dh


# ---------------------------------------------------------------- query and result
@dataclass
class Query:
    customer: str = ""
    drawing_no: str = ""
    revision: str = ""
    material_code: str = ""
    material_family: str = ""
    thickness: float | None = None
    quantity: int | None = None
    finish_code: str = ""                 # NONE = no finish
    processes: dict[str, float] = field(default_factory=dict)  # code -> count per part
    rush: bool = False
    area: float | None = None
    holes: int | None = None
    bends: int | None = None
    unit_price: int | None = None         # today's unit price (as shown and printed)
    standard_unit: float | None = None    # today's standard: QuoteEngine final price / quantity
    date: date | None = None              # only quotes before this date are searched (None: all)
    today: date | None = None             # for the age of a past quote (default: date)
    exclude: str = ""                     # quote number to leave out (the quote itself)


@dataclass
class Match:
    quote: PastQuote
    category: str
    score: float
    reasons: list[str]
    differences: list[str]
    price_diff: float | None = None       # (past unit - today's unit) / today's unit
    past_standard: float | None = None    # past conditions priced with today's master (per part)
    ratio: float | None = None            # past unit / past_standard
    leveled_unit: float | None = None     # today's standard x ratio
    warnings: list[str] = field(default_factory=list)
    standard_note: str = ""               # why no standard could be computed


@dataclass
class Reference:
    unit: float
    basis: str


# ---------------------------------------------------------------- search
class SimilarQuoteSearch:
    def __init__(self, quotes: list[PastQuote], masters: MasterLoader) -> None:
        self.quotes = quotes
        self.masters = masters
        self.engine = QuoteEngine(masters)
        self._by_material: dict[str, list[int]] = {}
        self._customer = [norm_customer(q.customer) for q in quotes]
        for i, q in enumerate(quotes):
            if same_material(q.material_code, q.material_code):
                self._by_material.setdefault(q.material_code.strip().upper(), []).append(i)
        self._standard: dict[int, tuple[float | None, str]] = {}

    # ------------------------------------------------------------ public
    def search(self, query: Query, limit: int = MAX_RESULTS) -> list[Match]:
        """Past quotes similar to the query (see the module rule), the smallest difference first."""
        customer = norm_customer(query.customer)
        if query.bends is None or query.holes is None or not same_material(query.material_code, query.material_code):
            return []
        found = []
        for i in self._by_material.get(query.material_code.strip().upper(), []):
            q = self.quotes[i]
            if (query.date is not None and q.date >= query.date) or (query.exclude and q.quote_no == query.exclude):
                continue
            if not is_similar(query.material_code, query.bends, query.holes, q.material_code, q.bends, q.holes):
                continue
            found.append((difference(query.bends, query.holes, q.bends, q.holes), -q.date.toordinal(), i))
        found.sort()
        matches = []
        for _, _, i in found[:limit]:
            q = self.quotes[i]
            if customer and self._customer[i] == customer and query.drawing_no and \
                    norm_drawing(q.drawing_no) == norm_drawing(query.drawing_no):
                category = REPEAT
            elif customer and self._customer[i] == customer:
                category = SAME_CUSTOMER
            else:
                category = OTHER_CUSTOMER
            matches.append(self._match(query, category, i, self._score(query, q)))
        return matches

    def reference(self, matches: list[Match]) -> Reference | None:
        """A reference unit price from the past pricing level: the latest repeat, else the median of the same
        customer's similar parts, else of the other customers'. Shown only; never applied to the quote."""
        for category in (REPEAT, SAME_CUSTOMER, OTHER_CUSTOMER):
            levels = [m for m in matches if m.category == category and m.leveled_unit]
            if not levels:
                continue
            if category == REPEAT:
                m = levels[0]
                return Reference(m.leveled_unit, f"リピート {m.quote.date:%Y/%m/%d} の出し値の水準")
            value = statistics.median(m.leveled_unit for m in levels[:3])
            return Reference(value, f"{category}の近い部品 {min(len(levels), 3)}件の出し値の水準（中央値）")
        return None

    def standard_unit(self, index: int) -> tuple[float | None, str]:
        """Today's standard unit price of a past quote (its conditions and CAD values, today's master)."""
        if index not in self._standard:
            self._standard[index] = self._compute_standard(self.quotes[index])
        return self._standard[index]

    # ------------------------------------------------------------ scoring
    def _score(self, query: Query, q: PastQuote) -> float:
        score = 0.0
        if query.material_code and q.material_code == query.material_code and q.material_code != UNREGISTERED:
            score += 3.0
        elif q.material_family and q.material_family == query.material_family:
            score += 1.0
        relation = thickness_relation(query.thickness, q.thickness)
        score += {"same": 2.0, "adjacent": 0.5}.get(relation, 0.0)
        a, b = quantity_band(query.quantity), quantity_band(q.quantity)
        if a is not None and b is not None:
            score += 2.0 if a == b else (0.5 if abs(a - b) == 1 else 0.0)
        if query.area and q.area:
            gap = abs(math.log(q.area / query.area))
            score += 2.0 * max(0.0, 1 - gap / math.log(1 + AREA_TOLERANCE)) if gap <= math.log(1 + AREA_TOLERANCE) else -0.5
        if query.bends is not None and q.bends is not None:
            score += 1.0 if q.bends == query.bends else (0.4 if abs(q.bends - query.bends) <= 2 else 0.0)
        if query.holes is not None and q.holes is not None:
            score += 0.5 if abs(q.holes - query.holes) <= max(2, 0.3 * query.holes) else 0.0
        if query.finish_code and q.finish_code == query.finish_code:
            score += 1.0
        mine = set(query.processes)
        theirs = {p["code"] for p in q.processes if p["code"] != UNREGISTERED}
        if mine or theirs:
            score += 1.0 * len(mine & theirs) / len(mine | theirs)
        else:
            score += 0.5
        today = query.today or query.date
        if today:
            score += 0.5 * max(0.0, 1 - (today - q.date).days / 365 / 3)
        return round(score, 4)

    # ------------------------------------------------------------ explanation
    def _match(self, query: Query, category: str, index: int, score: float) -> Match:
        q = self.quotes[index]
        match = Match(q, category, score, self._reasons(query, q, category), self._differences(query, q))
        if q.unit_price and query.unit_price:
            match.price_diff = (q.unit_price - query.unit_price) / query.unit_price
        standard, note = self.standard_unit(index)
        match.standard_note = note
        if standard and q.unit_price:
            match.past_standard = standard
            match.ratio = q.unit_price / standard
            if query.standard_unit:
                match.leveled_unit = query.standard_unit * match.ratio
        today = query.today or query.date
        if today and (today - q.date).days > 365 * OLD_YEARS:
            match.warnings.append(f"{OLD_YEARS}年以上前の見積です（価格水準が今と違う可能性があります）")
        if category == REPEAT:
            gap, basis = None, ""
            if match.leveled_unit and query.unit_price:
                gap, basis = (query.unit_price - match.leveled_unit) / match.leveled_unit, "前回の出し値の水準"
            elif match.price_diff is not None:
                gap, basis = -match.price_diff / (1 + match.price_diff), "前回の単価"
            if gap is not None and abs(gap) > REPEAT_WARNING:
                match.warnings.append(f"リピートで今回の単価が{basis}より {gap:+.0%}（±{REPEAT_WARNING:.0%}超）")
        if q.rush:
            match.warnings.append("過去の見積は特急（割増を含む）")
        return match

    def _reasons(self, query: Query, q: PastQuote, category: str) -> list[str]:
        reasons = []
        if category == REPEAT:
            rev = f"（改訂 {q.revision}）" if q.revision else ""
            reasons.append(f"同じ顧客・同じ図番{rev}")
        elif category == SAME_CUSTOMER:
            reasons.append("同じ顧客")
        if query.material_code and q.material_code == query.material_code and q.material_code != UNREGISTERED:
            reasons.append(f"材質同じ（{q.material_code}）")
        elif q.material_family and q.material_family == query.material_family:
            reasons.append(f"材質の系統同じ（{FAMILY_LABEL.get(q.material_family, q.material_family)}）")
        relation = thickness_relation(query.thickness, q.thickness)
        if relation == "same":
            reasons.append(f"板厚同じ（t{q.thickness:g}）")
        elif relation == "adjacent":
            reasons.append(f"板厚が隣（t{q.thickness:g}）")
        a, b = quantity_band(query.quantity), quantity_band(q.quantity)
        if a is not None and a == b:
            reasons.append(f"数量帯同じ（{band_label(a)}）")
        if query.area and q.area and abs(math.log(q.area / query.area)) <= math.log(1 + AREA_TOLERANCE):
            reasons.append(f"大きさが近い（展開面積 {query.area / q.area - 1:+.0%}）")  # this quote vs the past one, as in the differences
        if query.bends is not None and q.bends is not None and q.bends == query.bends:
            reasons.append(f"曲げ数同じ（{q.bends}）")
        if query.finish_code and q.finish_code == query.finish_code:
            reasons.append("表面処理同じ")
        common = sorted(set(query.processes) & {p["code"] for p in q.processes})
        if common:
            reasons.append("追加加工が共通（" + "・".join(self._process_name(c) for c in common) + "）")
        return reasons

    def _differences(self, query: Query, q: PastQuote) -> list[str]:
        diffs = []
        if query.revision != q.revision and (query.revision or q.revision) and \
                norm_drawing(query.drawing_no) == norm_drawing(q.drawing_no):
            diffs.append(f"改訂 {q.revision or '-'}→{query.revision or '-'}")
        if not (query.material_code and q.material_code == query.material_code):
            past = q.material_text or "-"
            if not q.material_code:
                past += "（系統のみ）" if q.material_family else "（不明）"
            elif q.material_code == UNREGISTERED:
                past += "（マスター未登録）"
            diffs.append(f"材質 {past}→{query.material_code or '-'}")
        if q.thickness is not None and query.thickness is not None and abs(q.thickness - query.thickness) > 1e-6:
            diffs.append(f"板厚 {q.thickness:g}→{query.thickness:g}")
        if q.quantity != query.quantity:
            diffs.append(f"数量 {q.quantity if q.quantity is not None else '-'}→{query.quantity if query.quantity is not None else '-'}")
        past_finish = q.finish_code or ""
        if past_finish != (query.finish_code or "NONE"):
            if not past_finish:
                diffs.append(f"表面処理 記録なし→{self._finish_name(query.finish_code)}")
            elif past_finish == UNREGISTERED:
                diffs.append(f"表面処理 {q.finish_text}（マスター未登録）→{self._finish_name(query.finish_code)}")
            else:
                diffs.append(f"表面処理 {self._finish_name(past_finish)}→{self._finish_name(query.finish_code)}")
        past_procs: dict[str, float] = {}
        for p in q.processes:
            if p["code"] != UNREGISTERED:
                past_procs[p["code"]] = past_procs.get(p["code"], 0) + (p["count"] or 0)
        for code in sorted(set(past_procs) | set(query.processes)):
            before, now = past_procs.get(code, 0), query.processes.get(code, 0)
            if before != now:
                diffs.append(f"{self._process_name(code)} {now - before:+g}")
        for p in q.processes:
            if p["code"] == UNREGISTERED:
                diffs.append(f"過去のみ：{p['text']}（マスター未登録）")
        if q.rush != query.rush:
            diffs.append(f"特急 {'あり' if q.rush else 'なし'}→{'あり' if query.rush else 'なし'}")
        if query.area and q.area and abs(q.area / query.area - 1) > 0.05:
            diffs.append(f"展開面積 {query.area / q.area - 1:+.0%}")
        if query.bends is not None and q.bends is not None and q.bends != query.bends:
            diffs.append(f"曲げ {q.bends}→{query.bends}")
        if query.holes is not None and q.holes is not None and q.holes != query.holes:
            diffs.append(f"穴 {q.holes}→{query.holes}")
        if not q.has_shape:
            diffs.append("過去の見積に形状の数値なし")
        return diffs or ["条件の違いなし"]

    def _process_name(self, code: str) -> str:
        row = self.masters.process_rates.get(code)
        return row["display_name"] if row else code

    def _finish_name(self, code: str) -> str:
        if not code or code == "NONE":
            return "なし"
        row = self.masters.surface_treatments.get(code)
        return row["display_name"] if row else code

    # ------------------------------------------------------------ standard price of a past quote
    def _compute_standard(self, q: PastQuote) -> tuple[float | None, str]:
        if not q.has_shape or q.thickness is None:
            return None, "形状の数値がないため標準単価を計算できません"
        if not q.quantity:
            return None, "数量がありません"
        if q.material_code not in self.masters.materials:
            return None, "材質がマスターのコードに対応しません"
        if q.finish_code == UNREGISTERED or (q.finish_code and q.finish_code not in self.masters.surface_treatments):
            return None, "表面処理がマスターにありません"
        processes = []
        for p in q.processes:
            if p["code"] not in self.masters.process_rates or not p["count"]:
                return None, "追加加工にマスターにないもの（または個数不明）があります"
            processes.append(AdditionalProcess(process_code=p["code"], quantity=p["count"],
                                               unit=self.masters.process_rates[p["code"]]["unit"], source="user"))
        analysis = SheetMetalAnalysis(status="success", file_name=q.quote_no, thickness_mm=q.thickness,
                                      blank_area_mm2=q.area, cut_length_mm=q.cut, hole_count=q.holes, bend_count=q.bends)
        finish = q.finish_code if q.finish_code not in ("", "NONE") else None
        condition = QuoteCondition(material=q.material_code, quantity=q.quantity, surface_treatment=finish,
                                   rush=q.rush, additional_processes=processes)
        result = self.engine.calculate(analysis, condition)
        return result.final_price / q.quantity, ("表面処理の記録なし（なしとして計算）" if not q.finish_code else "")


def query_for(analysis: SheetMetalAnalysis, condition: QuoteCondition, unit_price: int | None, standard_unit: float | None,
              customer: str, drawing_no: str = "", revision: str = "", today: date | None = None) -> Query:
    """The quote being made on screen as a search query (the whole history is searched)."""
    from src.past_quotes import material_family

    processes: dict[str, float] = {}
    for p in condition.additional_processes:
        if p.confirmed:
            processes[p.process_code] = processes.get(p.process_code, 0) + p.quantity
    return Query(customer=customer, drawing_no=drawing_no, revision=revision, material_code=condition.material,
                 material_family=material_family(condition.material), thickness=analysis.thickness_mm,
                 quantity=condition.quantity, finish_code=condition.surface_treatment or "NONE", processes=processes,
                 rush=condition.rush, area=analysis.blank_area_mm2, holes=analysis.hole_count, bends=analysis.bend_count,
                 unit_price=unit_price, standard_unit=standard_unit, today=today)
