"""Read price-relevant ordering conditions from a sheet-metal drawing PDF.

    PdfConditionReader().read("drawing.pdf") -> dict   (format: scripts/evaluate_pdf.py)

Division of labour
  1. Rendering: every page is rendered; raster pages (scan / FAX / handwriting) and A3 sheets are also
     sent as two zoomed halves. Vector pages send their text layer with positions (exact characters).
  2. Claude transcribes the EVIDENCE: for each condition the verbatim text of the value that is valid now
     (after revision tables, handwritten corrections and additions), where it is written, the revision
     changes, every process callout, the parts-list quantity header - plus its own interpretation.
  3. Rules decide the values (src/pdf_terms.py): master codes from the verbatim text (no fuzzy matching,
     unknown items become UNREGISTERED), numbers parsed from the text, processes summed per part, parts
     list totals divided by the order quantity, the latest revision wins.
  4. A field goes to needs_review only when something disagrees: the rules and Claude's interpretation,
     the verbatim text and the PDF text layer (vector), the value and the latest revision, or Claude
     marked it uncertain. Everything else is confirmed.
Money is never computed here (QuoteEngine does that from the master).
"""
from __future__ import annotations

import io
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from src.claude_api import ClaudeClient, image_block
from src.master_loader import UNREGISTERED, MasterLoader, normalize_term
from src.pdf_terms import AMBIGUOUS, Terms, per_part

PRICE_FIELDS = ("material", "thickness_mm", "quantity", "surface_treatment", "processes", "rush")
LONG_EDGE = 1568
VECTOR_MIN_CHARS = 20


# ================================================================== PDF pages
@dataclass
class Page:
    number: int
    width_mm: float
    height_mm: float
    texts: list[tuple[float, float, str]] = field(default_factory=list)  # (x mm, y mm from top, text)
    images: list[tuple[str, bytes, str]] = field(default_factory=list)   # (label, bytes, media type)
    tiles: list[tuple[str, bytes, str]] = field(default_factory=list)    # second read of raster pages: 2x2 tiles, 3x
    vector: bool = False


def load_pages(path: str | Path) -> list[Page]:
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(str(path))
    pages = []
    try:
        for i in range(len(doc)):
            page = doc[i]
            w_pt, h_pt = page.get_size()
            to_mm = 25.4 / 72
            out = Page(i + 1, w_pt * to_mm, h_pt * to_mm)
            tp = page.get_textpage()
            for k in range(tp.count_rects()):
                left, bottom, right, top = tp.get_rect(k)
                text = tp.get_text_bounded(left, bottom, right, top).strip()
                if text:
                    out.texts.append((round(left * to_mm, 1), round((h_pt - top) * to_mm, 1), text))
            vector = sum(len(t) for *_, t in out.texts) >= VECTOR_MIN_CHARS
            out.vector = vector
            large = max(w_pt, h_pt) * to_mm > 320  # A3 and larger
            scale = LONG_EDGE / max(w_pt, h_pt)
            full = page.render(scale=scale).to_pil().convert("RGB")
            out.images.append((f"ページ{i + 1} 全体", *_encode(full, vector)))
            if not vector or large:
                zoom = page.render(scale=scale * 2).to_pil().convert("RGB")
                W, H = zoom.size
                if W >= H:
                    halves = [("左半分", (0, 0, W // 2 + W // 20, H)), ("右半分", (W // 2 - W // 20, 0, W, H))]
                else:
                    halves = [("上半分", (0, 0, W, H // 2 + H // 20)), ("下半分", (0, H // 2 - H // 20, W, H))]
                for label, box in halves:
                    crop = zoom.crop(box)
                    crop.thumbnail((LONG_EDGE, LONG_EDGE))
                    out.images.append((f"ページ{i + 1} {label}（2倍拡大）", *_encode(crop, vector)))
            if not vector:
                zoom = page.render(scale=scale * 3).to_pil().convert("RGB")
                W, H = zoom.size
                names = {(0, 0): "左上", (1, 0): "右上", (0, 1): "左下", (1, 1): "右下"}
                for (cx, cy), label in names.items():
                    box = (max(0, cx * W // 2 - W // 16), max(0, cy * H // 2 - H // 16),
                           min(W, (cx + 1) * W // 2 + W // 16), min(H, (cy + 1) * H // 2 + H // 16))
                    crop = zoom.crop(box)
                    crop.thumbnail((LONG_EDGE, LONG_EDGE))
                    out.tiles.append((f"ページ{i + 1} {label}の1/4（3倍拡大）", *_encode(crop, False)))
            pages.append(out)
    finally:
        doc.close()
    return pages


def _encode(img, vector: bool) -> tuple[bytes, str]:
    buf = io.BytesIO()
    if vector:
        img.save(buf, format="PNG", optimize=True)
        return buf.getvalue(), "image/png"
    img.save(buf, format="JPEG", quality=88)
    return buf.getvalue(), "image/jpeg"


# ================================================================== prompt
def _system_prompt(masters: MasterLoader) -> str:
    materials = "、".join(f"{code}（{row['display_name']}）" for code, row in masters.materials.items())
    finishes = "、".join(f"{code}（{row['display_name']}）" for code, row in masters.surface_treatments.items())
    processes = "、".join(f"{p['process_code']}（{p['display_name']}）" for p in masters.llm_process_catalog()
                          if p["process_code"] not in {"POLISH", "BUFF_400", "BUFF_MIRROR", "SURFACE_TREATMENT"})
    return f"""あなたは板金加工の見積担当者です。部品図面（PDF）の画像と、ある場合はPDFのテキスト層を見て、見積に使う加工条件の「根拠となる記載」をそのまま書き写してください。

## 書き写す項目
- 材質・板厚・数量・表面処理：いま有効な値が書かれた部分の文字列を、図面の表記どおりに（全角・半角・記号・単位も含めて）text に書き写す。値を含む最小の部分でよい（「材質：SPCC t1.6」なら材質は「SPCC」、板厚は「t1.6」）
- 追加加工：タップ、皿もみ（皿穴）、圧入ナット・圧入スタッド（PEM等）、スポット溶接、TIG溶接、その他の加工指示（バーリング、スペーサ、リベット、M10以上のタップなど）を、図中の引出線・注記・部品表・メモ・手書きの追記から1件ずつ、表記どおり書き写す
- 特急・図番・改訂・特記事項

## 規則
- いま有効な値だけを答える。改訂表に「数量変更 50→100」などがあれば最新の改訂の値。表題欄の値が古いままのこともある。二重線で消されて書き直された値は、書き直した値。手書きの「M4タップ 2ヶ所追加」は追加の1件として書き写す
- 書かれていない項目は null。推測しない（図の寸法から板厚を推測しない）。欄があっても空欄なら text も code も null。「注記参照」は注記を見る
- 手書き（色付きの文字・二重線の訂正・追記）と押印（特急印・承認印・受付印など）は、内容にかかわらずすべて annotations に書き写す。手書きや押印の「至急」「特急」は、印刷された「急ぎません」より優先する
- 数量は今回の製作数量。「月産500個予定（参考）」のような参考値、部品表の員数、FAXの送信ヘッダーの数字は数量ではない
- 材質は部品そのものの材質。部品表の金具の行（SUS、SS、SWCH）や「相手部品：A5052」は部品の材質ではない
- 単なる穴（2-φ10、φ4.5キリ（M4用）、M3用キリ穴、□40×20抜き、THRU）は追加加工ではない。タップの下穴を数えない
- 同じ加工が2か所に分けて指示されていれば、それぞれを別の件として書き写す（合計はこちらで計算する）。ただし、全体図と拡大図に同じ引出線が写っているだけのものは1件（同じ記載を2回書き写さない）
- 単なる穴・キリ穴・抜きは processes に入れない
- text には値の部分だけを書き写し、「材質：」「FINISH:」のような欄名・見出しは含めない。表題欄に「注記参照」「SEE NOTES」とあれば、注記の該当する記載を書き写す（where は notes）
- 部品表の金具の行は where を parts_list とし、部品表の数量欄の数を bom_count に入れる。部品表の数量欄の見出し（「数量（100台分）」「員数」「手配数（合計）」など）を parts_list_quantity_header に書き写す
- 特急：特急・至急・急ぎ・短納期・URGENT・RUSH・EXPEDITE などの指示（押印・手書きを含む）があれば true。「急ぎません」「納期：別途打合せ」、日付だけの納期は false
- 特記事項 flags：厳しい公差の指定（tolerance）、外観面・キズ不可の指定（appearance）、検査成績書・全数検査・ミルシートなどの要求（inspection）。普通公差（JIS B 0405、ISO 2768、UNLESS OTHERWISE SPECIFIED ±0.2 など）、「バリなきこと」などの定型注記、塗装の色・艶の指定は含めない
- 図番：図面番号の欄の値（旧図番は除く）。改訂：改訂表または改訂欄の最新の記号（A、B、2、△3 など）。改訂がなければ null
- code 欄には、マスターの対応するコードを参考として答える。マスターにない材質・処理・加工は UNREGISTERED（似たコードに寄せない。SUS430≠SUS304、バーリングタップ≠M4タップ、圧入スペーサ≠圧入スタッド）
- 読み取りに自信がない項目（かすれ・つぶれ、手書きが読みにくい、改訂の解釈が不確か等）は uncertain に項目名を入れる。自信があるものは入れない

## マスター
- 材質：{materials}
- 表面処理：{finishes}
- 追加加工：{processes}
"""


def _nullable(t: str) -> dict:
    return {"type": [t, "null"]}


WHERE = {"type": "string", "enum": ["title_block", "notes", "revision_table", "parts_list", "callout", "free_text",
                                    "handwriting", "stamp", "other"]}


def _schema(masters: MasterLoader) -> dict:
    mat_codes = sorted(masters.materials) + [UNREGISTERED]
    fin_codes = sorted(masters.surface_treatments) + [UNREGISTERED]
    proc_codes = sorted(p["process_code"] for p in masters.llm_process_catalog()) + [UNREGISTERED]
    value = lambda extra: {"type": "object", "properties": {"text": _nullable("string"), "where": WHERE, **extra},
                           "required": ["text", "where", *extra]}
    return {
        "type": "object",
        "properties": {
            "drawing_no": _nullable("string"),
            "revision": _nullable("string"),
            "revision_changes": {"type": "array", "items": {"type": "object", "properties": {
                "rev": {"type": "string"},
                "field": {"type": "string", "enum": ["material", "thickness", "quantity", "surface_treatment", "other"]},
                "from": _nullable("string"), "to": _nullable("string")}, "required": ["rev", "field", "from", "to"]}},
            "material": value({"code": {"type": ["string", "null"], "enum": mat_codes + [None]}}),
            "thickness": value({"value": _nullable("number")}),
            "quantity": value({"value": _nullable("integer")}),
            "surface_treatment": value({"code": {"type": ["string", "null"], "enum": fin_codes + [None]}}),
            "processes": {"type": "array", "items": {"type": "object", "properties": {
                "text": {"type": "string"}, "where": WHERE, "bom_count": _nullable("integer"),
                "handwritten_addition": {"type": "boolean"},
                "code": {"type": "string", "enum": proc_codes}, "count": _nullable("integer")},
                "required": ["text", "where", "bom_count", "handwritten_addition", "code", "count"]}},
            "parts_list_quantity_header": _nullable("string"),
            "rush": {"type": "object", "properties": {"value": {"type": "boolean"}, "text": _nullable("string")},
                     "required": ["value", "text"]},
            "flags": {"type": "array", "items": {"type": "object", "properties": {
                "category": {"type": "string", "enum": ["tolerance", "appearance", "inspection"]},
                "text": {"type": "string"}}, "required": ["category", "text"]}},
            "annotations": {"type": "array", "items": {"type": "object", "properties": {
                "text": {"type": "string"}, "kind": {"type": "string", "enum": ["handwriting", "stamp"]}},
                "required": ["text", "kind"]}},
            "uncertain": {"type": "array", "items": {"type": "string", "enum": [
                "material", "thickness", "quantity", "surface_treatment", "processes", "rush", "drawing_no", "revision"]}},
        },
        "required": ["drawing_no", "revision", "revision_changes", "material", "thickness", "quantity",
                     "surface_treatment", "processes", "parts_list_quantity_header", "rush", "flags", "annotations",
                     "uncertain"],
    }


# ================================================================== reader
class PdfConditionReader:
    """Reads the conditions of one drawing. Raster PDFs (scan / FAX / handwriting) are read twice from
    different renderings (full page + zoomed halves, and 3x zoomed quarters) and a price field is confirmed
    only when both reads give the same value; vector PDFs are read once and checked against the text layer."""

    def __init__(self, client: ClaudeClient | None = None, masters: MasterLoader | None = None,
                 max_attempts: int = 2, second_read: bool = True) -> None:
        self.masters = masters or MasterLoader(Path(__file__).resolve().parents[1] / "data")
        self.terms = Terms(self.masters)
        self.client = client or ClaudeClient()
        self._second_client = None
        self.max_attempts = max_attempts
        self.second_read = second_read
        self._system = _system_prompt(self.masters)
        self._schema = _schema(self.masters)

    # ------------------------------------------------------------ public
    def read(self, path: str | Path) -> dict:
        pages = load_pages(path)
        raster = not all(p.vector for p in pages)
        clients = [self.client]
        if raster and self.second_read:
            if self._second_client is None:
                self._second_client = ClaudeClient(model=self.client.model, mode=self.client.mode,
                                                   cache_dir=self.client.cache_dir, api_key=self.client._api_key)
            clients.append(self._second_client)
        before = [c.usage.as_dict() for c in clients]
        jobs = [(self.client, self._content(pages, tiles=False))]
        if len(clients) == 2:
            jobs.append((clients[1], self._content(pages, tiles=True)))
        if len(jobs) == 1:
            raws = [self._ask(*jobs[0])]
        else:
            from concurrent.futures import ThreadPoolExecutor

            with ThreadPoolExecutor(len(jobs)) as pool:
                raws = list(pool.map(lambda job: self._ask(*job), jobs))
        result = self.interpret(raws[0], pages)
        if len(raws) == 2:
            result = self.reconcile(result, self.interpret(raws[1], pages))
        usage: dict = {}
        for client, b in zip(clients, before):
            a = client.usage.as_dict()
            for k, v in a.items():
                if isinstance(v, (int, float)):
                    usage[k] = usage.get(k, 0) + v - b[k]
        usage["llm_input_tokens"] = usage.get("llm_input_tokens", 0) + usage.get("llm_cached_input_tokens", 0)
        usage["llm_output_tokens"] = usage.get("llm_output_tokens", 0) + usage.get("llm_cached_output_tokens", 0)
        usage["llm_calls"] = usage.get("llm_calls", 0) + usage.get("llm_cache_hits", 0)
        result["usage"] = usage
        result["evidence"] = raws
        return result

    # ------------------------------------------------------------ LLM
    def _content(self, pages: list[Page], tiles: bool) -> list[dict]:
        content: list[dict] = []
        for page in pages:
            images = page.images[:1] + page.tiles if tiles and page.tiles else page.images
            for label, data, media in images:
                content.append({"type": "text", "text": f"[{label}]"})
                content.append(image_block(data, media))
            if page.vector:
                lines = "\n".join(f"({x:.0f},{y:.0f}) {t}" for x, y, t in sorted(page.texts, key=lambda r: (r[1], r[0])))
                content.append({"type": "text", "text": f"[ページ{page.number} テキスト層（x,y は左上からのmm）]\n{lines}"})
        content.append({"type": "text", "text": "この図面の加工条件の根拠を、規則に従って report で返してください。"})
        return content

    def _ask(self, client: ClaudeClient, content: list[dict]) -> dict:
        last = None
        for attempt in range(self.max_attempts):
            system = self._system if attempt == 0 else self._system + "\n（前回の応答が形式に合いませんでした。すべての必須項目を返してください。）"
            try:
                raw = client.complete_json(system, content, self._schema, max_tokens=4096)
            except RuntimeError as exc:  # no structured result
                last = exc
                continue
            problem = _schema_problem(raw)
            if problem is None:
                return raw
            last = ValueError(problem)
        raise RuntimeError(f"図面の読み取り結果が形式に合いません: {last}")

    # ------------------------------------------------------------ rules
    def interpret(self, raw: dict, pages: list[Page] | None = None) -> dict:
        terms = self.terms
        review = _Review()
        layer = normalize_term(" ".join(t for p in (pages or []) for *_, t in p.texts))
        vector = bool(pages) and all(p.vector for p in pages)

        def on_layer(text: str | None) -> bool:
            return not vector or not text or normalize_term(text) in layer

        changes = _latest_changes(raw.get("revision_changes") or [])
        header = raw.get("parts_list_quantity_header") or ""
        bom_total = bool(re.search(r"台分|合計|総数|TOTAL|FOR\s*\d+\s*(?:UNITS|SETS|PCS)", header.upper()))

        # ---- material / surface treatment: the rules decide, Claude's code only when the rules cannot
        out: dict = {}
        for key, resolve in (("material", terms.material), ("surface_treatment", terms.finish)):
            item = raw.get(key) or {}
            text, llm_code = item.get("text"), item.get("code")
            value = resolve(text) if text else None
            if value in (None, AMBIGUOUS):
                if text or llm_code:
                    review.add(key, "規則で解釈できない表記")
                value = llm_code or None
            elif llm_code and llm_code != value and (value != UNREGISTERED or not self._strong_unregistered(key, text)):
                review.add(key, "規則とLLMの解釈が異なる")
            latest = changes.get(key)
            if latest is not None:
                expected = resolve(latest)
                if expected not in (None, AMBIGUOUS) and expected != value:
                    value = expected
                    review.add(key, "最新の改訂の値と異なる")
            if not on_layer(text):
                review.add(key, "テキスト層に見つからない")
            out[key] = value

        # ---- thickness / quantity
        for key, llm_key, parse in (("thickness_mm", "thickness", terms.thickness), ("quantity", "quantity", terms.quantity)):
            item = raw.get(llm_key) or {}
            text, llm_value = item.get("text"), item.get("value")
            latest = changes.get(llm_key)
            if latest is not None and (item.get("where") == "revision_table" or parse(text) is None):
                text = latest  # the value after the latest revision, not the whole "changed from A to B" line
            if key == "quantity" and item.get("where") == "parts_list" and not bom_total:
                text, llm_value = None, None  # a per-unit count in the parts list is not the order quantity
            value = parse(text) if text else None
            if value is None:
                if text or llm_value is not None:
                    review.add(key, "規則で数値を読み取れない")
                value = llm_value
            elif llm_value is not None and abs(float(llm_value) - float(value)) > 1e-9:
                review.add(key, "規則とLLMの読み取りが異なる")
            if latest is not None:
                expected = parse(latest)
                if expected is not None and value is not None and abs(float(expected) - float(value)) > 1e-9:
                    value = expected
                    review.add(key, "最新の改訂の値と異なる")
            if not on_layer(text if text != latest else None):
                review.add(key, "テキスト層に見つからない")
            out[key] = None if value is None else (float(value) if key == "thickness_mm" else int(value))

        # ---- processes: every callout parsed by the rules and summed per part
        totals: dict[str, int] = {}
        unregistered = 0
        for item in raw.get("processes") or []:
            text, where = item.get("text") or "", item.get("where")
            parsed = terms.process(text)
            llm_code, llm_count = item.get("code"), item.get("count")
            if not parsed and terms.is_plain_hole(text):
                continue  # a plain hole / cut-out: never an additional process
            if where == "parts_list" and parsed:
                count = item.get("bom_count")
                if bom_total and count is not None:
                    divided = per_part(count, out.get("quantity"))
                    if out.get("quantity") is None:
                        review.add("processes", "部品表が合計表記で部品数量が不明")
                    elif not divided.exact:
                        review.add("processes", "部品表の合計が部品数量で割り切れない")
                    count = divided.value
                parsed = [(code, count) for code, _ in parsed]
            if not parsed:
                review.add("processes", "規則で解釈できない加工指示")
                parsed = [(llm_code, llm_count)] if llm_code else []
            if not on_layer(text):
                review.add("processes", "テキスト層に見つからない")
            for code, count in parsed:
                if code != UNREGISTERED and llm_code not in (code, UNREGISTERED):
                    review.add("processes", "規則とLLMの加工の判定が異なる")
                if code == UNREGISTERED:
                    unregistered += 1
                    continue
                if count is None:
                    review.add("processes", "加工の個数を読み取れない")
                    count = llm_count
                totals[code] = totals.get(code, 0) + int(count or 0)
        out["processes"] = [{"code": c, "count_per_part": n} for c, n in sorted(totals.items())]
        out["processes"] += [{"code": UNREGISTERED, "count_per_part": None}] * unregistered

        # ---- rush
        rush = raw.get("rush") or {}
        rule = terms.rush(rush.get("text"))
        out["rush"] = bool(rush.get("value"))
        marked = [a.get("text") for a in raw.get("annotations") or [] if terms.rush(a.get("text"))]
        if marked and not out["rush"]:  # a handwritten / stamped rush overrides the printed text
            out["rush"], rule = True, True
        if rush.get("text") and rule is not None and rule != out["rush"]:
            review.add("rush", "特急の記載と判定が矛盾")
        if out["rush"] and rule is None:
            review.add("rush", "特急の根拠となる語がない")

        # ---- support fields and special requirements
        out["drawing_no"] = raw.get("drawing_no") or None
        if out["drawing_no"] and not on_layer(out["drawing_no"]):
            review.add("drawing_no", "テキスト層に見つからない")
        out["revision"] = (raw.get("revision") or "").replace("△", "").strip() or None
        out["flags"] = sorted({f["category"] for f in raw.get("flags") or [] if terms.flag_ok(f["category"], f.get("text"))})
        out["needs_review"] = sorted(f for f in review.reasons if f in PRICE_FIELDS + ("drawing_no", "revision"))
        out["review_reasons"] = {f: sorted(set(r)) for f, r in review.reasons.items() if f in out["needs_review"]}
        return out

    def _strong_unregistered(self, key: str, text: str | None) -> bool:
        """An explicit unregistered item (SUS430, クロムめっき, ...) rather than an unknown notation."""
        from src.pdf_terms import GENERIC_MATERIAL, upper

        if key == "material":
            return bool(GENERIC_MATERIAL.search(upper(text)))
        return bool(self.terms.FINISH_UNREGISTERED.search(upper(text)))

    @staticmethod
    def reconcile(first: dict, second: dict) -> dict:
        """Two independent reads of a raster drawing: a price field is confirmed only when both agree."""
        out = dict(first)
        reasons = {f: list(r) for f, r in first.get("review_reasons", {}).items()}
        for f in PRICE_FIELDS:
            a, b = first.get(f), second.get(f)
            same = _canonical(f, a) == _canonical(f, b)
            if not same:
                reasons.setdefault(f, []).append("2回の読み取りが一致しない")
                if f == "rush":
                    out[f] = bool(a) or bool(b)  # a rush mark seen by either read is shown (and must be confirmed)
                elif a in (None, []) and b not in (None, []):
                    out[f] = b  # one read found a value the other missed: show it, but ask for a review
            elif f in second.get("needs_review", []) and f not in reasons:
                reasons[f] = list(second["review_reasons"].get(f, []))
        for f in ("drawing_no", "revision"):
            if not out.get(f) and second.get(f):
                out[f] = second[f]
        out["review_reasons"] = {f: sorted(set(r)) for f, r in reasons.items()}
        out["needs_review"] = sorted(out["review_reasons"])
        return out


def _canonical(field_name: str, value):
    if field_name == "processes":
        return sorted((p["code"], p.get("count_per_part")) for p in value or [])
    if field_name == "thickness_mm" and value is not None:
        return round(float(value), 3)
    return value


class _Review:
    def __init__(self) -> None:
        self.reasons: dict[str, list[str]] = {}

    def add(self, field_name: str, reason: str) -> None:
        self.reasons.setdefault(field_name, []).append(reason)


def _latest_changes(changes: list[dict]) -> dict[str, str]:
    """Value after the latest revision that changed each field."""
    latest: dict[str, tuple] = {}
    for change in changes:
        f, to = change.get("field"), change.get("to")
        if f in (None, "other") or not to:
            continue
        key = _rev_key(change.get("rev") or "")
        if f not in latest or key > latest[f][0]:
            latest[f] = (key, to)
    return {f: v for f, (_, v) in latest.items()}


def _rev_key(rev: str) -> tuple:
    rev = rev.replace("△", "").strip().upper()
    if rev.isdigit():
        return (0, int(rev))
    return (1, rev)


def _schema_problem(raw) -> str | None:
    if not isinstance(raw, dict):
        return "not an object"
    for key in ("material", "thickness", "quantity", "surface_treatment", "rush"):
        if not isinstance(raw.get(key), dict):
            return f"{key} missing"
    if not isinstance(raw.get("processes"), list):
        return "processes missing"
    return None


__all__ = ["PdfConditionReader", "load_pages", "json"]
