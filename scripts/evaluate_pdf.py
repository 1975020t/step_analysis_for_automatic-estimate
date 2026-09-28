"""Evaluate a drawing-PDF condition reader against the PDF golden set.

    python scripts/generate_pdf_golden.py --per-level 60 --seed 31 --out pdf_data
    python scripts/evaluate_pdf.py --data pdf_data --reader src.pdf_reader:PdfConditionReader \
        --verify-frozen --gate --report analysis/pdf_eval_latest.md

The reader class is built with no arguments and must expose .read(path) -> dict (or a pydantic model):

    {
      "drawing_no": str | None,          "revision": str | None,
      "material": str | None,            # master code (data/materials.csv), "UNREGISTERED" or None (not written)
      "thickness_mm": float | None,
      "quantity": int | None,            # order quantity
      "surface_treatment": str | None,   # master code (data/surface_treatments.csv; "NONE" = explicitly none),
                                         # "UNREGISTERED" or None (not written)
      "processes": [{"code": str, "count_per_part": int | None}],   # data/process_rates.csv codes or "UNREGISTERED"
      "rush": bool,
      "flags": [str],                    # subset of "tolerance", "appearance", "inspection"
      "needs_review": [str],             # fields a person must confirm before the quote is final (field names above)
      "usage": {...}                     # optional: llm_calls, llm_input_tokens, llm_output_tokens, ...
    }

Per drawing and field, one outcome:
  CORRECT          value equals the truth and the field is not in needs_review
  CORRECT_FLAGGED  value equals the truth, but the reader asked for a review
                   (a review flag on a null value is ignored: null already means "ask the user")
  WRONG_FLAGGED    wrong value, reader asked for a review (a person will catch it)
  DANGEROUS        wrong value, NOT flagged (a silent wrong condition -> wrong price)
  REJECTED         the reader crashed / timed out (a person has to enter everything)
Accuracy counts CORRECT + CORRECT_FLAGGED. "Auto-confirmed" drawings have every price field CORRECT.
"""
from __future__ import annotations

import argparse
import csv
import importlib
import json
import re
import statistics
import sys
import time
import unicodedata
from collections import Counter, defaultdict
from multiprocessing import Pool
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DEFAULT_READER = "src.pdf_reader:PdfConditionReader"
DEFAULT_FROZEN = ROOT / "golden" / "datasets" / "pdf_v1.json"
OUTCOMES = ("CORRECT", "CORRECT_FLAGGED", "WRONG_FLAGGED", "DANGEROUS", "REJECTED")
PRICE_FIELDS = ("material", "thickness_mm", "quantity", "surface_treatment", "processes", "rush")
SUPPORT_FIELDS = ("drawing_no", "revision")
FLAG_FIELD = ("flags",)
ALL_FIELDS = PRICE_FIELDS + SUPPORT_FIELDS + FLAG_FIELD
FIELD_LABEL = {"material": "材質", "thickness_mm": "板厚", "quantity": "数量", "surface_treatment": "表面処理",
               "processes": "追加加工", "rush": "特急", "drawing_no": "図番", "revision": "改訂", "flags": "特記事項"}
KIND_LABEL = {"vector": "ベクター（CAD出力）", "scan": "スキャン", "fax": "FAX", "handwritten": "手書き・押印"}
FROZEN_KEYS = ("kind", "style", "unseen", "level", "truth")

# acceptance (analysis/handoff_pdf.md)
GATE = {
    "price_field_accuracy": 0.95,        # every price field, all drawings
    "price_field_accuracy_kind": 0.90,   # every price field, within every PDF kind
    "support_field_accuracy": 0.90,      # drawing number, revision
    "flags_accuracy": 0.90,
    "dangerous_field_rate": 0.01,        # per price field: wrong and not flagged
    "dangerous_drawing_rate": 0.02,      # drawings with any dangerous price field
    "auto_confirmed_rate": 0.75,         # drawings whose price fields are all correct without a review flag
}
GATE_UNSEEN = {"price_field_accuracy": 0.90}  # holdout drawings in layouts the development set does not have


def load(spec):
    module, _, name = spec.partition(":")
    return getattr(importlib.import_module(module), name)


# ------------------------------------------------------------------ comparison
def _norm_text(s):
    if s is None:
        return None
    s = unicodedata.normalize("NFKC", str(s)).upper()
    s = re.sub(r"\s+|REV\.?|△|\.", "", s)
    return s or None


def _processes(value):
    reg, unreg = {}, False
    for p in value or []:
        code = p.get("code") if isinstance(p, dict) else getattr(p, "code", None)
        n = p.get("count_per_part") if isinstance(p, dict) else getattr(p, "count_per_part", None)
        if not code:
            continue
        if code == "UNREGISTERED":
            unreg = True
            continue
        reg[code] = reg.get(code, 0) + (int(n) if n is not None else -10_000)  # a missing count is wrong
    return reg, unreg


def field_equal(field, got, truth):
    if field == "thickness_mm":
        if got is None or truth is None:
            return got is None and truth is None
        try:
            return abs(float(got) - float(truth)) < 1e-6
        except (TypeError, ValueError):
            return False
    if field == "quantity":
        try:
            return (None if got is None else int(got)) == truth
        except (TypeError, ValueError):
            return False
    if field == "processes":
        return _processes(got) == _processes(truth)
    if field == "rush":
        return bool(got) == bool(truth)
    if field == "flags":
        return set(got or []) == set(truth or [])
    if field in ("drawing_no", "revision"):
        return _norm_text(got) == _norm_text(truth)
    return (got or None) == (truth or None)


def evaluate_file(job):
    path, record, cfg = job
    started = time.time()
    try:
        reader = cfg.get("_reader") or load(cfg["reader"])()
        out = reader.read(path)
        if hasattr(out, "model_dump"):
            out = out.model_dump()
        error = None
    except Exception as exc:  # the reader crashed: nothing is usable
        out, error = {}, f"{type(exc).__name__}: {exc}"[:300]
    seconds = time.time() - started
    truth = record["truth"]
    review = set(out.get("needs_review") or [])
    if "surface_treatment" in review or "finish" in review:
        review.add("surface_treatment")
    if "thickness" in review:
        review.add("thickness_mm")
    row = {"name": record["name"], "level": record["level"], "kind": record["kind"], "style": record["style"],
           "unseen": record.get("unseen", False), "seconds": round(seconds, 2), "error": error or ""}
    for f in ALL_FIELDS:
        if error:
            outcome = "REJECTED"
        else:
            ok = field_equal(f, out.get(f), truth.get(f))
            flagged = f in review and out.get(f) is not None  # null already means "not on the drawing, ask the user"
            outcome = ("CORRECT_FLAGGED" if flagged else "CORRECT") if ok else ("WRONG_FLAGGED" if flagged else "DANGEROUS")
        row[f] = outcome
        row[f"{f}_got"] = json.dumps(out.get(f), ensure_ascii=False) if not error else ""
        row[f"{f}_truth"] = json.dumps(truth.get(f), ensure_ascii=False)
    usage = out.get("usage") or {}
    for k in ("llm_calls", "llm_input_tokens", "llm_output_tokens"):
        row[k] = usage.get(k, 0)
    row["auto_confirmed"] = all(row[f] == "CORRECT" for f in PRICE_FIELDS)
    row["dangerous"] = any(row[f] == "DANGEROUS" for f in PRICE_FIELDS)
    row["sources"] = json.dumps(record["meta"].get("sources", {}), ensure_ascii=False)
    return row


# ------------------------------------------------------------------ summaries
def accuracy(rows, f):
    return sum(r[f] in ("CORRECT", "CORRECT_FLAGGED") for r in rows) / max(len(rows), 1)


def rate(rows, f, outcome):
    return sum(r[f] == outcome for r in rows) / max(len(rows), 1)


def summarize(rows):
    s = {"n": len(rows)}
    for f in ALL_FIELDS:
        s[f] = {"accuracy": accuracy(rows, f), "dangerous": rate(rows, f, "DANGEROUS"),
                "flagged": sum(r[f] in ("CORRECT_FLAGGED", "WRONG_FLAGGED") for r in rows) / max(len(rows), 1)}
    s["auto_confirmed"] = sum(r["auto_confirmed"] for r in rows) / max(len(rows), 1)
    s["all_price_correct"] = sum(all(r[f] in ("CORRECT", "CORRECT_FLAGGED") for f in PRICE_FIELDS) for r in rows) / max(len(rows), 1)
    s["dangerous_drawings"] = sum(r["dangerous"] for r in rows) / max(len(rows), 1)
    return s


def gate(rows):
    fails = []
    overall = summarize(rows)
    for f in PRICE_FIELDS:
        if overall[f]["accuracy"] < GATE["price_field_accuracy"]:
            fails.append(f"{f} accuracy {overall[f]['accuracy']:.1%} < {GATE['price_field_accuracy']:.0%}")
        if overall[f]["dangerous"] > GATE["dangerous_field_rate"]:
            fails.append(f"{f} dangerous {overall[f]['dangerous']:.1%} > {GATE['dangerous_field_rate']:.0%}")
    for kind in sorted({r["kind"] for r in rows}):
        sub = summarize([r for r in rows if r["kind"] == kind])
        for f in PRICE_FIELDS:
            if sub[f]["accuracy"] < GATE["price_field_accuracy_kind"]:
                fails.append(f"{kind}: {f} accuracy {sub[f]['accuracy']:.1%} < {GATE['price_field_accuracy_kind']:.0%}")
    for f in SUPPORT_FIELDS:
        if overall[f]["accuracy"] < GATE["support_field_accuracy"]:
            fails.append(f"{f} accuracy {overall[f]['accuracy']:.1%} < {GATE['support_field_accuracy']:.0%}")
    if overall["flags"]["accuracy"] < GATE["flags_accuracy"]:
        fails.append(f"flags accuracy {overall['flags']['accuracy']:.1%} < {GATE['flags_accuracy']:.0%}")
    if overall["dangerous_drawings"] > GATE["dangerous_drawing_rate"]:
        fails.append(f"dangerous drawings {overall['dangerous_drawings']:.1%} > {GATE['dangerous_drawing_rate']:.0%}")
    if overall["auto_confirmed"] < GATE["auto_confirmed_rate"]:
        fails.append(f"auto-confirmed {overall['auto_confirmed']:.1%} < {GATE['auto_confirmed_rate']:.0%}")
    unseen = [r for r in rows if r["unseen"]]
    if unseen:
        sub = summarize(unseen)
        for f in PRICE_FIELDS:
            if sub[f]["accuracy"] < GATE_UNSEEN["price_field_accuracy"]:
                fails.append(f"unseen layouts: {f} accuracy {sub[f]['accuracy']:.1%} < {GATE_UNSEEN['price_field_accuracy']:.0%}")
    return fails


def _pct(x):
    return f"{100 * x:.1f}%"


def report(rows, meta):
    overall = summarize(rows)
    lines = [f"# 図面PDF 読み取り評価（{meta['data']}）", "",
             f"- 読み取り器: `{meta['reader']}`", f"- 図面数: {len(rows)}（{meta['when']}）",
             f"- 平均処理時間: {meta['mean_seconds']:.1f} 秒/図面、LLM呼び出し {meta['llm_calls']} 回、"
             f"入力 {meta['llm_input_tokens']:,} / 出力 {meta['llm_output_tokens']:,} トークン",
             f"- 正解データ照合: {meta['frozen']}", "",
             "## 図面単位", "",
             "| 指標 | 値 |", "|---|---:|",
             f"| 価格項目がすべて正しい | {_pct(overall['all_price_correct'])} |",
             f"| 自動確定（価格項目がすべて正しく、要確認なし） | {_pct(overall['auto_confirmed'])} |",
             f"| 危険誤答を含む図面 | {_pct(overall['dangerous_drawings'])} |", "",
             "## 項目別", "", "| 項目 | 正解率 | 危険誤答 | 要確認の割合 |", "|---|---:|---:|---:|"]
    for f in ALL_FIELDS:
        s = overall[f]
        lines.append(f"| {FIELD_LABEL[f]} (`{f}`) | {_pct(s['accuracy'])} | {_pct(s['dangerous'])} | {_pct(s['flagged'])} |")
    lines += ["", "## PDFの種類別（価格項目の正解率）", "",
              "| 種類 | 図面数 | " + " | ".join(FIELD_LABEL[f] for f in PRICE_FIELDS) + " | 自動確定 | 危険誤答図面 |",
              "|---|---:|" + "---:|" * (len(PRICE_FIELDS) + 2)]
    for kind in ("vector", "scan", "fax", "handwritten"):
        sub = [r for r in rows if r["kind"] == kind]
        if not sub:
            continue
        s = summarize(sub)
        lines.append(f"| {KIND_LABEL[kind]} | {len(sub)} | " + " | ".join(_pct(s[f]['accuracy']) for f in PRICE_FIELDS)
                     + f" | {_pct(s['auto_confirmed'])} | {_pct(s['dangerous_drawings'])} |")
    lines += ["", "## 様式別", "", "| 様式 | 図面数 | 価格項目すべて正しい | 自動確定 |", "|---|---:|---:|---:|"]
    for style in sorted({r["style"] for r in rows}):
        sub = [r for r in rows if r["style"] == style]
        s = summarize(sub)
        lines.append(f"| {style} | {len(sub)} | {_pct(s['all_price_correct'])} | {_pct(s['auto_confirmed'])} |")
    if any(r["unseen"] for r in rows):
        lines += ["", "## 開発用にない様式（ホールドアウトのみ）", "", "| 区分 | 図面数 | 価格項目すべて正しい | 自動確定 |", "|---|---:|---:|---:|"]
        for flag, label in ((False, "開発用と同じ様式"), (True, "開発用にない様式")):
            sub = [r for r in rows if r["unseen"] == flag]
            s = summarize(sub)
            lines.append(f"| {label} | {len(sub)} | {_pct(s['all_price_correct'])} | {_pct(s['auto_confirmed'])} |")
    by_source = defaultdict(lambda: [0, 0])
    for r in rows:
        for field, where in json.loads(r["sources"]).items():
            key = {"thickness": "thickness_mm", "finish": "surface_treatment"}.get(field, field)
            if key not in PRICE_FIELDS:
                continue
            for w in ([where] if isinstance(where, str) else sorted(set(where))):
                by_source[(key, w)][0] += 1
                by_source[(key, w)][1] += r[key] in ("CORRECT", "CORRECT_FLAGGED")
    lines += ["", "## 書かれている場所別（正解率）", "", "| 項目 | 場所 | 件数 | 正解率 |", "|---|---|---:|---:|"]
    for (f, w), (n, ok) in sorted(by_source.items()):
        lines.append(f"| {FIELD_LABEL[f]} | {w} | {n} | {_pct(ok / n)} |")
    wrong = [r for r in rows if any(r[f] in ("DANGEROUS", "REJECTED") for f in PRICE_FIELDS)]
    lines += ["", f"## 危険誤答・失敗の例（{len(wrong)}件中、先頭20件）", "", "| 図面 | 種類 | 項目 | 読み取り | 正解 |", "|---|---|---|---|---|"]
    for r in wrong[:20]:
        for f in PRICE_FIELDS:
            if r[f] in ("DANGEROUS", "REJECTED"):
                got = r[f"{f}_got"] if r[f] == "DANGEROUS" else r["error"]
                lines.append(f"| {r['name']} | {r['kind']} | {FIELD_LABEL[f]} | `{got[:60]}` | `{r[f'{f}_truth'][:60]}` |")
    fails = gate(rows)
    lines += ["", "## 受入条件", "", "合格" if not fails else "不合格:\n\n" + "\n".join(f"- {x}" for x in fails), ""]
    return "\n".join(lines)


def verify_frozen(index, frozen_path):
    frozen = {p["name"]: p for p in json.loads(Path(frozen_path).read_text(encoding="utf-8"))["drawings"]}
    current = {p["name"]: p for p in index["drawings"]}
    problems = [f"missing or extra: {n}" for n in sorted(set(frozen) ^ set(current))]
    for n in sorted(set(frozen) & set(current)):
        for k in FROZEN_KEYS:
            if frozen[n].get(k) != current[n].get(k):
                problems.append(f"{n}: {k} differs")
    return problems


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--data", default="pdf_data")
    p.add_argument("--reader", default=DEFAULT_READER)
    p.add_argument("--levels", nargs="*")
    p.add_argument("--kinds", nargs="*", choices=list(KIND_LABEL))
    p.add_argument("--names", nargs="*", help="evaluate only these drawings (e.g. Lv2_0016)")
    p.add_argument("--limit", type=int, help="first N drawings (after filters), for quick checks")
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--verify-frozen", nargs="?", const=str(DEFAULT_FROZEN))
    p.add_argument("--gate", action="store_true", help="exit 1 unless the acceptance criteria hold")
    p.add_argument("--report")
    args = p.parse_args(argv)

    data = Path(args.data)
    index = json.loads((data / "index.json").read_text(encoding="utf-8"))
    frozen_msg = "未実施"
    if args.verify_frozen:
        problems = verify_frozen(index, args.verify_frozen)
        if problems:
            print("PDF data does not match the frozen truth:\n  " + "\n  ".join(problems[:20]))
            return 2
        frozen_msg = f"一致（`{Path(args.verify_frozen).name}`）"
        print(f"PDF data matches {args.verify_frozen}")
    records = index["drawings"]
    if args.levels:
        records = [r for r in records if r["level"] in args.levels]
    if args.kinds:
        records = [r for r in records if r["kind"] in args.kinds]
    if args.names:
        records = [r for r in records if r["name"] in args.names]
    if args.limit:
        records = records[: args.limit]
    cfg = {"reader": args.reader}
    jobs = [(str(data / r["file"]), r, cfg) for r in records]
    started = time.time()
    if args.workers > 1:
        with Pool(args.workers) as pool:
            rows = list(pool.imap(evaluate_file, jobs))
    else:
        cfg["_reader"] = load(args.reader)()
        rows = [evaluate_file(j) for j in jobs]
    with (data / "results.csv").open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    meta = {"data": data.name, "reader": args.reader, "when": time.strftime("%Y-%m-%d"), "frozen": frozen_msg,
            "mean_seconds": statistics.mean(r["seconds"] for r in rows),
            "llm_calls": sum(int(r["llm_calls"] or 0) for r in rows),
            "llm_input_tokens": sum(int(r["llm_input_tokens"] or 0) for r in rows),
            "llm_output_tokens": sum(int(r["llm_output_tokens"] or 0) for r in rows)}
    text = report(rows, meta)
    if args.report:
        Path(args.report).write_text(text, encoding="utf-8")
    overall = summarize(rows)
    print(f"{len(rows)} drawings in {time.time() - started:.0f}s  all-price-correct {_pct(overall['all_price_correct'])}  "
          f"auto-confirmed {_pct(overall['auto_confirmed'])}  dangerous drawings {_pct(overall['dangerous_drawings'])}")
    for f in ALL_FIELDS:
        print(f"  {f:18s} accuracy {_pct(overall[f]['accuracy']):>7s}  dangerous {_pct(overall[f]['dangerous']):>6s}")
    if args.gate:
        fails = gate(rows)
        print("GATE PASS" if not fails else "GATE FAIL\n  " + "\n  ".join(fails))
        return 0 if not fails else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
