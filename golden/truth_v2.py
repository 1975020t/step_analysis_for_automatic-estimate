"""golden_v2 truth: fixes the hole count of golden_v1 (the generator itself is unchanged).

Bug in golden_v1 (golden/sheetgen.py, SheetPart.build): `hole_count` is the number of interiors of
the shapely layout `flat_outline().difference(cutouts)`. A bend-relief cut-out (golden/sampler.py,
`_tray`) is placed so that it ends exactly on the base edge; in floating point it can stop ~1e-14 mm
short of it. Shapely then keeps a zero-width ligament and counts the relief as a closed hole, while
the folded and the unfolded B-Rep solids (OpenCascade, tolerance 1e-7 mm) both have an open notch.
The area and cut length of golden_v1 come from the unfolded solid and are not affected.

golden_v2 takes the hole count from the same unfolded solid that already defines the area and the
cut length: inner wires of its top face. It never uses the analyzer under test.
"""
from __future__ import annotations


def hole_count_from_flat_solid(flat) -> int:
    top = max((face for face in flat.Faces()
               if face.geomType() == "PLANE" and face.normalAt().z > 0.999), key=lambda face: face.Area())
    return len(top.Wires()) - 1


def apply_v2(built) -> dict:
    """Update the truth dict of a built part in place to golden_v2 semantics and return it."""
    _, flat, truth = built
    v1 = truth["hole_count"]
    truth["hole_count"] = hole_count_from_flat_solid(flat)
    truth["truth_version"] = "v2"
    if truth["hole_count"] != v1:
        truth["v1_hole_count"] = v1
    return truth
