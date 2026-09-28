from pathlib import Path

from streamlit.testing.v1 import AppTest

from src.models import FlatPatternSummary, SheetMetalAnalysis


APP_PATH = Path(__file__).resolve().parents[1] / "app.py"


def test_initial_ui_has_step_input_and_analyze_action():
    app = AppTest.from_file(APP_PATH).run(timeout=20)

    assert not app.exception
    assert [u.label for u in app.get("file_uploader")][0] == "STEP／展開図DXFファイル"
    assert app.get("file_uploader")[1].label.startswith("図面PDF")
    assert [button.label for button in app.button] == ["解析を実行"]
    assert any("チャット解釈モード" in item.value for item in app.info)


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
    assert any("チャット解釈モード" in item.value for item in app.info)


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


def test_pdf_conditions_panel_shows_statuses_and_keeps_the_quote_estimate_until_confirmed():
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
    assert "✅ 確定" in text and "⚠️ 要確認" in text and "❌ 未登録" in text
    assert any("検査・証明書" in w.value for w in app.warning)
    assert any("概算見積" in w.value for w in app.warning)
    lines = app.dataframe[-1].value
    assert "TAP_M4" in set(lines["コード"]) and "RUSH" not in set(lines["コード"])  # rush is 要確認: not priced yet

    app.checkbox(key="pdf_rush_ok").check().run(timeout=30)
    app.checkbox(key="pdf_surface_treatment_ok").check().run(timeout=30)
    assert not app.exception
    assert "RUSH" in set(app.dataframe[-1].value["コード"])
    assert not any("概算見積" in w.value for w in app.warning)
