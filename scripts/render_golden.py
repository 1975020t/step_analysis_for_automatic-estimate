"""Render a contact sheet of golden parts: folded 3D (left) and truth flat pattern (right).

    python scripts/render_golden.py --data golden_data --per-level 3 --out golden_data/contact_sheet.png
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np


def _setup_fonts(plt):
    from matplotlib import font_manager
    for f in font_manager.findSystemFonts():
        if "NotoSansCJK" in f or "msgothic" in f.lower() or "meiryo" in f.lower():
            font_manager.fontManager.addfont(f)
    plt.rcParams["font.family"] = ["Noto Sans CJK JP", "Meiryo", "MS Gothic", "sans-serif"]


def draw_folded(ax, shape):
    from mpl_toolkits.mplot3d.art3d import Poly3DCollection
    vs, tris = shape.tessellate(0.1, 0.2)
    V = np.array([[v.x, v.y, v.z] for v in vs])
    light = np.array([0.4, -0.5, 0.8]) / np.linalg.norm([0.4, -0.5, 0.8])
    polys, cols = [V[list(t)] for t in tris], []
    for p in polys:
        n = np.cross(p[1] - p[0], p[2] - p[0]); m = np.linalg.norm(n)
        c = 0.35 + 0.55 * (abs(n @ light) / m if m else 0.5)
        cols.append((0.25 * c + 0.1, 0.45 * c + 0.1, 0.8 * c + 0.1))
    ax.add_collection3d(Poly3DCollection(polys, facecolors=cols, edgecolor="none"))
    lo, hi = V.min(0), V.max(0); c, r = (lo + hi) / 2, (hi - lo).max() / 2
    ax.set_xlim(c[0] - r, c[0] + r); ax.set_ylim(c[1] - r, c[1] + r); ax.set_zlim(c[2] - r, c[2] + r)
    ax.set_box_aspect((1, 1, 1)); ax.view_init(30, -60); ax.set_axis_off()


def draw_flat(ax, shape):
    ztop = shape.BoundingBox().zmax
    for face in shape.Faces():
        if face.geomType() == "PLANE" and face.normalAt().z > 0.99 and abs(face.Center().z - ztop) < 1e-3:
            for edge in face.Edges():
                pts = [edge.positionAt(k / 24) for k in range(25)]
                ax.plot([p.x for p in pts], [p.y for p in pts], color="#1f5f99", lw=0.8)
    ax.set_aspect("equal"); ax.set_xticks([]); ax.set_yticks([])
    for s in ax.spines.values():
        s.set_visible(False)


def main(argv=None):
    import cadquery as cq
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", default="golden_data")
    parser.add_argument("--per-level", type=int, default=3)
    parser.add_argument("--out", default=None)
    args = parser.parse_args(argv)
    _setup_fonts(plt)
    data = Path(args.data)
    index = json.loads((data / "index.json").read_text())
    results = {}
    if (data / "results.csv").exists():
        import csv
        results = {r["name"]: r for r in csv.DictReader((data / "results.csv").open(encoding="utf-8"))}
    chosen = []
    for level in sorted({p["level"] for p in index["parts"]}):
        chosen += [p for p in index["parts"] if p["level"] == level and not p["name"].startswith("G")][: args.per_level]
    cols = 2
    rows = (len(chosen) + cols - 1) // cols
    fig = plt.figure(figsize=(16, 3.6 * rows))
    for i, truth in enumerate(chosen):
        folder = data / truth["level"]
        row, col = divmod(i, cols)
        ax = fig.add_subplot(rows, 4, row * 4 + col * 2 + 1, projection="3d")
        draw_folded(ax, cq.importers.importStep(str(folder / f"{truth['name']}.step")).val())
        ax.set_title(f"{truth['name']}  {truth['note']}\n板厚{truth['thickness_mm']}mm・{truth['bend_count']}曲げ・"
                     f"穴{truth['hole_count']}", fontsize=9)
        ax2 = fig.add_subplot(rows, 4, row * 4 + col * 2 + 2)
        draw_flat(ax2, cq.importers.importStep(str(folder / f"{truth['name']}_unfolded.step")).val())
        title = f"展開図（正解） 面積 {truth['blank_area_mm2']:,.0f} mm²"
        if truth["name"] in results:
            title += f"\n判定: {results[truth['name']]['outcome']}"
        ax2.set_title(title, fontsize=9, color="#b3261e" if results.get(truth["name"], {}).get("outcome") == "DANGEROUS" else "#333")
    plt.tight_layout()
    out = Path(args.out) if args.out else data / "contact_sheet.png"
    plt.savefig(out, dpi=100)
    print(out)


if __name__ == "__main__":
    main()
