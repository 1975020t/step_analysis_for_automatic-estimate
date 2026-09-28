"""Similar past quotes: import, repeats, the material / thickness rule, explanations, missing data, the price
reference, speed, the history file and outcomes. No LLM, and the committed history is never written."""
from __future__ import annotations

import hashlib
import socket
import sys
import time
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path

import pytest

from scripts.evaluate_similar_quotes import enlarge, query_of, reference_errors
from src.master_loader import UNREGISTERED, MasterLoader
from src.models import AdditionalProcess, QuoteCondition, SheetMetalAnalysis
from src.past_quotes import HistoryStore, PastQuote, from_document, import_csv, material_family
from src.quote_document import PartInfo, Recipient, build_document, load_company
from src.quote_engine import QuoteEngine
from src.similar_quotes import (OTHER_CUSTOMER, REPEAT, SAME_CUSTOMER, Query, SimilarQuoteSearch, norm_customer,
                                norm_drawing, query_for)

ROOT = Path(__file__).resolve().parents[1]
SEED = ROOT / "data" / "past_quotes" / "past_quotes_seed.csv"
COMMITTED = ROOT / "data" / "past_quotes" / "history.csv"
MASTERS = MasterLoader(ROOT / "data")
SEED_THICKNESS = [0.8, 1.0, 1.2, 1.6, 2.0, 2.3, 3.2, 4.5]


@pytest.fixture(scope="module")
def seed():
    quotes, problems = import_csv(SEED, MASTERS)
    assert not problems
    return quotes, SimilarQuoteSearch(quotes, MASTERS)


def by_no(quotes, number):
    return next(q for q in quotes if q.quote_no == number)


# ---------------------------------------------------------------- import
def test_seed_import_keeps_the_text_and_maps_it_to_codes(seed):
    quotes, _ = seed
    assert len(quotes) == 2084
    assert all("出典" not in q.original for q in quotes)  # the test-only column is dropped at import
    first = by_no(quotes, "M-2301-0001")
    assert (first.customer, first.drawing_no, first.material_text, first.material_code, first.material_family) == (
        "サンプルロボティクス株式会社", "RB237255", "SPCC", "SPCC", "steel")
    assert first.processes == [{"code": "PRESS_STUD", "count": 1, "text": "圧入スタッド×1"}]
    assert first.has_shape and first.area == 14411.0 and first.original["品名"] == "端子台取付板"
    texts = {q.material_text: (q.material_code, q.material_family) for q in quotes}
    assert texts["SUS"] == ("", "stainless") and texts["AL"] == ("", "aluminum") and texts["SS"] == ("", "steel")
    assert texts["SUS430"] == (UNREGISTERED, "stainless") and texts["C1100P"] == (UNREGISTERED, "copper")
    assert texts["冷延"] == ("SPCC", "steel") and texts["ボンデ"] == ("SECC", "steel") and texts["SUS304-2B"][0] == "SUS304"
    finishes = {q.finish_text: q.finish_code for q in quotes}
    assert finishes[""] == "" and finishes["生地"] == "NONE" and finishes["ユニクロ"] == "ZINC_CLEAR"
    assert finishes["クロムめっき"] == UNREGISTERED and finishes["粉体 黒"] == "POWDER_COAT"
    burring = next(q for q in quotes if "バーリングタップ" in q.processes_text)
    assert any(p["code"] == UNREGISTERED and "バーリングタップ" in p["text"] for p in burring.processes)
    assert sum(not q.has_shape for q in quotes) > 700


def test_import_with_other_column_names_and_missing_columns(tmp_path):
    csv_text = ("得意先,見積No,日付,図面番号,材料,板厚,個数,処理,加工,見積単価,受注結果,社内メモ\n"
                "株式会社テスト工業,A-1,2024-05-10,TX-100,ＳＵＳ３０４,t1.5,20,生地,M4タップ 4ヶ所、バーリングタップ M3 2ヶ所,\"1,280\",成約,注記\n"
                "株式会社テスト工業,A-2,2024/06/01,TX-101,アルミ,2,5,,,900,,\n"
                ",A-3,2024/06/02,TX-102,SPCC,1,1,,,100,,\n")
    path = tmp_path / "old.csv"
    path.write_bytes(csv_text.encode("cp932"))  # Excel on Windows
    quotes, problems = import_csv(path, MASTERS, mapping={"unit_price": "見積単価"})
    assert len(quotes) == 2 and len(problems) == 1 and "4行目" in problems[0]
    a, b = quotes
    assert (a.customer, a.quote_no, a.date, a.drawing_no) == ("株式会社テスト工業", "A-1", date(2024, 5, 10), "TX-100")
    assert (a.material_code, a.thickness, a.quantity, a.finish_code, a.unit_price, a.outcome) == (
        "SUS304", 1.5, 20, "NONE", 1280, "受注")
    assert [p["code"] for p in a.processes] == ["TAP_M4", UNREGISTERED] and a.processes[1]["count"] == 2
    assert a.original["社内メモ"] == "注記" and a.revision == "" and not a.has_shape
    assert (b.material_code, b.material_family, b.finish_code, b.outcome) == ("", "aluminum", "", "未回答")
    with pytest.raises(ValueError):
        import_csv(path, MASTERS, mapping={"unit_price": "ない列"})


# ---------------------------------------------------------------- search rules
def test_every_earlier_repeat_is_in_the_results(seed):
    quotes, search = seed
    groups: dict[tuple[str, str], list[PastQuote]] = {}
    for q in quotes:
        groups.setdefault((norm_customer(q.customer), norm_drawing(q.drawing_no)), []).append(q)
    checked = 0
    for i, q in enumerate(quotes):
        earlier = [x for x in groups[(norm_customer(q.customer), norm_drawing(q.drawing_no))] if x.date < q.date]
        if not earlier:
            continue
        results = search.search(query_of(q, None))
        got = {r.quote.quote_no: r for r in results}
        assert all(e.quote_no in got and got[e.quote_no].category == REPEAT for e in earlier), q.quote_no
        assert [r.category for r in results[:len(earlier)]] == [REPEAT] * len(earlier)  # repeats come first
        checked += 1
    assert checked >= 500


def test_same_drawing_number_of_another_customer_is_not_a_repeat(seed):
    quotes, search = seed
    q = next(q for q in quotes if q.drawing_no)
    query = replace(query_of(q, None), customer="別の株式会社", date=None, exclude="")
    assert all(r.category != REPEAT for r in search.search(query))


def test_similar_results_keep_the_material_family_and_the_thickness_rule(seed):
    quotes, search = seed
    for q in quotes[::7]:
        if not q.material_family or q.thickness not in SEED_THICKNESS:
            continue
        k = SEED_THICKNESS.index(q.thickness)
        allowed = set(SEED_THICKNESS[max(0, k - 1):k + 2])
        for r in search.search(query_of(q, None)):
            if r.category == REPEAT:
                continue
            assert r.quote.material_family == q.material_family, (q.quote_no, r.quote.quote_no)
            assert r.quote.thickness in allowed, (q.quote_no, r.quote.thickness, q.thickness)


def test_at_most_five_results_each_with_reasons_and_differences(seed):
    quotes, search = seed
    counts = []
    for q in quotes[::5]:
        results = search.search(query_of(q, None))
        assert len(results) <= 5
        counts.append(len(results))
        for r in results:
            assert r.reasons and r.differences
        order = [{REPEAT: 0, SAME_CUSTOMER: 1, OTHER_CUSTOMER: 2}[r.category] for r in results]
        assert order == sorted(order)
    assert max(counts) == 5


def test_quotes_without_cad_values_are_found(seed):
    quotes, search = seed
    legacy_found = 0
    for q in quotes[::3]:
        results = search.search(replace(query_of(q, None), area=None, holes=None, bends=None))
        legacy_found += sum(not r.quote.has_shape for r in results)
    assert legacy_found > 100
    legacy = next(q for q in quotes if not q.has_shape and q.drawing_no)
    later = replace(query_of(legacy, None), date=date(2030, 1, 1), exclude="")
    hit = next(r for r in search.search(later) if r.quote.quote_no == legacy.quote_no)
    assert hit.category == REPEAT and hit.leveled_unit is None and "形状の数値がない" in hit.standard_note
    assert "過去の見積に形状の数値なし" in hit.differences


def test_explanations_name_the_differences():
    quote = PastQuote(quote_no="P-1", date=date(2025, 1, 10), customer="株式会社テスト", drawing_no="TX-1", revision="A",
                      part_name="ブラケット", material_text="SPCC", material_code="SPCC", material_family="steel",
                      thickness=1.6, quantity=100, finish_text="なし", finish_code="NONE", processes_text="M4タップ×2",
                      processes=[{"code": "TAP_M4", "count": 2, "text": "M4タップ×2"}], unit_price=1000,
                      area=20000.0, cut=800.0, holes=4, bends=2)
    search = SimilarQuoteSearch([quote], MASTERS)
    query = Query(customer="テスト(株)", drawing_no="tx-1", revision="B", material_code="SPCC", material_family="steel",
                  thickness=1.6, quantity=50, finish_code="NONE", processes={"TAP_M4": 4}, area=21000.0, holes=4, bends=2,
                  unit_price=1300, standard_unit=1200.0, today=date(2026, 9, 28))
    (m,) = search.search(query)
    assert m.category == REPEAT  # customer names and drawing numbers are compared after normalisation
    assert "同じ顧客・同じ図番（改訂 A）" in m.reasons and "材質同じ（SPCC）" in m.reasons and "板厚同じ（t1.6）" in m.reasons
    assert "改訂 A→B" in m.differences and "数量 100→50" in m.differences and "M4タップ +2" in m.differences
    assert m.price_diff == pytest.approx((1000 - 1300) / 1300)
    standard, _ = search.standard_unit(0)
    assert m.ratio == pytest.approx(1000 / standard) and m.leveled_unit == pytest.approx(1200.0 * 1000 / standard)
    assert any("リピートで今回の単価が" in w for w in m.warnings) == (abs(1300 / m.leveled_unit - 1) > 0.20)
    assert search.reference([m]).unit == pytest.approx(m.leveled_unit)


def test_repeat_warning_when_the_price_moved_more_than_20_percent(seed):
    quotes, search = seed
    q = next(q for i, q in enumerate(quotes) if q.drawing_no and search.standard_unit(i)[0])
    base = replace(query_of(q, search.standard_unit(quotes.index(q))[0]), date=date(2030, 1, 1), exclude="")
    hit = next(r for r in search.search(replace(base, unit_price=q.unit_price)) if r.quote.quote_no == q.quote_no)
    level = hit.leveled_unit
    near = next(r for r in search.search(replace(base, unit_price=round(level * 1.1))) if r.quote.quote_no == q.quote_no)
    far = next(r for r in search.search(replace(base, unit_price=round(level * 1.3))) if r.quote.quote_no == q.quote_no)
    assert not any("リピートで" in w for w in near.warnings)
    assert any("リピートで" in w and "+30%" in w for w in far.warnings)
    assert any("2年以上前" in w for w in far.warnings)  # 2030 vs a seed quote from 2023-2026


# ---------------------------------------------------------------- price reference and speed
def test_the_reference_price_is_closer_to_the_actual_price_than_the_standard(seed):
    quotes, _ = seed
    rows, result = reference_errors(quotes, MASTERS)
    assert len(rows) > 1000
    for group in ("全体", "リピート（前回の見積がある）"):
        auto, similar, with_reference = result[group]
        assert similar["mape"] < auto["mape"], group
        assert with_reference > 0.9 * auto["n"]


def test_search_is_fast_on_the_seed_and_on_10000_quotes(seed):
    quotes, _ = seed
    for history in (quotes, enlarge(quotes, 10_000)):
        search = SimilarQuoteSearch(history, MASTERS)
        worst = 0.0
        for q in quotes[::40]:
            start = time.perf_counter()
            search.search(replace(query_of(q, None), date=None, exclude=""))
            worst = max(worst, time.perf_counter() - start)
        assert worst < 1.0, (len(history), worst)
    assert len(history) == 10_000


# ---------------------------------------------------------------- history
def test_an_issued_quote_enters_the_history_and_is_found_next_time(_history_copy):
    store = HistoryStore(_history_copy)
    before = len(store.load())
    analysis = SheetMetalAnalysis(status="success", file_name="tx.step", thickness_mm=1.6, blank_area_mm2=20000.0,
                                  cut_length_mm=800.0, hole_count=4, bend_count=2)
    condition = QuoteCondition(material="SPCC", quantity=50, surface_treatment="ZINC_CLEAR",
                               additional_processes=[AdditionalProcess(process_code="TAP_M4", quantity=4, unit="hole")])
    quote = QuoteEngine(MASTERS).calculate(analysis, condition)
    doc = build_document(analysis=analysis, condition=condition, quote=quote, masters=MASTERS,
                         company=load_company(ROOT / "data"), recipient=Recipient("株式会社テスト新規"),
                         part=PartInfo(name="ブラケット", drawing_no="NEW-001", revision="A", shape_file="tx.step"),
                         issued_at=datetime(2026, 9, 28, 10, 0), number="Q20260928-001")
    added, _ = store.append([from_document(doc, MASTERS)])
    assert added == 1
    history = store.load()
    assert len(history) == before + 1
    row = by_no(history, "Q20260928-001")
    assert (row.customer, row.drawing_no, row.revision, row.material_code, row.finish_code, row.quantity) == (
        "株式会社テスト新規", "NEW-001", "A", "SPCC", "ZINC_CLEAR", 50)
    assert row.unit_price == doc.lines[0].unit_price and row.source == "app" and row.outcome == "未回答"
    assert row.processes == [{"code": "TAP_M4", "count": 4, "text": "M4タップ×4"}] and row.has_shape

    search = SimilarQuoteSearch(history, MASTERS)
    query = query_for(analysis, condition, doc.lines[0].unit_price, quote.final_price / 50, "株式会社テスト新規",
                      "NEW-001", "B", today=date(2026, 10, 1))
    first = search.search(query)[0]
    assert first.category == REPEAT and first.quote.quote_no == "Q20260928-001" and "改訂 A→B" in first.differences
    assert first.leveled_unit == pytest.approx(doc.lines[0].unit_price, rel=1e-3)  # same conditions: same level

    store.set_outcome("Q20260928-001", "受注")
    assert by_no(store.load(), "Q20260928-001").outcome == "受注"
    with pytest.raises(ValueError):
        store.set_outcome("Q20260928-001", "保留")
    assert store.append([from_document(doc, MASTERS)]) == (0, 1)  # the same quote is not added twice


def test_committed_history_is_the_imported_seed_and_is_not_touched_by_tests(_history_copy):
    assert Path(_history_copy) != COMMITTED
    digest = hashlib.sha256(COMMITTED.read_bytes()).hexdigest()
    committed = HistoryStore(COMMITTED).load()
    seed_quotes, _ = import_csv(SEED, MASTERS)
    assert [q.quote_no for q in committed if q.source.startswith("import:")] == [q.quote_no for q in seed_quotes]
    HistoryStore(_history_copy).set_outcome(committed[0].quote_no, "失注", committed[0].customer)
    assert hashlib.sha256(COMMITTED.read_bytes()).hexdigest() == digest


def test_no_llm_api_and_no_network(monkeypatch, seed):
    for key in ("ANTHROPIC_API_KEY", "ANALYSIS_ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setitem(sys.modules, "anthropic", None)
    monkeypatch.setitem(sys.modules, "openai", None)

    def no_network(*args, **kwargs):
        raise AssertionError("network access")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    quotes, _ = import_csv(SEED, MASTERS)
    search = SimilarQuoteSearch(quotes, MASTERS)
    assert search.search(query_of(quotes[-1], None))
    assert material_family("SUS304") == "stainless"
