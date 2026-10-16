"""Evaluate the PDF-only shape estimate (src/pdf_shape.py) against the shape of the same part's STEP.

    python scripts/evaluate_pdf_shape.py --data pdf_data --gate --report analysis/pdf_shape_eval_latest.md

Every drawing of a PDF golden set (pdf_data, pdf_shift_dev, ...) comes with the STEP of the same part, and its
index.json holds the part's shape values measured from that model (`part_truth`: thickness, blank area, cut
length, holes, bends). For each drawing:

  * the estimator reads only the PDF (the thickness is given: on the screens it comes from the drawing reading
    or the person, and the thickness reading is evaluated by scripts/evaluate_pdf.py);
  * the quote is computed twice with the same conditions (the drawing's true material / finish / processes /
    rush from the golden set, QuoteEngine and data/*.csv): once with the estimated shape values, once with the
    true ones. The price error is estimated / true - 1.

Quantities: the drawing's quantity (10 when the drawing has none), and 1 and 100 for reference (the setup
cost dilutes the shape at small quantities). Results per drawing go to <data>/shape_results.csv.
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.master_loader import UNREGISTERED, MasterLoader  # noqa: E402
from src.models import AdditionalProcess, QuoteCondition, SheetMetalAnalysis  # noqa: E402
from src.quote_engine import QuoteEngine  # noqa: E402

KIND_LABEL = {"vector": "ベクター（CAD出力）", "scan": "スキャン", "fax": "FAX", "handwritten": "手書き・押印"}
SOURCE_LABEL = {"flat": "展開図を測った（縮尺は外形寸法から）", "model": "投影図から組んだ形（外形寸法と照合）",
                "none": "測れなかった（入力が必要）"}
METRICS = ("blank_area_mm2", "cut_length_mm", "hole_count", "bend_count")
# acceptance: the price of a PDF-only quote within ±20% of the price from the STEP for 90% of the drawings
GATE = {"within20": 0.90, "within20_kind": 0.80}
DEFAULT_QTY = 10


def condition(truth: dict, masters: MasterLoader, quantity: int | None) -> QuoteCondition:
    material = truth.get("material")
    if material in (None, UNREGISTERED) or material not in masters.materials:
        material = "SPCC"
    finish = truth.get("surface_treatment")
    if finish in (None, UNREGISTERED) or finish not in masters.surface_treatments:
        finish = None
    procs = [AdditionalProcess(process_code=p["code"], quantity=p["count_per_part"], source="drawing")
             for p in truth.get("processes") or [] if p["code"] in masters.process_rates and p.get("count_per_part")]
    return QuoteCondition(material=material, quantity=quantity or truth.get("quantity") or DEFAULT_QTY,
                          additional_processes=procs, surface_treatment=finish, rush=bool(truth.get("rush")))


def price(engine: QuoteEngine, values: dict, cond: QuoteCondition) -> float:
    analysis = SheetMetalAnalysis(status="success", file_name="eval", **values)
    return engine.calculate(analysis, cond).final_price


def run_one(estimator, data: Path, item: dict, engine: QuoteEngine, masters: MasterLoader) -> dict:
    truth = item["part_truth"]
    row = {"name": item["name"], "level": item["level"], "kind": item["kind"], "style": item["style"],
           "pages": item["meta"].get("pages", 1)}
    started = time.time()
    try:
        result = estimator.estimate(data / item["file"], thickness_mm=truth["thickness_mm"])
    except Exception as exc:  # noqa: BLE001
        row.update(status="error", error=f"{type(exc).__name__}: {exc}"[:200], seconds=round(time.time() - started, 1))
        return row
    a = result.analysis
    row.update(status=a.status, seconds=round(time.time() - started, 1),
               dims=" x ".join(f"{v:g}" for v in result.dimensions_mm),
               flat_ok=bool(result.flat and result.flat.get("ok")),
               flat_reason=(result.flat or {}).get("reason", "展開図なし" if not result.flat else ""),
               bends_llm=result.bends_llm, holes_llm=result.holes_llm,
               bends_img=(result.flat or {}).get("bends"), holes_img=(result.flat or {}).get("holes"),
               solid_img=(result.flat or {}).get("solid_bend_lines"), leader_img=(result.flat or {}).get("leader_lines"),
               recount=" / ".join(str(v) for v in ((result.raw or {}).get("_counts") or {}).get("recount") or ()),
               input_tokens=result.usage.get("llm_input_tokens", 0) + result.usage.get("llm_cached_input_tokens", 0),
               output_tokens=result.usage.get("llm_output_tokens", 0) + result.usage.get("llm_cached_output_tokens", 0),
               warnings=" / ".join(a.warnings))
    for k in METRICS:
        row[f"true_{k}"] = truth[k]
        row[f"est_{k}"] = getattr(a, k)
    ev = ((result.raw or {}).get("_counts") or {})
    fold = ev.get("fold") or {}
    row["length_source"] = "none" if a.cut_length_mm is None else ("flat" if fold else "model")
    row["fold_scale"] = fold.get("px_per_mm")
    row["fold_angles"] = " ".join(str(x) for x in fold.get("angles") or [])
    row["fold_ambiguous"] = fold.get("ambiguous")
    if a.status not in ("success", "partial") or a.cut_length_mm is None or a.blank_area_mm2 is None:
        return row   # not priced: the person enters the lengths (counts as a miss)
    values = {k: getattr(a, k) for k in ("thickness_mm",) + METRICS}
    true_values = {k: truth[k] for k in ("thickness_mm",) + METRICS}
    for label, qty in (("", None), ("_q1", 1), ("_q100", 100)):
        cond = condition(item["truth"], masters, qty)
        t, e = price(engine, true_values, cond), price(engine, values, cond)
        row[f"price_true{label}"] = round(t)
        row[f"price_est{label}"] = round(e)
        row[f"price_err{label}"] = round(e / t - 1, 4)
    return row


def _pct(v: float) -> str:
    return f"{100 * v:.1f}%"


def summarize(rows: list[dict], key: str = "price_err") -> dict:
    errs = [abs(r[key]) for r in rows if r.get(key) is not None]
    n = len(rows)
    if not n:
        return {}
    within = lambda t: sum(1 for e in errs if e <= t) / n  # noqa: E731 - unpriced drawings count as misses
    return {"n": n, "priced": len(errs), "within10": within(0.10), "within20": within(0.20),
            "median": statistics.median(errs) if errs else None,
            "p90": sorted(errs)[int(0.9 * (len(errs) - 1))] if errs else None,
            "bias": statistics.median([r[key] for r in rows if r.get(key) is not None]) if errs else None}


def metric_summary(rows: list[dict]) -> dict:
    ok = [r for r in rows if r.get("est_cut_length_mm") is not None]
    counted = [r for r in rows if r.get("est_bend_count") is not None and r.get("est_hole_count") is not None]
    out = {}
    for k in ("blank_area_mm2", "cut_length_mm"):
        errs = [abs(r[f"est_{k}"] / r[f"true_{k}"] - 1) for r in ok]
        out[k] = {"median": statistics.median(errs) if errs else None,
                  "within20": sum(e <= 0.2 for e in errs) / len(rows) if rows else 0}
    for k in ("hole_count", "bend_count"):
        out[k] = {"exact": sum(r[f"est_{k}"] == r[f"true_{k}"] for r in counted) / len(rows) if rows else 0,
                  "within1": sum(abs(r[f"est_{k}"] - r[f"true_{k}"]) <= 1 for r in counted) / len(rows) if rows else 0}
    return out


def report(rows: list[dict], args, elapsed: float) -> str:
    s = summarize(rows)
    lines = [f"# 図面PDFだけからの見積の評価（{args.data}）", "",
             f"- データ: `{args.data}`（{len(rows)}枚。各図面と同じ部品のSTEPから測った形状の値を正解にする）",
             "- 推定器: `src.pdf_shape:PdfShapeEstimator`（板厚は与える。条件は正解の材質・表面処理・追加加工・特急）",
             f"- 金額の誤差 = 図面PDFからの見積 ÷ STEPからの見積 − 1。数量は図面の数量（書かれていなければ {DEFAULT_QTY}）",
             f"- 実行時間 {elapsed:.0f} 秒", ""]
    lines += ["## 金額の誤差", "", "| 対象 | 枚数 | ±10%以内 | ±20%以内 | 誤差の中央値 | 90パーセンタイル | 偏り（中央値） |", "|---|---:|---:|---:|---:|---:|---:|"]

    def row_of(label, rs, key="price_err"):
        x = summarize(rs, key)
        if not x:
            return
        lines.append(f"| {label} | {x['n']} | {_pct(x['within10'])} | {_pct(x['within20'])} | "
                     f"{_pct(x['median']) if x['median'] is not None else '-'} | {_pct(x['p90']) if x['p90'] is not None else '-'} | "
                     f"{('+' if (x['bias'] or 0) >= 0 else '') + _pct(x['bias']) if x['bias'] is not None else '-'} |")
    row_of("すべて（図面の数量）", rows)
    row_of("すべて（数量 1）", rows, "price_err_q1")
    row_of("すべて（数量 100）", rows, "price_err_q100")
    by = defaultdict(list)
    for r in rows:
        by[("kind", r["kind"])].append(r)
        by[("level", r["level"])].append(r)
        by[("flat", SOURCE_LABEL.get(r.get("length_source"), "-"))].append(r)
    for kind in KIND_LABEL:
        if ("kind", kind) in by:
            row_of(KIND_LABEL[kind], by[("kind", kind)])
    for lv in sorted(k[1] for k in by if k[0] == "level"):
        row_of(lv, by[("level", lv)])
    for label in SOURCE_LABEL.values():
        if ("flat", label) in by:
            row_of(label, by[("flat", label)])
    m = metric_summary(rows)
    lines += ["", "## 形状の値", "", "| 値 | 指標 | 結果 |", "|---|---|---:|",
              f"| 展開面積 | 誤差の中央値 / ±20%以内 | {_pct(m['blank_area_mm2']['median'] or 0)} / {_pct(m['blank_area_mm2']['within20'])} |",
              f"| 切断長 | 誤差の中央値 / ±20%以内 | {_pct(m['cut_length_mm']['median'] or 0)} / {_pct(m['cut_length_mm']['within20'])} |",
              f"| 穴数 | 一致 / ±1以内 | {_pct(m['hole_count']['exact'])} / {_pct(m['hole_count']['within1'])} |",
              f"| 曲げ数 | 一致 / ±1以内 | {_pct(m['bend_count']['exact'])} / {_pct(m['bend_count']['within1'])} |"]
    errors = [r for r in rows if r.get("status") not in ("success", "partial")]
    tokens_in = sum(r.get("input_tokens") or 0 for r in rows)
    tokens_out = sum(r.get("output_tokens") or 0 for r in rows)
    flat_ok = sum(1 for r in rows if r.get("flat_ok"))
    lines += ["", "## その他", "",
              f"- 金額を出せなかった図面: {len(errors)}枚" + (f"（{', '.join(r['name'] + ':' + (r.get('error') or r.get('status', '')) for r in errors[:10])}）" if errors else ""),
              f"- 切断長・展開面積を測れず、入力が必要な図面: {sum(1 for r in rows if r.get('length_source') == 'none')}枚（±20%以内には数えない）",
              f"- 展開図を画像で計測できた図面: {flat_ok}枚（{_pct(flat_ok / len(rows)) if rows else '-'}）",
              f"- LLMのトークン数: 入力 {tokens_in:,}・出力 {tokens_out:,}（1枚あたり 入力 {tokens_in // max(1, len(rows)):,}・出力 {tokens_out // max(1, len(rows)):,}）",
              f"- 1枚あたりの処理時間（平均、キャッシュ込み）: {statistics.mean([r.get('seconds') or 0 for r in rows]):.1f} 秒"]
    worst = sorted([r for r in rows if r.get("price_err") is not None], key=lambda r: -abs(r["price_err"]))[:12]
    if worst and not args.hide_examples:
        lines += ["", "## 誤差の大きい図面", "", "| 図面 | 種類 | 誤差 | 曲げ（推定/正解） | 穴（推定/正解） | 切断長（推定/正解） | 寸法 | 長さの出どころ |",
                  "|---|---|---:|---|---|---|---|---|"]
        for r in worst:
            lines.append(f"| {r['name']} | {r['kind']} | {'+' if r['price_err'] >= 0 else ''}{_pct(r['price_err'])} | "
                         f"{r['est_bend_count']}/{r['true_bend_count']} | {r['est_hole_count']}/{r['true_hole_count']} | "
                         f"{r['est_cut_length_mm']:.0f}/{r['true_cut_length_mm']:.0f} | {r.get('dims', '')} | "
                         f"{SOURCE_LABEL.get(r.get('length_source'), '-')}{'' if r.get('flat_ok') or r.get('length_source') == 'flat' else '（' + (r.get('flat_reason') or '-') + '）'} |")
    if args.gate:
        lines += ["", "## 合否", ""] + [f"- {name}: {'合格' if ok else '不合格'}（{detail}）" for name, ok, detail in gate(rows)]
    return "\n".join(lines) + "\n"


def gate(rows: list[dict]) -> list[tuple[str, bool, str]]:
    s = summarize(rows)
    out = [("金額が±20%以内の図面（すべて）", s["within20"] >= GATE["within20"], f"{_pct(s['within20'])}、基準 {_pct(GATE['within20'])}以上")]
    by = defaultdict(list)
    for r in rows:
        by[r["kind"]].append(r)
    for kind, rs in sorted(by.items()):
        k = summarize(rs)
        out.append((f"金額が±20%以内の図面（{KIND_LABEL.get(kind, kind)}）", k["within20"] >= GATE["within20_kind"],
                    f"{_pct(k['within20'])}、基準 {_pct(GATE['within20_kind'])}以上"))
    return out


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--data", default="pdf_data")
    p.add_argument("--levels", nargs="*")
    p.add_argument("--kinds", nargs="*", choices=list(KIND_LABEL))
    p.add_argument("--names", nargs="*")
    p.add_argument("--limit", type=int)
    p.add_argument("--every", type=int, default=1, help="every N-th drawing (quick checks)")
    p.add_argument("--workers", type=int, default=6)
    p.add_argument("--gate", action="store_true")
    p.add_argument("--hide-examples", action="store_true")
    p.add_argument("--report")
    args = p.parse_args()

    from src.pdf_shape import PdfShapeEstimator

    data = Path(args.data)
    if not data.is_absolute():
        data = ROOT / data
    items = json.loads((data / "index.json").read_text(encoding="utf-8"))["drawings"]
    items = [i for i in items if (not args.levels or i["level"] in args.levels) and (not args.kinds or i["kind"] in args.kinds)
             and (not args.names or i["name"] in args.names)][::args.every]
    if args.limit:
        items = items[:args.limit]
    masters = MasterLoader(ROOT / "data")
    engine = QuoteEngine(masters)
    estimator = PdfShapeEstimator()
    started = time.time()
    with ThreadPoolExecutor(args.workers) as pool:
        rows = list(pool.map(lambda it: run_one(estimator, data, it, engine, masters), items))
    elapsed = time.time() - started
    fields = sorted({k for r in rows for k in r}, key=lambda k: (k not in ("name", "level", "kind", "style"), k))
    with open(data / "shape_results.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    text = report(rows, args, elapsed)
    print(text)
    if args.report:
        Path(args.report).write_text(text, encoding="utf-8")
    if args.gate and not all(ok for _, ok, _ in gate(rows)):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
