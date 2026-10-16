"""Estimate the shape values of a sheet-metal part from its drawing PDF alone (no STEP / DXF).

    PdfShapeEstimator().estimate("drawing.pdf", thickness_mm=1.6) -> PdfShapeResult

When only the drawing PDF is available, the five shape values the quote needs (板厚・展開面積・切断長・穴数・
曲げ数) are estimated, not measured. The result is always an estimate (SheetMetalAnalysis.status = "partial",
confidence medium / low), so the quote is shown as 概算 and every value can be overwritten by the person.

Division of labour
  1. Claude reads what is WRITTEN or DRAWN (src/claude_api.py, one call): the overall dimensions as written
     (width / depth / height, verbatim), where the flat pattern is on the page (if any), and its own list of the
     bends and the holes. Claude never computes a shape value.
  2. Image measurement of the flat pattern (src/pdf_flat_pattern.py): in the region Claude points at, the flat
     pattern's outline is found on the page image; its closed inner contours (holes), its bend lines, the length
     of the outline and of every hole, and its area are measured in page pixels.
  3. The scale of the flat pattern (src/pdf_fold.py): the flat pattern is folded along its bend lines into the
     part; the fold whose box has the proportions of the written overall dimensions gives pixels per mm. Cut
     length = outline + holes, blank area = area inside, both converted with that scale. Nothing is fitted to
     data: the length comes from the drawing's own geometry and its own dimensions.
  4. No flat pattern (or one that cannot be measured or scaled): Claude describes the part as a base with
     flanges and holes (a second call); the description is unfolded and folded back, and used only when the
     folded part has the written overall dimensions (low confidence). Otherwise the lengths stay empty and the
     person enters them.
  5. Checks on the numbers Claude read: on a vector PDF every dimension must exist in the text layer; a
     dimension that is not there, counts that disagree between Claude and the image, an ambiguous scale etc.
     lower the confidence and are listed as warnings.
Money is never computed here (QuoteEngine does that from the master).
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from pathlib import Path

from src.claude_api import ClaudeClient, image_block
from src.models import MetricQuality, SheetMetalAnalysis

LONG_EDGE = 1568          # image sent to Claude (long edge, px)
MEASURE_LONG_EDGE = 3000  # image measured by src/pdf_flat_pattern.py (long edge, px)
VECTOR_MIN_CHARS = 20
METHOD = "drawing_pdf_estimate"
REASON_CODE = "PDF_SHAPE_ESTIMATE"
ASSUMPTION = "図面PDFから測った値（展開図を外形寸法で縮尺を合わせて計測、展開図がなければ投影図から組んだ形。CADデータがあれば置き換える）"

# ---------------------------------------------------------------- pages
@dataclass
class ShapePage:
    number: int
    width_mm: float
    height_mm: float
    gray: object                      # numpy uint8 array (MEASURE_LONG_EDGE), for the image measurement
    px_per_mm: float
    image: bytes = b""                # LONG_EDGE image sent to Claude
    media_type: str = "image/png"
    image_size: tuple[int, int] = (0, 0)  # (width, height) of `image` in px
    texts: list[tuple[float, float, str]] = field(default_factory=list)  # (x mm, y mm from top, text)
    vector: bool = False


def load_shape_pages(path: str | Path) -> list[ShapePage]:
    import numpy as np
    import pypdfium2 as pdfium

    from src.services.pdfium_lock import PDFIUM_LOCK

    pages: list[ShapePage] = []
    with PDFIUM_LOCK:
        doc = pdfium.PdfDocument(str(path))
        try:
            for i in range(len(doc)):
                page = doc[i]
                w_pt, h_pt = page.get_size()
                to_mm = 25.4 / 72
                texts = []
                tp = page.get_textpage()
                for k in range(tp.count_rects()):
                    left, bottom, right, top = tp.get_rect(k)
                    text = tp.get_text_bounded(left, bottom, right, top).strip()
                    if text:
                        texts.append((round(left * to_mm, 1), round((h_pt - top) * to_mm, 1), text))
                vector = sum(len(t) for *_, t in texts) >= VECTOR_MIN_CHARS
                scale = MEASURE_LONG_EDGE / max(w_pt, h_pt)
                big = page.render(scale=scale).to_pil().convert("RGB")
                small = big.copy()
                small.thumbnail((LONG_EDGE, LONG_EDGE))
                buf = io.BytesIO()
                if vector:
                    small.save(buf, format="PNG", optimize=True)
                    media = "image/png"
                else:
                    small.save(buf, format="JPEG", quality=88)
                    media = "image/jpeg"
                pages.append(ShapePage(i + 1, w_pt * to_mm, h_pt * to_mm, np.asarray(big.convert("L"), dtype=np.uint8),
                                       scale * 72 / 25.4, buf.getvalue(), media, small.size, texts, vector))
        finally:
            doc.close()
    return pages


# ---------------------------------------------------------------- prompt
SYSTEM_PROMPT = """あなたは板金部品の図面を読み、見積のために部品の形を数える担当者です。図面には投影図（正面図・平面図・側面図・断面図・等角図など）があり、展開図（板を平らに広げた図）が添えられていることもあります。
図面に書かれていること・描かれていることだけを返し、推測で埋めないでください。数値の計算はしないでください。

1. 外形寸法（overall_dimensions）：部品全体の外形の大きさを表す寸法を、図に書かれている数値のまま最大3つ返す（幅・奥行・高さ）。
   それぞれ、どの図に付いた、どの向きの寸法かを view と direction に書く。穴の位置・穴の径・フランジ1枚だけの長さなど、部品の一部の寸法は含めない。
   平板の部品で、板厚のほかに高さ方向の寸法がなければ2つでよい。部品全体の寸法が図に書かれていなければ、空のリストにする。
2. 展開図（flat_pattern）：展開図（「展開図」「FLAT PATTERN」などの見出しがあるか、曲げ線の入った平らな形として投影図とは別に描かれた図）があれば、そのページ番号と、展開図の図形全体を囲む枠を返す。
   曲げのない平板の部品で、展開図が別に描かれていなければ null（平面図を展開図として返さない）。枠の座標はそのページの画像のピクセル座標（左上が(0,0)、x0,y0,x1,y1）で、
   図形が切れないよう少し広めに囲む。展開図の見出しや寸法線は枠に入れなくてよい。展開図がなければ null。
   投影図（展開図以外の部品の図）全体を囲む枠も、同じ形で views_region に返す（表題欄・注記・部品表は含めない）。
3. 曲げ（bends）：部品の曲げを1つずつ列挙する。各曲げについて、どこにある曲げかと、どの図で確かめたかを書く。
   展開図があれば、展開図の曲げ線（外形線と違う線種の直線。一点鎖線・破線・細線など）を1本ずつ数える。展開図がなければ、投影図で板が折れ曲がっている箇所を1つずつ数える。
   Z曲げは2つ、ヘミング（折り返し）は1つ、段曲げは段の数だけ数える。短いタブの曲げも1つと数える。
4. 穴・抜き（holes）：部品の内側にある閉じた開口（丸穴・長穴・角穴・その他の形の抜き）を1つずつ列挙する。外周から切り込んだ切り欠きは含めない。
   展開図があれば展開図で数え、なければ投影図で数える。同じ穴を別の図で重複して数えない。引出線の注記の個数は参考にとどめ、図に描かれた開口を数える。
   曲げ・穴の各項目の説明は短く（20字以内）。
5. 図面の種類（drawing_kind）：1つの板金部品の図面なら part、組立図や複数部品の図面なら assembly、部品の形が描かれていなければ other。
6. 自信のない項目は uncertain に項目名（overall_dimensions / flat_pattern / bends / holes）と理由を書く。
"""

RECOUNT_FLAT_PROMPT = """あなたは板金部品の展開図（板を平らに広げた図）を見て、見積のために曲げ線と穴を数える担当者です。渡す画像は図面から展開図の部分を拡大したものです。
1. 曲げ線（bends）：外形線とは違う線種（一点鎖線・破線・細線）の直線で、板を折る位置を示す線を1本ずつ列挙する。外形の端から端まで、またはタブの付け根を横切る線。
   ノイズの点や、外形線・穴の輪郭は含めない。
2. 穴（holes）：外形線の内側にある閉じた輪郭（丸穴・長穴・角穴・その他の抜き）を1つずつ列挙する。外周から切り込んだ切り欠きは含めない。ノイズの点は含めない。
各項目の where は短く（20字以内）。"""
RECOUNT_VIEWS_PROMPT = """あなたは板金部品の図面を見て、見積のために曲げと穴を数える担当者です。渡す画像は図面から部品の投影図（正面図・平面図・側面図など）の部分を拡大したものです。
1. 曲げ（bends）：板が折れ曲がっている箇所を1つずつ列挙する。板厚の2本線が折れ曲がる角として、正面図や側面図に現れる。同じ曲げを別の図で重複して数えない。
   Z曲げは2つ、ヘミング（折り返し）は1つ、段曲げは段の数だけ数える。
2. 穴（holes）：部品の内側にある閉じた開口（丸穴・長穴・角穴・その他の抜き）を1つずつ列挙する。穴が開口として見える図（穴が丸く見える図）で数え、同じ穴を別の図で重複して数えない。
   外周から切り込んだ切り欠きは含めない。
各項目の where は短く（20字以内）。"""
RECOUNT_SCHEMA = {
    "type": "object",
    "properties": {
        "bends": {"type": "array", "items": {"type": "object", "properties": {"where": {"type": "string"}}, "required": ["where"]}},
        "holes": {"type": "array", "items": {"type": "object", "properties": {"where": {"type": "string"}}, "required": ["where"]}},
    },
    "required": ["bends", "holes"],
}
MODEL_PROMPT = """あなたは板金部品の図面（投影図）を見て、部品の形を「ベース（いちばん大きな平らな面）と、その辺から曲げたフランジ」の組み合わせとして書き出す担当者です。この図面には展開図がありません。
長さは図に書かれた寸法の数値を使い、書かれていない長さは図の比率から読み取ってください。書き出した形はあとで折り曲げて、図面の外形寸法と照合します。計算や推測で形を足さないでください。
1. base：ベースの width（平面図で見た左右の長さ）と depth（前後の長さ）を mm で。
2. flanges：ベースの辺から曲がる面を1つずつ。edge はどの辺か（平面図で見て right / left / back（奥）/ front（手前））。
   length は曲げの外側から先端までの長さ、angle は曲げ角（90 など）、direction は up（平面図の見る側へ曲がる）か down。
   辺の一部だけから曲がる面（タブなど）は、その面の width と、辺の端（right/left なら手前、back/front なら左）からの offset も書く。
   その面の先でさらに曲がる面（Z曲げ・ヘミング・段曲げ）は then に順に書く（length・angle・direction）。
3. holes：部品の内側の穴・抜き（外周からの切り欠きは含めない）。shape は round / slot / rect、size は丸穴の直径・長穴の全長・角穴の幅、
   size2 は長穴の幅・角穴の高さ、count は同じ穴の数。注記に径が書かれていればその値を使う。
"""
_SEG = {"type": "object", "properties": {"length": {"type": "number"}, "angle": {"type": "number"},
                                         "direction": {"type": "string", "enum": ["up", "down"]}},
        "required": ["length", "angle", "direction"]}
MODEL_SCHEMA = {
    "type": "object",
    "properties": {
        "base": {"type": "object", "properties": {"width": {"type": "number"}, "depth": {"type": "number"}},
                 "required": ["width", "depth"]},
        "flanges": {"type": "array", "items": {"type": "object", "properties": {
            "edge": {"type": "string", "enum": ["right", "left", "back", "front"]},
            "length": {"type": "number"}, "angle": {"type": "number"}, "direction": {"type": "string", "enum": ["up", "down"]},
            "width": {"type": "number"}, "offset": {"type": "number"}, "then": {"type": "array", "items": _SEG}},
            "required": ["edge", "length", "angle", "direction"]}},
        "holes": {"type": "array", "items": {"type": "object", "properties": {
            "shape": {"type": "string", "enum": ["round", "slot", "rect"]}, "size": {"type": "number"},
            "size2": {"type": "number"}, "count": {"type": "integer"}}, "required": ["shape", "size", "count"]}},
    },
    "required": ["base", "flanges", "holes"],
}
MAX_COUNT = 40   # more bends or holes than this from a list means the answer broke down

_BOX = {"type": "object", "properties": {k: {"type": "integer"} for k in ("x0", "y0", "x1", "y1")},
        "required": ["x0", "y0", "x1", "y1"]}
SCHEMA = {
    "type": "object",
    "properties": {
        "drawing_kind": {"type": "string", "enum": ["part", "assembly", "other"]},
        "overall_dimensions": {"type": "array", "items": {"type": "object", "properties": {
            "value": {"type": "string", "description": "図に書かれている寸法の数値（書かれているまま）"},
            "view": {"type": "string"},
            "direction": {"type": "string", "enum": ["width", "depth", "height"]}},
            "required": ["value", "view", "direction"]}},
        "flat_pattern": {"anyOf": [{"type": "object", "properties": {"page": {"type": "integer"}, "box": _BOX},
                                    "required": ["page", "box"]}, {"type": "null"}]},
        "views_region": {"anyOf": [{"type": "object", "properties": {"page": {"type": "integer"}, "box": _BOX},
                                    "required": ["page", "box"]}, {"type": "null"}]},
        "bends": {"type": "array", "items": {"type": "object", "properties": {
            "where": {"type": "string"}, "seen_in": {"type": "string"}}, "required": ["where", "seen_in"]}},
        "holes": {"type": "array", "items": {"type": "object", "properties": {
            "shape": {"type": "string"}, "where": {"type": "string"}}, "required": ["shape", "where"]}},
        "uncertain": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["drawing_kind", "overall_dimensions", "flat_pattern", "views_region", "bends", "holes", "uncertain"],
}


def _number(text) -> float | None:
    if text is None:
        return None
    s = str(text).translate(str.maketrans("０１２３４５６７８９．，", "0123456789.,")).replace(",", "")
    m = re.search(r"\d+(?:\.\d+)?", s)
    return float(m.group()) if m else None


def _on_layer(value: float, pages: list[ShapePage]) -> bool:
    """A dimension read by Claude exists as a number in the text layer of a vector page."""
    for page in pages:
        for *_, text in page.texts:
            for m in re.finditer(r"\d+(?:\.\d+)?", text.translate(str.maketrans("０１２３４５６７８９．", "0123456789."))):
                if abs(float(m.group()) - value) < 1e-6:
                    return True
    return False


# ---------------------------------------------------------------- result
@dataclass
class PdfShapeResult:
    analysis: SheetMetalAnalysis
    dimensions_mm: list[float] = field(default_factory=list)
    bends_llm: int | None = None
    holes_llm: int | None = None
    flat: dict | None = None          # image measurement of the flat pattern (src/pdf_flat_pattern.py)
    raw: dict | None = None           # Claude's answer
    usage: dict = field(default_factory=dict)

    def evidence(self) -> dict:
        return {"dimensions_mm": self.dimensions_mm, "bends_llm": self.bends_llm, "holes_llm": self.holes_llm,
                "flat_pattern": self.flat, "llm": self.raw, "usage": self.usage}


class RecordedShapeEstimator:
    """Replays recorded shape estimates (PDF_SHAPE_ESTIMATOR=recorded:<file.json>): no Claude API call. For the demo
    and the screenshots. The file: {"shapes": {"<pdf file stem>": {"analysis": {...}, "dimensions_mm": [...]}}}."""

    def __init__(self, path: str | Path) -> None:
        import json

        self.shapes = json.loads(Path(path).read_text(encoding="utf-8")).get("shapes") or {}

    def estimate(self, path: str | Path, thickness_mm: float | None = None) -> PdfShapeResult:
        stem = Path(str(path)).stem
        record = next((r for k, r in self.shapes.items() if stem == k or stem.endswith(k)), None)
        if record is None:
            raise ValueError("記録された形状の推定がありません")
        analysis = SheetMetalAnalysis.model_validate(record["analysis"])
        if thickness_mm is not None:
            analysis = analysis.model_copy(update={"thickness_mm": thickness_mm})
        return PdfShapeResult(analysis, list(record.get("dimensions_mm") or []))


class PdfShapeEstimator:
    """Estimates the five shape values from a drawing PDF (see the module docstring)."""

    def __init__(self, client: ClaudeClient | None = None, max_attempts: int = 2) -> None:
        self.client = client or ClaudeClient()
        self.max_attempts = max_attempts

    def estimate(self, path: str | Path, thickness_mm: float | None = None) -> PdfShapeResult:
        from src.pdf_flat_pattern import measure_flat_pattern

        name = Path(path).name
        pages = load_shape_pages(path)
        client = self._call_client()
        raw = self._ask(client, pages)

        warnings: list[str] = []
        if raw.get("drawing_kind") != "part":
            warnings.append("1つの板金部品の図面として読めませんでした（組立図・形のない図面など）")
        dims, off_layer = [], []
        for item in raw.get("overall_dimensions") or []:
            value = _number(item.get("value"))
            if value is None or value <= 0:
                continue
            if all(p.vector for p in pages) and not _on_layer(value, pages):
                off_layer.append(value)
            dims.append(value)
        dims = dims[:3]
        if off_layer:
            warnings.append("図面の文字にない外形寸法を読み取りました: " + "、".join(f"{v:g}" for v in off_layer))
        first = (_sane(len(raw.get("bends") or [])), _sane(len(raw.get("holes") or [])))

        flat, recount = None, None
        fp = _region(raw.get("flat_pattern"), pages)
        if fp is not None:
            page, box = fp
            flat = measure_flat_pattern(page.gray, page.px_per_mm, box)
            if not flat.get("ok"):   # not measurable (FAX noise, no closed outline): Claude counts on a zoomed crop
                recount = self._recount(client, page, box, RECOUNT_FLAT_PROMPT, "展開図")
        else:
            vr = _region(raw.get("views_region"), pages)
            if vr is not None:
                recount = self._recount(client, *vr, RECOUNT_VIEWS_PROMPT, "投影図")
        bends, holes, quality = self._counts(flat, first, recount, warnings)
        uncertain = " ".join(raw.get("uncertain") or [])
        evidence = {"first": first, "recount": recount}

        if len(dims) < 2 or raw.get("drawing_kind") != "part":
            if len(dims) < 2:
                warnings.append("部品全体の外形寸法を図面から読み取れませんでした")
            analysis = SheetMetalAnalysis(
                status="unsupported", file_name=name, thickness_mm=thickness_mm, reason_code=REASON_CODE,
                reason_codes=[REASON_CODE], message="図面PDFから形状の値を推定できませんでした", warnings=warnings)
            return PdfShapeResult(analysis, dims, first[0], first[1], flat, {**raw, "_counts": evidence}, _usage(client))

        dims_conf = "low" if off_layer or "overall_dimensions" in uncertain else "medium"
        q = {
            "hole_count": MetricQuality(method=METHOD, confidence=quality["holes"], evidence=[quality["holes_how"]]),
            "bend_count": MetricQuality(method=METHOD, confidence=quality["bends"], evidence=[quality["bends_how"]]),
        }
        metrics, how, conf = self._measure(client, pages, flat, dims, thickness_mm, evidence, warnings)
        if metrics:
            dims_text = "外形寸法 " + " × ".join(f"{v:g}" for v in dims) + " mm（図面に書かれた寸法）"
            for key in ("cut_length_mm", "blank_area_mm2"):
                q[key] = MetricQuality(method=METHOD, confidence=_lower(dims_conf, conf), evidence=how + [dims_text])
        if thickness_mm is not None:
            q["thickness_mm"] = MetricQuality(method="drawing", confidence="medium", evidence=["図面に書かれた板厚"])
        analysis = SheetMetalAnalysis(
            status="partial", file_name=name, thickness_mm=thickness_mm, hole_count=holes, bend_count=bends,
            reason_code=REASON_CODE, reason_codes=[REASON_CODE],
            message="図面PDFから推定した概算の形状（CADデータがあれば置き換えてください）",
            warnings=warnings, assumptions=[ASSUMPTION], metric_quality=q, **metrics)
        return PdfShapeResult(analysis, dims, first[0], first[1], flat, {**raw, "_counts": evidence}, _usage(client))

    # ------------------------------------------------------------ lengths
    def _measure(self, client, pages, flat, dims, thickness_mm, evidence, warnings) -> tuple[dict, list, str]:
        """Cut length and blank area, measured: from the flat pattern on the drawing when it can be scaled by
        folding it into the written overall dimensions (src/pdf_fold.py), else from the part as Claude describes
        it (base + flanges) when that description folds into the written dimensions. Neither: none (the person
        enters them)."""
        from src.pdf_fold import build_model, check_model, fold_scale

        t = thickness_mm
        if flat and flat.get("ok"):
            scale = fold_scale(flat, dims, t)
            evidence["fold"] = scale
            if scale:
                k = scale["px_per_mm"]
                cut = (flat["outer_px"] + sum(flat["holes_px"])) / k
                area = flat["area_px"] / (k * k)
                angles = "・".join(f"{abs(a)}°" for a in scale["angles"]) or "なし"
                how = [f"展開図の外周と穴の周長・面積を画像で測った（{flat['outer_px']:.0f} px ほか）",
                       f"展開図を曲げ線で折った形（曲げ角 {angles}）が外形寸法と合う縮尺 1 mm = {k:.3f} px で換算"
                       f"（折った形の外形 {' × '.join(f'{v:g}' for v in scale['extents_mm'])} mm）"]
                conf = "medium"
                if scale["ambiguous"] or not scale["span_matches"]:
                    conf = "low"
                    warnings.append("展開図の縮尺が1つに決まりませんでした（外形寸法に合う折り方が複数あります）。"
                                    "切断長・展開面積を確かめてください")
                return {"cut_length_mm": round(cut, 1), "blank_area_mm2": round(area, 1)}, how, conf
            warnings.append("展開図を折った形が図面の外形寸法と合いませんでした（縮尺を決められません）")
        model = self._model(client, pages)
        evidence["model"] = model
        built = build_model(model, t) if isinstance(model, dict) else None
        if built:
            check = check_model(built, dims, t)
            evidence["model_check"] = check
            if check["ok"]:
                how = ["展開図から測れないため、投影図から部品をベースとフランジの組み合わせとして書き出し（LLM）、その展開形の外周と穴の周長を計算",
                       f"書き出した形を折ると外形 {' × '.join(f'{v:g}' for v in check['extents_mm'])} mm で、図面の外形寸法と一致"]
                return {"cut_length_mm": built["cut_length_mm"], "blank_area_mm2": built["blank_area_mm2"]}, how, "low"
            warnings.append("投影図から書き出した部品の形が図面の外形寸法と合いませんでした")
        warnings.append("切断長・展開面積を図面から測れませんでした。値を入力してください")
        return {}, [], "low"

    def _model(self, client, pages) -> dict | None:
        content = self._content(pages)[:-1] + [{"type": "text", "text": "この部品をベースとフランジの組み合わせとして report で返してください。"}]
        try:
            raw = _repair(client.complete_json(MODEL_PROMPT, content, MODEL_SCHEMA, max_tokens=3000))
        except RuntimeError:
            return None
        return raw if isinstance(raw, dict) else None

    # ------------------------------------------------------------ counts
    @staticmethod
    def _counts(flat: dict | None, first: tuple, recount: tuple | None, warnings: list[str]) -> tuple:
        """The bend / hole counts and how they were found.
        1. the image measurement of the flat pattern, when it worked (confidence medium when Claude's first
           reading agrees, low otherwise);
        2. else Claude's count on a zoomed crop of the flat pattern / the views (the recount), else its first
           reading. Counts only Claude made are low confidence; a count that broke down (None) stays empty and
           the person enters it."""
        q: dict = {}
        if flat and flat.get("ok"):
            bends, holes = int(flat["bends"]), int(flat["holes"])
            for key, value, llm, what in (("bends", bends, first[0], "曲げ線"), ("holes", holes, first[1], "内側の閉じた輪郭")):
                q[key] = "medium" if value == llm else "low"
                q[f"{key}_how"] = f"展開図の{what}を画像で数えた（{value}。LLMの数 {llm if llm is not None else '-'}）"
            if bends != first[0]:
                warnings.append(f"曲げ数が画像の計測（{bends}）とLLMの読み取り（{first[0]}）で違います。画像の計測を使っています")
            if holes != first[1]:
                warnings.append(f"穴数が画像の計測（{holes}）とLLMの読み取り（{first[1]}）で違います。画像の計測を使っています")
            return bends, holes, q
        why = (flat or {}).get("reason") or "展開図がない"
        src = recount if recount is not None else (None, None)
        out = []
        for key, i, label in (("bends", 0, "曲げ数"), ("holes", 1, "穴数")):
            value = src[i] if src[i] is not None else first[i]
            q[key] = "low"
            how = "拡大した図でLLMが数えた" if src[i] is not None else "LLMが図面全体で数えた"
            q[f"{key}_how"] = f"{how}（{value if value is not None else '-'}。画像での確認なし：{why}）"
            if src[i] is not None and first[i] is not None and src[i] != first[i]:
                warnings.append(f"{label}がLLMの2回の読み取りで違います（{first[i]} と {src[i]}）。拡大した図での数を使っています")
            out.append(value)
        warnings.append(f"曲げ数・穴数を画像で確かめられませんでした（{why}）")
        return out[0], out[1], q

    def _recount(self, client, page: "ShapePage", box: dict, prompt: str, label: str) -> tuple | None:
        """Claude's bend / hole count on the region zoomed in (None, None for a count that broke down)."""
        from PIL import Image

        H, W = page.gray.shape
        x0, x1 = sorted((box["x0"], box["x1"]))
        y0, y1 = sorted((box["y0"], box["y1"]))
        m = 40   # a margin of 4 % of the page: the box is approximate
        crop = Image.fromarray(page.gray).crop((max(0, (x0 - m) * W // 1000), max(0, (y0 - m) * H // 1000),
                                                min(W, (x1 + m) * W // 1000), min(H, (y1 + m) * H // 1000)))
        if crop.width < 20 or crop.height < 20:
            return None
        crop.thumbnail((LONG_EDGE, LONG_EDGE))
        buf = io.BytesIO()
        crop.save(buf, format="PNG", optimize=True)
        content = [{"type": "text", "text": f"[{label}（拡大）]"}, image_block(buf.getvalue(), "image/png"),
                   {"type": "text", "text": "曲げと穴を report で返してください。"}]
        try:
            raw = _repair(client.complete_json(prompt, content, RECOUNT_SCHEMA, max_tokens=2048))
        except RuntimeError:
            return None
        if not isinstance(raw, dict):
            return None
        out = []
        for key in ("bends", "holes"):
            items = raw.get(key)
            out.append(_sane(len(items)) if isinstance(items, list) else None)
        return tuple(out)

    # ------------------------------------------------------------ LLM
    def _content(self, pages: list[ShapePage]) -> list[dict]:
        content: list[dict] = []
        for page in pages:
            w, h = page.image_size
            content.append({"type": "text", "text": f"[ページ{page.number} 全体（{w}×{h}ピクセル）]"})
            content.append(image_block(page.image, page.media_type))
            if page.vector:
                lines = "\n".join(f"({x:.0f},{y:.0f}) {t}" for x, y, t in sorted(page.texts, key=lambda r: (r[1], r[0])))
                content.append({"type": "text", "text": f"[ページ{page.number} テキスト層（x,y は左上からのmm）]\n{lines}"})
        content.append({"type": "text", "text": "この部品の外形寸法・展開図の位置・曲げ・穴を report で返してください。"})
        return content

    def _call_client(self):
        """A client of its own for this call (same model / cache), so the token counts are this drawing's even
        when several drawings are estimated at once. A test's fake client is used as it is."""
        c = self.client
        if type(c) is ClaudeClient:   # a subclass (a test's fake) is used as it is
            return ClaudeClient(model=c.model, mode=c.mode, cache_dir=c.cache_dir, api_key=c._api_key,
                                max_retries=c.max_retries)
        return c

    def _ask(self, client, pages: list[ShapePage]) -> dict:
        content = self._content(pages)
        last = None
        for attempt in range(self.max_attempts):
            system = SYSTEM_PROMPT if attempt == 0 else SYSTEM_PROMPT + "\n（前回の応答が形式に合いませんでした。すべての必須項目を返してください。）"
            try:
                raw = client.complete_json(system, content, SCHEMA, max_tokens=4096)
            except RuntimeError as exc:
                last = exc
                continue
            raw = _repair(raw)
            problem = _schema_problem(raw)
            if problem is None:
                return raw
            last = ValueError(problem)
        raise RuntimeError(f"図面の形状の読み取り結果が形式に合いません: {last}")


def _usage(client) -> dict:
    return {k: v for k, v in client.usage.as_dict().items() if isinstance(v, (int, float))} if hasattr(client, "usage") else {}


def _repair(raw):
    """Claude sometimes returns a list - or even the whole answer - serialised as a JSON string inside a field."""
    import json

    if not isinstance(raw, dict):
        return raw
    out = dict(raw)
    for key, value in raw.items():
        if not isinstance(value, str) or not value.strip().startswith(("{", "[")):
            continue
        try:
            parsed = json.loads(value)
        except ValueError:
            continue
        if isinstance(parsed, dict) and key in parsed and set(parsed) >= {"overall_dimensions", "bends"}:
            return _repair({**out, **parsed})   # the whole answer was put into one field
        if isinstance(parsed, dict) and key in parsed:
            out[key] = parsed[key]
        else:
            out[key] = parsed
    return out


def _schema_problem(raw) -> str | None:
    """Claude's answer breaks the schema now and then (a list returned as a string, a missing key)."""
    if not isinstance(raw, dict):
        return "応答が辞書ではない"
    missing = [k for k in SCHEMA["required"] if k not in raw and k != "views_region"]
    if missing:
        return "必須項目がない: " + ", ".join(missing)
    for key in ("overall_dimensions", "bends", "holes"):
        if not isinstance(raw[key], list) or not all(isinstance(x, dict) for x in raw[key]):
            return f"{key} がリストではない"
    fp = raw["flat_pattern"]
    if fp is not None and not (isinstance(fp, dict) and isinstance(fp.get("box"), dict)
                               and all(isinstance(fp["box"].get(k), (int, float)) for k in ("x0", "y0", "x1", "y1"))):
        return "flat_pattern の枠が不正"
    return None


def _sane(count: int) -> int | None:
    return count if 0 <= count <= MAX_COUNT else None


def _region(item, pages: list[ShapePage]):
    """(page, box in 0-1000 of the page) of a region Claude gave in pixels of the page image, or None."""
    if not isinstance(item, dict) or not isinstance(item.get("box"), dict):
        return None
    try:
        number = int(item.get("page") or 1)
        if not 1 <= number <= len(pages):
            return None
        page = pages[number - 1]
        w, h = page.image_size
        box = {k: int(round(1000 * float(item["box"][k]) / (w if k.startswith("x") else h))) for k in ("x0", "y0", "x1", "y1")}
    except (TypeError, ValueError, KeyError):
        return None
    return page, box


def _lower(*levels: str) -> str:
    order = ["high", "medium", "low", "unavailable"]
    return max(levels, key=order.index)
