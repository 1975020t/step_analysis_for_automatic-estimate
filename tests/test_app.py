from pathlib import Path

from streamlit.testing.v1 import AppTest

from src.models import FlatPatternSummary, SheetMetalAnalysis


APP_PATH = Path(__file__).resolve().parents[1] / "app.py"


def test_initial_ui_has_step_input_and_analyze_action():
    app = AppTest.from_file(APP_PATH).run(timeout=20)

    assert not app.exception
    assert len(app.get("file_uploader")) == 1
    assert app.get("file_uploader")[0].label == "STEPファイル"
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
