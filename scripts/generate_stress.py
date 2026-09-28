"""Generate the stress set (golden/stress.py): parts built differently from golden_v1.

    python scripts/generate_stress.py --per-level 40 --seed 21 --out stress_data
    python scripts/evaluate_golden.py --data stress_data --tolerance 0.10 --gate \
        --gate-levels A_Lv0 A_Lv1 A_Lv2 A_Lv3 A_Lv4 C1_slanted C2_angles C4_short C5_side C6_bigflange C7_many \
        --report analysis/stress_eval.md

Output layout matches generate_golden.py (<out>/<level>/<name>.step + _unfolded.step + _truth.json,
<out>/index.json), so the evaluation harness runs on it unchanged.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from multiprocessing import Pool
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from golden.stress import LEVELS, build  # noqa: E402


def _one(job):
    level, index, seed, out = job
    try:
        import cadquery as cq

        _, (folded, flat, truth) = build(level, index, seed)
        folder = Path(out) / level
        folder.mkdir(parents=True, exist_ok=True)
        truth = {**truth, "name": f"{level}_{index:04d}", "level": level}
        cq.exporters.export(folded, str(folder / f"{truth['name']}.step"))
        cq.exporters.export(flat, str(folder / f"{truth['name']}_unfolded.step"))
        (folder / f"{truth['name']}_truth.json").write_text(json.dumps(truth, ensure_ascii=False, indent=2))
        return truth
    except Exception as exc:
        return {"name": f"{level}_{index:04d}", "level": level, "error": str(exc)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--per-level", type=int, default=40)
    parser.add_argument("--seed", type=int, default=21)
    parser.add_argument("--levels", nargs="*", default=list(LEVELS))
    parser.add_argument("--out", default="stress_data")
    parser.add_argument("--workers", type=int, default=2)
    args = parser.parse_args(argv)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    jobs = [(lv, i, args.seed, str(out)) for lv in args.levels for i in range(args.per_level)]
    started = time.time()
    with Pool(args.workers) as pool:
        truths = list(pool.imap(_one, jobs, chunksize=2))
    failed = [t for t in truths if "error" in t]
    (out / "index.json").write_text(json.dumps(
        {"seed": args.seed, "per_level": args.per_level, "dataset": "stress_v1", "truth_version": "v2",
         "parts": [t for t in truths if "error" not in t], "generation_failures": failed},
        ensure_ascii=False, indent=2))
    print(f"generated {len(truths) - len(failed)} parts ({len(failed)} failed) in {time.time() - started:.0f}s -> {out}")
    for f in failed[:10]:
        print("  failed:", f["name"], f["error"][:200])
    from golden.file_normalize import normalize_tree  # byte-identical files on every run (kept in Git)
    normalize_tree(out)
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main(argv=None))
