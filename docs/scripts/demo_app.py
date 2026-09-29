"""The Streamlit demo (app.py) for the screenshots of the documents, with a recorded drawing-PDF reading.

    streamlit run docs/scripts/demo_app.py      (from the repository root)

The drawing reader returns docs/scripts/recorded_reading.json instead of calling the Claude API, so the
screen of the drawing conditions can be photographed without an API key. Everything else is the real app.
capture_screens.py copies this file into a checkout of the app (DOCS_APP_ROOT) and runs it from there, so that
the app's own .streamlit/config.toml and static/ (the UI font) are used.
"""
from __future__ import annotations

import json
import os
import runpy
import sys
from pathlib import Path

ROOT = Path(os.environ.get("DOCS_APP_ROOT") or Path(__file__).resolve().parents[2])
sys.path.insert(0, str(ROOT))

import src.services.estimate as estimate  # noqa: E402

RECORDED = json.loads(Path(os.environ.get("DOCS_RECORDED_READING") or Path(__file__).with_name("recorded_reading.json"))
                      .read_text(encoding="utf-8"))


class RecordedReader:
    def read(self, path):
        return {k: v for k, v in json.loads(json.dumps(RECORDED)).items() if not k.startswith("_")}


estimate.default_reader_factory = RecordedReader
estimate.llm_key_configured = lambda: True
runpy.run_path(str(ROOT / "app.py"), run_name="__main__")
