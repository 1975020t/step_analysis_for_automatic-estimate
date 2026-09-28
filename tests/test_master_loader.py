from pathlib import Path

import pytest

from src.master_loader import MasterLoader


def test_all_material_and_process_rows_declare_charge_scope():
    masters = MasterLoader("data")
    assert {row["charge_scope"] for row in masters.materials.values()} <= {"per_part", "per_order"}
    assert {row["charge_scope"] for row in masters.process_rates.values()} <= {"per_part", "per_order"}


def test_invalid_charge_scope_is_rejected(tmp_path: Path):
    (tmp_path / "materials.csv").write_text(
        "material,density_kg_m3,price_per_kg,waste_factor,charge_scope\nX,1,1,1,unknown\n",
        encoding="utf-8",
    )
    (tmp_path / "process_rates.csv").write_text(
        "process_code,display_name,calculation_type,unit_price,unit,charge_scope,aliases\n"
        "SETUP,setup,flat,1,job,per_order,\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="charge_scope"):
        MasterLoader(tmp_path)


def test_aliases_resolve_notation_variants_to_master_codes():
    masters = MasterLoader("data")
    assert masters.resolve_alias("material", "ＳＰＣＣ－ＳＤ") == "SPCC"
    assert masters.resolve_alias("material", "sus304 2b") == "SUS304"
    assert masters.resolve_alias("material", "A5052P-H32") == "AL5052"
    assert masters.resolve_alias("surface_treatment", "三価クロメート(有色)") == "ZINC_YELLOW"
    assert masters.resolve_alias("process", "M4 TAP") == "TAP_M4"
    assert masters.resolve_alias("process", "PEMナット") == "PRESS_NUT"


def test_unknown_terms_are_not_mapped_to_similar_codes():
    masters = MasterLoader("data")
    assert masters.resolve_alias("material", "SUS430") is None
    assert masters.resolve_alias("material", "SS304") is None  # not SS400, not SUS304
    assert masters.resolve_alias("surface_treatment", "クロムめっき") is None
    assert masters.resolve_alias("process", "M10タップ") is None


def test_pricing_policy_and_surface_treatments_are_loaded():
    masters = MasterLoader("data")
    assert masters.policy("margin_rate", 0.0) == 0.25
    assert masters.policy("rush_surcharge_rate", 0.0) > 0
    assert masters.surface_treatment("NONE")["unit_price"] == "0"
    assert {row["charge_scope"] for row in masters.surface_treatments.values()} == {"per_part"}
