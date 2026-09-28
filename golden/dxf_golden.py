"""Flat-pattern DXF golden data: the same parts as the STEP golden set, drawn the way real DXFs look.

A part from golden/sampler.py is built as usual; its UNFOLDED solid (the source of the STEP truth) gives
the flat outline. The top face's edges (lines and circular arcs) become DXF entities, bend lines are
added from the flange definitions, and then "dirt" is added depending on the variant:

  D0  clean:     cut contour on layer CUT, bend lines on layer BEND. Nothing else.
  D1  annotated: realistic drawing on meaningful layers (English or Japanese names): frame and title
                 block, dimensions, hole centre marks, bend notes, engraving/marking lines on their
                 own layer. Only the CUT-layer geometry is cut.
  D2  messy:     everything on layer "0" or on meaningless numbered layers; entities are told apart
                 only by linetype/colour and position. Contours are split into several collinear
                 segments, some entities are duplicated, joints have tiny gaps (<= 0.005 mm), some
                 contours are LWPOLYLINEs with arc bulges, holes may be block INSERTs, and 30 % of files
                 are drawn in inches ($INSUNITS = 1).
  X   must-not-confirm: D1-style drawings with a real problem - the outer contour is open (gap
                 0.3-2 mm) or the file holds two parts. A correct analyzer must not return a confirmed
                 ("success") result for these.

Truth: blank area, cut length and hole count come from the unfolded solid (golden_v2 semantics, see
golden/truth_v2.py); bend count is the number of flanges. Thickness is NOT in the DXF: the analyzer
receives it as an input, like a user would type it.
"""
from __future__ import annotations

import math
import random
import zlib
from pathlib import Path

import ezdxf
import numpy as np

from golden.sampler import generate
from golden.truth_v2 import apply_v2

VARIANTS = ("D0", "D1", "D2", "X")
MM_PER_INCH = 25.4

LAYER_SCHEMES = {
    "en": {"cut": "CUT", "bend": "BEND", "dim": "DIM", "text": "TEXT", "frame": "FRAME", "center": "CENTER", "mark": "MARK"},
    "ja": {"cut": "外形", "bend": "曲げ線", "dim": "寸法", "text": "文字", "frame": "図枠", "center": "中心線", "mark": "ケガキ"},
}


# ------------------------------------------------------------------ geometry extraction
def _xy(v):
    return (float(v.x), float(v.y))


def _arc_sweep(center, start, mid, end):
    """Signed sweep angle (radians, + = counter-clockwise) of the arc start -> mid -> end."""
    a = lambda p: math.atan2(p[1] - center[1], p[0] - center[0])
    a_s, a_m, a_e = a(start), a(mid), a(end)
    ccw = (a_e - a_s) % (2 * math.pi)
    if (a_m - a_s) % (2 * math.pi) <= ccw:
        return ccw
    return -((a_s - a_e) % (2 * math.pi))


def flat_geometry(flat_solid):
    """Loops of the flat pattern's top face as lists of segments.

    Each loop: list of ("line", p0, p1) or ("arc", center, radius, p0, p1, sweep) in walking order,
    or a single ("circle", center, radius). Returns (outer_loop, [inner_loops]).
    """
    tops = [f for f in flat_solid.Faces() if f.geomType() == "PLANE" and f.normalAt().z > 0.999]
    top = max(tops, key=lambda f: f.Center().z)
    outer_wire = top.outerWire()
    loops = []
    for wire in top.Wires():
        edges = list(wire.Edges())
        if len(edges) == 1 and edges[0].geomType() == "CIRCLE":
            e = edges[0]
            loops.append((wire.isSame(outer_wire), [("circle", _xy(e.arcCenter()), float(e.radius()))]))
            continue
        segs = []
        for e in edges:
            p0, p1, pm = _xy(e.startPoint()), _xy(e.endPoint()), _xy(e.positionAt(0.5))
            if e.geomType() == "LINE":
                segs.append(["line", p0, p1])
            elif e.geomType() == "CIRCLE":
                c = _xy(e.arcCenter())
                segs.append(["arc", c, float(e.radius()), p0, p1, _arc_sweep(c, p0, pm, p1)])
            else:  # not produced by the sampler; keep as a polyline approximation
                pts = [_xy(e.positionAt(k / 16)) for k in range(17)]
                segs += [["line", a, b] for a, b in zip(pts, pts[1:])]
        loops.append((wire.isSame(outer_wire), _chain(segs)))
    outer = next(l for o, l in loops if o)
    inners = [l for o, l in loops if not o]
    return outer, inners


def _rev(seg):
    if seg[0] == "line":
        return ["line", seg[2], seg[1]]
    return ["arc", seg[1], seg[2], seg[4], seg[3], -seg[5]]


def _chain(segs):
    """Order segments head-to-tail (reversing where needed)."""
    start, end = (lambda s: s[1] if s[0] == "line" else s[3]), (lambda s: s[2] if s[0] == "line" else s[4])
    out = [segs.pop(0)]
    while segs:
        tail = end(out[-1])
        best = min(range(len(segs)), key=lambda i: min(math.dist(tail, start(segs[i])), math.dist(tail, end(segs[i]))))
        s = segs.pop(best)
        out.append(s if math.dist(tail, start(s)) <= math.dist(tail, end(s)) else _rev(s))
    return out


def bend_lines(part):
    """Bend centre lines in flat coordinates (u from 0 to span at v = BA/2 in each flange frame)."""
    lines = []
    for f in part.flanges:
        C = f.canon
        p = lambda u, v: tuple((C @ np.array([u, v, 0.0, 1.0]))[:2])
        lines.append((p(0.0, f.ba / 2), p(f.span, f.ba / 2), f))
    return lines


# ------------------------------------------------------------------ DXF writing
class Writer:
    def __init__(self, variant: str, rng: random.Random, inch: bool = False):
        self.v, self.rng, self.inch = variant, rng, inch
        self.doc = ezdxf.new("R2010", setup=True)
        self.doc.header["$INSUNITS"] = 1 if inch else 4
        self.msp = self.doc.modelspace()
        self.s = 1 / MM_PER_INCH if inch else 1.0
        if variant in ("D0",):
            self.layers = LAYER_SCHEMES["en"]
        elif variant in ("D1", "X"):
            self.layers = LAYER_SCHEMES[rng.choice(["en", "ja"])]
        else:  # D2: meaningless layers
            if rng.random() < 0.5:
                self.layers = {k: "0" for k in LAYER_SCHEMES["en"]}
            else:
                nums = rng.sample(range(1, 20), 7)
                self.layers = {k: str(n) for k, n in zip(LAYER_SCHEMES["en"], nums)}
        for name in set(self.layers.values()):
            if name not in self.doc.layers:
                self.doc.layers.add(name)
        # D2 tells kinds apart only by linetype / colour on the entity
        self.style = {
            "cut": {"color": 7, "linetype": "CONTINUOUS"},
            "bend": {"color": rng.choice([1, 3, 4]), "linetype": rng.choice(["DASHED", "DASHDOT", "PHANTOM"])},
            "dim": {"color": rng.choice([2, 8])},
            "text": {"color": 2},
            "frame": {"color": 7, "linetype": "CONTINUOUS"},
            "center": {"color": 1, "linetype": "CENTER"},
            "mark": {"color": 5, "linetype": "CONTINUOUS"},
        }

    def attrs(self, kind):
        a = {"layer": self.layers[kind]}
        if self.v == "D2" or kind in ("bend", "center"):
            a.update(self.style[kind])
        return a

    def P(self, p):
        return (p[0] * self.s, p[1] * self.s)

    # -- primitives (coordinates in mm; scaled on write)
    def line(self, a, b, kind="cut"):
        self.msp.add_line(self.P(a), self.P(b), dxfattribs=self.attrs(kind))

    def arc(self, c, r, p0, p1, sweep, kind="cut"):
        a0 = math.degrees(math.atan2(p0[1] - c[1], p0[0] - c[0]))
        a1 = math.degrees(math.atan2(p1[1] - c[1], p1[0] - c[0]))
        if sweep < 0:
            a0, a1 = a1, a0
        self.msp.add_arc(self.P(c), r * self.s, a0, a1, dxfattribs=self.attrs(kind))

    def circle(self, c, r, kind="cut"):
        self.msp.add_circle(self.P(c), r * self.s, dxfattribs=self.attrs(kind))

    def text(self, s, at, h=3.5, kind="text"):
        self.msp.add_text(s, height=h * self.s, dxfattribs=self.attrs(kind)).set_placement(self.P(at))

    # -- contours
    def loop(self, segs, messy=False):
        if segs[0][0] == "circle":
            _, c, r = segs[0]
            if messy and self.rng.random() < 0.3:
                self.hole_block(c, r)
            elif messy and self.rng.random() < 0.3:  # circle drawn as two half arcs
                self.arc(c, r, (c[0] + r, c[1]), (c[0] - r, c[1]), math.pi)
                self.arc(c, r, (c[0] - r, c[1]), (c[0] + r, c[1]), math.pi)
            else:
                self.circle(c, r)
            if messy and self.rng.random() < 0.15:
                self.circle(c, r)  # duplicate
            return
        if messy and self.rng.random() < 0.35:
            self.polyline(segs)
            return
        for seg in segs:
            if seg[0] == "line":
                a, b = seg[1], seg[2]
                if messy and self.rng.random() < 0.3 and math.dist(a, b) > 6:
                    n = self.rng.randint(2, 4)
                    cuts = sorted(self.rng.uniform(0.2, 0.8) for _ in range(n - 1))
                    pts = [a] + [(a[0] + (b[0] - a[0]) * u, a[1] + (b[1] - a[1]) * u) for u in cuts] + [b]
                    for p, q in zip(pts, pts[1:]):
                        self.line(p, self._jitter(q) if messy else q)
                else:
                    self.line(a, self._jitter(b) if messy else b)
                if messy and self.rng.random() < 0.1:
                    self.line(b, a)  # duplicate, reversed
            else:
                _, c, r, p0, p1, sw = seg
                self.arc(c, r, p0, p1, sw)

    def _jitter(self, p):
        """Tiny drawing gap at a joint (<= 0.005 mm): must be closed by the analyzer."""
        if self.rng.random() < 0.25:
            ang = self.rng.uniform(0, 2 * math.pi)
            d = self.rng.uniform(0.001, 0.005)
            return (p[0] + d * math.cos(ang), p[1] + d * math.sin(ang))
        return p

    def polyline(self, segs):
        pts = []
        for seg in segs:
            if seg[0] == "line":
                pts.append((*self.P(seg[1]), 0.0))
            else:
                pts.append((*self.P(seg[3]), math.tan(seg[5] / 4)))
        self.msp.add_lwpolyline(pts, format="xyb", close=True, dxfattribs=self.attrs("cut"))

    def hole_block(self, c, r):
        name = f"HOLE_D{2 * r:.2f}".replace(".", "_")
        if name not in self.doc.blocks:
            self.doc.blocks.new(name=name).add_circle((0, 0), r * self.s, dxfattribs={"layer": "0"})
        self.msp.add_blockref(name, self.P(c), dxfattribs=self.attrs("cut"))

    # -- annotations
    def annotate(self, part, outer, inners, bends, bbox, offset=(0.0, 0.0)):
        (x0, y0, x1, y1), rng = bbox, self.rng
        W, H = x1 - x0, y1 - y0
        m = max(15.0, 0.25 * max(W, H))
        # frame and title block
        fx0, fy0, fx1, fy1 = x0 - m, y0 - m - 40, x1 + m + 60, y1 + m
        for a, b in [((fx0, fy0), (fx1, fy0)), ((fx1, fy0), (fx1, fy1)), ((fx1, fy1), (fx0, fy1)), ((fx0, fy1), (fx0, fy0))]:
            self.line(a, b, "frame")
        tx0, ty0 = fx1 - 90, fy0
        for a, b in [((tx0, ty0), (tx0, ty0 + 36)), ((tx0, ty0 + 36), (fx1, ty0 + 36))] + \
                    [((tx0, ty0 + 9 * k), (fx1, ty0 + 9 * k)) for k in (1, 2, 3)]:
            self.line(a, b, "frame")
        rows = [f"品名 {part.name}", f"材質 {rng.choice(['SPCC', 'SECC', 'SUS304', 'A5052P'])}",
                f"板厚 t{part.t:g}", f"数量 {rng.choice([1, 5, 10, 50, 100])}"]
        for k, s in enumerate(reversed(rows)):
            self.text(s, (tx0 + 3, ty0 + 9 * k + 2.5), 4.0)
        # overall dimensions
        dim_attr = self.attrs("dim")
        for base, p1, p2, ang in [((x0, y0 - 10), (x0, y0), (x1, y0), 0), ((x0 - 10, y0), (x0, y0), (x0, y1), 90)]:
            d = self.msp.add_linear_dim(base=self.P(base), p1=self.P(p1), p2=self.P(p2), angle=ang,
                                        dxfattribs=dim_attr, override={"dimtxt": 3.5 * self.s, "dimasz": 2.5 * self.s, "dimlfac": 1.0, "dimdec": 1})
            d.render()
        # centre marks on holes, hole callouts
        for loop in inners:
            if loop[0][0] == "circle":
                (cx, cy), r = loop[0][1], loop[0][2]
                e = r + 2.0
                self.line((cx - e, cy), (cx + e, cy), "center")
                self.line((cx, cy - e), (cx, cy + e), "center")
                if rng.random() < 0.5:
                    self.text(f"φ{2 * r:.1f}", (cx + r + 1.5, cy + r + 1.0), 2.5)
        # bend notes
        for (a, b, f) in bends:
            mx, my = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
            self.text(f"{'山' if f.up else '谷'} {f.angle:g}° R{f.radius:g}", (mx + 2, my + 2), 2.5)
        # engraving / marking lines (not cut) inside the part, on their own layer
        if self.v in ("D1", "X") and rng.random() < 0.4:
            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
            L = min(W, H) * 0.15
            self.line((cx - L, cy), (cx + L, cy), "mark")


def _bbox(outer):
    pts = []
    for seg in outer:
        if seg[0] == "line":
            pts += [seg[1], seg[2]]
        elif seg[0] == "arc":
            c, r = seg[1], seg[2]
            pts += [(c[0] - r, c[1] - r), (c[0] + r, c[1] + r)]
        else:
            c, r = seg[1], seg[2]
            pts += [(c[0] - r, c[1] - r), (c[0] + r, c[1] + r)]
    xs, ys = zip(*pts)
    return min(xs), min(ys), max(xs), max(ys)


def _translate(segs, dx, dy):
    out = []
    for s in segs:
        if s[0] == "line":
            out.append(["line", (s[1][0] + dx, s[1][1] + dy), (s[2][0] + dx, s[2][1] + dy)])
        elif s[0] == "arc":
            out.append(["arc", (s[1][0] + dx, s[1][1] + dy), s[2], (s[3][0] + dx, s[3][1] + dy),
                        (s[4][0] + dx, s[4][1] + dy), s[5]])
        else:
            out.append(["circle", (s[1][0] + dx, s[1][1] + dy), s[2]])
    return out


def _open_gap(outer, rng):
    """Shorten one straight segment of the outer contour so the contour is open by 0.3-2 mm."""
    candidates = [i for i, s in enumerate(outer) if s[0] == "line" and math.dist(s[1], s[2]) > 6]
    i = rng.choice(candidates)
    a, b = outer[i][1], outer[i][2]
    g = rng.uniform(0.3, 2.0)
    L = math.dist(a, b)
    outer = [list(s) for s in outer]
    outer[i][2] = (b[0] - (b[0] - a[0]) * g / L, b[1] - (b[1] - a[1]) * g / L)
    return outer, round(g, 3)


# ------------------------------------------------------------------ public API
def build_dxf(level: str, index: int, seed: int, variant: str, out_dir: Path) -> dict:
    """Write <out_dir>/<name>.dxf and return its truth record."""
    part, built = generate(level, index, seed)
    part.level = level
    apply_v2(built)
    _, flat, base_truth = built
    outer, inners = flat_geometry(flat)
    bends = bend_lines(part)
    rng = random.Random(zlib.crc32(f"dxf:{seed}:{level}:{index}:{variant}".encode()))
    inch = variant == "D2" and rng.random() < 0.3
    w = Writer(variant, rng, inch=inch)
    name = f"{level}_{index:04d}_{variant}"
    messy = variant == "D2"
    truth = {k: base_truth[k] for k in ("level", "note", "thickness_mm", "k_factor", "blank_area_mm2",
                                        "cut_length_mm", "hole_count", "bend_count", "flat_bbox_mm")}
    truth.update(name=name, part=f"{level}_{index:04d}", variant=variant, seed=[seed, index],
                 units="inch" if inch else "mm", must_not_confirm=False)

    problem = None
    if variant == "X":
        problem = rng.choice(["open_contour", "two_parts"])
        truth.update(must_not_confirm=True, problem=problem)
        if problem == "open_contour":
            outer, gap = _open_gap(outer, rng)
            truth["gap_mm"] = gap
    w.loop(outer, messy)
    for loop in inners:
        w.loop(loop, messy)
    for a, b, _ in bends:
        if messy and rng.random() < 0.2:  # bend line drawn in two pieces
            m = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
            w.line(a, m, "bend"); w.line(m, b, "bend")
        else:
            w.line(a, b, "bend")
    bbox = _bbox(outer)
    if problem == "two_parts":
        dx = bbox[2] - bbox[0] + 20.0
        w.loop(_translate(outer, dx, 0), False)
        for loop in inners:
            w.loop(_translate(loop, dx, 0), False)
        for a, b, _ in bends:
            w.line((a[0] + dx, a[1]), (b[0] + dx, b[1]), "bend")
        bbox = (bbox[0], bbox[1], bbox[2] + dx, bbox[3])
    if variant != "D0":
        w.annotate(part, outer, inners, bends, bbox)
    out_dir.mkdir(parents=True, exist_ok=True)
    w.doc.saveas(out_dir / f"{name}.dxf")
    return truth
