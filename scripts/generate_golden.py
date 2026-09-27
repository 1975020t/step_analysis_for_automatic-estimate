"""Generate the synthetic golden data set (folded STEP + unfolded STEP + truth JSON).

    python scripts/generate_golden.py --per-level 60 --seed 1 --out golden_data

Output is deterministic for a given (seed, per-level) and is git-ignored; regenerate on demand.
Also writes the hand-written curated parts (golden/curated.py) into <out>/curated.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from multiprocessing import Pool
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from golden.sampler import LEVELS, generate  # noqa: E402


def _one(job):
    level, index, seed, out = job
    try:
        part, built = generate(level, index, seed)
        return part.export(Path(out) / level, built)
    except Exception as exc:
        return {"name": f"{level}_{index:04d}", "level": level, "error": str(exc)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--per-level", type=int, default=60)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--levels", nargs="*", default=list(LEVELS))
    parser.add_argument("--out", default="golden_data")
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--no-curated", action="store_true")
    args = parser.parse_args(argv)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    jobs = [(lv, i, args.seed, str(out)) for lv in args.levels for i in range(args.per_level)]
    started = time.time()
    with Pool(args.workers) as pool:
        truths = list(pool.imap(_one, jobs, chunksize=4))
    if not args.no_curated:
        from golden.curated import build_curated
        truths += build_curated(out / "curated")
    failed = [t for t in truths if "error" in t]
    (out / "index.json").write_text(json.dumps(
        {"seed": args.seed, "per_level": args.per_level, "parts": [t for t in truths if "error" not in t],
         "generation_failures": failed}, ensure_ascii=False, indent=2))
    print(f"generated {len(truths) - len(failed)} parts ({len(failed)} failed) in {time.time() - started:.0f}s -> {out}")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
