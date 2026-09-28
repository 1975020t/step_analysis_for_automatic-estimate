"""Generate the flat-pattern DXF golden set (see golden/dxf_golden.py for the variants).

    python scripts/generate_dxf_golden.py --per-level 40 --seed 21 --out dxf_data          # development (800 files)
    python scripts/generate_dxf_golden.py --per-level 20 --seed 22 --out dxf_holdout       # final acceptance (400 files)

Every part is drawn in all four variants (D0, D1, D2, X), so results can be compared per variant on the
same shapes. Output is deterministic for a given (seed, per-level) and is git-ignored.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from multiprocessing import Pool
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from golden.dxf_golden import VARIANTS, build_dxf  # noqa: E402
from golden.sampler import LEVELS  # noqa: E402


def _one(job):
    level, index, seed, variant, out = job
    try:
        return build_dxf(level, index, seed, variant, Path(out) / variant)
    except Exception as exc:  # keep going; failures are listed in index.json
        return {"name": f"{level}_{index:04d}_{variant}", "level": level, "variant": variant, "error": repr(exc)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--per-level", type=int, default=40)
    parser.add_argument("--seed", type=int, default=21)
    parser.add_argument("--levels", nargs="*", default=list(LEVELS))
    parser.add_argument("--variants", nargs="*", default=list(VARIANTS))
    parser.add_argument("--out", default="dxf_data")
    parser.add_argument("--workers", type=int, default=2)
    args = parser.parse_args(argv)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    jobs = [(lv, i, args.seed, v, str(out)) for lv in args.levels for i in range(args.per_level) for v in args.variants]
    started = time.time()
    with Pool(args.workers) as pool:
        truths = list(pool.imap(_one, jobs, chunksize=4))
    failed = [t for t in truths if "error" in t]
    (out / "index.json").write_text(json.dumps(
        {"seed": args.seed, "per_level": args.per_level, "parts": [t for t in truths if "error" not in t],
         "generation_failures": failed}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"generated {len(truths) - len(failed)} DXF files ({len(failed)} failed) in {time.time() - started:.0f}s -> {out}")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
