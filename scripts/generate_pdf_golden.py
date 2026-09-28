"""Generate the drawing-PDF golden set (see golden/pdf_golden.py and analysis/pdf_golden_design.md).

    python scripts/generate_pdf_golden.py --per-level 60 --seed 31 --out pdf_data                          # development (300)
    python scripts/generate_pdf_golden.py --per-level 40 --seed 32 --unseen-ratio 0.2 --out pdf_holdout    # final acceptance (200)

Writes <out>/pdf/<name>.pdf, <out>/step/<name>.step (the part drawn) and <out>/index.json (truth).
Output is deterministic for a given (seed, per-level, unseen-ratio) and is git-ignored.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from multiprocessing import Pool
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from golden.pdf_golden import build_pdf  # noqa: E402
from golden.sampler import LEVELS  # noqa: E402


def _one(job):
    level, index, seed, out, unseen, with_step = job
    try:
        return build_pdf(level, index, seed, Path(out), unseen_ratio=unseen, with_step=with_step)
    except Exception as exc:  # keep going; failures are listed in index.json
        return {"name": f"{level}_{index:04d}", "level": level, "error": repr(exc)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--per-level", type=int, default=60)
    parser.add_argument("--seed", type=int, default=31)
    parser.add_argument("--levels", nargs="*", default=list(LEVELS))
    parser.add_argument("--unseen-ratio", type=float, default=0.0,
                        help="share of drawings in holdout-only layouts/wording (0 for development, 0.2 for the holdout)")
    parser.add_argument("--no-step", action="store_true", help="do not export the STEP of each drawn part")
    parser.add_argument("--out", default="pdf_data")
    parser.add_argument("--workers", type=int, default=2)
    args = parser.parse_args(argv)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    jobs = [(lv, i, args.seed, str(out), args.unseen_ratio, not args.no_step) for lv in args.levels for i in range(args.per_level)]
    started = time.time()
    with Pool(args.workers) as pool:
        records = list(pool.imap(_one, jobs, chunksize=2))
    failed = [r for r in records if "error" in r]
    (out / "index.json").write_text(json.dumps(
        {"seed": args.seed, "per_level": args.per_level, "unseen_ratio": args.unseen_ratio,
         "drawings": [r for r in records if "error" not in r], "generation_failures": failed},
        ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"generated {len(records) - len(failed)} drawing PDFs ({len(failed)} failed) in {time.time() - started:.0f}s -> {out}")
    for f in failed[:10]:
        print("  FAILED", f["name"], f["error"])
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
