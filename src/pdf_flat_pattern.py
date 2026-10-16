"""Count the bends and holes of a flat pattern drawn on a drawing page, by image measurement.

    measure_flat_pattern(gray, px_per_mm, box) -> dict

`gray` is the page rendered as a grayscale array (vector pages and scans alike), `box` the region where the
flat pattern is (x0, y0, x1, y1 in 0-1000 of the page, given by Claude in src/pdf_shape.py). Nothing here is
read by an LLM: the counts come from the contours on the image.

  1. Ink: pixels clearly darker than the paper, with small breaks in the lines closed. A page as noisy as a FAX
     is not measured (its thin lines break into dots and its specks look like holes and dashes).
  2. Outline: the connected line drawing in the region whose filled inside is largest and overlaps the box most;
     it must be closed (its inside is not connected to the outside).
  3. Holes: closed contours inside the outline (and small solid dots, for holes too small to show their
     inside), plus inside regions enclosed by the outline that do not reach its outer edge.
  4. Bend lines: straight chords that split the inside of the outline into panels (a solid bend line), and
     straight chains of short dashes inside the outline (a chain / dashed bend line).
The result says ok=False with a reason when no closed outline is found or the counts look implausible; the
caller then falls back to Claude's own counts and marks them as unverified.
"""
from __future__ import annotations

import math

import numpy as np

MIN_OUTLINE_MM = 8.0      # an outline smaller than this (both sides) is not a flat pattern
MIN_DASH_MM = 0.5         # shorter ink pieces are dots / noise
LINE_TOL_MM = 0.45        # distance of a dash centre from a bend line
GAP_STEPS_MM = (0.1, 0.2)  # small gaps in the lines closed before looking for the outline
MAX_SPECKS_PER_100MM2 = 2.0  # a noisier image (FAX: 7-10) is not measured: its lines and dots are unreliable
MAX_BENDS = 40


def _ink(gray: np.ndarray) -> np.ndarray:
    bg = float(np.median(gray))
    return gray < min(bg - 45.0, 200.0)


def _speck_density(raw: np.ndarray, ppm: float) -> float:
    """Isolated specks of ink (smaller than 0.25 mm square) per 100 mm² of the region: the noise of a FAX."""
    from scipy import ndimage as ndi

    lab, n = ndi.label(raw, structure=np.ones((3, 3)))
    if n == 0:
        return 0.0
    sizes = np.bincount(lab.ravel())[1:]
    specks = int((sizes <= max(4.0, (0.25 * ppm) ** 2)).sum())
    return 100.0 * specks / (raw.size / ppm ** 2)


def _repaired(raw: np.ndarray, ppm: float):
    """Repairs of small breaks in the lines: (label, ink), the lightest first. The first repair that gives a
    closed outline is used."""
    from scipy import ndimage as ndi

    for gap in GAP_STEPS_MM:
        r = max(1, int(round(gap * ppm)))
        yy, xx = np.ogrid[-r:r + 1, -r:r + 1]
        yield f"gap {gap}mm", ndi.binary_closing(raw, structure=(xx * xx + yy * yy) <= r * r) | raw


def measure_flat_pattern(gray: np.ndarray, px_per_mm: float, box: dict) -> dict:
    from scipy import ndimage as ndi

    H, W = gray.shape
    x0, x1 = sorted((int(box["x0"]), int(box["x1"])))
    y0, y1 = sorted((int(box["y0"]), int(box["y1"])))
    x0, x1 = max(0, min(1000, x0)), max(0, min(1000, x1))
    y0, y1 = max(0, min(1000, y0)), max(0, min(1000, y1))
    if x1 - x0 < 5 or y1 - y0 < 5:
        return {"ok": False, "reason": "展開図の位置が分からない"}
    bx = (x0 * W // 1000, y0 * H // 1000, x1 * W // 1000, y1 * H // 1000)
    mx, my = int(0.06 * W), int(0.06 * H)     # Claude's box is approximate: look a little around it
    cx0, cy0, cx1, cy1 = max(0, bx[0] - mx), max(0, bx[1] - my), min(W, bx[2] + mx), min(H, bx[3] + my)
    crop = gray[cy0:cy1, cx0:cx1]
    raw = _ink(crop)
    ppm = px_per_mm
    specks = _speck_density(raw, ppm)
    if specks > MAX_SPECKS_PER_100MM2:   # FAX: its lines break into dots and lose whole stretches; tried, not measurable
        return {"ok": False, "reason": "画像のノイズが多く、線を数えられない", "specks_per_100mm2": round(specks, 2)}
    target = (bx[0] - cx0, bx[1] - cy0, bx[2] - cx0, bx[3] - cy0)
    found = None
    for step, ink in _repaired(raw, ppm):  # lines of a FAX / a poor scan break up: close ever larger gaps
        found = _outline(ink, target, ppm, (cx1 - cx0, cy1 - cy0), (W, H))
        if found is not None:
            found = (*found, step)
            break
    if found is None:
        return {"ok": False, "reason": "展開図の閉じた外形線が見つからない"}
    lab, objs, best, repair = found
    i, sl, filled, outline = best
    out = _count_inside(lab, objs, i, sl, filled, outline, ppm)
    out["line_repair"] = repair
    out["outline_box_px"] = [int(sl[1].start + cx0), int(sl[0].start + cy0), int(sl[1].stop + cx0), int(sl[0].stop + cy0)]
    out["outline_mm_on_page"] = [round((sl[1].stop - sl[1].start) / ppm, 1), round((sl[0].stop - sl[0].start) / ppm, 1)]
    if out["bends"] > MAX_BENDS:
        return {**out, "ok": False, "reason": "曲げ線の数が多すぎる（線の判別に失敗）"}
    return {**out, "ok": True, "reason": ""}


def _outline(ink, target, ppm, crop_size, page_size):
    """(labels, objects, (id, slice, filled, mask)) of the closed outline that best matches the target box."""
    from scipy import ndimage as ndi

    lab, n = ndi.label(ink, structure=np.ones((3, 3)))
    if n == 0:
        return None
    objs = ndi.find_objects(lab)
    best, best_score = None, 0.0
    for i, sl in enumerate(objs, 1):
        h, w = sl[0].stop - sl[0].start, sl[1].stop - sl[1].start
        if h < MIN_OUTLINE_MM * ppm or w < MIN_OUTLINE_MM * ppm:
            continue
        if w > 0.9 * crop_size[0] and h > 0.9 * crop_size[1] and (w > 0.5 * page_size[0] or h > 0.5 * page_size[1]):
            continue  # the sheet frame / a table around everything
        m = lab[sl] == i
        filled = ndi.binary_fill_holes(m)
        inside = int(filled.sum() - m.sum())
        if inside < 0.3 * h * w:
            continue  # not a closed outline
        iou = _iou((sl[1].start, sl[0].start, sl[1].stop, sl[0].stop), target)
        if iou < 0.2:
            continue
        score = iou * math.sqrt(inside)
        if score > best_score:
            best, best_score = (i, sl, filled, m), score
    return None if best is None else (lab, objs, best)


def _iou(a, b) -> float:
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def _count_inside(lab, objs, oid, sl, filled, outline, ppm) -> dict:
    from scipy import ndimage as ndi

    inner = filled & ~outline
    # --- panels and holes enclosed by the outline itself
    rl, rn = ndi.label(inner)
    sizes = np.bincount(rl.ravel())[1:] if rn else np.array([])
    # the outside of the outline, with a border around the slice (a rectangular outline fills its whole slice)
    outside = np.pad(~filled, 1, constant_values=True)
    reach = ndi.binary_dilation(outside, iterations=max(2, int(0.6 * ppm)))[1:-1, 1:-1]
    panel_ids, enclosed_holes, enclosed_perims = [], 0, []
    min_region = (0.6 * ppm) ** 2
    for r, size in enumerate(sizes, 1):
        if size < min_region:
            continue
        region = rl == r
        if (region & reach).any():
            panel_ids.append(r)
        else:
            enclosed_holes += 1   # a hole drawn touching the outline (blur / close to an edge)
            enclosed_perims.append((_perimeter(region)[0], int(size)))
    panels = len(panel_ids)
    leaders, chord_segments = _leader_chords(rl, panel_ids, filled, outline, ppm) if panels > 1 else (0, [])
    chords = max(0, panels - 1 - leaders)

    # --- other ink inside the outline
    y0, x0 = sl[0].start, sl[1].start
    inner_ids = np.unique(lab[sl][inner])
    inner_ids = inner_ids[(inner_ids != 0) & (inner_ids != oid)]
    holes, dashes, hole_perims, hole_areas, ring_widths = 0, [], [], [], []
    for j in inner_ids:
        s2 = objs[j - 1]
        piece = lab[s2] == j
        ys, xs = np.nonzero(piece)
        ys, xs = ys + s2[0].start - y0, xs + s2[1].start - x0
        if ys.min() < 0 or xs.min() < 0 or ys.max() >= filled.shape[0] or xs.max() >= filled.shape[1] \
                or not filled[ys, xs].all():
            continue  # crosses the outline: not inside
        h, w = piece.shape
        enclosed = int(ndi.binary_fill_holes(piece).sum() - piece.sum())
        if enclosed > (0.25 * ppm) ** 2 and min(h, w) > 0.6 * ppm:
            holes += 1
            whole = ndi.binary_fill_holes(piece)
            outer_len, inner_len = _perimeter(whole)[0], _perimeter(whole & ~piece)[0]
            hole_perims.append((outer_len + inner_len) / 2)   # the centre of the drawn line
            hole_areas.append((int(whole.sum()) + int((whole & ~piece).sum())) / 2)
            ring_widths.append(max(0.0, (outer_len - inner_len) / (2 * math.pi)))
            continue
        if min(h, w) > 0.6 * ppm and max(h, w) < 2.5 * min(h, w) and piece.sum() > 0.45 * h * w:
            holes += 1   # a small hole drawn as a dot
            hole_perims.append(math.pi * (h + w) / 2)
            hole_areas.append(math.pi * (h + w) ** 2 / 16)
            continue
        pts = np.stack([xs, ys], 1).astype(float)
        if len(pts) < 3:
            continue
        centre = pts.mean(0)
        _, sv, vt = np.linalg.svd(pts - centre, full_matrices=False)
        length = sv[0] / math.sqrt(len(pts)) * math.sqrt(12.0)
        width = (sv[1] / math.sqrt(len(pts)) * math.sqrt(12.0)) if len(sv) > 1 else 0.0
        if length < MIN_DASH_MM * ppm or width > 0.6 * ppm:
            continue
        dashes.append((centre, vt[0], length))
    lines = _lines(dashes, ppm)
    # --- lengths (page pixels) for the cut length: the outer contour, the holes, and the straight edges / bend
    # lines that the scale is found from (src/pdf_shape.py)
    line_w = float(np.median(ring_widths)) if ring_widths else 0.3 * ppm
    hole_perims += [p + math.pi * line_w for p, _ in enclosed_perims]   # traced inside the drawn line
    hole_areas += [a + p * line_w / 2 for p, a in enclosed_perims]
    body = ndi.binary_opening(filled, structure=_disk(max(2, int(round(0.4 * ppm)))))   # without leaders sticking out
    outer_len, polygon = _perimeter(body)
    edges = [float(np.hypot(*(polygon[(k + 1) % len(polygon)] - polygon[k]))) for k in range(len(polygon))]
    segments = list(chord_segments)   # bend lines as (end, end) in pixels of the slice
    for members in lines:
        c = np.array([dashes[j][0] for j in members])
        d = dashes[members[0]][1]
        t = c @ d
        half = float(np.mean([dashes[j][2] for j in members])) / 2
        o = c.mean(0) - d * float(t.mean())
        segments.append((o + d * (t.min() - half), o + d * (t.max() + half)))
    bend_lengths = [float(np.hypot(*(b - a))) for a, b in segments]
    structure = _spans(body, segments, line_w, ppm)
    return {"bends": chords + len(lines), "holes": holes + enclosed_holes, "solid_bend_lines": chords, "leader_lines": leaders,
            "dashed_bend_lines": len(lines), "panels": panels, "dash_pieces": len(dashes),
            "outer_px": round(outer_len, 1), "holes_px": [round(p, 1) for p in hole_perims],
            "area_px": round(float(body.sum()) - outer_len * line_w / 2 - sum(hole_areas), 1),
            "edges_px": [round(e, 1) for e in edges if e >= 3 * ppm], "bend_lines_px": [round(b, 1) for b in bend_lengths],
            "line_width_px": round(line_w, 2), "spans_px": structure["spans"],
            "panels_px": structure["panels"], "folds_px": structure["folds"], "base_panel": structure["base"],
            "bbox_px": [float(np.ptp(np.nonzero(body)[1]) + 1 - line_w), float(np.ptp(np.nonzero(body)[0]) + 1 - line_w)]}


def _leader_chords(rl, panel_ids, filled, outline, ppm) -> tuple[int, list]:
    """Straight lines that split the inside of the outline but go on outside the part (a leader or a dimension
    line drawn across it) are not bend lines: count them."""
    from scipy import ndimage as ndi

    near = max(2, int(round(0.25 * ppm)))
    cover = np.zeros(outline.shape, dtype=np.int16)
    for r in panel_ids:
        cover += ndi.binary_dilation(rl == r, iterations=near)
    chord_px = outline & (cover >= 2)
    body = ndi.binary_opening(filled, iterations=max(2, int(round(1.0 * ppm))))   # the part without thin lines
    cl, cn = ndi.label(chord_px, structure=np.ones((3, 3)))
    leaders, lengths = 0, []
    H, W = outline.shape
    for k in range(1, cn + 1):
        ys, xs = np.nonzero(cl == k)
        if len(xs) < max(5, ppm):
            continue
        pts = np.stack([xs, ys], 1).astype(float)
        c = pts.mean(0)
        _, _, vt = np.linalg.svd(pts - c, full_matrices=False)
        d = vt[0]
        t = (pts - c) @ d
        leader = False
        for end, sign in ((t.max(), 1.0), (t.min(), -1.0)):
            hits = 0
            for step in np.arange(1.0, 3.0, 0.25) * ppm:
                x, y = c + d * (end + sign * step)
                xi, yi = int(round(x)), int(round(y))
                if 0 <= xi < W and 0 <= yi < H and outline[max(0, yi - 1):yi + 2, max(0, xi - 1):xi + 2].any() \
                        and not body[yi, xi]:
                    hits += 1
            if hits >= 6:   # the line goes on outside the part
                leader = True
                break
        if leader:
            leaders += 1
        else:
            lengths.append((c + d * t.min(), c + d * t.max()))
    return leaders, lengths


def _lines(dashes: list, ppm: float) -> list[list[int]]:
    """Group dash pieces lying on one straight line (tolerates a skewed scan)."""
    remaining = set(range(len(dashes)))
    tol = LINE_TOL_MM * ppm
    lines = []

    def members(c, d, cand):
        nrm = np.array([-d[1], d[0]])
        out = []
        for j in cand:
            cj, dj, _ = dashes[j]
            if abs(float(np.dot(cj - c, nrm))) > tol:
                continue
            if abs(float(np.dot(dj, d))) < math.cos(math.radians(20)):
                continue
            out.append(j)
        return out

    while remaining:
        best: list[int] = []
        for i in sorted(remaining, key=lambda k: -dashes[k][2])[:60]:
            c, d, _ = dashes[i]
            got = members(c, d, remaining)
            if len(got) >= 2:  # refit through the centres when they spread out
                pts = np.array([dashes[j][0] for j in got])
                spread = np.ptp(pts @ d)
                if spread > 3 * ppm:
                    cc = pts.mean(0)
                    _, _, vt = np.linalg.svd(pts - cc, full_matrices=False)
                    dd = vt[0] if float(np.dot(vt[0], d)) >= 0 else -vt[0]
                    got = members(cc, dd, remaining) or got
            if len(got) > len(best):
                best = got
        if not best:
            break
        if len(best) < 2 and dashes[best[0]][2] < 2.0 * ppm:
            break
        lines.append(best)
        remaining -= set(best)
    return lines


# ---------------------------------------------------------------- contour lengths
def _disk(r: int) -> np.ndarray:
    yy, xx = np.ogrid[-r:r + 1, -r:r + 1]
    return (xx * xx + yy * yy) <= r * r


def _trace(mask: np.ndarray) -> np.ndarray:
    """The outer boundary of the largest connected region of `mask`, as ordered pixel centres (x, y)
    (Moore-neighbour tracing)."""
    from scipy import ndimage as ndi

    lab, n = ndi.label(mask, structure=np.ones((3, 3)))
    if n == 0:
        return np.zeros((0, 2))
    if n > 1:
        lab = lab == (int(np.argmax(np.bincount(lab.ravel())[1:])) + 1)
    m = np.pad(lab > 0, 1)
    ys, xs = np.nonzero(m)
    k = int(np.lexsort((xs, ys))[0])
    start = (int(ys[k]), int(xs[k]))
    nb = [(0, -1), (-1, -1), (-1, 0), (-1, 1), (0, 1), (1, 1), (1, 0), (1, -1)]   # clockwise from west
    cur, back = start, (start[0], start[1] - 1)
    first_back = back
    pts = [cur]
    for _ in range(4 * int(m.sum()) + 8):
        i = nb.index((back[0] - cur[0], back[1] - cur[1]))
        for step in range(1, 9):
            dy, dx = nb[(i + step) % 8]
            cand = (cur[0] + dy, cur[1] + dx)
            if m[cand]:
                pdy, pdx = nb[(i + step - 1) % 8]
                back = (cur[0] + pdy, cur[1] + pdx)
                cur = cand
                break
        else:
            break   # a single pixel
        if cur == start and back == first_back:
            break
        pts.append(cur)
    return np.array([(x - 1, y - 1) for y, x in pts], dtype=float)


def _simplify(pts: np.ndarray, eps: float) -> np.ndarray:
    """Douglas-Peucker on a closed polygon (the pixel staircase becomes straight edges)."""
    if len(pts) < 4:
        return pts
    far = int(np.argmax(np.hypot(*(pts - pts[0]).T)))

    def dp(seg):
        a, b = seg[0], seg[-1]
        if len(seg) < 3:
            return [a, b]
        ab = b - a
        n = np.hypot(*ab)
        rel = seg - a
        d = np.abs(ab[0] * rel[:, 1] - ab[1] * rel[:, 0]) / n if n > 0 else np.hypot(*rel.T)
        k = int(np.argmax(d))
        if d[k] <= eps:
            return [a, b]
        return dp(seg[:k + 1])[:-1] + dp(seg[k:])

    half1 = dp(np.vstack([pts[:far + 1]]))
    half2 = dp(np.vstack([pts[far:], pts[:1]]))
    return np.array(half1[:-1] + half2[:-1])


def _perimeter(mask: np.ndarray, eps: float = 1.0) -> tuple[float, np.ndarray]:
    """Length (px) of the outer boundary of the largest region of `mask`, and the simplified polygon."""
    pts = _trace(mask)
    if len(pts) < 3:
        return 0.0, pts
    poly = _simplify(pts, eps)
    if len(poly) < 3:
        return 0.0, poly
    closed = np.vstack([poly, poly[:1]])
    return float(np.hypot(*np.diff(closed, axis=0).T).sum()), poly


# ---------------------------------------------------------------- lengths kept by folding
def _spans(body: np.ndarray, segments: list, line_w: float, ppm: float) -> dict:
    """Lengths of the flat pattern that folding does not change (page pixels).

    Folding about a bend line keeps every distance measured along that line. So for each direction of bend
    lines, the panels reached from the base panel through bend lines of that direction only keep their extent
    along it: the folded part measures the same along that axis. With no bend line (a flat part) the outline's
    width and height are the part's. Returned: [{"px": length, "dir": angle in degrees, "kind": ...}]."""
    from scipy import ndimage as ndi

    if not segments:
        ys, xs = np.nonzero(body)
        if len(xs) == 0:
            return {"spans": [], "panels": [], "folds": [], "base": 0}
        return {"spans": [{"px": float(np.ptp(xs) + 1 - line_w), "dir": 0.0, "kind": "plate"},
                          {"px": float(np.ptp(ys) + 1 - line_w), "dir": 90.0, "kind": "plate"}],
                "panels": [_hull(body, line_w)], "folds": [], "base": 0}
    # panels: the part with the bend lines cut out of it
    cut = np.zeros(body.shape, dtype=bool)
    H, W = body.shape
    r = max(1, int(math.ceil(line_w / 2 + 1)))
    ext = []
    for a, b in segments:   # a dashed line stops short of the outline: carry it on to the edge of the part
        d = (b - a) / max(1e-9, np.hypot(*(b - a)))
        ends = []
        for e, sign in ((a, -1.0), (b, 1.0)):
            for step in range(1, int(4 * ppm)):
                x, y = e + sign * d * step
                xi, yi = int(round(x)), int(round(y))
                if not (0 <= xi < W and 0 <= yi < H) or not body[yi, xi]:
                    break
            ends.append(e + sign * d * step)
        ext.append((ends[0], ends[1]))
    segments = ext
    for a, b in segments:
        n = int(np.hypot(*(b - a))) + 2
        for x, y in np.linspace(a, b, n):
            xi, yi = int(round(x)), int(round(y))
            cut[max(0, yi - r):yi + r + 1, max(0, xi - r):xi + r + 1] = True
    lab, n = ndi.label(body & ~cut)
    if n == 0:
        return {"spans": [], "panels": [], "folds": [], "base": 0}
    sizes = np.bincount(lab.ravel())
    sizes[0] = 0
    keep = sizes >= (1.5 * ppm) ** 2
    # which panels each bend line joins: look a little to both sides along its length
    edges = []
    for k, (a, b) in enumerate(segments):
        d = (b - a) / max(1e-9, np.hypot(*(b - a)))
        nrm = np.array([-d[1], d[0]])
        sides = ([], [])
        for f in (0.2, 0.35, 0.5, 0.65, 0.8):
            q = a + (b - a) * f
            for side, sign in ((0, 1.0), (1, -1.0)):
                for off in (r + 2, r + 4, r + 7):
                    x, y = q + sign * nrm * off
                    xi, yi = int(round(x)), int(round(y))
                    if 0 <= xi < W and 0 <= yi < H and lab[yi, xi] and keep[lab[yi, xi]]:
                        sides[side].append(int(lab[yi, xi]))
                        break
        if sides[0] and sides[1]:
            p0, p1 = max(set(sides[0]), key=sides[0].count), max(set(sides[1]), key=sides[1].count)
            if p0 != p1:
                ang = math.degrees(math.atan2(d[1], d[0])) % 180
                edges.append((p0, p1, ang, d, a))
    base = int(np.argmax(sizes))
    ys, xs = np.nonzero(lab)
    labels = lab[ys, xs]
    out = []
    groups: list[list] = []
    for e in edges:   # bend lines of one direction (within 8 degrees)
        for g in groups:
            if min(abs(e[2] - g[0][2]), 180 - abs(e[2] - g[0][2])) < 8:
                g.append(e)
                break
        else:
            groups.append([e])
    for g in groups:
        d = g[0][3]
        reached, todo = {base}, [base]
        while todo:
            cur = todo.pop()
            for p0, p1, *_ in g:
                for u, v in ((p0, p1), (p1, p0)):
                    if u == cur and v not in reached:
                        reached.add(v)
                        todo.append(v)
        sel = np.isin(labels, list(reached))
        proj = xs[sel] * d[0] + ys[sel] * d[1]
        out.append({"px": float(np.ptp(proj) + 1 - line_w), "dir": round(g[0][2], 1), "kind": "along_bends",
                    "panels": len(reached), "bend_lines": len(g)})
        sel = labels == base   # the base panel across these bend lines (not kept exactly: the bends add a little)
        proj = xs[sel] * -d[1] + ys[sel] * d[0]
        out.append({"px": float(np.ptp(proj) + 1), "dir": round((g[0][2] + 90) % 180, 1), "kind": "base_across"})
    # the panels and the bend lines joining them, to fold the flat pattern into the part (src/pdf_fold.py)
    ids = sorted({base} | {e[0] for e in edges} | {e[1] for e in edges})
    index = {pid: k for k, pid in enumerate(ids)}
    panels = [_hull(lab == pid, line_w) for pid in ids]
    folds = [{"panels": [index[e[0]], index[e[1]]], "point": [round(float(v), 1) for v in e[4]],
              "dir": [round(float(v), 4) for v in e[3]]} for e in edges]
    return {"spans": out, "panels": panels, "folds": folds, "base": index[base]}


def _hull(mask: np.ndarray, line_w: float) -> list:
    """Convex hull of a region (page pixels), as [[x, y], ...]: enough to fold it and take the extents."""
    from scipy import ndimage as ndi
    from scipy.spatial import ConvexHull

    edge = mask & ~ndi.binary_erosion(mask)
    ys, xs = np.nonzero(edge)
    pts = np.stack([xs, ys], 1).astype(float)
    if len(pts) < 3:
        return pts.tolist()
    try:
        hull = ConvexHull(pts)
    except Exception:   # a degenerate (straight) region
        return pts[[0, -1]].tolist()
    return pts[hull.vertices].round(1).tolist()
