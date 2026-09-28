"""Quotation PDF: amounts and rounding, printed items, 概算, confidentiality, numbering, fonts, layout, reproducibility."""
from __future__ import annotations

import re
import socket
import sys
from datetime import datetime
from pathlib import Path

import pypdfium2 as pdfium
import pytest

from src.master_loader import MasterLoader
from src.models import AdditionalProcess, QuoteCondition, QuoteLine, QuoteResult, SheetMetalAnalysis
from src.pdf_quote import CONFIRMED, MISSING, REVIEW, UNREG, ConditionItem
from src.quote_document import (DocumentLine, PartInfo, Recipient, build_document, load_company, log_row,
                                price_summary, tax_amount, unit_price, unregistered_process_texts)
from src.quote_engine import QuoteEngine
from src.quote_log import QuoteLog
from src.quote_pdf import render_internal, render_quote

ROOT = Path(__file__).resolve().parents[1]
MASTERS = MasterLoader(ROOT / "data")
ISSUED = datetime(2026, 9, 28, 10, 30)
MM = 72 / 25.4


def analysis(**overrides) -> SheetMetalAnalysis:
    values = dict(status="success", file_name="KB-24-0617_tray.step", thickness_mm=1.2, blank_area_mm2=52000.0,
                  cut_length_mm=1900.0, hole_count=6, bend_count=4)
    return SheetMetalAnalysis(**{**values, **overrides})


def condition(**overrides) -> QuoteCondition:
    values = dict(material="SPCC", quantity=100, surface_treatment="ZINC_CLEAR",
                  additional_processes=[AdditionalProcess(process_code="TAP_M4", quantity=4, unit="hole", source="drawing")])
    return QuoteCondition(**{**values, **overrides})


PART = PartInfo(name="制御盤取付トレー", drawing_no="KB-24-0617", revision="B", revision_date="2026.09.15",
                shape_file="KB-24-0617_tray.step", drawing_file="KB-24-0617.pdf")
RECIPIENT = Recipient("サンプル電機株式会社", "購買部　山田 太郎")


def document(a=None, c=None, **kwargs):
    a, c = a or analysis(), c or condition()
    quote = QuoteEngine(MASTERS).calculate(a, c)
    values = dict(analysis=a, condition=c, quote=quote, masters=MASTERS, company=load_company(ROOT / "data"),
                  recipient=RECIPIENT, part=PART, issued_at=ISSUED, number="Q20260928-001")
    return build_document(**{**values, **kwargs})


def estimate_document():
    items = [ConditionItem("material", "材質", "SPCC", "冷間圧延鋼板 SPCC", CONFIRMED),
             ConditionItem("thickness_mm", "板厚", 1.2, "1.2 mm", CONFIRMED),
             ConditionItem("quantity", "数量", 100, "100 個", CONFIRMED),
             ConditionItem("surface_treatment", "表面処理", None, "-", MISSING),
             ConditionItem("processes", "追加加工", [{"code": "TAP_M4", "count_per_part": 4},
                                                  {"code": "UNREGISTERED", "count_per_part": None}], "M4タップ ×4", UNREG),
             ConditionItem("rush", "特急", True, "あり", REVIEW, ["2回の読み取りが一致しない"])]
    c = condition(surface_treatment=None, pending=["表面処理：記載なし", "追加加工：未登録", "特急：要確認"])
    return document(c=c, items=items, unregistered_texts=["バーリングタップ M4"])


def text_of(pdf: bytes) -> str:
    doc = pdfium.PdfDocument(pdf)
    return "\n".join(doc[i].get_textpage().get_text_range() for i in range(len(doc)))


def flat(text: str) -> str:
    return re.sub(r"\s+", "", text)


# ---------------------------------------------------------------- amounts
def test_rounding_rules():
    assert unit_price(777_541.3, 100) == 7_776          # 1円未満切り上げ
    assert unit_price(777_600.0, 100) == 7_776          # exact: not raised
    assert unit_price(0.1 + 0.2, 3) == 1                 # float noise does not add a yen (0.30000000000000004 / 3)
    assert unit_price(3 * 7_776.000_000_000_1, 3) == 7_776
    assert tax_amount(777_600, 0.10) == 77_760
    assert tax_amount(12_345, 0.10) == 1_234             # 1円未満切り捨て
    assert tax_amount(12_349, 0.08) == 987
    summary = price_summary(QuoteResult(lines=[], subtotal_cost=0, margin_rate=0.25, final_price=777_541.3,
                                        rounded_final_price=777_550, estimated_minutes=0), 100, 0.10)
    assert (summary.unit_price, summary.amount, summary.subtotal, summary.tax, summary.total) == (
        7_776, 777_600, 777_600, 77_760, 855_360)


def test_document_amounts_follow_the_engine_and_what_changed_on_screen():
    doc = document()
    quote = doc.internal.quote
    line = doc.lines[0]
    assert quote.final_price == QuoteEngine(MASTERS).calculate(analysis(), condition()).final_price  # engine unchanged
    assert line.unit_price == unit_price(quote.final_price, 100) and line.amount == line.unit_price * 100
    assert doc.subtotal == line.amount and doc.tax == tax_amount(doc.subtotal, 0.10)
    assert doc.total == doc.subtotal + doc.tax
    # The screen used to show the final price rounded up to 10 yen (tax not separated). It now shows the
    # document's values: unit price rounded up to the yen x quantity, then the tax.
    assert quote.rounded_final_price == int(-(-quote.final_price // 10) * 10)
    assert doc.subtotal >= quote.final_price and doc.subtotal - quote.final_price < 100  # < 1 yen per part


def test_multiple_lines_are_summed():
    doc = document()
    doc.lines.append(DocumentLine(name="カバー", drawing_no="KB-24-0618", spec=["SPCC t1.0", "レーザー切断"],
                                  quantity=10, unit_price=1_235, amount=12_350))
    assert doc.subtotal == doc.lines[0].amount + 12_350
    text = flat(text_of(render_quote(doc)))
    assert "カバー" in text and "KB-24-0618" in text and f"{doc.total:,}" in text


# ---------------------------------------------------------------- printed items
def test_confirmed_quote_prints_every_item_and_no_estimate_wording():
    doc = document(flags=["inspection"],
                   free_remarks="図面の注記どおり脱脂のこと")
    text = flat(text_of(render_quote(doc)))
    company = doc.company
    expected = [
        "御見積書", "見積番号", "Q20260928-001", "発行日", "2026年9月28日", "有効期限", "2026年10月28日",
        "サンプル電機株式会社御中", "購買部山田太郎様", "下記のとおり御見積申し上げます。",
        "件名", "KB-24-0617制御盤取付トレー製作", "納期", "受注後10営業日", "受渡場所", "貴社指定場所",
        "取引条件", flat(company.payment_terms), "発行日より30日",
        flat(company.name), company.postal_code, flat(company.address), company.tel, company.fax,
        company.registration_no, flat(company.contact), "承認", "担当",
        "御見積金額（税込）", f"¥{doc.total:,}",
        "No", "品名・図番", "仕様", "数量", "単位", "単価", "金額",
        "制御盤取付トレー", "KB-24-0617Rev.B", "SPCCt1.2三価クロメート（光沢）", "レーザー切断・曲げ4箇所・M4タップ4箇所",
        "100", "個", f"{doc.lines[0].unit_price:,}", f"{doc.lines[0].amount:,}",
        "小計", f"{doc.subtotal:,}", "消費税（10%）", f"{doc.tax:,}", "合計",
        "備考", "本見積は図面KB-24-0617Rev.B（2026.09.15）および3DデータKB-24-0617_tray.stepに基づきます。",
        *[flat(r) for r in company.remarks], "検査成績書", "図面の注記どおり脱脂のこと", "1/1",
    ]
    missing = [item for item in expected if item not in text]
    assert not missing
    assert "概算" not in text
    assert doc.title == "御見積書" and not doc.pending


def test_rush_changes_lead_time_and_is_noted():
    doc = document(c=condition(rush=True))
    text = flat(text_of(render_quote(doc)))
    assert "受注後5営業日" in text and "特急対応" in text
    assert "特急割増" in [line.name[:4] for line in doc.internal.quote.lines if line.code == "RUSH"][0]


def test_estimate_title_and_every_unsettled_condition_in_the_remarks():
    doc = estimate_document()
    assert doc.is_estimate and doc.title == "概算御見積書"
    text = flat(text_of(render_quote(doc)))
    assert "概算御見積書" in text and "本見積は概算です" in text
    for note in ("表面処理：図面に記載なし（金額に含めていません）",
                 "追加加工：バーリングタップM4（マスター未登録のため別途見積）",
                 "特急：ありは読み取り値の確認が必要（特急割増を含めていません）"):
        assert note in text
    assert len(doc.pending) == 3
    assert "SPCCt1.2表面処理：未定" in text
    assert text.index("本見積は概算です") < text.index("本見積は図面")  # the estimate block comes first


def test_estimate_from_the_shape_analysis_is_listed():
    doc = document(a=analysis(status="partial", assumptions=["Kファクター未指定のため既定値0.33で展開"]))
    assert doc.is_estimate
    assert any(note.startswith("形状解析：Kファクター未指定") for note in doc.pending)
    assert "形状解析" in flat(text_of(render_quote(doc)))


def test_manual_condition_without_a_drawing():
    """No drawing PDF: STEP + conditions entered by hand. Nothing is pending, no drawing in the basis."""
    doc = document(part=PartInfo(name="bracket", shape_file="bracket.step"), items=None)
    text = flat(text_of(render_quote(doc)))
    assert doc.subject == "bracket 製作" and "本見積は3Dデータbracket.stepに基づきます。" in text
    dxf = document(part=PartInfo(shape_file="flat.dxf"))
    assert dxf.subject == "flat 製作" and "展開図データflat.dxf" in flat(text_of(render_quote(dxf)))


def test_recipient_is_required():
    with pytest.raises(ValueError):
        document(recipient=Recipient("  "))


# ---------------------------------------------------------------- confidentiality
def test_external_quote_has_no_cost_breakdown_or_margin_and_the_internal_one_does():
    doc = estimate_document()
    quote = doc.internal.quote
    external = flat(text_of(render_quote(doc)))
    for word in ("材料費", "ピアス加工", "段取り", "粗利", "原価", "社外秘", "25%", "margin", "MATERIAL", "SETUP"):
        assert word not in external, word
    for line in quote.lines:
        assert f"{line.amount:,.1f}" not in external
    assert f"{quote.final_price:,.1f}" not in external

    internal = flat(text_of(render_internal(doc)))
    for word in ("社外秘", "見積根拠（社内用）", "原価の内訳", "材料費", "段取り", "粗利率", "25%", "最終価格",
                 "展開面積", "切断長", "穴数", "曲げ数", "解析の状態：確定", "図面から読み取った条件", "記載なし", "未登録"):
        assert word in internal, word
    assert f"{quote.final_price:,.1f}" in internal and f"{doc.total:,}" in internal


# ---------------------------------------------------------------- numbering and log
def test_numbers_do_not_repeat_on_the_same_day(tmp_path):
    log = QuoteLog(tmp_path / "output" / "quote_log.csv")
    doc = document(number="")
    numbers = [log.issue(ISSUED, log_row(doc)) for _ in range(3)]
    assert numbers == ["Q20260928-001", "Q20260928-002", "Q20260928-003"]
    assert log.issue(datetime(2026, 9, 29, 9, 0), log_row(doc)) == "Q20260929-001"
    rows = log.rows()
    assert len(rows) == 4 and len({r["quote_no"] for r in rows}) == 4
    assert rows[0]["customer"] == "サンプル電機株式会社" and rows[0]["total"] == str(doc.total)
    assert rows[0]["drawing_no"] == "KB-24-0617" and rows[0]["quantity"] == "100" and rows[0]["is_estimate"] == "0"
    assert rows[0]["input_files"] == "KB-24-0617_tray.step | KB-24-0617.pdf"
    assert rows[0]["issued_at"] == "2026-09-28T10:30:00"
    assert not (tmp_path / "output" / "quote_log.lock").exists()


# ---------------------------------------------------------------- fonts, layout, reproducibility
def test_japanese_font_is_embedded_and_no_other_font_is_used():
    for pdf in (render_quote(document()), render_internal(document())):
        fonts = set(re.findall(rb"/BaseFont\s*/([A-Za-z0-9+\-_]+)", pdf))
        assert fonts and all(re.fullmatch(rb"[A-Z]{6}\+IPAGothic", f) for f in fonts), fonts
        assert pdf.count(b"/FontFile2") == len(fonts)


def _char_boxes(pdf: bytes):
    page = pdfium.PdfDocument(pdf)[0]
    textpage = page.get_textpage()
    boxes = []
    for i in range(textpage.count_chars()):
        char = textpage.get_text_range(i, 1)
        if char.strip():
            boxes.append((char, *textpage.get_charbox(i)))  # left, bottom, right, top (points)
    return boxes, page.get_height()


def test_long_texts_stay_inside_their_boxes():
    long_part = PartInfo(name="超長い品名の制御盤取付トレー（左右対称・補強リブ付き・二次加工込み）タイプＡ－１２３４５",
                         drawing_no="KB-24-0617-LONG-DRAWING-NUMBER", revision="C", shape_file="tray.step")
    doc = document(recipient=Recipient("株式会社とても長い名前のサンプル電機工業ホールディングス東日本製作所",
                                       "調達本部 第二購買部 精密板金部品グループ　山田 太郎"),
                   part=long_part, subject="KB-24-0617 超長い品名の制御盤取付トレー（左右対称・補強リブ付き）製作 一式",
                   delivery_place="貴社 第二工場 資材受入センター（北門）", c=condition(
                       additional_processes=[AdditionalProcess(process_code=code, quantity=q, unit="hole", source="user")
                                             for code, q in (("TAP_M3", 2), ("TAP_M4", 4), ("TAP_M5", 6), ("PRESS_NUT", 8),
                                                             ("PRESS_STUD", 3), ("COUNTERSINK", 5))]),
                   free_remarks="\n".join(["長い備考" * 30] * 4))
    pdf = render_quote(doc)
    boxes, height = _char_boxes(pdf)
    assert all(15 * MM - 1 <= left and right <= 195 * MM + 1 for _, left, _, right, _ in boxes)
    # the detail table: no character crosses a column line
    columns = [v * MM for v in (23.8, 73.4, 133, 143, 156.8, 175.6)]
    row_band = (height - (124 + 6.7 + 10) * MM, height - (124 + 6.7) * MM)
    in_row = [b for b in boxes if row_band[0] <= b[2] and b[4] <= row_band[1]]
    assert in_row
    assert not [b for b in in_row for x in columns if b[1] < x - 0.3 < b[3] and b[1] < x + 0.3 < b[3]]
    # the recipient line and the terms stay left of the company block
    left_block = [b for b in boxes if height - 100 * MM < b[2] < height - 40 * MM and b[1] < 110 * MM]
    assert all(b[3] <= 108 * MM for b in left_block)
    text = flat(text_of(pdf))
    assert flat(doc.recipient.company) in text and "超長い品名の制御盤取付トレー" in text
    assert len(pdfium.PdfDocument(pdf)) == 1


def test_same_input_and_issue_time_give_the_same_pdf():
    assert render_quote(estimate_document()) == render_quote(estimate_document())
    assert render_internal(estimate_document()) == render_internal(estimate_document())
    later = document(issued_at=datetime(2026, 9, 29, 10, 30))
    assert render_quote(later) != render_quote(document())


def test_no_llm_api_and_no_network(monkeypatch, tmp_path):
    for key in ("ANTHROPIC_API_KEY", "ANALYSIS_ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setitem(sys.modules, "anthropic", None)  # importing an LLM SDK would fail
    monkeypatch.setitem(sys.modules, "openai", None)

    def no_network(*args, **kwargs):
        raise AssertionError("network access")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    doc = estimate_document()
    doc.number = QuoteLog(tmp_path / "log.csv").issue(ISSUED, log_row(doc))
    assert render_quote(doc).startswith(b"%PDF") and render_internal(doc).startswith(b"%PDF")


# ---------------------------------------------------------------- inputs
def test_company_local_file_takes_precedence(tmp_path):
    (tmp_path / "company.csv").write_text("key,value\nname,仮の会社\n", encoding="utf-8")
    assert load_company(tmp_path).name == "仮の会社"
    (tmp_path / "company.local.csv").write_text("key,value\nname,本当の会社\nremark_2,二\nremark_1,一\n", encoding="utf-8")
    company = load_company(tmp_path)
    assert company.name == "本当の会社" and company.remarks == ["一", "二"]
    committed = load_company(ROOT / "data")
    assert "（仮）" in committed.name and committed.registration_no == "T0000000000000"


def test_policy_rows_for_the_document():
    assert MASTERS.policy("tax_rate", 0) == 0.10
    assert MASTERS.policy("quote_validity_days", 0) == 30
    assert MASTERS.policy("lead_time_days_normal", 0) == 10 and MASTERS.policy("lead_time_days_rush", 0) == 5


def test_unregistered_process_texts_from_the_reading_evidence():
    evidence = [{"processes": [{"text": "M4タップ 4箇所", "code": "TAP_M4"},
                               {"text": "バーリングタップ M4", "code": "UNREGISTERED"},
                               {"text": "φ5 穴", "code": None}]}]
    texts = unregistered_process_texts(evidence, MASTERS)
    assert "バーリングタップ M4" in texts and "M4タップ 4箇所" not in texts and "φ5 穴" not in texts
    assert unregistered_process_texts(None, MASTERS) == []
