"""Flat-pattern DXF analyzer (rule-based).

Input: a flat-pattern (展開図) DXF and the sheet thickness typed by the user (a flat DXF has none).
Output: SheetMetalAnalysis with blank area, cut length, hole count and bend count.

Pipeline
  1. Normalise the drawing: explode block INSERTs (block entities on layer 0 / BYBLOCK inherit the
     insert's layer, colour and linetype), turn LWPOLYLINE bulges, ARCs and CIRCLEs into curves, scale
     to mm with $INSUNITS. DIMENSION / TEXT / MTEXT are not geometry (texts are read for the thickness).
  2. Give every curve a role. Meaningful layer names (English / Japanese variants) decide first;
     otherwise the linetype does: continuous curves are cut candidates, non-continuous lines are
     bend-line or centre-line candidates.
  3. Merge endpoints closer than 0.02 mm (distance-based union-find, not grid rounding), drop
     duplicates, node the linework and split it into connected components.
  4. A component with several faces that encloses other geometry is the frame / title block. The
     remaining closed loops are nested: the outermost loop is the part, the loops inside it are holes.
  5. Problems are never confirmed: an open outline (gap >= 0.1 mm) -> OPEN_CONTOUR, two outlines ->
     MULTIPLE_PARTS, no closed outline -> NO_CUT_CONTOUR (all unsupported). Anything the rules cannot
     explain (a gap closed between 0.02 and 0.1 mm, stray or unexplained lines, a bend line that does
     not end on the outline, dimensions or a title-block thickness that disagree with the geometry)
     gives partial (概算).
  6. Bend lines = non-continuous (or bend-layer) straight lines, pieces merged, whose both ends lie on
     the outline. Centre lines are recognised by crossing at a hole centre.
"""
from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import BinaryIO

from src.models import AnalysisStage, FlatPatternSummary, MetricQuality, SheetMetalAnalysis

METRICS = ("thickness_mm", "blank_area_mm2", "cut_length_mm", "hole_count", "bend_count")
SLIVER_WIDTH_MM = 0.05  # faces thinner than this come from duplicated / overlapping curves
UNIT_MM = {1: 25.4, 2: 304.8, 4: 1.0, 5: 10.0, 6: 1000.0, 8: 25.4e-6, 9: 0.0254, 10: 914.4, 13: 1e-3, 14: 100.0}

ROLE_PATTERNS = [  # checked in order; first match wins
    ("bend", r"BEND|FOLD|曲げ|曲線|折り|折曲"),
    ("center", r"CENT(ER|RE)|^CL$|中心"),
    ("dim", r"DIM|寸法"),
    ("frame", r"FRAME|BORDER|TITLE|図枠|枠|表題"),
    ("mark", r"MARK|SCRIBE|ENGRAV|ETCH|ケガキ|罫書|刻印"),
    ("text", r"TEXT|NOTE|文字|注記"),
    ("cut", r"CUT|OUTLINE|OUTER|CONTOUR|PROFILE|外形|切断|輪郭|カット"),
]
NON_CUT_ROLES = {"bend", "center", "dim", "frame", "mark", "text"}
THICKNESS_TEXT = re.compile(r"(?:板厚|厚さ|THK|THICKNESS)\s*[:=：]?\s*[tT]?\s*(\d+(?:\.\d+)?)|(?<![A-Za-z])[tT]\s*=?\s*(\d+(?:\.\d+)?)")


def layer_role(name: str) -> str | None:
    upper = name.upper()
    for role, pattern in ROLE_PATTERNS:
        if re.search(pattern, upper):
            return role
    return None


@dataclass
class Curve:
    points: list[tuple[float, float]]
    role: str | None          # layer role, if the layer name means something
    continuous: bool
    color: int
    kind: str                 # LINE / ARC / CIRCLE / ...
    closed: bool = False


@dataclass
class Drawing:
    curves: list[Curve] = field(default_factory=list)
    texts: list[str] = field(default_factory=list)
    dimensions: list[tuple[float, float]] = field(default_factory=list)  # (measurement mm, angle deg)
    scale: float = 1.0
    units_known: bool = True


class DxfReadError(RuntimeError):
    pass


# ================================================================== reading / normalising
def read_drawing(path: Path, flatten_mm: float = 0.0005) -> Drawing:
    import ezdxf
    from ezdxf import path as ezpath

    try:
        doc = ezdxf.readfile(str(path))
    except (OSError, ezdxf.DXFError) as exc:
        raise DxfReadError(str(exc)) from exc
    units = int(doc.header.get("$INSUNITS", 0) or 0)
    drawing = Drawing(scale=UNIT_MM.get(units, 1.0), units_known=units in UNIT_MM)
    s = drawing.scale
    linetypes = {lt.dxf.name.upper(): lt for lt in doc.linetypes}
    layers = {layer.dxf.name: layer for layer in doc.layers}

    def is_continuous(name: str) -> bool:
        name = (name or "CONTINUOUS").upper()
        if name == "CONTINUOUS":
            return True
        lt = linetypes.get(name)
        try:
            return lt is not None and not any(abs(v) > 1e-9 for v in lt.simplified_line_pattern())
        except Exception:
            return False

    def resolve(entity, parent):
        layer_name = entity.dxf.get("layer", "0")
        if layer_name == "0" and parent is not None:
            layer_name = parent["layer"]
        layer = layers.get(layer_name)
        linetype = entity.dxf.get("linetype", "BYLAYER")
        if linetype.upper() == "BYBLOCK" and parent is not None:
            linetype = parent["linetype"]
        if linetype.upper() in ("BYLAYER", "BYBLOCK"):
            linetype = layer.dxf.get("linetype", "CONTINUOUS") if layer is not None else "CONTINUOUS"
        color = entity.dxf.get("color", 256)
        if color == 0 and parent is not None:
            color = parent["color"]
        if color in (0, 256):
            color = abs(layer.dxf.get("color", 7)) if layer is not None else 7
        return {"layer": layer_name, "linetype": linetype, "color": color}

    def visit(entity, parent, depth=0):
        kind = entity.dxftype()
        attrs = resolve(entity, parent)
        if kind == "INSERT":
            if depth > 8:
                return
            for sub in entity.virtual_entities():
                visit(sub, attrs, depth + 1)
            return
        if kind in ("TEXT", "MTEXT", "ATTRIB"):
            text = entity.plain_text() if kind == "MTEXT" else entity.dxf.get("text", "")
            drawing.texts.append(text)
            return
        if kind == "DIMENSION":
            try:
                drawing.dimensions.append((float(entity.get_measurement()) * s, float(entity.dxf.get("angle", 0.0))))
            except Exception:
                pass
            return
        if kind not in ("LINE", "ARC", "CIRCLE", "LWPOLYLINE", "POLYLINE", "ELLIPSE", "SPLINE"):
            return
        try:
            p = ezpath.make_path(entity)
        except Exception:
            return
        flat = [(float(v.x) * s, float(v.y) * s) for v in p.flattening(flatten_mm / s)]
        if len(flat) < 2:
            return
        closed = kind == "CIRCLE" or (kind in ("LWPOLYLINE", "POLYLINE") and entity.is_closed)
        if closed and math.dist(flat[0], flat[-1]) > 1e-9:
            flat.append(flat[0])
        drawing.curves.append(Curve(flat, layer_role(attrs["layer"]), is_continuous(attrs["linetype"]),
                                    int(attrs["color"]), kind, closed))

    for entity in doc.modelspace():
        visit(entity, None)
    return drawing


# ================================================================== endpoint merging
def merge_endpoints(curves: list[list[tuple[float, float]]], tol: float) -> tuple[list[list[tuple[float, float]]], float]:
    """Move endpoints closer than `tol` to their common centre (union-find, neighbour cells).

    Returns the curves and the largest gap that was closed.
    """
    ends = [p for c in curves for p in (c[0], c[-1])]
    parent = list(range(len(ends)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    cells: dict[tuple[int, int], list[int]] = defaultdict(list)
    largest = 0.0
    for i, (x, y) in enumerate(ends):
        cx, cy = math.floor(x / tol), math.floor(y / tol)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for j in cells.get((cx + dx, cy + dy), ()):
                    d = math.dist(ends[i], ends[j])
                    if d <= tol:
                        largest = max(largest, d)
                        parent[find(i)] = find(j)
        cells[(cx, cy)].append(i)
    groups: dict[int, list[int]] = defaultdict(list)
    for i in range(len(ends)):
        groups[find(i)].append(i)
    centre = {}
    for members in groups.values():
        xs = sum(ends[m][0] for m in members) / len(members)
        ys = sum(ends[m][1] for m in members) / len(members)
        for m in members:
            centre[m] = (xs, ys)
    out = []
    for j, c in enumerate(curves):
        c = list(c)
        c[0], c[-1] = centre[2 * j], centre[2 * j + 1]
        out.append(c)
    return out, largest


# ================================================================== analyzer
@dataclass
class _Component:
    lines: list
    faces: list
    dangles: int
    region: object = None   # union of faces (shapely)

    @property
    def closed(self):
        return bool(self.faces) and self.dangles == 0

    @property
    def multi_face(self):
        """Several real faces. Hairline faces between duplicated / overlapping curves do not count."""
        return sum(2 * f.area / max(f.length, 1e-12) >= SLIVER_WIDTH_MM for f in self.faces) > 1


class DxfAnalyzer:
    SNAP_MM = 0.02          # drawing gaps closed silently
    NEAR_GAP_MM = 0.1       # gaps up to this are closed but reported (partial); larger ones stay open
    ON_OUTLINE_MM = 0.05    # bend-line end on the outline
    MULTI_PART_RATIO = 0.05  # a second outer loop this large (area ratio) is another part

    def __init__(self, thickness_mm: float, k_factor: float = 0.33, k_factor_is_default: bool = True) -> None:
        self.thickness_mm = float(thickness_mm)
        self.k_factor = float(k_factor)
        self.k_factor_is_default = k_factor_is_default

    # ------------------------------------------------------------ entry
    def analyze(self, source: str | Path | BinaryIO, file_name: str | None = None) -> SheetMetalAnalysis:
        name = file_name or getattr(source, "name", None) or Path(str(source)).name
        temporary: Path | None = None
        try:
            if hasattr(source, "read"):
                if Path(name).suffix.lower() != ".dxf":
                    return self._failure(name, "error", "INVALID_FILE_TYPE", "拡張子が .dxf のファイルを指定してください。")
                data = source.read()
                with NamedTemporaryFile(suffix=".dxf", delete=False) as handle:
                    handle.write(data)
                    temporary = Path(handle.name)
                path = temporary
            else:
                path = Path(source)
                if path.suffix.lower() != ".dxf":
                    return self._failure(name, "error", "INVALID_FILE_TYPE", "拡張子が .dxf のファイルを指定してください。")
            if not self.thickness_mm > 0:
                return self._failure(name, "error", "THICKNESS_REQUIRED", "板厚（mm）を入力してください。")
            return self._analyze(path, Path(name).name)
        except DxfReadError as exc:
            return self._failure(name, "error", "DXF_READ_ERROR", f"DXFファイルを読み込めませんでした: {exc}")
        except Exception as exc:
            return self._failure(name, "error", "ANALYSIS_ERROR", f"解析中に予期しないエラーが発生しました: {exc}")
        finally:
            if temporary:
                temporary.unlink(missing_ok=True)

    # ------------------------------------------------------------ core
    def _analyze(self, path: Path, name: str) -> SheetMetalAnalysis:
        from shapely.geometry import LineString, Point
        from shapely.ops import unary_union

        drawing = read_drawing(path)
        stages = [AnalysisStage(name="DXF読込", status="success",
                                message=f"図形 {len(drawing.curves)}本、文字 {len(drawing.texts)}件、寸法 {len(drawing.dimensions)}件を読み込みました。")]
        warnings: list[str] = []
        reasons: list[str] = []
        assumptions: list[str] = []
        if not drawing.units_known:
            assumptions.append("DXFに単位（$INSUNITS）がないため、mmとして扱いました。")
            warnings.append("DXFの単位が不明です。mmとみなしました。")

        cut_curves = [c for c in drawing.curves if c.role == "cut" or (c.role not in NON_CUT_ROLES and c.continuous)]
        line_candidates = [c for c in drawing.curves if c not in cut_curves and c.role not in {"dim", "frame", "text", "mark"}
                           and c.kind == "LINE"]
        if not cut_curves:
            return self._failure(name, "unsupported", "NO_CUT_CONTOUR", "切断輪郭となる実線が見つかりません。", stages)

        # ---- linework: merge endpoints, drop duplicates, node, split into components
        merged, _ = merge_endpoints([c.points for c in cut_curves], self.SNAP_MM)
        components = self._components(merged)
        closed_gap = 0.0
        open_components = [comp for comp in components if not comp.closed and not comp.faces]
        if open_components:  # second pass: near gaps (0.02-0.1 mm) are closed, but reported
            merged2, gap = merge_endpoints(merged, self.NEAR_GAP_MM)
            components2 = self._components(merged2)
            if sum(not c.closed and not c.faces for c in components2) < len(open_components):
                components, merged, closed_gap = components2, merged2, gap
                reasons.append("SMALL_GAP_CLOSED")
                warnings.append(f"輪郭のすき間（最大 {gap:.3f} mm）を閉じて解析しました。図面の確認が必要です。")

        # ---- frame / title block: multi-face component that encloses other geometry
        frames = []
        for comp in components:
            if comp.multi_face and any(other is not comp and comp.region.contains(other.lines[0].representative_point())
                                       for other in components):
                frames.append(comp)
        rest = [comp for comp in components if comp not in frames]
        loops = [comp for comp in rest if comp.faces]
        opens = [comp for comp in rest if not comp.faces]
        for comp in loops:
            if comp.multi_face or comp.dangles:
                reasons.append("UNEXPLAINED_GEOMETRY")
                warnings.append("輪郭に内部の線または枝分かれがあります。")
        if not loops and not opens:
            return self._failure(name, "unsupported", "NO_CUT_CONTOUR", "図枠以外に切断輪郭が見つかりません。", stages)

        outer_loops = [comp for comp in loops
                       if not any(o is not comp and o.region.contains(comp.region.representative_point()) and
                                  o.region.area > comp.region.area for o in loops)]
        outer_loops.sort(key=lambda comp: -comp.region.area)

        # ---- open outline: an open chain that is not inside a closed part and is not short/interior
        part_region = outer_loops[0].region if outer_loops else None
        cut_color = self._dominant_color(cut_curves, part_region)
        for comp in opens:
            chain = unary_union(comp.lines)
            inside = part_region is not None and part_region.buffer(-self.SNAP_MM).contains(chain)
            if inside:
                colors = self._colors_near(cut_curves, chain)
                if colors and cut_color not in colors:
                    warnings.append("部品内の別色の実線（ケガキ線など）は切断線から除外しました。")
                    continue
                reasons.append("UNEXPLAINED_GEOMETRY")
                warnings.append("部品の内側に閉じていない実線があります（スリットか注記か判断できません）。")
                continue
            encloses = any(chain.convex_hull.contains(l.region.representative_point()) for l in loops)
            if encloses or part_region is None or chain.length > 0.25 * (part_region.length if part_region else 0):
                return self._failure(name, "unsupported", "OPEN_CONTOUR",
                                     "外形の輪郭が閉じていません（開いた箇所があります）。", stages)
            reasons.append("STRAY_GEOMETRY")
            warnings.append("部品の外に説明できない線があります。")
        if part_region is None:
            return self._failure(name, "unsupported", "OPEN_CONTOUR", "閉じた外形が見つかりません。", stages)
        for other in outer_loops[1:]:
            if other.region.area >= self.MULTI_PART_RATIO * part_region.area:
                return self._failure(name, "unsupported", "MULTIPLE_PARTS",
                                     f"部品の外形になり得る閉じた輪郭が{len(outer_loops)}つあります。1ファイル1部品のみ対応です。", stages)
            reasons.append("STRAY_GEOMETRY")
            warnings.append("部品の外に小さな閉じた輪郭があります。")

        # ---- holes: loops directly inside the part
        part = outer_loops[0]
        exterior = part.region.exterior if part.region.geom_type == "Polygon" else None
        if exterior is None:
            return self._failure(name, "unsupported", "UNEXPLAINED_GEOMETRY", "外形を1つの多角形として構成できません。", stages)
        from shapely.geometry import Polygon
        outline = Polygon(exterior)
        inner = [comp for comp in loops if comp is not part and outline.contains(comp.region.representative_point())]
        holes = [comp for comp in inner if not any(o is not comp and o.region.contains(comp.region.representative_point())
                                                   for o in inner)]
        if len(holes) != len(inner):
            reasons.append("UNEXPLAINED_GEOMETRY")
            warnings.append("穴の内側にさらに輪郭があります。")
        hole_rings = [Polygon(h.region.exterior) for h in holes]
        blank = outline
        for ring in hole_rings:
            blank = blank.difference(ring)
        valid = blank.geom_type == "Polygon" and blank.is_valid and len(blank.interiors) == len(hole_rings)
        if not valid:
            reasons.append("UNEXPLAINED_GEOMETRY")
            warnings.append("穴どうし、または穴と外形が重なっています。")
        area = float(outline.area - sum(r.area for r in hole_rings))
        outer_length = float(outline.exterior.length)
        inner_length = float(sum(r.exterior.length for r in hole_rings))

        # ---- bend lines / centre lines
        hole_centres = [r.centroid for r in hole_rings]
        bends, unexplained = self._bend_lines(line_candidates, outline, hole_centres)
        if unexplained:
            reasons.append("UNEXPLAINED_LINE")
            warnings.append(f"役割を判断できない破線・一点鎖線が{unexplained}本あります。")

        # ---- consistency with dimensions and the title block
        minx, miny, maxx, maxy = outline.bounds
        extents = (maxx - minx, maxy - miny)
        overall = [m for m, _ in drawing.dimensions if m > 0.5 * max(extents)]
        mismatched = [m for m in overall if not any(abs(m - e) <= max(0.1, 0.005 * e) for e in extents)]
        if mismatched:
            reasons.append("DIMENSION_MISMATCH")
            warnings.append(f"寸法の値（{', '.join(f'{m:.1f}' for m in mismatched)} mm）が外形の大きさと一致しません。")
        stated = self._stated_thickness(drawing.texts)
        if stated is not None and abs(stated - self.thickness_mm) > 1e-6 + 0.005 * self.thickness_mm:
            reasons.append("THICKNESS_MISMATCH")
            warnings.append(f"図面の板厚表記（t{stated:g}）と入力された板厚（{self.thickness_mm:g} mm）が異なります。")

        reasons = list(dict.fromkeys(reasons))
        exact = not reasons and drawing.units_known
        confidence = "high" if exact else "medium"
        stages.append(AnalysisStage(
            name="輪郭構成", status="success",
            message=f"外形1つ、穴{len(hole_rings)}個、曲げ線{len(bends)}本、図枠{len(frames)}個を識別しました。"))
        stages.append(AnalysisStage(
            name="自己検算", status="success" if exact else "failed",
            message="外形の閉合・単一性、穴の包含、曲げ線の端点、寸法・板厚表記の整合を確認しました。" if exact
            else "、".join(warnings)))
        flat = FlatPatternSummary(
            method="dxf_flat_pattern", area_mm2=round(area, 6), cut_length_mm=round(outer_length + inner_length, 6),
            boundary_count=1 + len(hole_rings), outer_boundary_count=1, inner_boundary_count=len(hole_rings),
            surface_region_count=1, outer_length_mm=round(outer_length, 6), inner_length_mm=round(inner_length, 6),
            bounding_box_mm=(round(extents[0], 6), round(extents[1], 6)),
            outer_loops=[list(outline.exterior.coords)], inner_loops=[list(r.exterior.coords) for r in hole_rings],
            bend_lines=[list(b.coords) for b in bends],
        )
        if closed_gap:
            warnings.append(f"閉じたすき間の最大値: {closed_gap:.3f} mm")
        return SheetMetalAnalysis(
            status="success" if exact else "partial", file_name=name, thickness_mm=round(self.thickness_mm, 6),
            blank_area_mm2=round(area, 6), cut_length_mm=round(outer_length + inner_length, 6),
            hole_count=len(hole_rings), bend_count=len(bends),
            reason_code=reasons[0] if reasons else None, reason_codes=reasons,
            message="展開図DXFの切断輪郭と曲げ線を解析し、検算が一致しました。" if exact
            else "一部に確認が必要な点があります。値は概算です。",
            flat_pattern=flat, stages=stages, warnings=warnings, assumptions=assumptions,
            metric_quality={
                "thickness_mm": MetricQuality(method="user_input", confidence="high",
                                              evidence=[f"図面の板厚表記 t{stated:g}"] if stated is not None else []),
                "blank_area_mm2": MetricQuality(method="dxf_closed_contour", confidence=confidence),
                "cut_length_mm": MetricQuality(method="dxf_contour_length", confidence=confidence),
                "hole_count": MetricQuality(method="dxf_inner_loops", confidence=confidence),
                "bend_count": MetricQuality(method="dxf_bend_lines", confidence=confidence),
            },
        )

    # ------------------------------------------------------------ helpers
    def _components(self, curves):
        from shapely.geometry import LineString
        from shapely.ops import polygonize_full, unary_union

        lines = [LineString(c) for c in curves if len(c) >= 2 and LineString(c).length > 1e-9]
        noded = unary_union(lines)
        segments = list(noded.geoms) if hasattr(noded, "geoms") else [noded]
        parent: dict = {}

        def find(k):
            parent.setdefault(k, k)
            while parent[k] != k:
                parent[k] = parent[parent[k]]
                k = parent[k]
            return k

        for seg in segments:
            coords = list(seg.coords)
            parent[find(coords[0])] = find(coords[-1])
        groups = defaultdict(list)
        for seg in segments:
            groups[find(seg.coords[0])].append(seg)
        components = []
        for segs in groups.values():
            polys, cuts, dangles, invalid = polygonize_full(segs)
            faces = list(getattr(polys, "geoms", []))
            loose = len(getattr(cuts, "geoms", [])) + len(getattr(dangles, "geoms", [])) + len(getattr(invalid, "geoms", []))
            comp = _Component(lines=segs, faces=faces, dangles=loose)
            if faces:
                comp.region = unary_union(faces)
            components.append(comp)
        return components

    def _bend_lines(self, candidates, outline, hole_centres):
        from shapely.geometry import LineString

        pieces = [c.points for c in candidates]
        role = [c.role for c in candidates]
        # merge collinear pieces that share an end (a bend line drawn in two parts)
        merged_pieces, _ = merge_endpoints([[p[0], p[-1]] for p in pieces], self.SNAP_MM)
        lines = [(LineString(p), r) for p, r in zip(merged_pieces, role)]
        joined = True
        while joined:
            joined = False
            for i in range(len(lines)):
                for j in range(i + 1, len(lines)):
                    a, b = lines[i][0], lines[j][0]
                    ends_a, ends_b = [a.coords[0], a.coords[-1]], [b.coords[0], b.coords[-1]]
                    shared = [p for p in ends_a if p in ends_b]
                    if len(shared) != 1 or not _collinear(a, b):
                        continue
                    far = [p for p in ends_a + ends_b if p != shared[0]]
                    lines[i] = (LineString(far), lines[i][1] or lines[j][1])
                    del lines[j]
                    joined = True
                    break
                if joined:
                    break
        # drop duplicates
        unique = []
        for line, r in lines:
            if not any(line.equals_exact(u, 1e-6) or line.equals_exact(LineString(u.coords[::-1]), 1e-6) for u, _ in unique):
                unique.append((line, r))
        boundary = outline.exterior
        bends, unexplained = [], 0
        for line, r in unique:
            start, end = line.coords[0], line.coords[-1]
            on_outline = all(boundary.distance(_pt(p)) <= self.ON_OUTLINE_MM for p in (start, end))
            inside = outline.buffer(self.ON_OUTLINE_MM).contains(line)
            mid = line.interpolate(0.5, normalized=True)
            at_centre = any(mid.distance(c) <= self.ON_OUTLINE_MM for c in hole_centres)
            if r == "center" or (r != "bend" and at_centre):
                continue
            if on_outline and inside:
                bends.append(line)
            elif r == "bend":
                bends.append(line)
                unexplained += 1
            else:
                unexplained += 1
        return bends, unexplained

    @staticmethod
    def _dominant_color(curves, region):
        if region is None:
            return None
        from shapely.geometry import LineString

        boundary = region.boundary
        counts = Counter(c.color for c in curves if boundary.distance(LineString(c.points)) < 1e-3)
        return counts.most_common(1)[0][0] if counts else None

    @staticmethod
    def _colors_near(curves, geometry):
        from shapely.geometry import LineString

        return {c.color for c in curves if LineString(c.points).distance(geometry) < 1e-3}

    @staticmethod
    def _stated_thickness(texts):
        for text in texts:
            match = THICKNESS_TEXT.search(text)
            if match:
                return float(match.group(1) or match.group(2))
        return None

    @staticmethod
    def _failure(file_name, status, reason_code, message, stages=None) -> SheetMetalAnalysis:
        stages = list(stages or []) + [AnalysisStage(name="DXF解析", status="failed", message=message)]
        return SheetMetalAnalysis(
            status=status, file_name=file_name, reason_code=reason_code, reason_codes=[reason_code],
            message=message, stages=stages,
            metric_quality={key: MetricQuality(method="unavailable", confidence="unavailable") for key in METRICS},
        )


def _pt(p):
    from shapely.geometry import Point

    return Point(p)


def _collinear(a, b, tol=1e-3) -> bool:
    (x0, y0), (x1, y1) = a.coords[0], a.coords[-1]
    length = math.hypot(x1 - x0, y1 - y0)
    if length == 0:
        return False
    for (x, y) in (b.coords[0], b.coords[-1]):
        if abs((x1 - x0) * (y - y0) - (y1 - y0) * (x - x0)) / length > tol:
            return False
    return True
