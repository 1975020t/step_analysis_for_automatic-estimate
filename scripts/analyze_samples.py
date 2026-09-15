from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.sheetmetal_analyzer import SheetMetalAnalyzer  # noqa: E402


def main() -> None:
    analyzer = SheetMetalAnalyzer()
    sample_dir = ROOT / "samples" / "nist-source" / "NIST-PMI-STEP-Files"
    paths = sorted(sample_dir.glob("nist_stc_*.stp"))
    if not paths:
        paths = [ROOT / "samples" / "nist_stc06.step"]
    results = [analyzer.analyze(path).model_dump() for path in paths]
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
