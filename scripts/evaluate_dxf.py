"""Evaluate a flat-pattern DXF analyzer against the DXF golden set.

    python scripts/generate_dxf_golden.py --per-level 40 --seed 21 --out dxf_data
    python scripts/evaluate_dxf.py --data dxf_data --analyzer src.dxf_analyzer:DxfAnalyzer \
        --verify-frozen --gate --report analysis/dxf_eval_latest.md

The analyzer class is built as  Analyzer(thickness_mm=<t>, k_factor=0.5, k_factor_is_default=False)
(thickness is not in a flat DXF - the user types it) and must expose .analyze(path) -> SheetMetalAnalysis.

Per file, one outcome:
  CORRECT          blank area and cut length within tolerance, hole and bend counts exact, quote not 概算
  CORRECT_FLAGGED  values correct, but the quote is 概算
  WRONG_FLAGGED    values wrong, quote 概算 (a reviewer is warned)
  DANGEROUS        values wrong, quote NOT 概算 (silent wrong price)
  REJECTED         unsupported / error / timeout / crash
For variant X (open contour, two parts in one file) any confirmed quote is DANGEROUS; REJECTED and
flagged results are the correct behaviour ("解析成功" for X = not confirmed).
"""
from __future__ import annotations

import argparse
import csv
import importlib
import json
import signal
import statistics
import sys
import time
from collections import Counter, defaultdict
from multiprocessing import Pool
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DEFAULT_ANALYZER = "src.dxf_analyzer:DxfAnalyzer"
DEFAULT_FROZEN = ROOT / "golden" / "datasets" / "dxf_v1.json"
OUTCOMES = ("CORRECT", "CORRECT_FLAGGED", "WRONG_FLAGGED", "DANGEROUS", "REJECTED")
METRICS = ("blank_area_mm2", "cut_length_mm")
VARIANT_LABEL = {"D0": "D0 きれい", "D1": "D1 注記あり", "D2": "D2 乱雑", "X": "X 確定してはいけない"}


class _Timeout(Exception):
    pass


def _alarm(*_):
    raise _Timeout()


def load(spec):
    module, _, name = spec.partition(":")
    return getattr(importlib.import_module(module), name)


def evaluate_file(job):
    path, truth, cfg = job
    from src.master_loader import MasterLoader
    from src.models import QuoteCondition
    from src.quote_engine import QuoteEngine, QuoteUnavailableError

    row = {"name": truth["name"], "part": truth["part"], "level": truth["level"], "variant": truth["variant"],
           "units": truth["units"], "problem": truth.get("problem", ""), "truth_holes": truth["hole_count"],
           "truth_bends": truth["bend_count"]}
    use_alarm = hasattr(signal, "SIGALRM") and cfg["timeout"]
    if use_alarm:
        signal.signal(signal.SIGALRM, _alarm)
        signal.alarm(cfg["timeout"])
    started = time.time()
    try:
        analyzer = load(cfg["analyzer"])(thickness_mm=truth["thickness_mm"], k_factor=truth["k_factor"],
                                         k_factor_is_default=False)
        result = analyzer.analyze(path)
    except _Timeout:
        row.update(status="timeout", reason="TIMEOUT", seconds=cfg["timeout"], outcome="REJECTED")
        return row
    except Exception as exc:
        row.update(status="error", reason=f"EXCEPTION:{type(exc).__name__}", seconds=round(time.time() - started, 2),
                   outcome="REJECTED")
        return row
    finally:
        if use_alarm:
            signal.alarm(0)
    row["seconds"] = round(time.time() - started, 2)
    row.update(getattr(analyzer, "last_usage", None) or {})
    row["status"], row["reason"] = result.status, ",".join(result.reason_codes)
    returned = result.status in {"success", "partial"}
    ok = True
    for key in METRICS:
        got = getattr(result, key)
        err = None if got is None else (got - truth[key]) / truth[key]
        row[f"err_{key}"] = None if err is None else round(err, 6)
        ok &= err is not None and abs(err) <= cfg["tolerance"]
    row["got_holes"], row["got_bends"] = result.hole_count, result.bend_count
    ok &= result.hole_count == truth["hole_count"] and result.bend_count == truth["bend_count"]
    flagged = None
    if returned:
        try:
            flagged = QuoteEngine(MasterLoader(ROOT / "data")).calculate(
                result, QuoteCondition(material="SS400", quantity=1)).is_estimate
        except QuoteUnavailableError:
            returned = False
    row["quote_is_estimate"] = flagged
    if not returned:
        row["outcome"] = "REJECTED"
    elif truth.get("must_not_confirm"):
        row["outcome"] = "WRONG_FLAGGED" if flagged else "DANGEROUS"
    elif ok:
        row["outcome"] = "CORRECT_FLAGGED" if flagged else "CORRECT"
    else:
        row["outcome"] = "WRONG_FLAGGED" if flagged else "DANGEROUS"
    return row


def summarize(rows, key):
    groups = defaultdict(list)
    for r in rows:
        groups[r[key]].append(r)
    out = {}
    for g, rs in sorted(groups.items()):
        c = Counter(r["outcome"] for r in rs)
        n = len(rs)
        is_x = all(r["variant"] == "X" for r in rs)
        success = (n - c["DANGEROUS"]) / n if is_x else (c["CORRECT"] + c["CORRECT_FLAGGED"]) / n
        out[g] = {"n": n, "success": success, **{k.lower(): c[k] / n for k in OUTCOMES},
                  "mean_seconds": statistics.mean(r["seconds"] for r in rs)}
    return out


def gate(by_variant, by_level, args):
    fails = []
    for v, s in by_variant.items():
        if v != "X" and s["success"] < args.min_success:
            fails.append(f"{v}: 解析成功 {s['success']:.1%} < {args.min_success:.0%}")
        if s["dangerous"] > args.max_dangerous:
            fails.append(f"{v}: 危険誤答 {s['dangerous']:.1%} > {args.max_dangerous:.0%}")
    for lv, s in by_level.items():
        if s["success"] < args.min_success:
            fails.append(f"{lv}（D0〜D2）: 解析成功 {s['success']:.1%} < {args.min_success:.0%}")
    return fails


def _pct(x):
    return f"{100 * x:.0f}%"


def report(rows, meta, by_variant, by_level, fails):
    L = ["# DXF（展開図）評価レポート", "",
         f"- データ: {meta['dataset']}（{meta['files']}ファイル＝{meta['parts']}部品×変種）",
         f"- 解析器: `{meta['analyzer']}`（板厚は正解値を入力として渡す）",
         f"- 判定基準: 展開面積・切断長の誤差 ±{meta['tolerance'] * 100:g}%以内、穴数・曲げ数（曲げ線の本数）は完全一致",
         "- X（開いた輪郭・2部品）は「確定（success）で返さない」ことが正解", "",
         "## 変種別サマリ", "",
         "| 変種 | 件数 | **解析成功** | 確定で正解 | 正解だが概算 | 誤り（概算表示あり） | **危険誤答** | 解析不可 | 平均処理時間 |",
         "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for v, s in by_variant.items():
        L.append(f"| {VARIANT_LABEL.get(v, v)} | {s['n']} | **{_pct(s['success'])}** | {_pct(s['correct'])} | "
                 f"{_pct(s['correct_flagged'])} | {_pct(s['wrong_flagged'])} | **{_pct(s['dangerous'])}** | "
                 f"{_pct(s['rejected'])} | {s['mean_seconds']:.2f}s |")
    L += ["", "## 形状レベル別（D0〜D2）", "", "| レベル | 件数 | 解析成功 | 危険誤答 |", "|---|---:|---:|---:|"]
    for lv, s in by_level.items():
        L.append(f"| {lv} | {s['n']} | {_pct(s['success'])} | {_pct(s['dangerous'])} |")
    if fails is not None:
        L += ["", "## 合否判定", ""] + (["- **合格**（すべての目標を満たす）"] if not fails else [f"- 不合格: {f}" for f in fails])
    L += ["", "## 項目別の一致率（値を返したケース、D0〜D2）", "", "| 変種 | 面積±許容内 | 切断長±許容内 | 穴数一致 | 曲げ数一致 |", "|---|---:|---:|---:|---:|"]
    tol = meta["tolerance"]
    for v in [v for v in by_variant if v != "X"]:
        rs = [r for r in rows if r["variant"] == v and r.get("status") in {"success", "partial"}]
        if not rs:
            L.append(f"| {v} | - | - | - | - |")
            continue
        f = lambda key: _pct(sum(r.get(key) is not None and abs(r[key]) <= tol for r in rs) / len(rs))
        L.append(f"| {v} | {f('err_blank_area_mm2')} | {f('err_cut_length_mm')} | "
                 f"{_pct(sum(r['got_holes'] == r['truth_holes'] for r in rs) / len(rs))} | "
                 f"{_pct(sum(r['got_bends'] == r['truth_bends'] for r in rs) / len(rs))} |")
    L += ["", "## 理由コード", "", "| 変種 | 理由コード（件数） |", "|---|---|"]
    for v in by_variant:
        c = Counter(code for r in rows if r["variant"] == v for code in (r.get("reason") or "").split(",") if code)
        L.append(f"| {v} | " + (", ".join(f"{k} ({n})" for k, n in c.most_common(8)) or "-") + " |")
    dangerous = [r for r in rows if r["outcome"] == "DANGEROUS"][:10]
    L += ["", f"## 危険誤答の例（{sum(r['outcome'] == 'DANGEROUS' for r in rows)}件中 最大10件）", "",
          "| ファイル | 単位 | 問題 | 面積誤差 | 切断長誤差 | 穴 解析/正解 | 曲げ 解析/正解 |", "|---|---|---|---:|---:|---:|---:|"]
    fm = lambda x: "-" if x is None else f"{x * 100:+.1f}%"
    for r in dangerous:
        L.append(f"| {r['name']} | {r['units']} | {r['problem'] or '-'} | {fm(r.get('err_blank_area_mm2'))} | "
                 f"{fm(r.get('err_cut_length_mm'))} | {r.get('got_holes')}/{r['truth_holes']} | {r.get('got_bends')}/{r['truth_bends']} |")
    L += ["", "再現手順:", "", "```", meta["command"], "```"]
    return "\n".join(L) + "\n"


def verify_frozen(index, frozen_path):
    frozen = {p["name"]: p for p in json.loads(Path(frozen_path).read_text(encoding="utf-8"))["parts"]}
    current = {p["name"]: p for p in index["parts"]}
    problems = sorted(set(frozen) ^ set(current))
    for n in set(frozen) & set(current):
        for k in (*METRICS, "hole_count", "bend_count", "thickness_mm", "units", "must_not_confirm"):
            a, b = frozen[n].get(k), current[n].get(k)
            if isinstance(a, float) and isinstance(b, float):
                if abs(a - b) > 1e-6 * max(1.0, abs(a)):
                    problems.append(f"{n}.{k}: {a} != {b}")
            elif a != b:
                problems.append(f"{n}.{k}: {a} != {b}")
    return problems


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--data", default="dxf_data")
    p.add_argument("--analyzer", default=DEFAULT_ANALYZER)
    p.add_argument("--report")
    p.add_argument("--csv")
    p.add_argument("--tolerance", type=float, default=0.10)
    p.add_argument("--workers", type=int, default=2)
    p.add_argument("--timeout", type=int, default=60)
    p.add_argument("--variants", nargs="*")
    p.add_argument("--levels", nargs="*")
    p.add_argument("--limit", type=int, help="first N parts per level (all their variants)")
    p.add_argument("--verify-frozen", nargs="?", const=str(DEFAULT_FROZEN))
    p.add_argument("--gate", action="store_true")
    p.add_argument("--min-success", type=float, default=0.90)
    p.add_argument("--max-dangerous", type=float, default=0.02)
    args = p.parse_args(argv)

    data = Path(args.data)
    index = json.loads((data / "index.json").read_text(encoding="utf-8"))
    if args.verify_frozen:
        problems = verify_frozen(index, args.verify_frozen)
        if problems:
            print("DXF data does not match the frozen truth:\n  " + "\n  ".join(problems[:20]))
            return 2
        print(f"DXF data matches {args.verify_frozen}")
    parts = [t for t in index["parts"] if (not args.variants or t["variant"] in args.variants)
             and (not args.levels or t["level"] in args.levels)]
    if args.limit:
        keep = {}
        for t in parts:
            keep.setdefault(t["level"], [])
            if t["part"] not in keep[t["level"]] and len(keep[t["level"]]) < args.limit:
                keep[t["level"]].append(t["part"])
        allowed = {x for v in keep.values() for x in v}
        parts = [t for t in parts if t["part"] in allowed]
    cfg = {"analyzer": args.analyzer, "tolerance": args.tolerance, "timeout": args.timeout}
    jobs = [(str(data / t["variant"] / f"{t['name']}.dxf"), t, cfg) for t in parts]
    started = time.time()
    with Pool(args.workers) as pool:
        rows = list(pool.imap(evaluate_file, jobs, chunksize=4))
    print(f"evaluated {len(rows)} DXF files in {time.time() - started:.0f}s")
    csv_path = Path(args.csv) if args.csv else data / "results.csv"
    fields = sorted({k for r in rows for k in r}, key=lambda k: (k not in ("name", "variant", "level", "outcome"), k))
    with csv_path.open("w", newline="", encoding="utf-8") as h:
        w = csv.DictWriter(h, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    by_variant = summarize(rows, "variant")
    by_level = summarize([r for r in rows if r["variant"] != "X"], "level")
    fails = gate(by_variant, by_level, args) if args.gate else None
    meta = {"dataset": f"seed={index['seed']}, 各レベル{index['per_level']}部品", "files": len(rows),
            "parts": len({r["part"] for r in rows}), "analyzer": args.analyzer, "tolerance": args.tolerance,
            "command": " ".join(["python scripts/evaluate_dxf.py", *(argv if argv is not None else sys.argv[1:])])}
    text = report(rows, meta, by_variant, by_level, fails)
    if args.report:
        Path(args.report).write_text(text, encoding="utf-8")
    print(text)
    if fails:
        print("GATE FAILED:\n  " + "\n  ".join(fails))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
