from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("cadquery")
ezdxf = pytest.importorskip("ezdxf")

from golden.dxf_golden import VARIANTS, build_dxf
from golden.dxf_naive_baseline import NaiveLayerDxfAnalyzer

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("variant", VARIANTS)
def test_every_variant_writes_a_readable_dxf_with_stable_truth(tmp_path, variant):
    first = build_dxf("Lv3", 1, 21, variant, tmp_path / "a")
    second = build_dxf("Lv3", 1, 21, variant, tmp_path / "b")
    assert first == second
    doc = ezdxf.readfile(str(tmp_path / "a" / f"{first['name']}.dxf"))
    assert len(doc.modelspace()) > 0
    assert first["must_not_confirm"] == (variant == "X")


@pytest.mark.parametrize("level", ["Lv0", "Lv2", "Lv4"])
def test_clean_dxf_geometry_reproduces_the_truth(tmp_path, level):
    """D0 has only named CUT/BEND layers, so a layer-trusting reader must match the truth exactly."""
    truth = build_dxf(level, 0, 21, "D0", tmp_path)
    r = NaiveLayerDxfAnalyzer(thickness_mm=truth["thickness_mm"]).analyze(tmp_path / f"{truth['name']}.dxf")
    assert r.blank_area_mm2 == pytest.approx(truth["blank_area_mm2"], rel=1e-4)
    assert r.cut_length_mm == pytest.approx(truth["cut_length_mm"], rel=1e-4)
    assert (r.hole_count, r.bend_count) == (truth["hole_count"], truth["bend_count"])


def test_generator_still_reproduces_frozen_dxf_v1(tmp_path):
    frozen = {p["name"]: p for p in json.loads((ROOT / "golden" / "datasets" / "dxf_v1.json").read_text(encoding="utf-8"))["parts"]}
    for level, index, variant in [("Lv1", 3, "D2"), ("Lv3", 7, "X"), ("Lv0", 12, "D1")]:
        truth = build_dxf(level, index, 21, variant, tmp_path)
        expected = frozen[truth["name"]]
        for key in ("blank_area_mm2", "cut_length_mm", "hole_count", "bend_count", "units", "must_not_confirm"):
            assert truth[key] == pytest.approx(expected[key]) if isinstance(expected[key], float) else truth[key] == expected[key]


class _Reject:
    def __init__(self, **_):
        pass

    def analyze(self, path):
        from src.models import SheetMetalAnalysis
        return SheetMetalAnalysis(status="unsupported", file_name=Path(path).name, reason_codes=["X"])


def test_harness_scores_confirmed_x_as_dangerous_and_rejection_as_success(tmp_path, monkeypatch):
    from scripts import evaluate_dxf

    x = build_dxf("Lv1", 0, 21, "X", tmp_path)
    d0 = build_dxf("Lv1", 0, 21, "D0", tmp_path)
    cfg = {"analyzer": "golden.dxf_naive_baseline:NaiveLayerDxfAnalyzer", "tolerance": 0.1, "timeout": 30}
    assert evaluate_dxf.evaluate_file((str(tmp_path / f"{d0['name']}.dxf"), d0, cfg))["outcome"] == "CORRECT"
    naive_x = evaluate_dxf.evaluate_file((str(tmp_path / f"{x['name']}.dxf"), x, cfg))
    assert naive_x["outcome"] in {"DANGEROUS", "REJECTED"}

    monkeypatch.setattr(evaluate_dxf, "load", lambda spec: _Reject)
    rejected = evaluate_dxf.evaluate_file((str(tmp_path / f"{x['name']}.dxf"), x, cfg))
    assert rejected["outcome"] == "REJECTED"
    assert evaluate_dxf.summarize([rejected], "variant")["X"]["success"] == 1.0
