"""Does the similar-quote reference bring the price closer to what was actually quoted? (no LLM)

    python scripts/evaluate_similar_quotes.py --report analysis/similar_quotes_eval.md

For every seed quote whose standard unit price can be computed with today's master (CAD values present, all
conditions in the master), only the quotes dated before it are searched. The reference unit price (the past
pricing level applied to today's standard, see src/similar_quotes.py) is compared with the unit price that was
actually quoted, and so is the automatic quote alone (the standard unit price). A quote with no usable
reference falls back to the standard. The 出典 column is not read (the importer drops it).
Also measures the search time on the seed and on a history enlarged to 10,000 quotes.
"""
from __future__ import annotations

import argparse
import statistics
import sys
import time
from dataclasses import replace
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.master_loader import UNREGISTERED, MasterLoader  # noqa: E402
from src.past_quotes import PastQuote, import_csv  # noqa: E402
from src.similar_quotes import Query, SimilarQuoteSearch, norm_customer, norm_drawing  # noqa: E402

SEED = ROOT / "data" / "past_quotes" / "past_quotes_seed.csv"


def query_of(quote: PastQuote, standard: float | None, unit_price: int | None = None) -> Query:
    """The quote as if it were being made now: its conditions and CAD values, searched before its date."""
    return Query(
        customer=quote.customer, drawing_no=quote.drawing_no, revision=quote.revision,
        material_code=quote.material_code if quote.material_code != UNREGISTERED else "",
        material_family=quote.material_family, thickness=quote.thickness, quantity=quote.quantity,
        finish_code=quote.finish_code or "NONE",
        processes={p["code"]: p["count"] or 0 for p in quote.processes if p["code"] != UNREGISTERED},
        rush=quote.rush, area=quote.area, holes=quote.holes, bends=quote.bends,
        unit_price=unit_price, standard_unit=standard, date=quote.date, exclude=quote.quote_no)


def enlarge(quotes: list[PastQuote], total: int) -> list[PastQuote]:
    """Copies of the history under other (fictional) customers, up to `total` quotes."""
    out = list(quotes)
    k = 1
    while len(out) < total:
        for q in quotes:
            if len(out) >= total:
                break
            out.append(replace(q, quote_no=f"{q.quote_no}-x{k}", customer=f"{q.customer}{k}号"))
        k += 1
    return out


def ape(pred: float, actual: float) -> float:
    return abs(pred - actual) / actual


def summary(errors: list[float]) -> dict[str, float]:
    return {"n": len(errors), "mape": statistics.fmean(errors), "median": statistics.median(errors),
            "within10": sum(e <= 0.10 for e in errors) / len(errors), "within5": sum(e <= 0.05 for e in errors) / len(errors)}


def reference_errors(quotes: list[PastQuote], masters: MasterLoader):
    """Per quote with a computable standard: the actual unit price, the standard and the reference from the
    quotes dated before it. Returns (rows, {group: (auto summary, similar summary, quotes with a reference)})."""
    search = SimilarQuoteSearch(quotes, masters)
    earlier_repeat = {}
    seen: dict[tuple[str, str], list[date]] = {}
    for q in sorted(quotes, key=lambda q: q.date):
        key = (norm_customer(q.customer), norm_drawing(q.drawing_no))
        earlier_repeat[q.quote_no] = any(d < q.date for d in seen.get(key, []))
        seen.setdefault(key, []).append(q.date)

    rows = []
    for i, q in enumerate(quotes):
        standard, _ = search.standard_unit(i)
        if not standard or not q.unit_price:
            continue
        matches = search.search(query_of(q, standard))
        ref = search.reference(matches)
        rows.append({"repeat": earlier_repeat[q.quote_no], "actual": q.unit_price, "standard": standard,
                     "reference": ref.unit if ref else None, "basis": ref.basis.split(" ")[0] if ref else "なし"})
    groups = {"全体": rows, "リピート（前回の見積がある）": [r for r in rows if r["repeat"]],
              "リピート以外": [r for r in rows if not r["repeat"]]}
    result = {}
    for name, group in groups.items():
        auto = summary([ape(r["standard"], r["actual"]) for r in group])
        similar = summary([ape(r["reference"] or r["standard"], r["actual"]) for r in group])
        result[name] = (auto, similar, sum(r["reference"] is not None for r in group))
    return rows, result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data", default=str(SEED))
    parser.add_argument("--report", default=None)
    args = parser.parse_args()

    masters = MasterLoader(ROOT / "data")
    quotes, problems = import_csv(args.data, masters)
    rows, result = reference_errors(quotes, masters)

    # speed
    timings = {}
    for label, history in (("種データ", quotes), ("1万件に増やした履歴", enlarge(quotes, 10_000))):
        s = SimilarQuoteSearch(history, masters)
        sample = [q for q in quotes if q.has_shape][:200]
        times = []
        for q in sample:
            t0 = time.perf_counter()
            s.search(replace(query_of(q, None), date=None, exclude=""))  # worst case: the whole history
            times.append(time.perf_counter() - t0)
        timings[label] = (len(history), statistics.fmean(times), max(times))

    lines = ["# 類似見積：参考価格の効果", "",
             f"- データ: `{Path(args.data).relative_to(ROOT) if Path(args.data).is_relative_to(ROOT) else args.data}`"
             f"（{len(quotes):,}件取り込み、取り込めなかった行 {len(problems)}件）",
             f"- 対象: 今のマスターで標準単価を計算できる見積 {len(rows):,}件（形状の数値があり、材質・表面処理・追加加工がすべてマスターにあるもの）",
             "- 方法: 各見積について、それより前の日付の見積だけを過去として類似見積を検索し、参考価格（リピートがあれば最新のリピート、"
             "なければ同じ顧客の近い部品、それもなければ他の顧客の近い部品の「出し値÷その条件の標準単価」を今回の標準単価に掛けた値）を、"
             "実際に出した単価と比べる。参考にできる見積がないときは標準単価のまま。比較対象は自動見積（標準単価）だけの場合",
             "- 類似の条件（`src/similar_quotes.is_similar`）: 材料が同じ・曲げ数の差1以内・穴数の差2以内、差の小さい順の10件"
             "（2026-10-05 に、図面一覧と見積結果で同じ条件にするため、材質の系統と板厚で探す方式から変更。変更前は全体7.9%・リピート4.9%）",
             "- 誤差は |推定単価 − 実際の単価| ÷ 実際の単価。`出典` 列は使っていない（取り込み時に捨てている）", "",
             "## 単価の誤差", "",
             "| 対象 | 件数 | 参考価格あり | 方式 | 平均誤差 | 中央値 | ±5%以内 | ±10%以内 |",
             "|---|---:|---:|---|---:|---:|---:|---:|"]
    for name, (auto, similar, with_ref) in result.items():
        for label, s in (("自動見積（標準単価）だけ", auto), ("類似見積を使う", similar)):
            lines.append(f"| {name} | {s['n']:,} | {with_ref:,} | {label} | {s['mape']:.1%} | {s['median']:.1%} | "
                         f"{s['within5']:.1%} | {s['within10']:.1%} |")
    better = all(result[k][1]["mape"] < result[k][0]["mape"] for k in ("全体", "リピート（前回の見積がある）"))
    lines += ["", f"判定：全体とリピートのそれぞれで、類似見積を使うほうが平均誤差が小さい → **{'満たす' if better else '満たさない'}**", "",
              "## 参考価格の根拠の内訳", "", "| 根拠 | 件数 |", "|---|---:|"]
    for basis in ("リピート", "同じ顧客の近い部品", "他の顧客の近い部品", "なし"):
        n = sum(r["basis"].startswith(basis.split("の")[0]) if basis != "なし" else r["basis"] == "なし" for r in rows)
        lines.append(f"| {basis} | {n:,} |")
    lines += ["", "## 検索の速さ", "", "| 履歴 | 件数 | 平均 | 最大 |", "|---|---:|---:|---:|"]
    for label, (n, mean, worst) in timings.items():
        lines.append(f"| {label} | {n:,} | {mean * 1000:.1f} ms | {worst * 1000:.1f} ms |")
    lines += ["", "1万件の履歴は、種データを架空の別顧客名で複製して作った（検索件数の目安）。1件の検索は日付で絞らず履歴全体を対象にした（最も遅い場合）。最大5件を返すまで（説明文と標準単価の再計算を含む）。", "",
              "## 再現", "", "```", "python scripts/evaluate_similar_quotes.py --report analysis/similar_quotes_eval.md", "```", ""]
    text = "\n".join(lines)
    print(text)
    if args.report:
        Path(args.report).write_text(text, encoding="utf-8")
    return 0 if better and all(worst < 1.0 for _, _, worst in timings.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
