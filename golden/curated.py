"""Hand-written curated parts (known shapes with analytic truth), grouped with the random levels."""
from __future__ import annotations

from pathlib import Path

from shapely.geometry import Polygon, box

from golden.sheetgen import SheetPart, circle, rect, slot

LEVEL = {'G01': 'Lv0', 'G02': 'Lv1', 'G03': 'Lv1', 'G04': 'Lv1', 'G05': 'Lv1', 'G06': 'Lv2', 'G07': 'Lv2', 'G08': 'Lv3'}


def curated_parts() -> list[SheetPart]:
    parts: list[SheetPart] = []


    # G01 Lv0: flat plate, round + slot + rectangular holes, edge notch, corner chamfer
    p = SheetPart("G01_plate_holes_notch", 1.6, Polygon([(0, 0), (150, 0), (150, 80), (12, 80), (0, 68)]),
                  note="Lv0 平板：丸穴・長穴・角穴・外周切欠き・角落とし")
    p.cut_base(circle(20, 20, 8), circle(130, 20, 8), slot(75, 55, 30, 8), rect(75, 20, 20, 12), rect(150, 40, 16, 10))
    parts.append(p)

    # G02 Lv1: L bracket, holes in both legs, SPCC-ish t=2.0
    p = SheetPart("G02_L_bracket_holes", 2.0, box(0, 0, 60, 50), note="Lv1 L字ブラケット：両面に穴、フランジに長穴")
    p.cut_base(circle(20, 15, 6.6), circle(20, 35, 6.6))
    f = p.flange(p.base, ((60, 0), (60, 50)), 36, angle=90, radius=2.0)
    f.cutouts += [slot(25, f.ba + 20, 16, 6.6)]
    parts.append(p)

    # G03 Lv1: Z bracket (up then down), holes, t=1.2
    p = SheetPart("G03_Z_bracket", 1.2, box(0, 0, 40, 30), note="Lv1 Z字：上曲げ→下曲げ、各面に穴")
    p.cut_base(circle(15, 15, 4.5))
    f1 = p.flange(p.base, ((40, 0), (40, 30)), 25, angle=90, radius=1.2)
    f2 = p.flange(f1.panel, p.far_edge(f1), 30, angle=90, radius=1.2, up=False)
    f2.cutouts += [circle(15, f2.ba + 18, 4.5)]
    parts.append(p)

    # G04 Lv1: hat channel (4 parallel bends), holes in web and flanges
    p = SheetPart("G04_hat_channel", 1.6, box(0, 0, 40, 120), note="Lv1 ハット形：4曲げ、ウェブ・つばに穴")
    p.cut_base(circle(20, 30, 8), circle(20, 90, 8))
    wl = p.flange(p.base, ((0, 120), (0, 0)), 30, radius=1.6)
    wr = p.flange(p.base, ((40, 0), (40, 120)), 30, radius=1.6)
    bl = p.flange(wl.panel, p.far_edge(wl), 20, radius=1.6, up=False)
    br = p.flange(wr.panel, p.far_edge(wr), 20, radius=1.6, up=False)
    for g in (bl, br):
        g.cutouts += [circle(30, g.ba + 10, 5.5), circle(90, g.ba + 10, 5.5)]
    parts.append(p)

    # G05 Lv1: non-90 angles (45 and 135) with different radii
    p = SheetPart("G05_angles_45_135", 2.3, box(0, 0, 70, 40), note="Lv1 45°/135°曲げ、R違い")
    p.cut_base(rect(35, 20, 20, 10))
    p.flange(p.base, ((70, 0), (70, 40)), 30, angle=45, radius=3.0)
    p.flange(p.base, ((0, 40), (0, 0)), 25, angle=135, radius=2.3)
    parts.append(p)

    # G06 Lv2: open tray, 4 flanges with corner gaps, holes in base and flanges
    p = SheetPart("G06_tray_holes", 1.2, box(0, 0, 160, 100), note="Lv2 トレー：4辺フランジ、底面・フランジに穴")
    p.cut_base(circle(30, 30, 10), circle(130, 70, 10), rect(80, 50, 40, 20))
    sides = [((0, 0), (160, 0)), ((160, 0), (160, 100)), ((160, 100), (0, 100)), ((0, 100), (0, 0))]
    for i, e in enumerate(sides):
        g = p.flange(p.base, e, 25, radius=1.2)
        g.cutouts += [circle(g.span / 2, g.ba + 12, 6)]
        if i == 0:
            g.cutouts += [slot(30, g.ba + 12, 20, 5)]
    parts.append(p)

    # G07 Lv2: partial-width flange with relief notches, t=3.2
    p = SheetPart("G07_partial_flange_relief", 3.2, box(0, 0, 100, 60), note="Lv2 部分幅フランジ＋曲げリリーフ")
    p.cut_base(rect(60, 3, 4, 6), rect(20, 3, 4, 6))  # reliefs at flange ends (on the bend edge)
    g = p.flange(p.base, ((22, 0), (58, 0)), 30, radius=3.2)
    g.cutouts += [circle(18, g.ba + 15, 9)]
    parts.append(p)

    # G08 Lv3: box with return flanges (flange on flange) - like BenDFM #3-#5
    p = SheetPart("G08_box_return_flanges", 1.6, box(0, 0, 120, 80), note="Lv3 箱：側面フランジ＋返しフランジ（2段）")
    p.cut_base(circle(60, 40, 20))
    for e in [((0, 0), (120, 0)), ((120, 80), (0, 80))]:
        s = p.flange(p.base, e, 40, radius=1.6)
        s.cutouts += [rect(s.span / 2, s.ba + 20, 30, 12)]
        r = p.flange(s.panel, p.far_edge(s), 12, radius=1.6)
        r.cutouts += [circle(20, r.ba + 6, 4.5), circle(r.span - 20, r.ba + 6, 4.5)]
    for e in [((120, 0), (120, 80)), ((0, 80), (0, 0))]:
        p.flange(p.base, ((e[0][0], e[0][1] + (5 if e[0][1] == 0 else -5)), (e[1][0], e[1][1] + (-5 if e[1][1] == 80 else 5))), 40, radius=1.6)
    parts.append(p)

    for part in parts:
        part.level = LEVEL[part.name[:3]]
    return parts


def build_curated(out_dir: Path, truth_version: str = "v1") -> list[dict]:
    truths = []
    for part in curated_parts():
        built = part.build()
        if not SheetPart.is_consistent(built[2]):
            raise RuntimeError(f"curated part {part.name} is inconsistent: {built[2]['_checks']}")
        if truth_version == "v2":
            from golden.truth_v2 import apply_v2
            apply_v2(built)
        truths.append(part.export(out_dir, built))
    return truths
