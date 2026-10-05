"""The masters kept in the database, as the MasterLoader that QuoteEngine reads (same columns, same strings as the
CSV files), and the per-quote overlay for prices the user entered for something the master does not have.

The overlay is a copy: the master tables are never changed by a quote (no automatic registration).
"""
from __future__ import annotations

import copy

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.models import CompanyRow, FinishRow, MaterialRow, PolicyRow, ProcessRow
from src.master_loader import MasterLoader
from src.quote_document import Company

CUSTOM_PROCESS_PREFIX = "CUSTOM_P"
CUSTOM_FINISH = "CUSTOM_FINISH"
CUSTOM_WASTE_FACTOR = "1.15"  # a material outside the master: the usual yield factor of the master's sheet materials


class RowsMasterLoader(MasterLoader):
    """MasterLoader built from rows (dicts of strings, the CSV column names) instead of files."""

    def __init__(self, materials: dict, processes: dict, finishes: dict, policy: dict) -> None:  # noqa: D107
        self.data_dir = None
        self.materials, self.process_rates, self.surface_treatments = materials, processes, finishes
        self.pricing_policy = policy
        self._validate_charge_scopes()
        self._alias_index = {
            "material": self._build_alias_index(self.materials),
            "process": self._build_alias_index(self.process_rates),
            "surface_treatment": self._build_alias_index(self.surface_treatments),
        }


def material_row(r: MaterialRow) -> dict:
    return {"material": r.code, "display_name": r.display_name, "density_kg_m3": r.density_kg_m3,
            "price_per_kg": r.price_per_kg, "waste_factor": r.waste_factor, "charge_scope": r.charge_scope,
            "aliases": r.aliases or ""}


def process_row(r: ProcessRow) -> dict:
    return {"process_code": r.code, "display_name": r.display_name, "calculation_type": r.calculation_type,
            "unit_price": r.unit_price, "unit": r.unit, "charge_scope": r.charge_scope, "aliases": r.aliases or ""}


def finish_row(r: FinishRow) -> dict:
    return {"treatment_code": r.code, "display_name": r.display_name, "unit_price": r.unit_price,
            "charge_scope": r.charge_scope, "aliases": r.aliases or ""}


def load_masters(session: Session) -> RowsMasterLoader:
    materials = {r.code: material_row(r) for r in session.scalars(select(MaterialRow).order_by(MaterialRow.sort, MaterialRow.code))}
    processes = {r.code: process_row(r) for r in session.scalars(select(ProcessRow).order_by(ProcessRow.sort, ProcessRow.code))}
    finishes = {r.code: finish_row(r) for r in session.scalars(select(FinishRow).order_by(FinishRow.sort, FinishRow.code))}
    policy = {r.key: r.value for r in session.scalars(select(PolicyRow).order_by(PolicyRow.sort))}
    return RowsMasterLoader(materials, processes, finishes, policy)


def load_company_db(session: Session) -> Company:
    """Same fields and rules as src.quote_document.load_company, from the company table."""
    values = {r.key: (r.value or "").strip() for r in session.scalars(select(CompanyRow).order_by(CompanyRow.sort))}
    remark_keys = sorted((k for k in values if k.startswith("remark_")), key=lambda k: int(k[7:]) if k[7:].isdigit() else 0)
    return Company(
        name=values.get("name", ""), postal_code=values.get("postal_code", ""), address=values.get("address", ""),
        tel=values.get("tel", ""), fax=values.get("fax", ""), registration_no=values.get("registration_no", ""),
        contact=values.get("contact", ""), payment_terms=values.get("payment_terms", ""),
        delivery_place=values.get("delivery_place") or "貴社指定場所",
        remarks=[values[k] for k in remark_keys if values[k]], source="database")


def with_custom(masters: MasterLoader, material: dict | None = None, finish: dict | None = None,
                processes: list[dict] | None = None) -> MasterLoader:
    """A copy of the masters with this quote's own rows (unit prices entered by the user).
    material: {"name", "price_per_kg", "density_kg_m3"} (code = the name);
    finish: {"name", "unit_price"} (code CUSTOM_FINISH); processes: [{"code", "name", "unit_price"}] per piece."""
    if not (material or finish or processes):
        return masters
    overlay = copy.copy(masters)
    overlay.materials = dict(masters.materials)
    overlay.process_rates = dict(masters.process_rates)
    overlay.surface_treatments = dict(masters.surface_treatments)
    if material:
        overlay.materials[material["name"]] = {
            "material": material["name"], "display_name": material["name"],
            "density_kg_m3": f"{float(material['density_kg_m3']):g}", "price_per_kg": f"{float(material['price_per_kg']):g}",
            "waste_factor": CUSTOM_WASTE_FACTOR, "charge_scope": "per_part", "aliases": ""}
    if finish:
        overlay.surface_treatments[CUSTOM_FINISH] = {
            "treatment_code": CUSTOM_FINISH, "display_name": finish["name"], "unit_price": f"{float(finish['unit_price']):g}",
            "charge_scope": "per_part", "aliases": ""}
    for p in processes or []:
        overlay.process_rates[p["code"]] = {
            "process_code": p["code"], "display_name": p["name"], "calculation_type": "per_piece",
            "unit_price": f"{float(p['unit_price']):g}", "unit": "piece", "charge_scope": "per_part", "aliases": ""}
    return overlay
