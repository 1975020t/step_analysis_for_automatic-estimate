"""Drawing primitives, hidden-line views and scan / FAX / handwriting effects for the drawing-PDF golden set.

Everything here is presentation only: the truth of a drawing is decided in golden/pdf_golden.py before
anything is drawn. Coordinates are millimetres on the layout sheet, origin bottom-left.
"""
from __future__ import annotations

import io
import math
import random
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

FONT_FILE = Path(__file__).resolve().parent / "fonts" / "ipag.ttf"  # IPA Gothic, IPA Font License v1.0
JA = "IPAGothic"
EN = "Helvetica"
EN_BOLD = "Helvetica-Bold"
if JA not in pdfmetrics.getRegisteredFontNames():
    pdfmetrics.registerFont(TTFont(JA, str(FONT_FILE)))  # embedded subset with ToUnicode -> text is extractable


# ------------------------------------------------------------------ vector sheet
class Sheet:
    """Thin wrapper over a reportlab canvas in mm. Records the box of tagged texts (for handwriting
    corrections drawn later on the scanned image)."""

    def __init__(self, c: canvas.Canvas, layout_size, font=JA):
        self.c = c
        self.layout_size = layout_size  # (w, h) in layout mm; the page may be a scaled copy (A3)
        self.font = font
        self.page = 0
        self.boxes: dict[str, tuple[int, float, float, float, float]] = {}

    def new_page(self):
        self.c.showPage()
        self.page += 1

    def line(self, x0, y0, x1, y1, w=0.25, dash=None):
        c = self.c
        c.setLineWidth(w)
        if dash:
            c.setDash(list(dash), 0)
        c.line(x0 * mm, y0 * mm, x1 * mm, y1 * mm)
        if dash:
            c.setDash()

    def rect(self, x, y, w, h, lw=0.35):
        self.c.setLineWidth(lw)
        self.c.rect(x * mm, y * mm, w * mm, h * mm)

    def width(self, s, size, font=None):
        return self.c.stringWidth(s, font or self.font, size * mm) / mm

    def text(self, s, x, y, size=3.0, font=None, anchor="l", angle=0, tag=None, max_w=None):
        """Draw text with its baseline at y. Shrinks the font to fit max_w. Returns the drawn width (mm)."""
        font = font or self.font
        w = self.width(s, size, font)
        if max_w and w > max_w:
            size *= max_w / w
            w = max_w
        c = self.c
        c.saveState()
        c.setFont(font, size * mm)
        c.translate(x * mm, y * mm)
        c.rotate(angle)
        dx = {"l": 0, "c": -w / 2, "r": -w}[anchor]
        c.drawString(dx * mm, 0, s)
        c.restoreState()
        if tag:
            x0 = x + dx
            self.boxes[tag] = (self.page, x0, y - 0.25 * size, x0 + w, y + 0.8 * size)
        return w

    def strike(self, x0, y, x1, size):
        """Japanese-style correction: a double line through a value."""
        for dy in (0.22, 0.42):
            self.line(x0 - 0.5, y + dy * size, x1 + 0.5, y + dy * size, 0.2)

    def polyline(self, pts, w=0.35, dash=None):
        c = self.c
        c.setLineWidth(w)
        if dash:
            c.setDash(list(dash), 0)
        path = c.beginPath()
        path.moveTo(pts[0][0] * mm, pts[0][1] * mm)
        for x, y in pts[1:]:
            path.lineTo(x * mm, y * mm)
        c.drawPath(path, stroke=1, fill=0)
        if dash:
            c.setDash()

    def arrow(self, x, y, ang, size=2.0):
        c = self.c
        a1, a2 = math.radians(ang + 160), math.radians(ang - 160)
        p = c.beginPath()
        p.moveTo(x * mm, y * mm)
        p.lineTo((x + size * math.cos(a1)) * mm, (y + size * math.sin(a1)) * mm)
        p.lineTo((x + size * math.cos(a2)) * mm, (y + size * math.sin(a2)) * mm)
        p.close()
        c.drawPath(p, stroke=0, fill=1)

    def hdim(self, x0, x1, y_obj, y_dim, label, size=2.6, font=None):
        ext = 1.5 if y_dim > y_obj else -1.5
        self.line(x0, y_obj, x0, y_dim + ext, 0.15)
        self.line(x1, y_obj, x1, y_dim + ext, 0.15)
        self.line(x0, y_dim, x1, y_dim, 0.15)
        self.arrow(x0, y_dim, 180)
        self.arrow(x1, y_dim, 0)
        self.text(label, (x0 + x1) / 2, y_dim + 0.7, size, font, "c")

    def vdim(self, y0, y1, x_obj, x_dim, label, size=2.6, font=None):
        ext = 1.5 if x_dim > x_obj else -1.5
        self.line(x_obj, y0, x_dim + ext, y0, 0.15)
        self.line(x_obj, y1, x_dim + ext, y1, 0.15)
        self.line(x_dim, y0, x_dim, y1, 0.15)
        self.arrow(x_dim, y0, 270)
        self.arrow(x_dim, y1, 90)
        self.text(label, x_dim - 0.7, (y0 + y1) / 2, size, font, "c", angle=90)

    def leader(self, tip, label_xy, label, size=2.6, font=None, tag=None):
        lx, ly = label_xy
        self.line(tip[0], tip[1], lx, ly, 0.15)
        self.arrow(tip[0], tip[1], math.degrees(math.atan2(tip[1] - ly, tip[0] - lx)))
        w = self.width(label, size, font)
        self.line(lx, ly, lx + w + 1.0, ly, 0.15)
        self.text(label, lx + 0.5, ly + 0.6, size, font, tag=tag)

    def projection_symbol(self, x, y, third=True):
        tz = [(0, 1.5), (7, 0), (7, 6), (0, 4.5)]
        pts = [(x + a, y + b) for a, b in tz]
        for p, q in zip(pts, pts[1:] + pts[:1]):
            self.line(*p, *q, 0.2)
        cx = 13 if third else -6
        self.c.setLineWidth(0.2)
        self.c.circle((x + cx) * mm, (y + 3) * mm, 3 * mm)
        self.c.circle((x + cx) * mm, (y + 3) * mm, 1.5 * mm)

    def table(self, x, y_top, widths, rows, row_h=6.0, size=2.8, header_size=None, tags=None, font=None):
        """rows[0] is the header. Draws top-down from y_top. tags: {(row, col): tag}. Returns bottom y."""
        tags = tags or {}
        total = sum(widths)
        for r, row in enumerate(rows):
            y = y_top - row_h * (r + 1)
            cx = x
            for k, (w, val) in enumerate(zip(widths, row)):
                self.rect(cx, y, w, row_h, 0.2)
                s = size if r else (header_size or size * 0.9)
                self.text(str(val), cx + 1.0, y + row_h * 0.3, s, font, tag=tags.get((r, k)), max_w=w - 1.6)
                cx += w
        self.rect(x, y_top - row_h * len(rows), total, row_h * len(rows), 0.4)
        return y_top - row_h * len(rows)


# ------------------------------------------------------------------ views of the part
VIEW_DIRS = {
    "front": ((0, -1, 0), (1, 0, 0)),
    "top": ((0, 0, 1), (1, 0, 0)),
    "right": ((1, 0, 0), (0, 1, 0)),
    "left": ((-1, 0, 0), (0, -1, 0)),
    "iso": ((1, -1, 1), (1, 1, 0)),
}


def _polys(compound, defl=0.05):
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.GCPnts import GCPnts_QuasiUniformDeflection
    from OCP.TopAbs import TopAbs_EDGE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS
    out = []
    if compound is None or compound.IsNull():
        return out
    ex = TopExp_Explorer(compound, TopAbs_EDGE)
    while ex.More():
        curve = BRepAdaptor_Curve(TopoDS.Edge_s(ex.Current()))
        d = GCPnts_QuasiUniformDeflection(curve, defl)
        if d.IsDone() and d.NbPoints() >= 2:
            out.append(np.array([[d.Value(i).X(), d.Value(i).Y()] for i in range(1, d.NbPoints() + 1)]))
        ex.Next()
    return out


def project(shape, view_dir, x_dir):
    """Orthographic hidden-line projection (OpenCascade HLR). Returns (visible, hidden) 2-D polylines."""
    from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt
    from OCP.HLRAlgo import HLRAlgo_Projector
    from OCP.HLRBRep import HLRBRep_Algo, HLRBRep_HLRToShape
    algo = HLRBRep_Algo()
    algo.Add(shape.wrapped)
    algo.Projector(HLRAlgo_Projector(gp_Ax2(gp_Pnt(0, 0, 0), gp_Dir(*view_dir), gp_Dir(*x_dir))))
    algo.Update()
    algo.Hide()
    h = HLRBRep_HLRToShape(algo)
    vis = _polys(h.VCompound()) + _polys(h.OutLineVCompound()) + _polys(h.Rg1LineVCompound())
    hid = _polys(h.HCompound()) + _polys(h.OutLineHCompound())
    return vis, hid


class Views:
    """Projected views of the folded part plus its flat pattern (from golden/dxf_golden.py)."""

    def __init__(self, folded, flat_outer, flat_inners, bends, names=("front", "top", "right", "iso")):
        self.v = {k: project(folded, *VIEW_DIRS[k]) for k in names}
        bb = folded.BoundingBox()
        self.bbox = (bb.xlen, bb.ylen, bb.zlen)
        self.flat_outer, self.flat_inners, self.bends = flat_outer, flat_inners, bends

    def extent(self, view):
        vis, hid = self.v[view]
        pts = np.vstack(vis + hid) if (vis or hid) else np.zeros((1, 2))
        return pts.min(0), pts.max(0)

    def size(self, view, s):
        lo, hi = self.extent(view)
        return (hi - lo) * s

    def draw(self, sh: Sheet, view, ox, oy, s, hidden=True):
        lo, _ = self.extent(view)
        vis, hid = self.v[view]
        for p in vis:
            sh.polyline([(ox + (x - lo[0]) * s, oy + (y - lo[1]) * s) for x, y in p], 0.35)
        if hidden:
            for p in hid:
                sh.polyline([(ox + (x - lo[0]) * s, oy + (y - lo[1]) * s) for x, y in p], 0.18, dash=(1.4, 0.9))

    def edge_points(self, view, ox, oy, s, rng: random.Random, n):
        """n random points on visible edges of a drawn view (leader tips)."""
        lo, _ = self.extent(view)
        vis = [p for p in self.v[view][0] if len(p)]
        pts = []
        for _ in range(n):
            p = rng.choice(vis)
            x, y = p[rng.randrange(len(p))]
            pts.append((ox + (x - lo[0]) * s, oy + (y - lo[1]) * s))
        return pts

    def flat_polylines(self):
        def seg_pts(seg):
            if seg[0] == "line":
                return [seg[1], seg[2]]
            if seg[0] == "circle":
                c, r = seg[1], seg[2]
                return [(c[0] + r * math.cos(t), c[1] + r * math.sin(t)) for t in np.linspace(0, 2 * math.pi, 41)]
            _, c, r, p0, _p1, sw = seg
            a0 = math.atan2(p0[1] - c[1], p0[0] - c[0])
            return [(c[0] + r * math.cos(a0 + sw * k / 16), c[1] + r * math.sin(a0 + sw * k / 16)) for k in range(17)]
        cut = [seg_pts(sg) for loop in [self.flat_outer] + self.flat_inners for sg in loop]
        bend = [[a, b] for a, b, _ in self.bends]
        return cut, bend

    def flat_size(self, s):
        cut, _ = self.flat_polylines()
        pts = np.array([p for pl in cut for p in pl])
        return (pts.max(0) - pts.min(0)) * s

    def draw_flat(self, sh: Sheet, ox, oy, s):
        cut, bend = self.flat_polylines()
        lo = np.array([p for pl in cut for p in pl]).min(0)
        for pl in cut:
            sh.polyline([(ox + (x - lo[0]) * s, oy + (y - lo[1]) * s) for x, y in pl], 0.3)
        for pl in bend:
            sh.polyline([(ox + (x - lo[0]) * s, oy + (y - lo[1]) * s) for x, y in pl], 0.18, dash=(3, 1, 0.5, 1))


# ------------------------------------------------------------------ raster effects
def render_pages(pdf_bytes: bytes, dpi: int) -> list[Image.Image]:
    import pypdfium2 as pdfium
    doc = pdfium.PdfDocument(pdf_bytes)
    try:
        return [doc[i].render(scale=dpi / 72).to_pil().convert("RGB") for i in range(len(doc))]
    finally:
        doc.close()


def _font(px: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONT_FILE), max(8, int(px)))


def to_px(box_mm, layout_size, img_size):
    """(x0, y0, x1, y1) layout mm -> pixel box (left, top, right, bottom)."""
    (lw, lh), (W, H) = layout_size, img_size
    x0, y0, x1, y1 = box_mm
    return (x0 / lw * W, (lh - y1) / lh * H, x1 / lw * W, (lh - y0) / lh * H)


class InkMap:
    """Where the page already has ink, to put stamps and handwriting on empty paper (or near a spot)."""

    def __init__(self, img: Image.Image):
        a = np.asarray(img.convert("L")) < 200
        self.ink = a.astype(np.int32)
        self.H, self.W = a.shape

    def _integral(self):
        return np.pad(self.ink, ((1, 0), (1, 0))).cumsum(0).cumsum(1)

    def blank(self, w, h, rng: random.Random, near=None, radius=None):
        """Top-left pixel of a w x h box with the least ink (among a few of the best, picked at random).
        With near/radius only boxes whose top-left lies within radius of `near` are considered."""
        w, h = int(min(w, self.W * 0.9)), int(min(h, self.H * 0.5))
        S = self._integral()
        step = max(6, int(min(w, h) / 3))
        mx, my = int(self.W * 0.04), int(self.H * 0.05)
        cands = []
        for y in range(my, self.H - my - h, step):
            for x in range(mx, self.W - mx - w, step):
                if near is not None and math.hypot(x - near[0], y - near[1]) > radius:
                    continue
                inked = S[y + h, x + w] - S[y, x + w] - S[y + h, x] + S[y, x]
                cands.append((inked, x, y))
        if not cands:
            return (near if near is not None else (mx, my))
        cands.sort()
        best = [c for c in cands[:6] if c[0] <= cands[0][0] + 0.002 * w * h] or cands[:1]
        _, x, y = rng.choice(best)
        return x, y

    def occupy(self, l, t, w, h):
        self.ink[int(max(t, 0)):int(t + h), int(max(l, 0)):int(l + w)] = 1


def hand_text(img: Image.Image, xy, text, px, color, rng: random.Random, angle=0.0):
    """Handwriting-like text: every glyph slightly rotated, scaled and offset, thick strokes."""
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    x, y = xy
    base = math.radians(angle)
    for ch in text:
        size = px * rng.uniform(0.9, 1.12)
        f = _font(size)
        tile = Image.new("RGBA", (int(size * 1.6), int(size * 1.6)), (0, 0, 0, 0))
        ImageDraw.Draw(tile).text((size * 0.3, size * 0.2), ch, font=f, fill=color + (235,),
                                  stroke_width=max(1, int(size / 18)), stroke_fill=color + (235,))
        tile = tile.rotate(rng.uniform(-9, 9) + angle, resample=Image.BICUBIC)
        jx, jy = rng.uniform(-0.06, 0.06) * px, rng.uniform(-0.1, 0.1) * px
        overlay.alpha_composite(tile, (int(x + jx), int(y + jy - size * 0.2)))
        adv = f.getlength(ch) * rng.uniform(0.92, 1.08) if ch != " " else size * 0.4
        x += adv * math.cos(base)
        y -= adv * math.sin(base)
    img.paste(Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB"))
    return x


def hand_strike(img: Image.Image, box_px, color, rng: random.Random):
    """Hand-drawn double line through a value (Japanese correction convention)."""
    d = ImageDraw.Draw(img)
    l, t, r, b = box_px
    h = b - t
    for k in (0.42, 0.62):
        y = t + h * k
        pts = [(l - 4 + i * (r - l + 8) / 6, y + rng.uniform(-1.2, 1.2)) for i in range(7)]
        d.line(pts, fill=color, width=max(2, int(h / 9)))


def stamp(img: Image.Image, center, text, px, rng: random.Random, shape="circle", color=(205, 35, 40), sub=None):
    """Red seal (hanko) / date stamp / rectangular stamp, semi-transparent and slightly rotated."""
    size = int(px * (2.6 if shape == "circle" else 1.0))
    w, h = (size, size) if shape == "circle" else (int(px * (len(text) * 1.25 + 1.2)), int(px * (2.4 if sub else 1.7)))
    tile = Image.new("RGBA", (w + 8, h + 8), (0, 0, 0, 0))
    d = ImageDraw.Draw(tile)
    col = color + (190,)
    lw = max(2, int(px / 8))
    if shape == "circle":
        d.ellipse([4, 4, w + 4, h + 4], outline=col, width=lw)
        d.text(((w + 8) / 2, (h + 8) / 2), text, font=_font(px * (1.0 if len(text) <= 2 else 0.7)), fill=col, anchor="mm")
    else:
        d.rectangle([4, 4, w + 4, h + 4], outline=col, width=lw)
        ty = (h + 8) / 2 - (px * 0.45 if sub else 0)
        d.text(((w + 8) / 2, ty), text, font=_font(px), fill=col, anchor="mm")
        if sub:
            d.text(((w + 8) / 2, ty + px * 1.0), sub, font=_font(px * 0.65), fill=col, anchor="mm")
    tile = tile.rotate(rng.uniform(-12, 12), resample=Image.BICUBIC, expand=True)
    img.paste(Image.alpha_composite(img.convert("RGBA").crop(
        (int(center[0] - tile.width / 2), int(center[1] - tile.height / 2),
         int(center[0] - tile.width / 2) + tile.width, int(center[1] - tile.height / 2) + tile.height)), tile).convert("RGB"),
        (int(center[0] - tile.width / 2), int(center[1] - tile.height / 2)))


def degrade_scan(img: Image.Image, rng: random.Random, color=False) -> Image.Image:
    """Office scanner: lower contrast, paper tint, noise, slight skew and blur."""
    np_rng = np.random.default_rng(rng.randint(0, 1 << 30))
    arr = np.asarray(img).astype(np.float32)
    if not color:
        arr = arr.mean(axis=2, keepdims=True).repeat(3, axis=2)
    arr = arr * rng.uniform(0.82, 0.92) + rng.uniform(12, 26)
    arr[..., 2] -= rng.uniform(0, 8)  # slightly yellowish paper
    arr += np_rng.normal(0, rng.uniform(4, 9), arr.shape[:2])[..., None]
    out = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))
    out = out.rotate(rng.uniform(-1.3, 1.3), resample=Image.BICUBIC, fillcolor=(236, 235, 230))
    return out.filter(ImageFilter.GaussianBlur(rng.uniform(0.4, 0.8)))


def degrade_fax(img: Image.Image, rng: random.Random, header: str) -> Image.Image:
    """G3 FAX, standard resolution: 1-bit, half vertical resolution, speckle noise, transmission header."""
    np_rng = np.random.default_rng(rng.randint(0, 1 << 30))
    g = img.convert("L")
    W, H = g.size
    g = g.rotate(rng.uniform(-0.8, 0.8), resample=Image.BICUBIC, fillcolor=255)
    g = g.resize((W, H // 2), Image.BILINEAR).resize((W, H), Image.NEAREST)  # 8 x 3.85 lines/mm
    arr = np.asarray(g).astype(np.float32) + np_rng.normal(0, 18, (H, W))
    bw = (arr > rng.uniform(150, 185)).astype(np.uint8) * 255
    speck = np_rng.random((H, W))
    bw[speck < 0.0012] = 0
    bw[speck > 0.9985] = 255
    out = Image.fromarray(bw).convert("RGB")
    band = int(H * 0.028)
    d = ImageDraw.Draw(out)
    d.rectangle([0, 0, W, band], fill=(255, 255, 255))
    d.text((int(W * 0.02), band * 0.2), header, font=_font(band * 0.62), fill=(0, 0, 0))
    return out


def images_to_pdf(images: list[Image.Image], page_size_pt, fmt="JPEG", quality=72) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=page_size_pt, invariant=1)
    for img in images:
        data = io.BytesIO()
        if fmt == "JPEG":
            img.save(data, format="JPEG", quality=quality)
        else:
            img.convert("1").save(data, format="PNG")
        data.seek(0)
        c.drawImage(ImageReader(data), 0, 0, *page_size_pt)
        c.showPage()
    c.save()
    return buf.getvalue()
