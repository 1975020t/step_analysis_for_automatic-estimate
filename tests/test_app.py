from pathlib import Path

from streamlit.testing.v1 import AppTest

from src.models import FlatPatternSummary, SheetMetalAnalysis


APP_PATH = Path(__file__).resolve().parents[1] / "app.py"


def test_initial_ui_has_step_input_and_analyze_action():
    app = AppTest.from_file(APP_PATH).run(timeout=20)

    assert not app.exception
    assert len(app.get("file_uploader")) == 1
    assert app.get("file_uploader")[0].label == "STEP／展開図DXFファイル"
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
