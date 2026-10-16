"""Fold a flat pattern measured on a drawing into the part, to find the drawing's scale from its written dimensions.

    fold_scale(flat, dimensions_mm, thickness_mm) -> dict | None

`flat` is the result of src/pdf_flat_pattern.measure_flat_pattern: the panels of the flat pattern (convex hulls in
page pixels) and the bend lines joining them. The flat pattern on a drawing has no scale of its own (it is drawn
"for reference" at whatever size fits the sheet), but the part's overall dimensions are written on the drawing.

The flat pattern is folded along its bend lines into a 3D part (a rigid fold of each panel about its bend line,
like a press brake), trying bend angles: all 90 degrees first, then one or two bends at another usual angle
(30 / 45 / 60 / 120 / 135 / 180), each up or down. A fold whose bounding box has the proportions of the written
overall dimensions gives the scale (pixels per mm): every one of the two or three dimensions must agree within
the sheet thickness and 1.5 %. A flat part (no bend line) is the same test with no folding.

Nothing here is read by an LLM: the dimensions are the numbers written on the drawing (src/pdf_shape.py reads them
and checks them against the text layer of a CAD PDF), and the scale is accepted only when the geometry agrees
with all of them. When several folds agree at different scales the answer is ambiguous and is reported as such.
"""
from __future__ import annotations

import itertools
import math

import numpy as np

ODD_ANGLES = (30.0, 45.0, 60.0, 120.0, 135.0, 180.0)
MAX_FOLDS = 10           # more bend lines than this: only the all-90-degree folds are tried
MAX_FOLDS_TWO_ODD = 5    # two bends at an unusual angle are tried up to this many bend lines


def _rotation(point, direction, angle_deg: float) -> np.ndarray:
    """4x4 rotation about the line through `point` (x, y, 0) along `direction` (dx, dy, 0)."""
    d = np.array([direction[0], direction[1], 0.0])
    d /= np.linalg.norm(d)
    a = math.radians(angle_deg)
    K = np.array([[0, -d[2], d[1]], [d[2], 0, -d[0]], [-d[1], d[0], 0]])
    R = np.eye(3) + math.sin(a) * K + (1 - math.cos(a)) * K @ K
    p = np.array([point[0], point[1], 0.0])
    M = np.eye(4)
    M[:3, :3] = R
    M[:3, 3] = p - R @ p
    return M


def _tree(n_panels: int, folds: list, base: int) -> list[tuple[int, int, int]]:
    """(parent, child, fold index) in breadth-first order from the base panel (extra joints are ignored)."""
    seen, order, todo = {base}, [], [base]
    while todo:
        cur = todo.pop(0)
        for i, f in enumerate(folds):
            a, b = f["panels"]
            for u, v in ((a, b), (b, a)):
                if u == cur and v not in seen:
                    seen.add(v)
                    order.append((u, v, i))
                    todo.append(v)
    return order


def fold_extents(panels: list, folds: list, base: int, angles: list[float]) -> np.ndarray:
    """Extents (sorted, largest first) of the panels folded with the given signed angle per tree edge."""
    tree = _tree(len(panels), folds, base)
    T = {base: np.eye(4)}
    for (parent, child, i), ang in zip(tree, angles):
        f = folds[i]
        T[child] = T[parent] @ _rotation(f["point"], f["dir"], ang)
    pts = []
    for pid, M in T.items():
        P = np.asarray(panels[pid], dtype=float)
        if P.size == 0:
            continue
        H = np.column_stack([P, np.zeros(len(P)), np.ones(len(P))])
        pts.append((H @ M.T)[:, :3])
    allp = np.vstack(pts)
    return np.sort(allp.max(0) - allp.min(0))[::-1]


def _assignments(n: int):
    """Signed angles per tree edge: all 90 first, then one, then two unusual angles."""
    if n == 0:
        yield ()
        return
    signs = [(1.0,) + s for s in itertools.product((1.0, -1.0), repeat=n - 1)]   # mirror images give the same box
    for s in signs:
        yield tuple(90.0 * x for x in s)
    if n > MAX_FOLDS:
        return
    for i in range(n):
        for odd in ODD_ANGLES:
            for s in signs:
                yield tuple((odd if k == i else 90.0) * x for k, x in enumerate(s))
    if n > MAX_FOLDS_TWO_ODD:
        return
    for i, j in itertools.combinations(range(n), 2):
        for o1, o2 in itertools.product(ODD_ANGLES, repeat=2):
            for s in signs:
                yield tuple((o1 if k == i else o2 if k == j else 90.0) * x for k, x in enumerate(s))


def _fit(extents: np.ndarray, dims: list[float], t: float):
    """Scale (px per mm) and the worst residual (in tolerances) of a folded box against the written dimensions.
    The fold is of the drawn outline (about the sheet's middle), the written dimensions are outside: half the
    thickness is taken off each."""
    target = np.array([max(0.0, d - t / 2) for d in dims])
    E = extents[:len(dims)]
    use = target > 3 * t   # thickness-sized dimensions say nothing about the scale
    if use.sum() == 0:
        return None
    k = float((E[use] * target[use]).sum() / (target[use] ** 2).sum())
    if k <= 0:
        return None
    tol = t + 1.0 + 0.02 * np.array(dims) + 2.0 / k   # sheet thickness and bend radii, reading, 2 px of drawing
    worst = float(np.max(np.abs(E / k - target) / tol))
    return k, worst


def _square_up(panels: list, folds: list) -> tuple[list, list]:
    """Turn the flat pattern so that its bend lines (or, with none, the sides of its smallest enclosing rectangle)
    run along the axes: a scan is often a degree or two askew, which would widen the folded box."""
    if folds:
        angs = [math.atan2(f["dir"][1], f["dir"][0]) % (math.pi / 2) for f in folds]
        ref = angs[0]
        a = float(np.median([ref + ((x - ref + math.pi / 4) % (math.pi / 2)) - math.pi / 4 for x in angs]))
    else:
        P = np.vstack([np.asarray(p, dtype=float) for p in panels if len(p)])
        best = None
        for i in range(len(P)):
            e = P[(i + 1) % len(P)] - P[i]
            if not e.any():
                continue
            th = math.atan2(e[1], e[0])
            c, s_ = math.cos(-th), math.sin(-th)
            Q = P @ np.array([[c, s_], [-s_, c]])
            area = float(np.ptp(Q[:, 0]) * np.ptp(Q[:, 1]))
            if best is None or area < best[0]:
                best = (area, th)
        a = best[1] if best else 0.0
    c, s_ = math.cos(-a), math.sin(-a)
    R = np.array([[c, -s_], [s_, c]])
    rot = lambda pts: (np.asarray(pts, dtype=float) @ R.T).tolist()
    new_panels = [rot(p) if len(p) else p for p in panels]
    new_folds = [{**f, "point": rot([f["point"]])[0], "dir": rot([f["dir"]])[0]} for f in folds]
    return new_panels, new_folds


def fold_scale(flat: dict, dimensions_mm: list[float], thickness_mm: float | None) -> dict | None:
    """The drawing scale of a measured flat pattern, from folding it into the written overall dimensions.

    Returns {"px_per_mm", "angles", "extents_mm", "worst", "ambiguous", "candidates"} or None (no fold agrees)."""
    panels, folds, base = flat.get("panels_px") or [], flat.get("folds_px") or [], int(flat.get("base_panel") or 0)
    dims = sorted([float(d) for d in dimensions_mm if d and d > 0], reverse=True)[:3]
    if not panels or len(dims) < 2:
        return None
    t = float(thickness_mm or 1.0)
    panels, folds = _square_up(panels, folds)
    n = len(_tree(len(panels), folds, base))
    fits = []
    for angles in _assignments(n):
        ext = fold_extents(panels, folds, base, list(angles))
        r = _fit(ext, dims, t)
        if r is not None and r[1] <= 1.0:
            fits.append((r[1], r[0], angles, ext))
        if fits and len(fits) > 2000:
            break
    if not fits:
        return None
    # one answer per scale (agreeing folds within 2 %), ranked by: the lengths along the bend lines (kept by any
    # fold) matching written dimensions, then the fewest unusual bend angles, then the closest fit
    spans = [s["px"] for s in flat.get("spans_px") or [] if s.get("kind") in ("plate", "along_bends")]

    def odd(angles):
        return sum(1 for a in angles if abs(abs(a) - 90) > 1)

    def span_matches(k):
        return sum(1 for L in spans if any(abs(L / k - d) <= t + 0.5 + 0.015 * d for d in dims))

    clusters: list[list] = []
    for f in sorted(fits, key=lambda f: (odd(f[2]), f[0])):
        for c in clusters:
            if abs(f[1] / c[0][1] - 1) < 0.02:
                c.append(f)
                break
        else:
            clusters.append([f])
    ranked = sorted(clusters, key=lambda c: (-span_matches(c[0][1]), odd(c[0][2]), c[0][0]))
    best = ranked[0][0]
    key = lambda c: (span_matches(c[0][1]), odd(c[0][2]))
    tied = [c for c in ranked if key(c) == key(ranked[0])]
    worst, k, angles, ext = best
    return {"px_per_mm": k, "angles": [round(a) for a in angles], "extents_mm": [round(float(e) / k, 1) for e in ext],
            "worst": round(worst, 3), "span_matches": span_matches(k), "unusual_angles": odd(angles),
            "ambiguous": len(tied) > 1, "candidates": [round(c[0][1], 4) for c in ranked[:5]]}


# ---------------------------------------------------------------- a part described as base + flanges
EDGES = ("right", "left", "back", "front")


def build_model(model: dict, thickness_mm: float | None) -> dict | None:
    """The flat pattern of a part described as a rectangular base with flanges bent from its edges (and flanges
    bent from the far edge of a flange, for Z bends, hems and steps), as Claude reads it from the views of a
    drawing with no flat pattern (src/pdf_shape.py). Lengths in mm.

    Returns {"panels", "folds", "base", "angles", "cut_length_mm", "blank_area_mm2", "holes"} or None."""
    from shapely.geometry import box
    from shapely.ops import unary_union

    t = float(thickness_mm or 1.0)
    base = model.get("base") or {}
    W, D = _num(base.get("width")), _num(base.get("depth"))
    if not W or not D:
        return None
    rects = [(0.0, 0.0, W, D)]
    folds, angles = [], []
    for fl in model.get("flanges") or []:
        edge = fl.get("edge")
        if edge not in EDGES:
            continue
        along = W if edge in ("back", "front") else D
        span = _num(fl.get("width")) or along
        offset = _num(fl.get("offset")) or 0.0
        span = min(span, along)
        offset = min(max(0.0, offset), along - span)
        parent, at = 0, 0.0   # the panel the next bend starts from, and the distance out from the base edge
        for seg in [fl] + list(fl.get("then") or []):
            L = _num(seg.get("length"))
            if not L:
                break
            L = max(0.5, L - t / 2)   # the length drawn is to the outside of the bend: the flat is a little shorter
            angle = _num(seg.get("angle")) or 90.0
            sign = -1.0 if str(seg.get("direction", "up")).lower() in ("down", "下", "下向き") else 1.0
            if edge == "right":
                r, point, d = (W + at, offset, W + at + L, offset + span), (W + at, offset), (0.0, 1.0)
            elif edge == "left":
                r, point, d = (-at - L, offset, -at, offset + span), (-at, offset), (0.0, -1.0)
            elif edge == "back":
                r, point, d = (offset, D + at, offset + span, D + at + L), (offset, D + at), (-1.0, 0.0)
            else:
                r, point, d = (offset, -at - L, offset + span, -at), (offset, -at), (1.0, 0.0)
            rects.append(r)
            folds.append({"panels": [parent, len(rects) - 1], "point": list(point), "dir": list(d)})
            angles.append(sign * angle)
            parent, at = len(rects) - 1, at + L
    blank = unary_union([box(*r) for r in rects])
    holes = []
    for h in model.get("holes") or []:
        n = int(_num(h.get("count")) or 1)
        shape = str(h.get("shape", "round")).lower()
        a, b = _num(h.get("size")) or 0.0, _num(h.get("size2")) or 0.0
        if a <= 0:
            continue
        if shape in ("slot", "長穴"):
            per, area = 2 * max(0.0, a - b) + math.pi * b, max(0.0, a - b) * b + math.pi * b * b / 4
        elif shape in ("rect", "square", "角穴"):
            per, area = 2 * (a + (b or a)), a * (b or a)
        else:
            per, area = math.pi * a, math.pi * a * a / 4
        holes += [(per, area)] * max(1, min(n, 40))
    panels = [[[x0, y0], [x1, y0], [x1, y1], [x0, y1]] for x0, y0, x1, y1 in rects]
    return {"panels": panels, "folds": folds, "base": 0, "angles": angles,
            "cut_length_mm": round(blank.exterior.length + sum(p for p, _ in holes), 1) if blank.geom_type == "Polygon"
            else round(blank.length + sum(p for p, _ in holes), 1),
            "blank_area_mm2": round(blank.area - sum(a for _, a in holes), 1), "holes": len(holes)}


def check_model(built: dict, dimensions_mm: list[float], thickness_mm: float | None) -> dict:
    """Fold the described part with its own angles and compare the box with the written overall dimensions."""
    dims = sorted([float(d) for d in dimensions_mm if d and d > 0], reverse=True)[:3]
    t = float(thickness_mm or 1.0)
    tree = _tree(len(built["panels"]), built["folds"], built["base"])   # angles are per fold, in the tree's order
    ext = fold_extents(built["panels"], built["folds"], built["base"], [built["angles"][i] for *_, i in tree])
    out = {"extents_mm": [round(float(e), 1) for e in ext], "ok": False}
    if len(dims) < 2:
        return out
    r = _fit(ext, dims, t)
    if r is None:
        return out
    k, worst = r
    out.update(scale=round(k, 3), worst=round(worst, 3), ok=bool(worst <= 1.0 and abs(k - 1) <= 0.04))
    return out


def _num(v) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if x > 0 and math.isfinite(x) else None
