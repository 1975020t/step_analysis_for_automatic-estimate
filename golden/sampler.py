"""Seeded random sheet-metal parts per complexity level.

Levels (see analysis/golden_eval_baseline.md):
  Lv0  flat plate: holes, slots, rectangular cut-outs, edge notches, corner chamfers
  Lv1  parallel bends only: L / U / Z / hat / multi-step chains, holes on any panel
  Lv2  base + one level of flanges on 2-4 sides (tray), partial-width flanges with reliefs
  Lv3  Lv2 plus flanges on flanges (return flanges, steps) up to depth 3
  Lv4  hems (180 deg bends): must be analyzed AND reported as hems (priced as a separate process)

Every sample is built and checked for self-consistency (valid single solid, folded volume ==
unfolded volume at K=0.5, unfolded outline is one polygon). Inconsistent samples - e.g.
flanges colliding when folded or overlapping when unfolded - are discarded and resampled.
"""
from __future__ import annotations

import math
import random
import zlib

from shapely.geometry import Polygon, box

from golden.sheetgen import SheetPart, circle, rect, slot

THICKNESSES = [0.8, 1.0, 1.2, 1.6, 2.0, 2.3, 3.2, 4.5]
ODD_ANGLES = [30, 45, 60, 120, 135]
LEVELS = ("Lv0", "Lv1", "Lv2", "Lv3", "Lv4")


class Sampler:
    def __init__(self, seed: int):
        self.rng = random.Random(seed)

    # ---------- primitives ----------
    def thickness(self):
        return self.rng.choice(THICKNESSES)

    def radius(self, t):
        return round(t * self.rng.choice([0.5, 1.0, 1.0, 1.5, 2.0]), 3)

    def angle(self, p_odd=0.35):
        return 90.0 if self.rng.random() > p_odd else float(self.rng.choice(ODD_ANGLES))

    def cutout(self, t, max_size):
        r = self.rng.random()
        lo = max(2.0 * t, 3.0)
        if max_size <= lo * 1.2:
            return None
        if r < 0.55:
            return circle(0, 0, round(self.rng.uniform(lo, min(max_size, 25)), 2))
        if r < 0.8:
            width = round(self.rng.uniform(lo, min(max_size * 0.5, 12)), 2)
            length = round(self.rng.uniform(width * 1.6, max(width * 1.7, min(max_size, 45))), 2)
            return slot(0, 0, length, width, angle=self.rng.choice([0, 0, 90, 45]))
        w = round(self.rng.uniform(lo, min(max_size, 40)), 2)
        h = round(self.rng.uniform(lo, min(max_size, 30)), 2)
        return rect(0, 0, w, h, angle=self.rng.choice([0, 0, 0, 30]))

    def place_cutouts(self, panel_poly: Polygon, t: float, count: int, margin: float):
        """Place non-overlapping interior cut-outs inside panel_poly eroded by margin."""
        placed, shapes = [], []
        free = panel_poly.buffer(-margin)
        if free.is_empty:
            return placed
        minx, miny, maxx, maxy = free.bounds
        max_size = min(maxx - minx, maxy - miny)
        for _ in range(count):
            for _attempt in range(25):
                c = self.cutout(t, max_size)
                if c is None:
                    break
                c.u, c.v = self.rng.uniform(minx, maxx), self.rng.uniform(miny, maxy)
                s = c.shapely()
                if free.contains(s) and not any(s.buffer(max(2 * t, 2.0)).intersects(o) for o in shapes):
                    placed.append(c); shapes.append(s)
                    break
        return placed

    def edge_notch(self, t, W, H):
        """Rectangular notch centred on one side of a W x H base (becomes part of the outline)."""
        side = self.rng.randrange(4)
        w = self.rng.uniform(max(3 * t, 4), max(3 * t, 4) + min(W, H) * 0.2)
        d = self.rng.uniform(max(2 * t, 3), max(2 * t, 3) + min(W, H) * 0.15)
        if side == 0:
            return rect(self.rng.uniform(w, W - w), 0, w, 2 * d)
        if side == 1:
            return rect(W, self.rng.uniform(w, H - w), 2 * d, w)
        if side == 2:
            return rect(self.rng.uniform(w, W - w), H, w, 2 * d)
        return rect(0, self.rng.uniform(w, H - w), 2 * d, w)

    @staticmethod
    def base_edges(W, H):
        # counter-clockwise => parent (base) is on the LEFT of each edge
        return [((0, 0), (W, 0)), ((W, 0), (W, H)), ((W, H), (0, H)), ((0, H), (0, 0))]

    def holes_in_flange(self, part, f, p=0.6):
        if self.rng.random() < p:
            poly = box(0, f.ba, f.span, f.ba + f.length)
            margin = max(part.t + f.radius + 1.0, 3.0)
            f.cutouts += self.place_cutouts(poly, part.t, self.rng.randint(1, 3), margin)

    def base_holes(self, part, W, H, n_max=6, notch_p=0.3):
        margin = max(3 * part.t, 4.0)
        part.cut_base(*self.place_cutouts(box(0, 0, W, H), part.t, self.rng.randint(0, n_max), margin))
        if self.rng.random() < notch_p:
            part.cut_base(self.edge_notch(part.t, W, H))

    # ---------- levels ----------
    def lv0(self, name):
        t = self.thickness()
        W, H = self.rng.uniform(40, 300), self.rng.uniform(30, 200)
        outline = box(0, 0, W, H)
        if self.rng.random() < 0.4:
            c = self.rng.uniform(3, min(W, H) * 0.25)
            outline = Polygon([(0, 0), (W, 0), (W, H - c), (W - c, H), (0, H)])
        part = SheetPart(name, t, outline, note="平板")
        part.cut_base(*self.place_cutouts(outline, t, self.rng.randint(1, 10), max(3 * t, 4.0)))
        if self.rng.random() < 0.5:
            part.cut_base(self.edge_notch(t, W, H * 0.8))
        return part

    def lv1(self, name):
        t = self.thickness()
        kind = self.rng.choice(["L", "U", "Z", "hat", "chain"])
        W, H = self.rng.uniform(20, 150), self.rng.uniform(20, 200)  # W: across bends, H: bend length
        part = SheetPart(name, t, box(0, 0, W, H), note=f"平行曲げ {kind}")
        right, left = ((W, 0), (W, H)), ((0, H), (0, 0))
        L = lambda: self.rng.uniform(max(6 * t, 10), 80)
        flanges = []
        if kind == "L":
            flanges.append(part.flange(part.base, right, L(), angle=self.angle(), radius=self.radius(t)))
        elif kind == "U":
            a = self.angle(0.2)
            flanges += [part.flange(part.base, right, L(), angle=a, radius=self.radius(t)),
                        part.flange(part.base, left, L(), angle=a, radius=self.radius(t))]
        elif kind == "Z":
            f1 = part.flange(part.base, right, L(), angle=self.angle(), radius=self.radius(t))
            f2 = part.flange(f1.panel, part.far_edge(f1), L(), angle=f1.angle, radius=f1.radius, up=False)
            flanges += [f1, f2]
        elif kind == "hat":
            r = self.radius(t)
            wl = part.flange(part.base, left, L(), radius=r)
            wr = part.flange(part.base, right, wl.length, radius=r)
            brim = L() * 0.5
            flanges += [wl, wr, part.flange(wl.panel, part.far_edge(wl), brim, radius=r, up=False),
                        part.flange(wr.panel, part.far_edge(wr), brim, radius=r, up=False)]
        else:
            prev = part.flange(part.base, right, L(), angle=self.angle(), radius=self.radius(t))
            flanges.append(prev)
            for _ in range(self.rng.randint(1, 3)):
                prev = part.flange(prev.panel, part.far_edge(prev), L(), angle=self.angle(),
                                   radius=self.radius(t), up=self.rng.random() < 0.5)
                flanges.append(prev)
        self.base_holes(part, W, H, notch_p=0.0)
        for f in flanges:
            self.holes_in_flange(part, f)
        return part

    def _tray(self, name, note):
        t = self.thickness()
        W, H = self.rng.uniform(50, 300), self.rng.uniform(40, 220)
        part = SheetPart(name, t, box(0, 0, W, H), note=note)
        sides = self.rng.sample(range(4), self.rng.randint(2, 4))
        up = self.rng.random() < 0.85
        flanges = []
        for s in sides:
            (a, b) = self.base_edges(W, H)[s]
            r = self.radius(t)
            length = self.rng.uniform(max(6 * t, 10), 60)
            edge_len = math.dist(a, b)
            gap = r + t + 1.0  # keep clear of neighbouring flanges at the corners
            partial = self.rng.random() < 0.3
            if partial:
                s0 = self.rng.uniform(gap, edge_len * 0.4)
                s1 = self.rng.uniform(edge_len * 0.6, edge_len - gap)
            else:
                s0, s1 = gap, edge_len - gap
            ux, uy = (b[0] - a[0]) / edge_len, (b[1] - a[1]) / edge_len
            pa, pb = (a[0] + ux * s0, a[1] + uy * s0), (a[0] + ux * s1, a[1] + uy * s1)
            f = part.flange(part.base, (pa, pb), length, angle=self.angle(0.2), radius=r, up=up)
            flanges.append(f)
            if partial and self.rng.random() < 0.7:  # bend reliefs cut into the base at both ends
                rw, rd = max(t, 1.0), r + t + 0.5
                nx, ny = -uy, ux  # inward normal (base side)
                ang = math.degrees(math.atan2(uy, ux))
                for sp in (s0 - rw / 2, s1 + rw / 2):
                    cx, cy = a[0] + ux * sp + nx * rd / 2, a[1] + uy * sp + ny * rd / 2
                    part.cut_base(rect(cx, cy, rw, rd, angle=ang))
        self.base_holes(part, W, H, notch_p=0.0)
        for f in flanges:
            self.holes_in_flange(part, f)
        return part, flanges

    def lv2(self, name):
        return self._tray(name, "底面＋1段フランジ")[0]

    def lv3(self, name):
        part, flanges = self._tray(name, "多段フランジ")
        t = part.t
        frontier = list(flanges)
        added = 0
        for depth in range(self.rng.randint(1, 2)):
            nxt = []
            for f in frontier:
                if self.rng.random() < (0.8 if added == 0 else 0.5):
                    top = f.ba + f.length
                    s0, s1 = 0.0, f.span
                    if self.rng.random() < 0.3:
                        s0, s1 = f.span * self.rng.uniform(0.05, 0.3), f.span * self.rng.uniform(0.7, 0.95)
                    g = part.flange(f.panel, ((s1, top), (s0, top)), self.rng.uniform(max(5 * t, 8), 40),
                                    angle=self.angle(0.3), radius=self.radius(t), up=self.rng.random() < 0.6)
                    self.holes_in_flange(part, g, p=0.4)
                    nxt.append(g); added += 1
            frontier = nxt
        if added == 0:
            f = flanges[0]
            g = part.flange(f.panel, part.far_edge(f), max(5 * t, 8), radius=self.radius(t))
        return part

    def lv4(self, name):
        """Hem: 180-degree bend with a small radius (open hem). Analyzer must unfold it and report it."""
        t = self.thickness()
        W, H = self.rng.uniform(30, 150), self.rng.uniform(20, 200)
        part = SheetPart(name, t, box(0, 0, W, H), note="ヘム（180°曲げ）")
        r = round(t * self.rng.choice([0.5, 1.0]), 3)
        part.flange(part.base, ((W, 0), (W, H)), self.rng.uniform(max(4 * t, 6), min(W * 0.8, 30)),
                    angle=180.0, radius=r, up=True)
        if self.rng.random() < 0.5:
            part.flange(part.base, ((0, H), (0, 0)), self.rng.uniform(10, 50), radius=self.radius(t))
        self.base_holes(part, W * 0.7, H, notch_p=0.0)
        return part

    def sample(self, level: str, name: str):
        return getattr(self, level.lower())(name)


def generate(level: str, index: int, seed: int, max_tries: int = 30):
    """Deterministically generate one consistent part: returns (part, built) or raises."""
    last = None
    for attempt in range(max_tries):
        sampler = Sampler(zlib.crc32(f"{seed}:{level}:{index}:{attempt}".encode()))  # stable across runs
        part = sampler.sample(level, f"{level}_{index:04d}")
        part.level = level
        try:
            built = part.build()
        except Exception as exc:  # degenerate geometry -> resample
            last = exc
            continue
        if SheetPart.is_consistent(built[2]):
            built[2]["seed"] = [seed, index, attempt]
            return part, built
        last = built[2]["_checks"]
    raise RuntimeError(f"could not generate a consistent {level} part #{index}: {last}")
