"""Evaluate the sheet-metal analyzer against the golden data set.

    python scripts/generate_golden.py --out golden_data
    python scripts/evaluate_golden.py --data golden_data --report analysis/golden_eval_baseline.md

Each part gets exactly one outcome:
  CORRECT          values within tolerance and the quote is NOT marked 概算  (the goal)
  CORRECT_FLAGGED  values within tolerance but the quote is marked 概算      (over-cautious)
  WRONG_FLAGGED    values out of tolerance, quote marked 概算                (reviewer is warned)
  DANGEROUS        values out of tolerance, quote NOT marked 概算            (silent wrong price)
  REJECTED         analyzer returned unsupported/error                       (safe, but no quote)
For Lv4 (must be rejected) REJECTED is the correct outcome and any returned value is wrong.
"概算" is decided by the real QuoteEngine (is_estimate), not re-implemented here.
"""
from __future__ import annotations

import argparse
import csv
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

TOLERANCE = {"thickness_mm": 0.01, "blank_area_mm2": 0.01, "cut_length_mm": 0.01}
MUST_REJECT = {"Lv4"}
OUTCOMES = ("CORRECT", "CORRECT_FLAGGED", "WRONG_FLAGGED", "DANGEROUS", "REJECTED")


class _Timeout(Exception):
    pass


def _alarm(*_):
    raise _Timeout()


def evaluate_part(job):
    step_path, truth, timeout = job
    from src.master_loader import MasterLoader
    from src.models import QuoteCondition
    from src.quote_engine import QuoteEngine, QuoteUnavailableError
    from src.sheetmetal_analyzer import SheetMetalAnalyzer

    row = {"name": truth["name"], "level": truth["level"], "note": truth.get("note", ""),
           "truth_bends": truth["bend_count"], "truth_holes": truth["hole_count"]}
    use_alarm = hasattr(signal, "SIGALRM") and timeout
    if use_alarm:
        signal.signal(signal.SIGALRM, _alarm)
        signal.alarm(timeout)
    started = time.time()
    try:
        result = SheetMetalAnalyzer(k_factor=truth["k_factor"], k_factor_is_default=False).analyze(step_path)
    except _Timeout:
        row.update(status="timeout", reason="TIMEOUT", seconds=timeout, outcome="REJECTED")
        return row
    finally:
        if use_alarm:
            signal.alarm(0)
    row["seconds"] = round(time.time() - started, 2)
    row["status"] = result.status
    row["reason"] = ",".join(result.reason_codes)
    row["method"] = result.flat_pattern.method if result.flat_pattern else ""
    returned = result.status in {"success", "partial"}

    errors_ok = True
    for key, tol in TOLERANCE.items():
        got = getattr(result, key)
        err = None if got is None else (got - truth[key]) / truth[key]
        row[f"err_{key}"] = None if err is None else round(err, 6)
        errors_ok &= err is not None and abs(err) <= tol
    row["got_holes"], row["got_bends"] = result.hole_count, result.bend_count
    errors_ok &= result.hole_count == truth["hole_count"] and result.bend_count == truth["bend_count"]

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
    elif truth["level"] in MUST_REJECT:
        row["outcome"] = "WRONG_FLAGGED" if flagged else "DANGEROUS"
    elif errors_ok:
        row["outcome"] = "CORRECT_FLAGGED" if flagged else "CORRECT"
    else:
        row["outcome"] = "WRONG_FLAGGED" if flagged else "DANGEROUS"
    return row


def _pct(n, d):
    return f"{100 * n / d:.0f}%" if d else "-"


def _abs_pcts(values):
    vals = sorted(abs(v) * 100 for v in values if v is not None)
    if not vals:
        return "-", "-"
    p95 = vals[min(len(vals) - 1, int(round(0.95 * (len(vals) - 1))))]
    return f"{statistics.median(vals):.1f}%", f"{p95:.1f}%"


def build_report(rows, meta):
    by_level = defaultdict(list)
    for r in rows:
        by_level[r["level"]].append(r)
    lines = [
        "# ゴールデンデータ評価レポート",
        "",
        f"- データ: seed={meta['seed']}、各レベル{meta['per_level']}件＋手作り{meta['curated']}件（合計{len(rows)}件）",
        f"- 判定基準: 板厚・展開面積・切断長の誤差 ±{int(TOLERANCE['blank_area_mm2'] * 100)}%以内、穴数・曲げ数は完全一致",
        "- K=0.5（正解データと同値を「指定済み加工条件」として解析器へ渡す）",
        "- 「概算」判定は QuoteEngine.is_estimate をそのまま使用",
        "",
        "## レベル別サマリ",
        "",
        "| レベル | 件数 | 確定で正解 | 正解だが概算 | 誤り（概算表示あり） | **危険誤答** | 解析不可 | 平均処理時間 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for level in sorted(by_level):
        rs = by_level[level]
        c = Counter(r["outcome"] for r in rs)
        t = statistics.mean(r["seconds"] for r in rs)
        label = f"{level}（弾くべき）" if level in MUST_REJECT else level
        lines.append(f"| {label} | {len(rs)} | {_pct(c['CORRECT'], len(rs))} | {_pct(c['CORRECT_FLAGGED'], len(rs))} | "
                     f"{_pct(c['WRONG_FLAGGED'], len(rs))} | **{_pct(c['DANGEROUS'], len(rs))}** | "
                     f"{_pct(c['REJECTED'], len(rs))} | {t:.1f}s |")
    lines += ["", "## 値を返したケースの誤差（|誤差|の中央値 / 95パーセンタイル）", "",
              "| レベル | 面積 | 切断長 | 穴数一致 | 曲げ数一致 |", "|---|---:|---:|---:|---:|"]
    for level in sorted(by_level):
        rs = [r for r in by_level[level] if r["status"] in {"success", "partial"}]
        if not rs:
            lines.append(f"| {level} | - | - | - | - |")
            continue
        a = _abs_pcts(r["err_blank_area_mm2"] for r in rs)
        cl = _abs_pcts(r["err_cut_length_mm"] for r in rs)
        holes = sum(r["got_holes"] == r["truth_holes"] for r in rs)
        bends = sum(r["got_bends"] == r["truth_bends"] for r in rs)
        lines.append(f"| {level} | {a[0]} / {a[1]} | {cl[0]} / {cl[1]} | {_pct(holes, len(rs))} | {_pct(bends, len(rs))} |")
    lines += ["", "## 解析不可・概算の理由コード", "", "| レベル | 理由コード（件数） |", "|---|---|"]
    for level in sorted(by_level):
        c = Counter(code for r in by_level[level] for code in (r["reason"] or "").split(",") if code)
        lines.append(f"| {level} | " + (", ".join(f"{k} ({v})" for k, v in c.most_common()) or "-") + " |")
    dangerous = sorted((r for r in rows if r["outcome"] == "DANGEROUS"),
                       key=lambda r: -max(abs(r.get("err_blank_area_mm2") or 0), abs(r.get("err_cut_length_mm") or 0)))
    lines += ["", f"## 危険誤答の例（誤差の大きい順、全{len(dangerous)}件中 上位10件）", "",
              "| 部品 | 形状 | 展開方式 | 面積誤差 | 切断長誤差 | 穴 解析/正解 | 曲げ 解析/正解 |", "|---|---|---|---:|---:|---:|---:|"]
    for r in dangerous[:10]:
        fmt = lambda v: "-" if v is None else f"{v * 100:+.1f}%"
        lines.append(f"| {r['name']} | {r['note']} | {r['method']} | {fmt(r['err_blank_area_mm2'])} | "
                     f"{fmt(r['err_cut_length_mm'])} | {r['got_holes']}/{r['truth_holes']} | {r['got_bends']}/{r['truth_bends']} |")
    lines += ["", "## 結果区分の定義", "",
              "- **確定で正解**: 許容誤差内、かつ見積が「概算」表示にならない（目指す状態）",
              "- **正解だが概算**: 値は正しいが「概算」表示になる（保守的すぎる）",
              "- **誤り（概算表示あり）**: 値が誤っているが「概算」表示で担当者に注意が促される",
              "- **危険誤答**: 値が誤っているのに「概算」表示にならない（誤った金額が確定値として出る）",
              "- **解析不可**: unsupported / error / タイムアウト（金額は出ない）",
              "- Lv4は「解析不可」が正しい結果で、値を返した場合は誤りとして扱う",
              "", "再現手順:", "", "```", f"python scripts/generate_golden.py --per-level {meta['per_level']} --seed {meta['seed']} --out golden_data",
              "python scripts/evaluate_golden.py --data golden_data --report analysis/golden_eval_baseline.md", "```"]
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", default="golden_data")
    parser.add_argument("--report", default=None, help="markdown report path")
    parser.add_argument("--csv", default=None, help="per-part CSV path (default: <data>/results.csv)")
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--timeout", type=int, default=120, help="seconds per part (POSIX only)")
    parser.add_argument("--levels", nargs="*", default=None)
    args = parser.parse_args(argv)

    data = Path(args.data)
    index = json.loads((data / "index.json").read_text())
    parts = [p for p in index["parts"] if not args.levels or p["level"] in args.levels]
    jobs = []
    for truth in parts:
        folder = data / ("curated" if truth["name"].startswith("G") else truth["level"])
        jobs.append((str(folder / f"{truth['name']}.step"), truth, args.timeout))
    started = time.time()
    with Pool(args.workers) as pool:
        rows = list(pool.imap(evaluate_part, jobs, chunksize=2))
    print(f"evaluated {len(rows)} parts in {time.time() - started:.0f}s")

    csv_path = Path(args.csv) if args.csv else data / "results.csv"
    fields = sorted({k for r in rows for k in r}, key=lambda k: (k not in ("name", "level", "outcome"), k))
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    meta = {"seed": index["seed"], "per_level": index["per_level"],
            "curated": sum(r["name"].startswith("G") for r in rows)}
    report = build_report(rows, meta)
    if args.report:
        Path(args.report).write_text(report, encoding="utf-8")
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
