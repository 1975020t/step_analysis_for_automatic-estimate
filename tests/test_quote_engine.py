from src.master_loader import MasterLoader
from src.models import AdditionalProcess, CadFeatures, QuoteCondition
from src.quote_engine import QuoteEngine


def features() -> CadFeatures:
    return CadFeatures(
        bbox_x_mm=100,
        bbox_y_mm=80,
        bbox_z_mm=20,
        volume_mm3=120_000,
        surface_area_mm2=22_000,
        face_count=18,
        edge_count=42,
        vertex_count=24,
        estimated_hole_count=4,
        estimated_hole_diameters_mm=[10, 10, 10, 10],
    )


def engine() -> QuoteEngine:
    return QuoteEngine(MasterLoader("data"))


def test_material_change_changes_material_cost():
    quote_engine = engine()
    al = quote_engine.calculate(features(), QuoteCondition(material="AL5052", quantity=10))
    sus = quote_engine.calculate(features(), QuoteCondition(material="SUS304", quantity=10))
    assert al.lines[0].amount != sus.lines[0].amount


def test_quantity_change_changes_price():
    quote_engine = engine()
    ten = quote_engine.calculate(features(), QuoteCondition(material="AL5052", quantity=10))
    fifty = quote_engine.calculate(features(), QuoteCondition(material="AL5052", quantity=50))
    assert fifty.final_price > ten.final_price


def test_additional_processes_use_master_prices_and_removal_restores_price():
    quote_engine = engine()
    base_condition = QuoteCondition(material="AL5052", quantity=10)
    base = quote_engine.calculate(features(), base_condition)
    countersink = AdditionalProcess(process_code="COUNTERSINK", quantity=2, unit="hole")
    buff = AdditionalProcess(process_code="BUFF_400", quantity=1, unit="job")
    changed = quote_engine.calculate(
        features(),
        QuoteCondition(
            material="AL5052", quantity=10, additional_processes=[countersink, buff]
        ),
    )
    assert changed.subtotal_cost - base.subtotal_cost == 1_200 + 3_500
    removed = quote_engine.calculate(features(), base_condition)
    assert removed.final_price == base.final_price

