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
    (tmp_path / "complexity_rules.csv").write_text(
        "rule_code,min_faces,max_faces,multiplier\nS,0,99,1\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="charge_scope"):
        MasterLoader(tmp_path)
