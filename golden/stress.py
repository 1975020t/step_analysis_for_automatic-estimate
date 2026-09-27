"""Stress set: parts built differently from golden_v1, to test how far the analyzer generalizes.

golden_v1 / holdout_v1 come from one recipe (golden/sampler.py): an axis-aligned rectangular base in
the XY plane with flanges on its straight edges, corners always kept apart. This module builds parts
the frozen sets never contain. It only IMPORTS golden/sheetgen.py and golden/sampler.py (both frozen);
truth values come from the same unfolded-solid computation (golden_v2 hole count, golden/truth_v2.py).

Category A - placement (same shapes, new pose)
  A_Lv0 .. A_Lv4   a golden-recipe part (seed 21) moved by a random rotation + translation.
                   Area, cut length, holes and bends are invariant, so the truth is unchanged.

Category C - new shapes
  C1_slanted       convex polygon base (5-7 sides, slanted edges) with flanges on 2-4 of its edges
  C2_angles        tray whose flanges use acute/obtuse angles (30-150 deg), up and down mixed,
                   plus a second level with its own angles
  C3_closed        U-channel whose two return flanges meet edge to edge: the folded solid is a closed
                   tube, so the unfold graph has a cycle. The flat blank has two free edges that are
                   fused in 3D, so exact values are not expected; a correct analyzer must flag it (概算)
  C4_short         bends separated by very short flat lengths (jogs / offsets, 0.5t-2t between bends)
  C5_side          flanges attached to the SIDE edge of another flange (bend axes perpendicular)
  C6_bigflange     flanges much larger than the base (the largest face is not the base)
  C7_many          rectangular tray with flanges on all 4 sides and up to 3 levels (many bends)
"""
from __future__ import annotations

import math
import random
import zlib

import numpy as np
from shapely.geometry import Polygon, box

from golden.sampler import Sampler, generate
from golden.sheetgen import SheetPart
from golden.truth_v2 import apply_v2

A_LEVELS = ("A_Lv0", "A_Lv1", "A_Lv2", "A_Lv3", "A_Lv4")
C_LEVELS = ("C1_slanted", "C2_angles", "C3_closed", "C4_short", "C5_side", "C6_bigflange", "C7_many")
LEVELS = A_LEVELS + C_LEVELS
NOTES = {
    "C1_slanted": "多角形底面・斜め辺フランジ", "C2_angles": "鋭角・鈍角・上下混在トレー",
    "C3_closed": "閉じた筒（返し突き合わせ）", "C4_short": "短い平坦部（ジョグ）",
    "C5_side": "フランジ側辺のフランジ", "C6_bigflange": "底面より大きいフランジ", "C7_many": "多フランジ3段",
}


# ---------------------------------------------------------------- A: pose
def random_pose(rng: random.Random) -> np.ndarray:
    q = np.array([rng.gauss(0, 1) for _ in range(4)])
    w, x, y, z = q / np.linalg.norm(q)
    rotation = np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
    ])
    matrix = np.eye(4)
    matrix[:3, :3] = rotation
    matrix[:3, 3] = [rng.uniform(-500, 500) for _ in range(3)]
    return matrix


def build_a(level: str, index: int, seed: int):
    base_level = level.split("_")[1]
    part, built = generate(base_level, index, seed)
    folded, flat, truth = built
    apply_v2(built)
    pose = random_pose(random.Random(zlib.crc32(f"pose:{seed}:{level}:{index}".encode())))
    moved = SheetPart._xform(folded, pose)
    truth = dict(truth, name=f"{level}_{index:04d}", level=level, note=f"{truth['note']}（任意姿勢）",
                 pose=[[round(v, 9) for v in row] for row in pose.tolist()])
    return part, (moved, flat, truth)


# ---------------------------------------------------------------- C: new shapes
class StressSampler(Sampler):
    """Adds new shape families; primitives (thickness, radius, cut-outs) are the golden ones."""

    def odd_angle(self):
        return float(self.rng.choice([30, 45, 60, 75, 90, 105, 120, 135, 150]))

    def flange_len(self, t, hi=50):
        return self.rng.uniform(max(6 * t, 10), hi)

    def c1_slanted(self, name):
        t = self.thickness()
        n = self.rng.randint(5, 7)
        radius = self.rng.uniform(40, 120)
        angles = sorted(self.rng.uniform(0, 2 * math.pi) for _ in range(n))
        points = [(radius * math.cos(a) * self.rng.uniform(0.85, 1.15), radius * math.sin(a) * self.rng.uniform(0.85, 1.15))
                  for a in angles]
        poly = Polygon(points).convex_hull
        coords = list(poly.exterior.coords)[:-1]
        if not poly.exterior.is_ccw:
            coords = coords[::-1]
        part = SheetPart(name, t, Polygon(coords), note=NOTES["C1_slanted"])
        edges = [(coords[i], coords[(i + 1) % len(coords)]) for i in range(len(coords))]
        flanges = []
        for a, b in self.rng.sample(edges, min(len(edges), self.rng.randint(2, 4))):
            length = math.dist(a, b)
            r = self.radius(t)
            gap = max(r + t + 2.0, length * 0.15)
            if length - 2 * gap < 10:
                continue
            ux, uy = (b[0] - a[0]) / length, (b[1] - a[1]) / length
            pa = (a[0] + ux * gap, a[1] + uy * gap)
            pb = (b[0] - ux * gap, b[1] - uy * gap)
            flanges.append(part.flange(part.base, (pa, pb), self.flange_len(t, 40), angle=self.angle(0.3), radius=r))
        self.holes_in_base(part, poly, t)
        for f in flanges:
            self.holes_in_flange(part, f)
        return part

    def holes_in_base(self, part, poly, t):
        part.cut_base(*self.place_cutouts(poly, t, self.rng.randint(0, 5), max(3 * t, 4.0) + 2 * t))

    def c2_angles(self, name):
        t = self.thickness()
        W, H = self.rng.uniform(60, 250), self.rng.uniform(50, 200)
        part = SheetPart(name, t, box(0, 0, W, H), note=NOTES["C2_angles"])
        flanges = []
        for s in self.rng.sample(range(4), self.rng.randint(2, 4)):
            (a, b) = self.base_edges(W, H)[s]
            edge_len = math.dist(a, b)
            r = self.radius(t)
            gap = r + t + 3.0 + edge_len * 0.05
            ux, uy = (b[0] - a[0]) / edge_len, (b[1] - a[1]) / edge_len
            pa, pb = (a[0] + ux * gap, a[1] + uy * gap), (b[0] - ux * gap, b[1] - uy * gap)
            f = part.flange(part.base, (pa, pb), self.flange_len(t, 45), angle=self.odd_angle(), radius=r,
                            up=self.rng.random() < 0.5)
            flanges.append(f)
            if self.rng.random() < 0.6:
                part.flange(f.panel, part.far_edge(f), self.flange_len(t, 30), angle=self.odd_angle(),
                            radius=self.radius(t), up=self.rng.random() < 0.5)
        self.base_holes(part, W, H, notch_p=0.0)
        for f in flanges:
            self.holes_in_flange(part, f)
        return part

    def c3_closed(self, name):
        t = self.thickness()
        W, H = self.rng.uniform(40, 120), self.rng.uniform(40, 200)   # W across bends, H along bends
        r = self.radius(t)
        wall = self.rng.uniform(max(6 * t, 15), 60)
        part = SheetPart(name, t, box(0, 0, W, H), note=NOTES["C3_closed"])
        right, left = ((W, 0), (W, H)), ((0, H), (0, 0))
        walls = [part.flange(part.base, e, wall, radius=r) for e in (right, left)]
        # returns bend inward (towards the channel centre); solve their length so the free edges meet
        probe = []
        for w in walls:
            g = part.flange(w.panel, part.far_edge(w), 10.0, radius=r, up=True)
            probe.append(g)
        starts, dirs = [], []
        for g in probe:
            m = g.panel.fold @ g.panel.local
            p0 = (m @ np.array([g.span / 2, g.ba, 0, 1.0]))[:3]
            p1 = (m @ np.array([g.span / 2, g.ba + 1.0, 0, 1.0]))[:3]
            starts.append(p0)
            dirs.append(p1 - p0)
        gap = float(np.dot(starts[1] - starts[0], dirs[0]))
        part.flanges = part.flanges[:2]
        length = gap / 2
        if length < max(3 * t, 5):
            raise ValueError("channel too narrow for returns")
        for w in walls:
            part.flange(w.panel, part.far_edge(w), length, radius=r, up=True)
        self.base_holes(part, W, H, n_max=3, notch_p=0.0)
        for w in walls:
            self.holes_in_flange(part, w, p=0.5)
        return part

    def c4_short(self, name):
        t = self.thickness()
        W, H = self.rng.uniform(20, 100), self.rng.uniform(20, 150)
        part = SheetPart(name, t, box(0, 0, W, H), note=NOTES["C4_short"])
        prev = part.flange(part.base, ((W, 0), (W, H)), self.rng.uniform(0.5 * t, 2 * t), angle=self.odd_angle(),
                           radius=self.radius(t))
        up = False
        for _ in range(self.rng.randint(1, 3)):
            short = self.rng.random() < 0.6
            length = self.rng.uniform(0.5 * t, 2 * t) if short else self.flange_len(t, 40)
            prev = part.flange(prev.panel, part.far_edge(prev), length, angle=self.odd_angle(),
                               radius=self.radius(t), up=up)
            up = not up
        part.flange(prev.panel, part.far_edge(prev), self.flange_len(t, 40), angle=90.0, radius=self.radius(t),
                    up=up)
        self.base_holes(part, W, H, n_max=4, notch_p=0.0)
        return part

    def c5_side(self, name):
        t = self.thickness()
        W, H = self.rng.uniform(60, 200), self.rng.uniform(50, 160)
        part = SheetPart(name, t, box(0, 0, W, H), note=NOTES["C5_side"])
        r = self.radius(t)
        (a, b) = self.base_edges(W, H)[0]
        gap = r + t + 5.0
        f = part.flange(part.base, ((a[0] + gap, 0), (b[0] - gap, 0)), self.rng.uniform(25, 60), radius=r)
        margin = f.ba + r + t + 1.0
        top = f.ba + f.length
        sides = [((f.span, margin), (f.span, top)), ((0.0, top), (0.0, margin))]
        for edge in self.rng.sample(sides, self.rng.randint(1, 2)):
            part.flange(f.panel, edge, self.rng.uniform(max(6 * t, 10), 35), angle=self.odd_angle(),
                        radius=self.radius(t), up=self.rng.random() < 0.5)
        self.base_holes(part, W, H, notch_p=0.0)
        self.holes_in_flange(part, f)
        return part

    def c6_bigflange(self, name):
        t = self.thickness()
        W, H = self.rng.uniform(15, 40), self.rng.uniform(40, 150)
        part = SheetPart(name, t, box(0, 0, W, H), note=NOTES["C6_bigflange"])
        f = part.flange(part.base, ((W, 0), (W, H)), self.rng.uniform(80, 200), angle=self.angle(0.4),
                        radius=self.radius(t))
        if self.rng.random() < 0.6:
            part.flange(part.base, ((0, H), (0, 0)), self.rng.uniform(60, 180), angle=self.angle(0.4),
                        radius=self.radius(t), up=self.rng.random() < 0.5)
        if self.rng.random() < 0.5:
            part.flange(f.panel, part.far_edge(f), self.flange_len(t, 30), radius=self.radius(t),
                        up=self.rng.random() < 0.5)
        self.holes_in_flange(part, f, p=0.9)
        return part

    def c7_many(self, name):
        t = self.thickness()
        W, H = self.rng.uniform(100, 300), self.rng.uniform(80, 220)
        part = SheetPart(name, t, box(0, 0, W, H), note=NOTES["C7_many"])
        frontier = []
        for (a, b) in self.base_edges(W, H):
            edge_len = math.dist(a, b)
            r = self.radius(t)
            gap = r + t + 2.0
            ux, uy = (b[0] - a[0]) / edge_len, (b[1] - a[1]) / edge_len
            f = part.flange(part.base, ((a[0] + ux * gap, a[1] + uy * gap), (b[0] - ux * gap, b[1] - uy * gap)),
                            self.flange_len(t, 40), radius=r)
            frontier.append(f)
        for _ in range(2):
            nxt = []
            for f in frontier:
                if self.rng.random() < 0.85:
                    top = f.ba + f.length
                    s0, s1 = f.span * 0.1, f.span * 0.9
                    g = part.flange(f.panel, ((s1, top), (s0, top)), self.rng.uniform(max(5 * t, 8), 25),
                                    angle=self.angle(0.3), radius=self.radius(t), up=self.rng.random() < 0.5)
                    nxt.append(g)
            frontier = nxt
        self.base_holes(part, W, H, notch_p=0.0)
        for f in part.flanges[:4]:
            self.holes_in_flange(part, f, p=0.5)
        return part


def build_c(level: str, index: int, seed: int, max_tries: int = 40):
    last = None
    for attempt in range(max_tries):
        sampler = StressSampler(zlib.crc32(f"stress:{seed}:{level}:{index}:{attempt}".encode()))
        try:
            part = getattr(sampler, level.split("_")[0].lower() + "_" + level.split("_", 1)[1])(f"{level}_{index:04d}")
            part.level = level
            built = part.build()
        except Exception as exc:  # degenerate geometry -> resample
            last = exc
            continue
        if SheetPart.is_consistent(built[2]):
            apply_v2(built)
            built[2]["seed"] = [seed, index, attempt]
            return part, built
        last = built[2]["_checks"]
    raise RuntimeError(f"could not generate a consistent {level} part #{index}: {last}")


def build(level: str, index: int, seed: int):
    return build_a(level, index, seed) if level.startswith("A_") else build_c(level, index, seed)
