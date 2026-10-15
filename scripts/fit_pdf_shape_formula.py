"""Fit the formula of src/pdf_shape.py (blank area and cut length from the overall dimensions and the counts).

    python scripts/fit_pdf_shape_formula.py --data golden_data

The fit uses the STEP development set (golden_data: each part's STEP and its frozen truth
golden/datasets/golden_v2.json), never the drawing PDFs the estimate is evaluated on. The overall dimensions
are the bounding box of the folded part (what a drawing's overall dimensions show). Least squares on the
relative error. Prints the coefficients to copy into CUT_COEF / AREA_COEF and the fit's own errors.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def features(dims, bends, holes):
    a, b, c = sorted(dims, reverse=True)
    return [a + b + c, bends, holes, 1.0], [a * b, c * (a + b), c * c]


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--data", default="golden_data")
    p.add_argument("--truth", default=str(ROOT / "golden" / "datasets" / "golden_v2.json"))
    args = p.parse_args()
    import cadquery as cq

    parts = json.loads(Path(args.truth).read_text(encoding="utf-8"))["parts"]
    xc, xa, yc, ya = [], [], [], []
    for part in parts:
        path = Path(args.data) / part["level"] / f"{part['name']}.step"
        try:
            box = cq.importers.importStep(str(path)).val().BoundingBox()
        except Exception:  # noqa: BLE001 - a part the generator could not write
            continue
        fc, fa = features((box.xlen, box.ylen, box.zlen), part["bend_count"], part["hole_count"])
        xc.append(fc)
        xa.append(fa)
        yc.append(part["cut_length_mm"])
        ya.append(part["blank_area_mm2"])
    out = {}
    for name, x, y in (("cut", xc, yc), ("area", xa, ya)):
        x, y = np.array(x), np.array(y)
        w = 1.0 / y
        coef, *_ = np.linalg.lstsq(x * w[:, None], y * w, rcond=None)
        err = np.abs(x @ coef / y - 1)
        out[name] = coef
        print(f"{name}: coefficients {np.round(coef, 3).tolist()}  parts {len(y)}  "
              f"median error {statistics.median(err):.3f}  p90 {np.percentile(err, 90):.3f}")
    print("CUT_COEF  = perimeter, bend, hole, const =", np.round(out["cut"], 3).tolist())
    print("AREA_COEF = ab, c_ab, cc =", np.round(out["area"], 3).tolist())
    return 0


if __name__ == "__main__":
    sys.exit(main())
