from __future__ import annotations

import csv
from pathlib import Path
from typing import Any


class MasterLoader:
    def __init__(self, data_dir: str | Path = "data") -> None:
        self.data_dir = Path(data_dir)
        self.materials = self._load_indexed("materials.csv", "material")
        self.process_rates = self._load_indexed("process_rates.csv", "process_code")
        self.complexity_rules = self._load_rows("complexity_rules.csv")

    def _load_rows(self, filename: str) -> list[dict[str, str]]:
        path = self.data_dir / filename
        with path.open(encoding="utf-8-sig", newline="") as handle:
            return list(csv.DictReader(handle))

    def _load_indexed(self, filename: str, key: str) -> dict[str, dict[str, str]]:
        return {row[key]: row for row in self._load_rows(filename)}

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

    def complexity_multiplier(self, face_count: int) -> float:
        for rule in self.complexity_rules:
            if int(rule["min_faces"]) <= face_count <= int(rule["max_faces"]):
                return float(rule["multiplier"])
        raise ValueError(f"面数 {face_count} に対応する複雑度ルールがありません")

    def llm_process_catalog(self) -> list[dict[str, Any]]:
        catalog: list[dict[str, Any]] = []
        for code, row in self.process_rates.items():
            if code in {"BASE_MILLING", "DRILLING", "SETUP"}:
                continue
            catalog.append(
                {
                    "process_code": code,
                    "display_name": row["display_name"],
                    "aliases": [part for part in row.get("aliases", "").split("|") if part],
                    "unit": row["unit"],
                }
            )
        return catalog

