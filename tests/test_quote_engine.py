from src.master_loader import MasterLoader
from src.models import AdditionalProcess, MetricQuality, QuoteCondition, SheetMetalAnalysis
from src.quote_engine import QuoteEngine, QuoteUnavailableError
import pytest


def analysis(status="success") -> SheetMetalAnalysis:
    return SheetMetalAnalysis(
        status=status, file_name="part.step", thickness_mm=2,
        blank_area_mm2=8_000, cut_length_mm=360,
        hole_count=4, bend_count=3,
        metric_quality={
            "blank_area_mm2": MetricQuality(method="geometric", confidence="high")
        },
    )


def engine() -> QuoteEngine:
    return QuoteEngine(MasterLoader("data"))


def test_material_change_changes_material_cost():
    quote_engine = engine()
    al = quote_engine.calculate(analysis(), QuoteCondition(material="AL5052", quantity=10))
    sus = quote_engine.calculate(analysis(), QuoteCondition(material="SUS304", quantity=10))
    assert al.lines[0].amount != sus.lines[0].amount


def test_quantity_change_changes_price():
    quote_engine = engine()
    ten = quote_engine.calculate(analysis(), QuoteCondition(material="AL5052", quantity=10))
    fifty = quote_engine.calculate(analysis(), QuoteCondition(material="AL5052", quantity=50))
    assert fifty.final_price > ten.final_price


def test_additional_processes_use_master_prices_and_removal_restores_price():
    quote_engine = engine()
    base_condition = QuoteCondition(material="AL5052", quantity=10)
    base = quote_engine.calculate(analysis(), base_condition)
    countersink = AdditionalProcess(process_code="COUNTERSINK", quantity=2, unit="hole")
    buff = AdditionalProcess(process_code="BUFF_400", quantity=1, unit="job")
    changed = quote_engine.calculate(
        analysis(),
        QuoteCondition(
            material="AL5052", quantity=10, additional_processes=[countersink, buff]
        ),
    )
    assert changed.subtotal_cost - base.subtotal_cost == pytest.approx(12_000 + 35_000)
    removed = quote_engine.calculate(analysis(), base_condition)
    assert removed.final_price == base.final_price


def test_per_part_and_per_order_scopes_are_applied():
    quote = engine().calculate(analysis(), QuoteCondition(material="AL5052", quantity=10))
    by_code = {line.code: line for line in quote.lines}
    assert by_code["LASER_CUT"].quantity == 3_600
    assert by_code["PIERCE"].quantity == 40
    assert by_code["BEND"].quantity == 30
    assert by_code["SETUP"].quantity == 1


def test_missing_required_metric_blocks_quote():
    invalid = analysis().model_copy(update={"cut_length_mm": None})
    with pytest.raises(QuoteUnavailableError, match="cut_length_mm"):
        engine().calculate(invalid, QuoteCondition(material="AL5052"))


def test_partial_analysis_produces_explicit_estimate():
    partial = analysis(status="partial").model_copy(update={
        "warnings": ["切断長は概算です。"]
    })
    quote = engine().calculate(partial, QuoteCondition(material="AL5052", quantity=2))
    assert quote.is_estimate is True
    assert quote.warnings == ["切断長は概算です。"]
