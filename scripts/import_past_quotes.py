"""Import past quotes from a CSV (e.g. exported from Excel) into the quote history (no LLM).

    python scripts/import_past_quotes.py data/past_quotes/past_quotes_seed.csv
    python scripts/import_past_quotes.py old.csv --map customer=得意先コード名 --map unit_price=見積単価

Columns are matched by name (src/past_quotes.py COLUMN_ALIASES); --map field=列名 overrides one. Missing
columns are left blank. Quotes whose number is already in the history are skipped, so importing twice is
harmless. The history is data/past_quotes/history.csv (commit it).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.master_loader import MasterLoader  # noqa: E402
from src.past_quotes import COLUMN_ALIASES, HistoryStore, import_csv  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("csv")
    parser.add_argument("--map", action="append", default=[], metavar="FIELD=COLUMN",
                        help="項目と列名の対応（項目: " + ", ".join(COLUMN_ALIASES) + "）")
    parser.add_argument("--history", default=str(ROOT / "data" / "past_quotes" / "history.csv"))
    args = parser.parse_args()
    mapping = dict(item.split("=", 1) for item in args.map)
    quotes, problems = import_csv(args.csv, MasterLoader(ROOT / "data"), mapping)
    added, skipped = HistoryStore(args.history).append(quotes)
    for problem in problems:
        print(problem)
    print(f"{len(quotes)}件を読み込み、{added}件を履歴に追加（既にある見積番号 {skipped}件は飛ばした）: {args.history}")
    unmapped = sum(1 for q in quotes if not q.material_code)
    print(f"材質が系統だけ・不明: {unmapped}件、マスター未登録の材質: {sum(q.material_code == 'UNREGISTERED' for q in quotes)}件、"
          f"マスター未登録の表面処理: {sum(q.finish_code == 'UNREGISTERED' for q in quotes)}件、"
          f"マスター未登録の加工を含む: {sum(any(p['code'] == 'UNREGISTERED' for p in q.processes) for q in quotes)}件")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
