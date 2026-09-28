"""Drawing-PDF condition reader: rules, review policy, quote integration. No API calls (fake client)."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from src.claude_api import ClaudeClient
from src.master_loader import UNREGISTERED, MasterLoader
from src.models import QuoteCondition, SheetMetalAnalysis
from src.pdf_quote import CONFIRMED, MISSING, REVIEW, UNREG, condition_items, quote_condition
from src.pdf_terms import AMBIGUOUS, Terms
from src.quote_engine import QuoteEngine

ROOT = Path(__file__).resolve().parents[1]
MASTERS = MasterLoader(ROOT / "data")
T = Terms(MASTERS)


# ------------------------------------------------------------------ terms
@pytest.mark.parametrize("text,code", [
    ("SPCC-SD", "SPCC"), ("ＳＰＣＣ", "SPCC"), ("冷延鋼板（SPCC）", "SPCC"), ("STEEL, COLD ROLLED, SPCC", "SPCC"),
    ("AISI 304 / SUS304", "SUS304"), ("ALUMINUM 5052-H32", "AL5052"), ("SECC（ボンデ鋼板）", "SECC"),
    ("SUS430-2B", UNREGISTERED), ("SGCC (HOT-DIP GALVANIZED)", UNREGISTERED), ("C1100P COPPER", UNREGISTERED),
    ("SUS304 / SPCC", AMBIGUOUS), ("材質：SS400", "SS400"), (None, None),
])
def test_material(text, code):
    assert T.material(text) == code


@pytest.mark.parametrize("text,code", [
    ("三価クロメート（光沢）", "ZINC_CLEAR"), ("ZN PLATE, CLEAR CHROMATE (CR3+)", "ZINC_CLEAR"),
    ("亜鉛めっき 三価黒", "ZINC_BLACK"), ("YELLOW ZINC (TRIVALENT)", "ZINC_YELLOW"), ("紛体塗装 N7", "POWDER_COAT"),
    ("焼付け塗装 黒", "BAKING_PAINT"), ("カニゼンめっき", "ELECTROLESS_NI"), ("アルマイト（黒）", "ANODIZE_BLACK"),
    ("陽極酸化処理（白）", "ANODIZE_CLEAR"), ("表面処理：無処理", "NONE"), ("UNFINISHED", "NONE"),
    ("クロムめっき", UNREGISTERED), ("ZINC PHOSPHATE", UNREGISTERED), ("カチオン電着塗装", UNREGISTERED),
    ("化成処理（アロジン）", UNREGISTERED), ("電気亜鉛めっき鋼板 SECC", None), ("SEE NOTES", None),
])
def test_finish(text, code):
    assert T.finish(text) == code


@pytest.mark.parametrize("text,expected", [
    ("4X M4x0.7 TAP THRU", [("TAP_M4", 4)]), ("2×M3 タップ", [("TAP_M3", 2)]), ("図示位置 6-M4×0.7", [("TAP_M4", 6)]),
    ("M5タップ（2ヶ所）", [("TAP_M5", 2)]), ("M4タップ 2ヶ所追加", [("TAP_M4", 2)]), ("4X M10 TAP", [(UNREGISTERED, 4)]),
    ("バーリングタップ M4 3ヶ所", [(UNREGISTERED, 3)]), ("6X PEM SO-M3-10 STANDOFF", [(UNREGISTERED, 6)]),
    ("圧入ナット M3 ×6（図示）", [("PRESS_NUT", 6)]), ("3X PEM FH-M4-10 STUD", [("PRESS_STUD", 3)]),
    ("CSK FOR M3 FHS, 4 PLCS", [("COUNTERSINK", 4)]), ("スポット溶接 8点", [("SPOT_WELD", 8)]),
    ("コーナー2ヶ所 TIG溶接", [("TIG_WELD", 2)]), ("4-φ4.5 キリ（M4用）", []), ("Ø4.5 THRU (FOR M4)", []), ("2-φ10", []),
])
def test_process(text, expected):
    assert T.process(text) == expected


def test_numbers_rush_and_flags():
    assert T.thickness("t1.6") == 1.6 and T.thickness("THK 2.0") == 2.0 and T.thickness("1.6 / 2.0") is None
    assert T.quantity("1,000 pcs") == 1000 and T.quantity("N=50") == 50
    assert T.rush("特急") is True and T.rush("急ぎません（通常納期で可）") is False and T.rush("12/5") is None
    assert not T.flag_ok("tolerance", "普通公差 JIS B 0405-m")
    assert not T.flag_ok("appearance", "粉体塗装 マンセルN7 半艶")
    assert T.flag_ok("appearance", "外観面（A面）キズ・打痕不可")


# ------------------------------------------------------------------ interpretation rules
def evidence(**over):
    raw = {"drawing_no": "AB-21-0001", "revision": None, "revision_changes": [],
           "material": {"text": "SPCC", "where": "title_block", "code": "SPCC"},
           "thickness": {"text": "t1.6", "where": "title_block", "value": 1.6},
           "quantity": {"text": "100", "where": "title_block", "value": 100},
           "surface_treatment": {"text": "三価クロメート（光沢）", "where": "title_block", "code": "ZINC_CLEAR"},
           "processes": [{"text": "4-M4", "where": "callout", "bom_count": None, "handwritten_addition": False,
                          "code": "TAP_M4", "count": 4}],
           "parts_list_quantity_header": None, "rush": {"value": False, "text": None}, "flags": [], "annotations": [],
           "uncertain": []}
    raw.update(over)
    return raw


class FakeClient(ClaudeClient):
    def __init__(self, raws):
        super().__init__(model="test-model", mode="live", api_key="dummy")
        self.raws = list(raws)
        self.requests = []

    def complete_json(self, system, user, schema, tool_name="report", max_tokens=2048):
        self.requests.append(user)
        self.usage.calls += 1
        self.usage.input_tokens += 1000
        return copy.deepcopy(self.raws.pop(0))


def reader(*raws):
    from src.pdf_reader import PdfConditionReader

    return PdfConditionReader(client=FakeClient(raws), masters=MASTERS)


def test_rules_decide_codes_and_confirm_consistent_reads():
    out = reader().interpret(evidence())
    assert out["material"] == "SPCC" and out["surface_treatment"] == "ZINC_CLEAR"
    assert out["processes"] == [{"code": "TAP_M4", "count_per_part": 4}]
    assert out["needs_review"] == []


def test_split_callouts_add_up_and_plain_holes_are_ignored():
    procs = [{"text": t, "where": "callout", "bom_count": None, "handwritten_addition": h, "code": c, "count": n}
             for t, h, c, n in [("2-M4", False, "TAP_M4", 2), ("3-M4", False, "TAP_M4", 3), ("4-φ4.5 キリ（M4用）", False, "TAP_M4", 4),
                                ("M4タップ 2ヶ所追加", True, "TAP_M4", 2), ("バーリングタップ M4 2ヶ所", False, "TAP_M4", 2)]]
    out = reader().interpret(evidence(processes=procs))
    assert out["processes"] == [{"code": "TAP_M4", "count_per_part": 7}, {"code": UNREGISTERED, "count_per_part": None}]
    assert "processes" not in out["needs_review"]  # the rules are sure: burring tap is not an M4 tap


def test_parts_list_totals_are_divided_by_the_order_quantity():
    procs = [{"text": "PEMナット CLS-M4-1", "where": "parts_list", "bom_count": 400, "handwritten_addition": False,
              "code": "PRESS_NUT", "count": 4}]
    out = reader().interpret(evidence(processes=procs, parts_list_quantity_header="数量（100台分）"))
    assert out["processes"] == [{"code": "PRESS_NUT", "count_per_part": 4}]
    out = reader().interpret(evidence(processes=procs, parts_list_quantity_header="数量（100台分）",
                                      quantity={"text": None, "where": "title_block", "value": None}))
    assert "processes" in out["needs_review"]


def test_latest_revision_wins_and_per_unit_parts_list_count_is_not_the_quantity():
    raw = evidence(quantity={"text": "QTY CHANGED FROM 100 TO 20", "where": "revision_table", "value": 20},
                   revision_changes=[{"rev": "A", "field": "quantity", "from": "50", "to": "100"},
                                     {"rev": "B", "field": "quantity", "from": "100", "to": "20"}])
    assert reader().interpret(raw)["quantity"] == 20
    stale = evidence(quantity={"text": "100", "where": "title_block", "value": 100},
                     revision_changes=[{"rev": "1", "field": "quantity", "from": "100", "to": "20"}])
    out = reader().interpret(stale)
    assert out["quantity"] == 20 and "quantity" in out["needs_review"]
    per_unit = evidence(quantity={"text": "1", "where": "parts_list", "value": 1}, parts_list_quantity_header="員数")
    assert reader().interpret(per_unit)["quantity"] is None


def test_disagreements_and_unknown_notations_ask_for_a_review():
    raw = evidence(material={"text": "SPCC", "where": "notes", "code": "SPHC"},
                   surface_treatment={"text": "静電塗装 黒", "where": "notes", "code": "POWDER_COAT"},
                   rush={"value": True, "text": "12/5"})
    out = reader().interpret(raw)
    assert out["material"] == "SPCC"  # the rules decide, but the disagreement is shown
    assert set(out["needs_review"]) >= {"material", "surface_treatment", "rush"}
    assert out["review_reasons"]["material"] == ["規則とLLMの解釈が異なる"]


def test_handwritten_rush_overrides_printed_text():
    raw = evidence(rush={"value": False, "text": "NOT URGENT"}, annotations=[{"text": "至急！", "kind": "handwriting"},
                                                                          {"text": "OK 田中", "kind": "handwriting"}])
    out = reader().interpret(raw)
    assert out["rush"] is True and "rush" not in out["needs_review"]


def test_second_read_confirms_or_flags():
    from src.pdf_reader import PdfConditionReader

    r = reader()
    a, b = r.interpret(evidence()), r.interpret(evidence(quantity={"text": "10", "where": "title_block", "value": 10}))
    merged = PdfConditionReader.reconcile(a, b)
    assert merged["quantity"] == 100 and merged["needs_review"] == ["quantity"]
    missing = r.interpret(evidence(rush={"value": False, "text": None}))
    found = r.interpret(evidence(rush={"value": True, "text": "至急！"}))
    merged = PdfConditionReader.reconcile(missing, found)
    assert merged["rush"] is True and "rush" in merged["needs_review"]


def dev_pdf(kind):
    index = json.loads((ROOT / "pdf_data" / "index.json").read_text(encoding="utf-8"))
    return next(ROOT / "pdf_data" / r["file"] for r in index["drawings"] if r["kind"] == kind)


def test_vector_pdf_is_read_once_and_checked_against_the_text_layer():
    path = dev_pdf("vector")
    r = reader(evidence(drawing_no="NOT-ON-THE-DRAWING", material={"text": "SUS304", "where": "title_block", "code": "SUS304"}))
    out = r.read(path)
    assert len(r.client.requests) == 1
    assert any(block.get("type") == "image" for block in r.client.requests[0])
    assert "テキスト層" in r.client.requests[0][-2]["text"]
    layer_text = r.client.requests[0][-2]["text"]
    if "SUS304" not in layer_text:
        assert "material" in out["needs_review"]  # not on the text layer -> review
    assert "drawing_no" in out["needs_review"]
    assert out["usage"]["llm_calls"] == 1 and out["usage"]["llm_input_tokens"] == 1000


def test_raster_pdf_is_read_twice_from_different_renderings():
    path = dev_pdf("fax")
    from src.pdf_reader import PdfConditionReader

    first, second = FakeClient([evidence()]), FakeClient([evidence()])
    r = PdfConditionReader(client=first, masters=MASTERS)
    r._second_client = second
    out = r.read(path)
    assert out["needs_review"] == []
    labels_1 = [b["text"] for b in first.requests[0] if b.get("type") == "text"]
    labels_2 = [b["text"] for b in second.requests[0] if b.get("type") == "text"]
    assert any("2倍拡大" in s for s in labels_1) and any("3倍拡大" in s for s in labels_2)


def test_malformed_output_is_retried():
    path = dev_pdf("vector")
    r = reader({"material": "oops"}, evidence())
    assert r.read(path)["material"] == "SPCC"


# ------------------------------------------------------------------ quote integration
def analysis():
    return SheetMetalAnalysis(status="success", file_name="p.step", thickness_mm=1.6, blank_area_mm2=8000,
                              cut_length_mm=360, hole_count=4, bend_count=2)


def test_confirmed_conditions_are_priced_from_the_master():
    reading = reader().interpret(evidence(rush={"value": True, "text": "特急"}))
    items = condition_items(reading, MASTERS)
    assert [i.status for i in items] == [CONFIRMED] * 6
    condition = quote_condition(items, MASTERS, "SS400", analysis_thickness=1.6)
    assert (condition.material, condition.quantity, condition.surface_treatment, condition.rush) == ("SPCC", 100, "ZINC_CLEAR", True)
    quote = QuoteEngine(MASTERS).calculate(analysis(), condition)
    codes = {line.code: line for line in quote.lines}
    assert codes["TAP_M4"].quantity == 400 and codes["FINISH_ZINC_CLEAR"].amount == 40 * 100
    assert codes["RUSH"].amount == pytest.approx(0.30 * sum(l.amount for l in quote.lines if l.code != "RUSH"))
    assert not quote.is_estimate


def test_unregistered_review_and_missing_items_are_not_priced_and_make_an_estimate():
    reading = reader().interpret(evidence(
        surface_treatment={"text": "クロムめっき", "where": "notes", "code": UNREGISTERED},
        quantity={"text": None, "where": "title_block", "value": None},
        material={"text": "SPCC", "where": "notes", "code": "SPHC"}))
    items = {i.field: i for i in condition_items(reading, MASTERS)}
    assert (items["surface_treatment"].status, items["quantity"].status, items["material"].status) == (UNREG, MISSING, REVIEW)
    condition = quote_condition(list(items.values()), MASTERS, "SS400")
    assert condition.surface_treatment is None and condition.material == "SS400" and len(condition.pending) == 3
    quote = QuoteEngine(MASTERS).calculate(analysis(), condition)
    assert quote.is_estimate and not any(l.code.startswith("FINISH") for l in quote.lines)
    assert any("表面処理：未登録" in w for w in quote.warnings)


def test_plain_quote_condition_is_unchanged():
    quote = QuoteEngine(MASTERS).calculate(analysis(), QuoteCondition(material="SPCC", quantity=2))
    assert not quote.is_estimate and not any(l.code in ("RUSH",) or l.code.startswith("FINISH") for l in quote.lines)
