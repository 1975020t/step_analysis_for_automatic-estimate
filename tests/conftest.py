from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


import shutil

import pytest

HISTORY = ROOT / "data" / "past_quotes" / "history.csv"


@pytest.fixture(autouse=True)
def _history_copy(tmp_path, monkeypatch):
    """Every test works on a temporary copy of the committed quote history (and a temporary output log)."""
    copy = tmp_path / "history.csv"
    if HISTORY.exists():
        shutil.copyfile(HISTORY, copy)
    monkeypatch.setenv("PAST_QUOTES_PATH", str(copy))
    monkeypatch.setenv("QUOTE_LOG_PATH", str(tmp_path / "quote_log.csv"))
    return copy
