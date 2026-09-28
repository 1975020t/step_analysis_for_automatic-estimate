"""How the drawing conditions and the manual input (API, chat) are combined into the priced condition."""
from __future__ import annotations

from src.models import SheetMetalAnalysis
from src.services.estimate import EstimateService
from src.services.schemas import AdditionalProcessInput, ConditionInput
from src.services.settings import Settings

SERVICE = EstimateService(Settings.from_env(), reader_factory=lambda: None)
SHAPE = SheetMetalAnalysis(status="success", file_name="p.step", thickness_mm=2.0, blank_area_mm2=4800.0,
                           cut_length_mm=320.0, hole_count=2, bend_count=1)


def drawing(**reading):
    values = dict(material="SPCC", thickness_mm=2.0, quantity=None, surface_treatment=None,
                  processes=[{"code": "TAP_M4", "count_per_part": 4}], rush=False, needs_review=[], review_reasons={})
    return SERVICE.drawing_context({**values, **reading}, "d.pdf")


def test_manual_quantity_rush_and_finish_are_not_dropped_by_a_drawing():
    outcome = SERVICE.quote(SHAPE, ConditionInput(material="SPCC", quantity=100, rush=True,
                                                  surface_treatment="ZINC_CLEAR"), drawing())
    c = outcome.condition
    assert (c.quantity, c.rush, c.surface_treatment) == (100, True, "ZINC_CLEAR")
    assert not c.pending and not outcome.is_estimate
    assert "RUSH" in {line.code for line in outcome.quote.lines}


def test_manual_input_replaces_a_different_drawing_value_and_says_so():
    d = SERVICE.with_manual_input(drawing(quantity=50), ConditionInput(material="SPCC", quantity=100))
    item = next(i for i in d.items if i.field == "quantity")
    assert item.value == 100 and item.status == "確定" and "図面は 50 個" in item.display


def test_without_manual_input_the_drawing_decides():
    outcome = SERVICE.quote(SHAPE, ConditionInput(material="SPCC"), drawing(quantity=50))
    assert outcome.condition.quantity == 50
    missing = SERVICE.quote(SHAPE, ConditionInput(material="SPCC"), drawing())
    assert missing.is_estimate and any(r.startswith("数量：図面に記載なし") for r in missing.reasons)


def test_manual_no_finish_is_a_confirmed_value():
    outcome = SERVICE.quote(SHAPE, ConditionInput(material="SPCC", quantity=10, surface_treatment=None), drawing())
    assert not outcome.is_estimate


def test_a_process_on_the_drawing_and_in_the_input_is_counted_once():
    outcome = SERVICE.quote(SHAPE, ConditionInput(material="SPCC", quantity=10, additional_processes=[
        AdditionalProcessInput(process_code="TAP_M4", quantity=4, source="chat")]), drawing())
    taps = [p for p in outcome.condition.additional_processes if p.process_code == "TAP_M4"]
    assert len(taps) == 1 and taps[0].quantity == 4
    line = next(line for line in outcome.quote.lines if line.code == "TAP_M4")
    assert line.quantity == 4 * 10  # 10 parts × 4 holes, not 8 holes each


def test_a_dxf_without_bend_lines_is_confirmed_flat_only_when_the_user_says_so():
    import ezdxf

    doc = ezdxf.new()
    doc.header["$INSUNITS"] = 4
    doc.modelspace().add_lwpolyline([(0, 0), (100, 0), (100, 60), (0, 60)], close=True)
    import io
    buffer = io.StringIO()
    doc.write(buffer)
    data = buffer.getvalue().encode()
    assert SERVICE.analyze_bytes(data, "flat.dxf", thickness_mm=1.0).status == "partial"
    assert SERVICE.analyze_bytes(data, "flat.dxf", thickness_mm=1.0, flat_confirmed=True).status == "success"
