from pathlib import Path

from streamlit.testing.v1 import AppTest

from src.models import FlatPatternSummary, SheetMetalAnalysis


APP_PATH = Path(__file__).resolve().parents[1] / "app.py"


def cost_lines(app):
    return next(d.value for d in app.dataframe if "コード" in d.value.columns)


def test_initial_ui_has_step_input_and_analyze_action():
    app = AppTest.from_file(APP_PATH).run(timeout=20)

    assert not app.exception
    assert [u.label for u in app.get("file_uploader")][0] == "STEP／展開図DXFファイル"
    assert app.get("file_uploader")[1].label.startswith("図面PDF")
    assert [button.label for button in app.button] == ["解析を実行"]
    assert not any("モード" in item.value for item in list(app.info) + list(app.sidebar.info))  # no mode labels


def test_success_ui_shows_all_five_results_together():
    app = AppTest.from_file(APP_PATH)
    app.session_state["analysis_result"] = SheetMetalAnalysis(
        status="success",
        file_name="verified.step",
        thickness_mm=2.0,
        blank_area_mm2=4800.0,
        cut_length_mm=320.0,
        hole_count=2,
        bend_count=1,
        flat_pattern=FlatPatternSummary(
            method="test",
            area_mm2=4800.0,
            cut_length_mm=320.0,
            boundary_count=3,
            outer_boundary_count=1,
            inner_boundary_count=2,
            surface_region_count=3,
        ),
    )
    app.run(timeout=20)

    assert not app.exception
    assert [metric.label for metric in app.metric][:5] == [
        "板厚",
        "展開面積",
        "切断長",
        "穴数",
        "曲げ回数",
    ]
    assert [metric.value for metric in app.metric][:5] == [
        "2 mm",
        "4,800.0 mm²",
        "320.0 mm",
        "2",
        "1",
    ]
    assert not any("モード" in item.value for item in list(app.info) + list(app.sidebar.info))  # no mode labels


def test_unsupported_ui_shows_reason_code_and_message():
    app = AppTest.from_file(APP_PATH)
    app.session_state["analysis_result"] = SheetMetalAnalysis(
        status="unsupported",
        file_name="assembly.step",
        reason_code="MULTIPLE_SOLIDS",
        message="Solidが2個あります。",
    )
    app.run(timeout=20)

    assert not app.exception
    assert "解析不可" in app.error[0].value
    assert app.code[0].value == "MULTIPLE_SOLIDS"


def test_dxf_result_shows_values_flat_pattern_and_quote(tmp_path):
    import ezdxf

    from src.dxf_analyzer import DxfAnalyzer

    doc = ezdxf.new("R2010", setup=True)
    doc.header["$INSUNITS"] = 4
    msp = doc.modelspace()
    for a, b in [((0, 0), (100, 0)), ((100, 0), (100, 50)), ((100, 50), (0, 50)), ((0, 50), (0, 0))]:
        msp.add_line(a, b)
    msp.add_circle((25, 25), 5)
    msp.add_line((70, 0), (70, 50), dxfattribs={"linetype": "DASHED"})
    doc.saveas(tmp_path / "flat.dxf")
    app = AppTest.from_file(APP_PATH)
    result = DxfAnalyzer(thickness_mm=1.6).analyze(tmp_path / "flat.dxf")
    assert result.status == "success" and abs(result.blank_area_mm2 - (5000 - 25 * 3.14159265)) < 0.05
    app.session_state["analysis_result"] = result
    app.session_state["step_bytes"] = None
    app.run(timeout=30)

    assert not app.exception
    assert [metric.value for metric in app.metric][:5] == [
        "1.6 mm", f"{result.blank_area_mm2:,.1f} mm²", f"{result.cut_length_mm:,.1f} mm", "1", "1"]
    assert any(metric.label.startswith("見積金額") for metric in app.metric)


def click(app, label):
    next(b for b in app.button if b.label.startswith(label)).click().run(timeout=30)
    assert not app.exception
    return app


def test_pdf_conditions_panel_shows_statuses_and_quotes_the_values_on_screen():
    reading = {"material": "SPCC", "thickness_mm": 2.0, "quantity": 50, "surface_treatment": "UNREGISTERED",
               "processes": [{"code": "TAP_M4", "count_per_part": 4}], "rush": True, "flags": ["inspection"],
               "drawing_no": "AB-21-0001", "revision": "B", "needs_review": ["rush"],
               "review_reasons": {"rush": ["2回の読み取りが一致しない"]}}
    app = AppTest.from_file(APP_PATH)
    app.session_state["analysis_result"] = SheetMetalAnalysis(
        status="success", file_name="p.step", thickness_mm=2.0, blank_area_mm2=4800.0, cut_length_mm=320.0,
        hole_count=2, bend_count=1)
    app.session_state["pdf_reading"] = reading
    app.session_state["pdf_name"] = "drawing.pdf"
    app.run(timeout=30)

    assert not app.exception
    text = " ".join(m.value for m in app.markdown)
    assert "✅ 読み取り済み" in text and "⚠️ 要確認" in text and "❌ 未登録" in text
    assert any("検査・証明書" in w.value for w in app.warning)
    assert not any("この値で確定" in c.label for c in app.checkbox)  # no confirming step: there is no 概算 quote
    assert not any("概算見積" in w.value for w in app.warning)
    lines = cost_lines(app)
    assert {"TAP_M4", "RUSH"} <= set(lines["コード"])  # the values in the inputs are quoted
    app.checkbox(key="pdf_rush").uncheck().run(timeout=30)
    assert "RUSH" not in set(cost_lines(app)["コード"])


def test_quotation_page_outputs_a_pdf_with_the_screen_amounts(tmp_path, monkeypatch):
    import csv

    from src.master_loader import MasterLoader
    from src.models import QuoteCondition
    from src.quote_document import price_summary
    from src.quote_engine import QuoteEngine

    monkeypatch.setenv("QUOTE_LOG_PATH", str(tmp_path / "quote_log.csv"))
    analysis = SheetMetalAnalysis(status="success", file_name="bracket.step", thickness_mm=2.0, blank_area_mm2=4800.0,
                                  cut_length_mm=320.0, hole_count=2, bend_count=1)
    app = AppTest.from_file(APP_PATH)
    app.session_state["analysis_result"] = analysis
    app.run(timeout=30)
    assert not app.exception
    assert not any(t.key == "doc_customer" for t in app.text_input)  # the estimate page only estimates

    masters = MasterLoader("data")
    expected = price_summary(QuoteEngine(masters).calculate(analysis, QuoteCondition(material=masters.material_names[0])),
                             1, 0.10)
    metrics = {m.label: m.value for m in app.metric}
    assert metrics["単価（1個）"] == f"¥{expected.unit_price:,}"

    click(app, "見積書を作成する")  # the quotation page
    assert not any(b.label == "解析を実行" for b in app.button)
    metrics = {m.label: m.value for m in app.metric}
    assert metrics["消費税（10%）"] == f"¥{expected.tax:,}" and metrics["見積金額（税込）"] == f"¥{expected.total:,}"
    create = next(b for b in app.button if b.label == "見積書PDFを作成")
    assert create.disabled  # the recipient is required

    app.text_input(key="doc_customer").input("サンプル電機株式会社").run(timeout=30)
    app.checkbox(key="doc_internal").check().run(timeout=30)
    click(app, "見積書PDFを作成")
    labels = [d.proto.label for d in app.get("download_button")]
    assert len(labels) == 2 and "_御見積書.pdf" in labels[0] and "見積根拠（社内用）" in labels[1]
    with open(tmp_path / "quote_log.csv", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 1 and rows[0]["customer"] == "サンプル電機株式会社"
    assert rows[0]["total"] == str(expected.total) and rows[0]["is_estimate"] == "0"
    assert rows[0]["subject"] == "bracket 製作" and rows[0]["input_files"] == "bracket.step"
    app.text_input(key="doc_customer").input("別の会社").run(timeout=30)
    assert not app.get("download_button")  # a document made for other inputs is not offered

    click(app, "← 見積に戻る")
    assert any(b.label == "解析を実行" for b in app.button)
    click(app, "見積書を作成する")
    assert app.text_input(key="doc_customer").value == "別の会社"  # the inputs are kept between the pages


def test_analysis_values_to_check_are_shown_but_the_quotation_is_not_an_estimate(tmp_path, monkeypatch):
    monkeypatch.setenv("QUOTE_LOG_PATH", str(tmp_path / "quote_log.csv"))
    app = AppTest.from_file(APP_PATH)
    app.session_state["analysis_result"] = SheetMetalAnalysis(
        status="partial", file_name="p.step", thickness_mm=2.0, blank_area_mm2=4800.0, cut_length_mm=320.0,
        hole_count=2, bend_count=1, assumptions=["Kファクター未指定のため既定値0.33で展開"])
    app.run(timeout=30)
    assert not app.exception
    notice = [w.value for w in app.warning if w.value.startswith("確認してください")]
    assert notice and "形状解析：Kファクター未指定" in notice[0]
    click(app, "見積書を作成する")
    app.text_input(key="doc_customer").input("サンプル電機株式会社").run(timeout=30)
    click(app, "見積書PDFを作成")
    assert [d.proto.label for d in app.get("download_button")][0].endswith("_御見積書.pdf")  # never 概算御見積書


def _drawing_app(**reading):
    values = {"material": "SPCC", "thickness_mm": 2.0, "quantity": 50, "surface_treatment": "NONE",
              "processes": [{"code": "TAP_M4", "count_per_part": 4}], "rush": False, "flags": [], "needs_review": [],
              "review_reasons": {}}
    app = AppTest.from_file(APP_PATH)
    app.session_state["analysis_result"] = SheetMetalAnalysis(
        status="success", file_name="p.step", thickness_mm=2.0, blank_area_mm2=4800.0, cut_length_mm=320.0,
        hole_count=2, bend_count=1)
    app.session_state["pdf_reading"] = {**values, **reading}
    app.session_state["pdf_name"] = "drawing.pdf"
    return app


def test_unregistered_process_from_the_drawing_is_quoted_separately():
    app = _drawing_app(processes=[{"code": "TAP_M4", "count_per_part": 4}, {"code": "UNREGISTERED", "count_per_part": None}])
    app.session_state["pdf_unregistered"] = ["M10タップ 6ヶ所"]
    app.run(timeout=30)
    assert not app.exception
    notice = [w.value for w in app.warning if w.value.startswith("確認してください")]
    assert notice and "M10タップ 6ヶ所（マスター未登録のため別途見積）" in notice[0]
    processes = next(i for i in app.session_state["pdf_items"] if i.field == "processes").value
    assert {"code": "UNREGISTERED", "count_per_part": None, "text": "M10タップ 6ヶ所"} in processes  # its own row


def test_unregistered_material_is_chosen_by_the_user_not_the_first_in_the_list():
    app = _drawing_app(material="UNREGISTERED")
    app.run(timeout=30)
    assert not app.exception
    assert not any(m.label.startswith("単価") for m in app.metric)  # no amount with a guessed material
    assert any("材質を選ぶと金額を出します" in e.value for e in app.error)
    assert not any(b.label.startswith("見積書を作成する") for b in app.button)
    app.selectbox(key="pdf_material").select("SUS304").run(timeout=30)
    assert not app.exception
    assert "材料費（SUS304）" in set(cost_lines(app)["項目"])


def test_chat_sits_under_the_conditions_and_reaches_the_drawing_conditions():
    app = _drawing_app()
    app.run(timeout=30)
    assert not app.exception
    assert len(app.chat_input) == 1
    app.chat_input[0].set_value("数量を10個にして").run(timeout=30)
    assert not app.exception
    assert app.number_input(key="pdf_quantity").value == 10
    assert any("10個" in m.label for m in app.metric)  # 小計（税抜、10個）
    app.chat_input[0].set_value("皿もみを2箇所追加").run(timeout=30)
    assert not app.exception
    codes = list(cost_lines(app)["コード"])
    assert "TAP_M4" in codes and codes.count("COUNTERSINK") == 1  # the chat's process is priced once, next to the drawing's


def test_drawing_conditions_survive_a_visit_to_the_quotation_page(tmp_path, monkeypatch):
    monkeypatch.setenv("QUOTE_LOG_PATH", str(tmp_path / "quote_log.csv"))
    app = _drawing_app()
    app.run(timeout=30)
    app.number_input(key="pdf_quantity").set_value(30).run(timeout=30)
    app.selectbox(key="pdf_material").select("SUS304").run(timeout=30)
    click(app, "見積書を作成する")
    assert any("30個" in m.label for m in app.metric)
    click(app, "← 見積に戻る")
    assert app.number_input(key="pdf_quantity").value == 30 and app.selectbox(key="pdf_material").value == "SUS304"
    assert "TAP_M4" in set(cost_lines(app)["コード"])


def test_similar_quotes_are_buttons_that_open_a_page_then_history_and_outcome(_history_copy):
    from src.past_quotes import HistoryStore

    past = next(q for q in HistoryStore(_history_copy).load() if q.drawing_no and q.material_code == "SPCC")
    app = AppTest.from_file(APP_PATH)
    app.session_state["analysis_result"] = SheetMetalAnalysis(
        status="success", file_name="rep.step", thickness_mm=past.thickness, blank_area_mm2=30000.0,
        cut_length_mm=900.0, hole_count=4, bend_count=2)
    app.run(timeout=30)
    app.selectbox[0].select("SPCC").run(timeout=30)
    assert not app.exception
    buttons = [b.label for b in app.button if b.label[:1].isdigit()]
    assert 1 <= len(buttons) <= 5 and not any("似ている理由：" in m.value for m in app.markdown)  # buttons only

    click(app, "見積書を作成する")
    app.text_input(key="doc_customer").input(past.customer).run(timeout=30)
    app.text_input(key="doc_dwg").input(past.drawing_no).run(timeout=30)
    click(app, "← 見積に戻る")
    first = next(b.label for b in app.button if b.label.startswith("1. "))
    assert first.startswith("1. 【リピート】") and past.drawing_no in first
    click(app, "1. 【リピート】")  # the detail page of that quote
    text = " ".join(m.value for m in app.markdown)
    assert "似ている理由：" in text and "今回との違い：" in text and "同じ顧客・同じ図番" in text
    assert not any(b.label == "解析を実行" for b in app.button)
    click(app, "← 見積に戻る")

    click(app, "見積書を作成する")
    click(app, "見積書PDFを作成")
    history = HistoryStore(_history_copy).load()
    issued = [q for q in history if q.source == "app"]
    assert len(issued) == 1 and issued[0].customer == past.customer and issued[0].drawing_no == past.drawing_no

    app.radio(key=f"outcome_{issued[0].quote_no}").set_value("受注").run(timeout=30)
    click(app, "結果を記録")
    assert next(q for q in HistoryStore(_history_copy).load() if q.quote_no == issued[0].quote_no).outcome == "受注"
    click(app, "← 見積に戻る")
    first = next(b.label for b in app.button if b.label.startswith("1. "))
    assert issued[0].date.strftime("%Y/%m/%d") in first  # the new quote is found next time


def _thickness_row(app):
    return next(n for n in app.number_input if n.label == "板厚")


def test_thickness_row_shows_the_shape_thickness_when_the_drawing_has_none():
    app = _drawing_app(thickness_mm=None)
    app.run(timeout=30)
    assert not app.exception
    row = _thickness_row(app)
    assert row.value == 2.0 and row.disabled  # the priced thickness, not 0 and not editable
    text = " ".join(m.value for m in app.markdown)
    assert "📐 形状から" in text and "➖ 記載なし" not in text
    assert any("STEPの形状から測った板厚で計算します（図面：記載なし）" in c.value for c in app.caption)
    assert not any(w.value.startswith("確認してください") and "板厚" in w.value for w in app.warning)


def test_thickness_row_warns_when_the_drawing_differs_from_the_shape():
    app = _drawing_app(thickness_mm=3.2)
    app.run(timeout=30)
    assert not app.exception
    row = _thickness_row(app)
    assert row.value == 2.0 and row.disabled
    text = " ".join(m.value for m in app.markdown)
    assert "図面は 3.2 mm" in text and "（2 mm）で計算します" in text
    notice = [w.value for w in app.warning if w.value.startswith("確認してください")]
    assert notice and "板厚：図面 3.2 mm と形状 2 mm が異なる" in notice[0]
    assert "MATERIAL" in set(cost_lines(app)["コード"])
