from __future__ import annotations

import csv
import re
import unicodedata
from pathlib import Path
from typing import Any

UNREGISTERED = "UNREGISTERED"  # a condition that is written on the drawing but has no master row


def normalize_term(text: str) -> str:
    """Normalization used for alias matching: NFKC (full-width -> half-width), upper case, no spaces or
    separators. "ＳＵＳ３０４－２Ｂ" and "sus304 2b" both become "SUS3042B"."""
    text = unicodedata.normalize("NFKC", text or "").upper()
    text = text.replace("×", "X")
    return re.sub(r"[\s\-_・/()（）\[\]「」,.、。:：]+", "", text)


class MasterLoader:
    ALLOWED_CHARGE_SCOPES = {"per_part", "per_order"}

    def __init__(self, data_dir: str | Path = "data") -> None:
        self.data_dir = Path(data_dir)
        self.materials = self._load_indexed("materials.csv", "material")
        self.process_rates = self._load_indexed("process_rates.csv", "process_code")
        self.surface_treatments = self._load_indexed("surface_treatments.csv", "treatment_code", optional=True)
        self.pricing_policy = {row["key"]: row["value"] for row in self._load_rows("pricing_policy.csv", optional=True)}
        self._validate_charge_scopes()
        self._alias_index = {
            "material": self._build_alias_index(self.materials),
            "process": self._build_alias_index(self.process_rates),
            "surface_treatment": self._build_alias_index(self.surface_treatments),
        }

    def _load_rows(self, filename: str, optional: bool = False) -> list[dict[str, str]]:
        path = self.data_dir / filename
        if optional and not path.exists():
            return []
        with path.open(encoding="utf-8-sig", newline="") as handle:
            return list(csv.DictReader(handle))

    def _load_indexed(self, filename: str, key: str, optional: bool = False) -> dict[str, dict[str, str]]:
        return {row[key]: row for row in self._load_rows(filename, optional)}

    # ------------------------------------------------------------ lookups
    @property
    def material_names(self) -> list[str]:
        return list(self.materials)

    def material(self, code: str) -> dict[str, Any]:
        try:
            return self.materials[code]
        except KeyError as exc:
            raise ValueError(f"未登録の材料です: {code}") from exc

    def process(self, code: str) -> dict[str, Any]:
        try:
            return self.process_rates[code]
        except KeyError as exc:
            raise ValueError(f"未登録の工程です: {code}") from exc

    def surface_treatment(self, code: str) -> dict[str, Any]:
        try:
            return self.surface_treatments[code]
        except KeyError as exc:
            raise ValueError(f"未登録の表面処理です: {code}") from exc

    def policy(self, key: str, default: float) -> float:
        value = self.pricing_policy.get(key)
        return float(value) if value not in (None, "") else default

    # ------------------------------------------------------------ aliases
    @staticmethod
    def aliases_of(row: dict[str, str]) -> list[str]:
        return [part for part in (row.get("aliases") or "").split("|") if part]

    def _build_alias_index(self, rows: dict[str, dict[str, str]]) -> dict[str, str]:
        index: dict[str, str] = {}
        for code, row in rows.items():
            for term in [code, row.get("display_name", ""), *self.aliases_of(row)]:
                key = normalize_term(term)
                if key:
                    index.setdefault(key, code)
        return index

    def resolve_alias(self, kind: str, text: str) -> str | None:
        """Exact match of `text` against a master code, display name or alias after normalize_term().
        kind: "material" | "process" | "surface_treatment". Returns the code, or None when nothing matches
        (the caller decides whether that means UNREGISTERED or "not a condition at all"). Deliberately no
        fuzzy matching: an unknown material must never be silently mapped to a similar registered one."""
        return self._alias_index[kind].get(normalize_term(text))

    # ------------------------------------------------------------ LLM catalog
    def llm_process_catalog(self) -> list[dict[str, Any]]:
        catalog: list[dict[str, Any]] = []
        for code, row in self.process_rates.items():
            if code in {"LASER_CUT", "PIERCE", "BEND", "SETUP"}:
                continue
            catalog.append(
                {
                    "process_code": code,
                    "display_name": row["display_name"],
                    "aliases": self.aliases_of(row),
                    "unit": row["unit"],
                }
            )
        return catalog

    @property
    def llm_material_codes(self) -> list[str]:
        return sorted(self.materials)

    @property
    def llm_process_codes(self) -> list[str]:
        return sorted(item["process_code"] for item in self.llm_process_catalog())

    def _validate_charge_scopes(self) -> None:
        for kind, rows in (("材料", self.materials), ("工程", self.process_rates), ("表面処理", self.surface_treatments)):
            for code, row in rows.items():
                scope = row.get("charge_scope", "")
                if scope not in self.ALLOWED_CHARGE_SCOPES:
                    raise ValueError(f"{kind} {code} のcharge_scopeが不正です: {scope}")
