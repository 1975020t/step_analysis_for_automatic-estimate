"""Evaluate a sheet-metal analyzer against the golden data set.

    python scripts/generate_golden.py --out golden_data
    python scripts/evaluate_golden.py --data golden_data --tolerance 0.10 --gate

Each part (and each repeat) gets exactly one outcome:
  CORRECT          values within tolerance and the quote is NOT marked 概算  (the goal)
  CORRECT_FLAGGED  values within tolerance but the quote is marked 概算      (over-cautious)
  WRONG_FLAGGED    values out of tolerance, quote marked 概算                (reviewer is warned)
  DANGEROUS        values out of tolerance, quote NOT marked 概算            (silent wrong price)
  REJECTED         analyzer returned unsupported/error/timeout               (safe, but no quote)
"解析成功" = CORRECT + CORRECT_FLAGGED (values are right, whatever the flag).
Levels passed with --must-reject (none by default) expect REJECTED; any returned value there is wrong.
Hems (bends >= 170 deg, Lv4) are parts to ANALYZE: the analyzer must also report each hem as a bend
of >= 170 deg in bend_evidence, so the quote can price it as a separate process.
"概算" is decided by the real QuoteEngine (is_estimate), not re-implemented here.

--analyzer module:Class swaps the analyzer (e.g. an LLM-assisted one). The class must accept
(k_factor=float, k_factor_is_default=bool) and expose .analyze(path) -> SheetMetalAnalysis.
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

DEFAULT_ANALYZER = "src.sheetmetal_analyzer:SheetMetalAnalyzer"
DEFAULT_FROZEN = ROOT / "golden" / "datasets" / "golden_v1.json"
HEM_ANGLE_DEG = 170.0
OUTCOMES = ("CORRECT", "CORRECT_FLAGGED", "WRONG_FLAGGED", "DANGEROUS", "REJECTED")
METRICS = ("thickness_mm", "blank_area_mm2", "cut_length_mm")


class _Timeout(Exception):
    pass


def _alarm(*_):
    raise _Timeout()


def load_analyzer(spec: str):
    module, _, name = spec.partition(":")
    return getattr(importlib.import_module(module), name)


def evaluate_part(job):
    step_path, truth, cfg = job
    from src.master_loader import MasterLoader
    from src.models import QuoteCondition
    from src.quote_engine import QuoteEngine, QuoteUnavailableError

    analyzer_cls = load_analyzer(cfg.get("analyzer", DEFAULT_ANALYZER))
    tolerance = cfg.get("tolerance", 0.01)
    row = {"name": truth["name"], "level": truth["level"], "note": truth.get("note", ""),
           "run": cfg.get("run", 0), "truth_bends": truth["bend_count"], "truth_holes": truth["hole_count"]}
    timeout = cfg.get("timeout", 120)
    use_alarm = hasattr(signal, "SIGALRM") and timeout
    if use_alarm:
        signal.signal(signal.SIGALRM, _alarm)
        signal.alarm(timeout)
    started = time.time()
    try:
        analyzer = analyzer_cls(k_factor=truth["k_factor"], k_factor_is_default=False)
        result = analyzer.analyze(step_path)
    except _Timeout:
        row.update(status="timeout", reason="TIMEOUT", seconds=timeout, outcome="REJECTED", method="")
        return row
    except Exception as exc:  # an analyzer crash is a rejection, but record it
        row.update(status="error", reason=f"EXCEPTION:{type(exc).__name__}", seconds=round(time.time() - started, 2),
                   outcome="REJECTED", method="")
        return row
    finally:
        if use_alarm:
            signal.alarm(0)
    row["seconds"] = round(time.time() - started, 2)
    row.update(getattr(analyzer, "last_usage", None) or {})  # e.g. LLM token usage
    row["status"] = result.status
    row["reason"] = ",".join(result.reason_codes)
    row["method"] = result.flat_pattern.method if result.flat_pattern else ""
    returned = result.status in {"success", "partial"}

    values_ok = True
    for key in METRICS:
        got = getattr(result, key)
        err = None if got is None else (got - truth[key]) / truth[key]
        row[f"err_{key}"] = None if err is None else round(err, 6)
        values_ok &= err is not None and abs(err) <= tolerance
    row["got_holes"], row["got_bends"] = result.hole_count, result.bend_count
    values_ok &= result.bend_count == truth["bend_count"]
    truth_hems = sum(b["angle_deg"] >= HEM_ANGLE_DEG for b in truth.get("bends", []))
    got_hems = sum((b.angle_deg or 0) >= HEM_ANGLE_DEG for b in (result.bend_evidence or []))
    row["truth_hems"], row["got_hems"] = truth_hems, got_hems
    values_ok &= got_hems == truth_hems
    if cfg.get("holes", "exact") == "exact":
        values_ok &= result.hole_count == truth["hole_count"]

    flagged = None
    if returned:
        try:
            quote = QuoteEngine(MasterLoader(ROOT / "data")).calculate(
                result, QuoteCondition(material="SS400", quantity=1))
            flagged = quote.is_estimate
        except QuoteUnavailableError:
            returned = False
    row["quote_is_estimate"] = flagged

    if not returned:
        row["outcome"] = "REJECTED"
    elif truth["level"] in cfg.get("must_reject", ()):
        row["outcome"] = "WRONG_FLAGGED" if flagged else "DANGEROUS"
    elif values_ok:
        row["outcome"] = "CORRECT_FLAGGED" if flagged else "CORRECT"
    else:
        row["outcome"] = "WRONG_FLAGGED" if flagged else "DANGEROUS"
    return row


# ---------------------------------------------------------------- summaries
def level_summary(rows):
    by_level = defaultdict(list)
    for r in rows:
        by_level[r["level"]].append(r)
    summary = {}
    for level, rs in sorted(by_level.items()):
        c = Counter(r["outcome"] for r in rs)
        n = len(rs)
        summary[level] = {
            "n": n,
            **{k.lower(): c[k] / n for k in OUTCOMES},
            "success": (c["CORRECT"] + c["CORRECT_FLAGGED"]) / n,
            "mean_seconds": statistics.mean(r["seconds"] for r in rs),
        }
    return summary


def check_gate(summary, args):
    failures = []
    for level in args.gate_levels:
        s = summary.get(level)
        if s is None:
            failures.append(f"{level}: no data")
            continue
        if level not in args.must_reject and s["success"] < args.min_success:
            failures.append(f"{level}: 解析成功 {s['success']:.1%} < {args.min_success:.0%}")
        if s["dangerous"] > args.max_dangerous:
            failures.append(f"{level}: 危険誤答 {s['dangerous']:.1%} > {args.max_dangerous:.0%}")
    for level in set(args.must_reject) & set(summary):
        s = summary[level]
        if s["rejected"] < args.min_reject:
            failures.append(f"{level}: 解析不可 {s['rejected']:.1%} < {args.min_reject:.0%}")
        if s["dangerous"] > args.max_dangerous:
            failures.append(f"{level}: 危険誤答 {s['dangerous']:.1%} > {args.max_dangerous:.0%}")
    return failures


def _pct(x):
    return f"{100 * x:.0f}%"


def _abs_pcts(values):
    vals = sorted(abs(v) * 100 for v in values if v is not None)
    if not vals:
        return "-", "-"
    p95 = vals[min(len(vals) - 1, int(round(0.95 * (len(vals) - 1))))]
    return f"{statistics.median(vals):.1f}%", f"{p95:.1f}%"


def build_report(rows, meta, summary, gate_failures=None):
    must_reject = set(meta.get("must_reject", ()))
    by_level = defaultdict(list)
    for r in rows:
        by_level[r["level"]].append(r)
    lines = [
        "# ゴールデンデータ評価レポート",
        "",
        f"- データ: {meta['dataset']}（{meta['parts']}件）× 繰り返し{meta['repeat']}回",
        f"- 解析器: `{meta['analyzer']}`",
        f"- 判定基準: 板厚・展開面積・切断長の誤差 ±{meta['tolerance'] * 100:g}%以内、曲げ数は完全一致、"
        f"穴数は{'完全一致' if meta['holes'] == 'exact' else '判定に含めない'}、ヘム（{HEM_ANGLE_DEG:g}°以上の曲げ）の検出数も一致",
        "- K=0.5（正解データと同値を「指定済み加工条件」として解析器へ渡す）",
        "- 「概算」判定は QuoteEngine.is_estimate をそのまま使用",
        "",
        "## レベル別サマリ",
        "",
        "| レベル | 件数 | **解析成功** | 確定で正解 | 正解だが概算 | 誤り（概算表示あり） | **危険誤答** | 解析不可 | 平均処理時間 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for level, s in summary.items():
        label = f"{level}（弾くべき）" if level in must_reject else level
        success = "-" if level in must_reject else _pct(s["success"])
        lines.append(f"| {label} | {s['n']} | **{success}** | {_pct(s['correct'])} | {_pct(s['correct_flagged'])} | "
                     f"{_pct(s['wrong_flagged'])} | **{_pct(s['dangerous'])}** | {_pct(s['rejected'])} | "
                     f"{s['mean_seconds']:.1f}s |")
    if gate_failures is not None:
        lines += ["", "## 合否判定", ""]
        lines += ["- **合格**（すべての目標を満たす）"] if not gate_failures else [f"- 不合格: {f}" for f in gate_failures]
    lines += ["", "## 値を返したケースの誤差（|誤差|の中央値 / 95パーセンタイル）", "",
              "| レベル | 面積 | 切断長 | 穴数一致 | 曲げ数一致 | ヘム検出一致 |", "|---|---:|---:|---:|---:|---:|"]
    for level in summary:
        rs = [r for r in by_level[level] if r["status"] in {"success", "partial"}]
        if not rs:
            lines.append(f"| {level} | - | - | - | - | - |")
            continue
        a = _abs_pcts(r["err_blank_area_mm2"] for r in rs)
        cl = _abs_pcts(r["err_cut_length_mm"] for r in rs)
        holes = sum(r["got_holes"] == r["truth_holes"] for r in rs) / len(rs)
        bends = sum(r["got_bends"] == r["truth_bends"] for r in rs) / len(rs)
        hems = sum(r["got_hems"] == r["truth_hems"] for r in rs) / len(rs)
        lines.append(f"| {level} | {a[0]} / {a[1]} | {cl[0]} / {cl[1]} | {_pct(holes)} | {_pct(bends)} | {_pct(hems)} |")
    lines += ["", "## 解析不可・概算の理由コード", "", "| レベル | 理由コード（件数） |", "|---|---|"]
    for level in summary:
        c = Counter(code for r in by_level[level] for code in (r.get("reason") or "").split(",") if code)
        lines.append(f"| {level} | " + (", ".join(f"{k} ({v})" for k, v in c.most_common()) or "-") + " |")
    dangerous = sorted((r for r in rows if r["outcome"] == "DANGEROUS"),
                       key=lambda r: -max(abs(r.get("err_blank_area_mm2") or 0), abs(r.get("err_cut_length_mm") or 0)))
    lines += ["", f"## 危険誤答の例（誤差の大きい順、全{len(dangerous)}件中 上位10件）", "",
              "| 部品 | 形状 | 展開方式 | 面積誤差 | 切断長誤差 | 穴 解析/正解 | 曲げ 解析/正解 |", "|---|---|---|---:|---:|---:|---:|"]
    fmt = lambda v: "-" if v is None else f"{v * 100:+.1f}%"
    for r in dangerous[:10]:
        lines.append(f"| {r['name']} | {r['note']} | {r['method']} | {fmt(r.get('err_blank_area_mm2'))} | "
                     f"{fmt(r.get('err_cut_length_mm'))} | {r.get('got_holes')}/{r['truth_holes']} | "
                     f"{r.get('got_bends')}/{r['truth_bends']} |")
    llm_rows = [r for r in rows if r.get("llm_model")]
    if llm_rows:
        calls = sum(int(r.get("llm_calls") or 0) for r in llm_rows)
        tin = sum(int(r.get("llm_input_tokens") or 0) for r in llm_rows)
        tout = sum(int(r.get("llm_output_tokens") or 0) for r in llm_rows)
        hits = sum(int(r.get("llm_cache_hits") or 0) for r in llm_rows)
        lines += ["", "## LLM利用量", "",
                  f"- モデル: {', '.join(sorted({r['llm_model'] for r in llm_rows}))}",
                  f"- API呼び出し {calls}回（キャッシュ再利用 {hits}回）、入力 {tin:,} トークン、出力 {tout:,} トークン",
                  f"- 1件あたり平均: 入力 {tin / len(llm_rows):,.0f} / 出力 {tout / len(llm_rows):,.0f} トークン"]
    lines += ["", "## 結果区分の定義", "",
              "- **解析成功**: 値が許容誤差内（「確定で正解」＋「正解だが概算」）",
              "- **確定で正解**: 許容誤差内、かつ見積が「概算」表示にならない（目指す状態）",
              "- **正解だが概算**: 値は正しいが「概算」表示になる（保守的すぎる）",
              "- **誤り（概算表示あり）**: 値が誤っているが「概算」表示で担当者に注意が促される",
              "- **危険誤答**: 値が誤っているのに「概算」表示にならない（誤った金額が確定値として出る）",
              "- **解析不可**: unsupported / error / タイムアウト（金額は出ない）",
              "- ヘム（Lv4）は解析対象。180°前後の曲げを bend_evidence で報告できていない場合は誤りとして扱う",
              "- --must-reject で指定したレベルは「解析不可」が正しい結果で、値を返した場合は誤りとして扱う",
              "", "再現手順:", "", "```", meta["command"], "```"]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- dataset
def verify_frozen(index, frozen_path: Path):
    """Fail loudly if the regenerated data no longer matches the frozen golden truth."""
    frozen = {p["name"]: p for p in json.loads(frozen_path.read_text(encoding="utf-8"))["parts"]}
    current = {p["name"]: p for p in index["parts"]}
    problems = sorted(set(frozen) ^ set(current))
    for name in set(frozen) & set(current):
        for key in (*METRICS, "hole_count", "bend_count"):
            a, b = frozen[name][key], current[name][key]
            if abs(a - b) > 1e-6 * max(1.0, abs(a)):
                problems.append(f"{name}.{key}: frozen={a} regenerated={b}")
    return problems


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data", default="golden_data")
    parser.add_argument("--report", default=None, help="markdown report path")
    parser.add_argument("--csv", default=None, help="per-part CSV path (default: <data>/results.csv)")
    parser.add_argument("--analyzer", default=DEFAULT_ANALYZER, help="module:Class of the analyzer under test")
    parser.add_argument("--tolerance", type=float, default=0.01, help="relative error allowed (0.10 = ±10%%)")
    parser.add_argument("--holes", choices=["exact", "ignore"], default="exact", help="hole count in success criterion")
    parser.add_argument("--repeat", type=int, default=1, help="evaluate each part N times (for non-deterministic analyzers)")
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--timeout", type=int, default=120, help="seconds per part (POSIX only)")
    parser.add_argument("--levels", nargs="*", default=None)
    parser.add_argument("--limit", type=int, default=None, help="evaluate only the first N parts per level (smoke runs)")
    parser.add_argument("--verify-frozen", nargs="?", const=str(DEFAULT_FROZEN), default=None,
                        help="abort unless the data matches the frozen golden truth (default: golden/datasets/golden_v1.json)")
    parser.add_argument("--gate", action="store_true", help="exit 1 unless the targets below are met")
    parser.add_argument("--gate-levels", nargs="*", default=["Lv0", "Lv1", "Lv2", "Lv3", "Lv4"])
    parser.add_argument("--must-reject", nargs="*", default=[], help="levels whose correct outcome is REJECTED")
    parser.add_argument("--min-success", type=float, default=0.90)
    parser.add_argument("--max-dangerous", type=float, default=0.02)
    parser.add_argument("--min-reject", type=float, default=0.95, help="required rejection rate for must-reject levels")
    args = parser.parse_args(argv)

    data = Path(args.data)
    index = json.loads((data / "index.json").read_text(encoding="utf-8"))
    if args.verify_frozen:
        problems = verify_frozen(index, Path(args.verify_frozen))
        if problems:
            print("golden data does not match the frozen truth:\n  " + "\n  ".join(problems[:20]))
            return 2
        print(f"golden data matches {args.verify_frozen}")

    parts = [p for p in index["parts"] if not args.levels or p["level"] in args.levels]
    if args.limit:
        seen = Counter()
        kept = []
        for p in parts:
            if seen[p["level"]] < args.limit:
                kept.append(p); seen[p["level"]] += 1
        parts = kept
    cfg = {"analyzer": args.analyzer, "tolerance": args.tolerance, "holes": args.holes, "timeout": args.timeout,
           "must_reject": list(args.must_reject)}
    jobs = []
    for run in range(args.repeat):
        for truth in parts:
            folder = data / ("curated" if truth["name"].startswith("G") else truth["level"])
            jobs.append((str(folder / f"{truth['name']}.step"), truth, {**cfg, "run": run}))
    started = time.time()
    with Pool(args.workers) as pool:
        rows = list(pool.imap(evaluate_part, jobs, chunksize=2))
    print(f"evaluated {len(rows)} runs ({len(parts)} parts x {args.repeat}) in {time.time() - started:.0f}s")

    csv_path = Path(args.csv) if args.csv else data / "results.csv"
    fields = sorted({k for r in rows for k in r}, key=lambda k: (k not in ("name", "level", "run", "outcome"), k))
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    summary = level_summary(rows)
    gate_failures = check_gate(summary, args) if args.gate else None
    command = " ".join(["python scripts/evaluate_golden.py", *(argv if argv is not None else sys.argv[1:])])
    meta = {"dataset": f"seed={index['seed']}, 各レベル{index['per_level']}件＋手作り", "parts": len(parts),
            "repeat": args.repeat, "analyzer": args.analyzer, "tolerance": args.tolerance, "holes": args.holes,
            "command": command, "must_reject": list(args.must_reject)}
    report = build_report(rows, meta, summary, gate_failures)
    if args.report:
        Path(args.report).write_text(report, encoding="utf-8")
    print(report)
    (data / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    if gate_failures:
        print("GATE FAILED:\n  " + "\n  ".join(gate_failures))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
