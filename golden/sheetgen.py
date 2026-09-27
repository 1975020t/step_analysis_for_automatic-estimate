"""Parametric sheet-metal golden-data generator.

A part is defined in its FLAT layout (2D, mm): a base polygon plus a tree of
flanges. Each flange hangs off a straight edge of its parent and has a bend
(angle, inner radius, up/down) and a flat straight length. Holes/cutouts are
analytic 2D primitives (true circles/arcs, not polygons) in the panel's local frame.

From one definition we build:
  * folded 3D solid   (bend regions = exact cylindrical sectors, holes = true cylinders)
  * unfolded solid    (bend strips = bend allowance for the given K-factor)
  * ground truth      (from the unfolded solid: area = V/t, cut length = side area/t;
                       hole count from the 2D layout). Never uses the analyzer under test.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

import cadquery as cq
import numpy as np
from shapely import affinity
from shapely.geometry import Point, Polygon, box
from shapely.ops import unary_union


# ---------- analytic cutouts (local frame: u along edge, v away from bend) ----------
@dataclass
class Cutout:
    kind: str
    u: float
    v: float
    a: float          # diameter / width / slot length
    b: float = 0.0    # height / slot width
    angle: float = 0.0

    def shapely(self):  # only for layout checks + hole counting
        if self.kind == "circle":
            return Point(self.u, self.v).buffer(self.a / 2, quad_segs=64)
        if self.kind == "rect":
            s = box(-self.a / 2, -self.b / 2, self.a / 2, self.b / 2)
        else:
            s = box(-(self.a - self.b) / 2, -1e-9, (self.a - self.b) / 2, 1e-9).buffer(self.b / 2, quad_segs=64)
        return affinity.translate(affinity.rotate(s, self.angle, origin=(0, 0)), self.u, self.v)

    def tool(self, t):  # solid cutting tool through the sheet (local z in [0, t])
        wp = cq.Workplane("XY").workplane(offset=-1)
        if self.kind == "circle":
            s = wp.circle(self.a / 2).extrude(t + 2)
        elif self.kind == "rect":
            s = wp.rect(self.a, self.b).extrude(t + 2)
        else:
            s = wp.slot2D(self.a, self.b).extrude(t + 2)
        return s.val().rotate((0, 0, 0), (0, 0, 1), self.angle).translate((self.u, self.v, 0))


def circle(u, v, d):
    return Cutout("circle", u, v, d)


def rect(u, v, w, h, angle=0.0):
    return Cutout("rect", u, v, w, h, angle)


def slot(u, v, length, width, angle=0.0):
    return Cutout("slot", u, v, length, width, angle)


# ---------- part definition ----------
@dataclass
class Flange:
    parent: "Panel"
    edge: tuple[tuple[float, float], tuple[float, float]]  # in PARENT local coords, parent on the left
    length: float                                          # flat straight length
    angle: float = 90.0
    radius: float | None = None                            # inner radius (default = t)
    up: bool = True
    cutouts: list = field(default_factory=list)


@dataclass
class Panel:
    polygon: Polygon          # outline in local 2D frame (no cutouts)
    local: np.ndarray         # 4x4: local 3D -> flat 3D
    fold: np.ndarray          # 4x4: flat 3D -> folded 3D
    cutouts: list = field(default_factory=list)


def _affine2d(M):
    return [M[0, 0], M[0, 1], M[1, 0], M[1, 1], M[0, 3], M[1, 3]]


class SheetPart:
    def __init__(self, name, thickness, base_outline: Polygon, k_factor=0.5, note=""):
        self.name, self.t, self.k, self.note = name, thickness, k_factor, note
        self.base = Panel(base_outline, np.eye(4), np.eye(4))
        self.flanges: list[Flange] = []

    # --- API ---
    def cut_base(self, *cutouts):
        self.base.cutouts.extend(cutouts)
        return self

    def flange(self, parent: Panel, edge, length, **kw):
        f = Flange(parent, edge, length, **kw)
        f.radius = self.t if f.radius is None else f.radius
        f.panel = self._flange_panel(f)
        f.cutouts = f.panel.cutouts  # user appends to f.cutouts
        self.flanges.append(f)
        return f

    @staticmethod
    def far_edge(f: Flange):
        """Free edge of a flange in its own local frame (for chaining)."""
        top = f.ba + f.length
        return ((f.span, top), (0.0, top))

    # --- geometry ---
    def _flat_outline(self, panel):
        return affinity.affine_transform(panel.polygon, _affine2d(panel.local))

    def _flange_panel(self, f: Flange) -> Panel:
        (a, b) = f.edge
        pa = (f.parent.local @ np.array([a[0], a[1], 0, 1.0]))[:2]
        pb = (f.parent.local @ np.array([b[0], b[1], 0, 1.0]))[:2]
        # local frame: u runs pb->pa, v outward => u x v = +z (proper rotation, no mirroring)
        d = pa - pb
        f.span = float(np.linalg.norm(d)); d = d / f.span
        n = np.array([-d[1], d[0]])
        probe = pb + d * f.span / 2 + n * 1e-3
        if self._flat_outline(f.parent).contains(Point(*probe)):
            raise ValueError("edge orientation: traverse the parent edge with the parent on the LEFT")
        f.ba = math.radians(f.angle) * (f.radius + self.k * self.t)
        C = np.eye(4)
        C[:3, 0], C[:3, 1], C[:3, 3] = [*d, 0], [*n, 0], [*pb, 0]
        f.canon = C
        return Panel(box(0, f.ba, f.span, f.ba + f.length), C, f.parent.fold @ self._fold_matrix(f))

    def _fold_matrix(self, f):
        th = math.radians(f.angle) * (1 if f.up else -1)
        zc = self.t + f.radius if f.up else -f.radius
        T = np.eye(4); T[1, 3] = -f.ba
        c, s = math.cos(th), math.sin(th)
        R = np.array([[1, 0, 0, 0], [0, c, -s, 0], [0, s, c, 0], [0, 0, 0, 1]])
        Z1 = np.eye(4); Z1[2, 3] = -zc
        Z2 = np.eye(4); Z2[2, 3] = zc
        return f.canon @ Z2 @ R @ Z1 @ T @ np.linalg.inv(f.canon)

    @staticmethod
    def _xform(shape, M):
        from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
        from OCP.gp import gp_Trsf
        u, _, vt = np.linalg.svd(M[:3, :3]); R = u @ vt
        trsf = gp_Trsf()
        trsf.SetValues(*R[0], M[0, 3], *R[1], M[1, 3], *R[2], M[2, 3])
        return cq.Shape.cast(BRepBuilderAPI_Transform(shape.wrapped, trsf, True).Shape())

    @staticmethod
    def _extrude(poly: Polygon, t: float):
        pts = [cq.Vector(x, y, 0) for x, y in list(poly.exterior.coords)[:-1]]
        face = cq.Face.makeFromWires(cq.Wire.makePolygon(pts, close=True))
        return cq.Solid.extrudeLinear(face, cq.Vector(0, 0, t))

    def _panel_local_solid(self, panel):
        solid = self._extrude(panel.polygon, self.t)
        for c in panel.cutouts:
            solid = solid.cut(c.tool(self.t))
        return solid

    def _bend_solid(self, f):
        t, r, a = self.t, f.radius, math.radians(f.angle)
        if f.up:
            zc, sign = t + r, -1.0
        else:
            zc, sign = -r, 1.0
        pt = lambda R, ang: (R * math.sin(ang), zc + sign * R * math.cos(ang))
        r_bot, r_top = (r + t, r) if f.up else (r, r + t)
        prof = (cq.Workplane("YZ").moveTo(*pt(r_bot, 0)).threePointArc(pt(r_bot, a / 2), pt(r_bot, a))
                .lineTo(*pt(r_top, a)).threePointArc(pt(r_top, a / 2), pt(r_top, 0)).close())
        return self._xform(prof.extrude(f.span).val(), f.parent.fold @ f.canon)

    def panels(self):
        return [self.base] + [f.panel for f in self.flanges]

    def folded_solid(self):
        parts = [self._xform(self._panel_local_solid(p), p.fold @ p.local) for p in self.panels()]
        parts += [self._bend_solid(f) for f in self.flanges]
        result = parts[0]
        for p in parts[1:]:
            result = result.fuse(p)
        return result.clean()

    def flat_outline(self) -> Polygon:
        regions = [self._flat_outline(p) for p in self.panels()]
        regions += [affinity.affine_transform(box(0, 0, f.span, f.ba), _affine2d(f.canon)) for f in self.flanges]
        eps = 1e-6
        u = unary_union([g.buffer(eps, join_style=2) for g in regions]).buffer(-eps, join_style=2)
        return u.simplify(1e-7)

    def flat_solid(self):
        solid = self._extrude(self.flat_outline(), self.t)
        for p in self.panels():
            for c in p.cutouts:
                solid = solid.cut(self._xform(c.tool(self.t), p.local))
        return solid.clean()

    def flat_layout_with_holes(self) -> Polygon:
        cuts = [affinity.affine_transform(c.shapely(), _affine2d(p.local)) for p in self.panels() for c in p.cutouts]
        return self.flat_outline().difference(unary_union(cuts)) if cuts else self.flat_outline()

    # --- build / export ---
    def build(self):
        """Return (folded_solid, flat_solid, truth). truth['_checks'] holds self-consistency checks."""
        folded, flat = self.folded_solid(), self.flat_solid()
        t = self.t
        side = [f for f in flat.Faces() if not (f.geomType() == "PLANE" and abs(f.normalAt().z) > 0.999)]
        layout = self.flat_layout_with_holes()
        truth = {
            "name": self.name, "level": getattr(self, "level", None), "note": self.note,
            "thickness_mm": t, "k_factor": self.k,
            "blank_area_mm2": round(flat.Volume() / t, 4),
            "cut_length_mm": round(sum(f.Area() for f in side) / t, 4),
            "hole_count": len(getattr(layout, "interiors", [])),
            "bend_count": len(self.flanges),
            "flat_bbox_mm": [round(v, 3) for v in (layout.bounds[2] - layout.bounds[0], layout.bounds[3] - layout.bounds[1])],
            "bends": [{"angle_deg": f.angle, "inner_radius_mm": f.radius, "direction": "up" if f.up else "down",
                       "length_mm": round(f.span, 4), "bend_allowance_mm": round(f.ba, 4)} for f in self.flanges],
            "_checks": {  # generator self-consistency, independent of the analyzer
                "folded_valid": bool(folded.isValid()),
                "folded_solids": len(folded.Solids()),
                "folded_faces": len(folded.Faces()),
                "flat_valid": bool(flat.isValid()),
                "flat_is_single_polygon": layout.geom_type == "Polygon",
                "volume_folded_vs_flat": round(folded.Volume() / flat.Volume() - 1, 8),
                "area_solid_vs_layout": round(flat.Volume() / t / layout.area - 1, 6),
            },
        }
        return folded, flat, truth

    @staticmethod
    def is_consistent(truth, tol=1e-6) -> bool:
        c = truth["_checks"]
        return (c["folded_valid"] and c["flat_valid"] and c["folded_solids"] == 1 and c["flat_is_single_polygon"]
                and abs(c["volume_folded_vs_flat"]) <= tol and abs(c["area_solid_vs_layout"]) <= 1e-4)

    def export(self, out_dir: Path, built=None):
        out_dir.mkdir(parents=True, exist_ok=True)
        folded, flat, truth = built or self.build()
        cq.exporters.export(folded, str(out_dir / f"{self.name}.step"))
        cq.exporters.export(flat, str(out_dir / f"{self.name}_unfolded.step"))
        (out_dir / f"{self.name}_truth.json").write_text(json.dumps(truth, ensure_ascii=False, indent=2))
        return truth
