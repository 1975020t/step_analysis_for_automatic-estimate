"""NAIVE BASELINE for the DXF harness - not production code.

What a first, straightforward implementation would do: trust the layer names. Cut geometry = entities on
a layer called CUT / 外形, bend lines = entities on BEND / 曲げ線. Endpoints within 0.02 mm are merged,
the largest closed region is the part, its interior loops are holes. Always returns "success".

It exists to show how hard each variant is (analysis/dxf_golden_design.md) and to exercise the harness.
"""
from __future__ import annotations

from pathlib import Path

import ezdxf
import numpy as np
from ezdxf import path as ezpath
from shapely.geometry import LineString
from shapely.ops import polygonize, unary_union

from src.models import MetricQuality, SheetMetalAnalysis

CUT_LAYERS = {"CUT", "外形"}
BEND_LAYERS = {"BEND", "曲げ線"}


def _snap(lines, tol=0.02):
    """Merge endpoints closer than `tol` (union-find over a grid of cell size `tol`)."""
    ends = np.array([p for line in lines for p in (line[0], line[-1])], dtype=float)
    parent = list(range(len(ends)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    cells = {}
    for i, (x, y) in enumerate(ends):
        cx, cy = int(np.floor(x / tol)), int(np.floor(y / tol))
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for j in cells.get((cx + dx, cy + dy), ()):
                    if np.hypot(*(ends[i] - ends[j])) <= tol:
                        parent[find(i)] = find(j)
        cells.setdefault((cx, cy), []).append(i)
    groups = {}
    for i in range(len(ends)):
        groups.setdefault(find(i), []).append(i)
    centre = {}
    for members in groups.values():
        c = ends[members].mean(0)
        for i in members:
            centre[i] = (float(c[0]), float(c[1]))
    for j, line in enumerate(lines):
        line[0], line[-1] = centre[2 * j], centre[2 * j + 1]
    return [LineString(line) for line in lines if len(line) >= 2]


class NaiveLayerDxfAnalyzer:
    def __init__(self, thickness_mm: float, k_factor: float = 0.33, k_factor_is_default: bool = True):
        self.t = thickness_mm

    def analyze(self, path) -> SheetMetalAnalysis:
        doc = ezdxf.readfile(str(path))
        scale = 25.4 if doc.header.get("$INSUNITS", 4) == 1 else 1.0
        cut, bends = [], 0
        for e in doc.modelspace():
            for x in (list(e.virtual_entities()) if e.dxftype() == "INSERT" else [e]):
                if x.dxftype() not in ("LINE", "ARC", "CIRCLE", "LWPOLYLINE"):
                    continue
                if e.dxf.layer in BEND_LAYERS:
                    bends += 1
                elif e.dxf.layer in CUT_LAYERS:
                    cut.append([(v.x * scale, v.y * scale) for v in ezpath.make_path(x).flattening(0.001)])
        polys = sorted(polygonize(unary_union(_snap(cut))), key=lambda g: -g.area) if cut else []
        name = Path(path).name
        if not polys:
            return SheetMetalAnalysis(status="unsupported", file_name=name, reason_code="NO_CUT_CONTOUR",
                                      reason_codes=["NO_CUT_CONTOUR"], message="切断輪郭が見つかりません")
        part = polys[0]
        high = MetricQuality(method="naive_layer_polygonize", confidence="high")
        return SheetMetalAnalysis(
            status="success", file_name=name, thickness_mm=self.t, blank_area_mm2=part.area,
            cut_length_mm=part.exterior.length + sum(i.length for i in part.interiors),
            hole_count=len(part.interiors), bend_count=bends,
            metric_quality={k: high for k in ("thickness_mm", "blank_area_mm2", "cut_length_mm", "hole_count", "bend_count")})
