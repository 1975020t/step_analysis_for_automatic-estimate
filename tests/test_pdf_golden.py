"""Drawing-PDF golden data: generator invariants and the evaluation harness. No network, no holdout."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest

pytest.importorskip("cadquery")
pytest.importorskip("reportlab")
pytest.importorskip("pypdfium2")

from golden.pdf_golden import KINDS, UNREG, build_pdf, kind_for  # noqa: E402
from scripts import evaluate_pdf as ev  # noqa: E402
from src.master_loader import MasterLoader  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def _codes_ok(truth, masters):
    assert truth["material"] in (None, UNREG) or truth["material"] in masters.materials
    assert truth["surface_treatment"] in (None, UNREG) or truth["surface_treatment"] in masters.surface_treatments
    for p in truth["processes"]:
        assert p["code"] == UNREG or p["code"] in masters.process_rates
        assert p["count_per_part"] >= 1
    assert set(truth["flags"]) <= {"tolerance", "appearance", "inspection"}


def test_pdf_kind_mix_is_exact_per_block_of_ten():
    for level in ("Lv0", "Lv3"):
        counts = Counter(kind_for(31, level, i) for i in range(20))
        assert counts == {"vector": 10, "scan": 4, "fax": 4, "handwritten": 2}
    assert {kind_for(5, "Lv1", i) for i in range(10)} == set(KINDS)


def test_generation_is_deterministic_and_uses_master_codes(tmp_path):
    masters = MasterLoader(ROOT / "data")
    vector = next(i for i in range(10) if kind_for(31, "Lv1", i) == "vector")
    fax = next(i for i in range(10) if kind_for(31, "Lv1", i) == "fax")
    a = build_pdf("Lv1", vector, 31, tmp_path / "a", with_step=False)
    b = build_pdf("Lv1", vector, 31, tmp_path / "b", with_step=False)
    assert a == b
    assert (tmp_path / "a" / a["file"]).read_bytes() == (tmp_path / "b" / b["file"]).read_bytes()
    _codes_ok(a["truth"], masters)
    assert a["unseen"] is False
    if a["truth"]["thickness_mm"] is not None:  # the drawing never contradicts the drawn part
        assert a["truth"]["thickness_mm"] == a["part_truth"]["thickness_mm"]

    from golden.pdf_naive_baseline import pdf_text
    text = pdf_text(tmp_path / "a" / a["file"])
    if a["truth"]["drawing_no"]:
        assert a["truth"]["drawing_no"] in text  # CAD export: the text layer is extractable
    f = build_pdf("Lv1", fax, 31, tmp_path / "c", with_step=False)
    assert f["kind"] == "fax" and pdf_text(tmp_path / "c" / f["file"]).strip() == ""  # image only


def test_frozen_development_set_is_consistent_with_the_master():
    masters = MasterLoader(ROOT / "data")
    data = json.loads((ROOT / "golden" / "datasets" / "pdf_v1.json").read_text(encoding="utf-8"))
    drawings = data["drawings"]
    assert len(drawings) == 300 and not any(d["unseen"] for d in drawings)
    assert Counter(d["kind"] for d in drawings) == {"vector": 150, "scan": 60, "fax": 60, "handwritten": 30}
    for d in drawings:
        _codes_ok(d["truth"], masters)
    written = [d["meta"]["all_price_fields_written"] for d in drawings]
    assert 0.2 < sum(written) / len(written) < 0.5  # some drawings carry every price field, most leave something out


TRUTH = {"drawing_no": "AB-24-0001", "revision": "B", "material": "SPCC", "thickness_mm": 1.2, "quantity": 50,
         "surface_treatment": None, "processes": [{"code": "TAP_M4", "count_per_part": 4}, {"code": UNREG, "count_per_part": 2}],
         "rush": True, "flags": ["inspection"]}
RECORD = {"name": "Lv0_0000", "level": "Lv0", "kind": "vector", "style": "jis_cad", "unseen": False, "truth": TRUTH,
          "meta": {"sources": {"material": "title"}}}


class _Fake:
    def __init__(self, out):
        self.out = out

    def read(self, path):
        return self.out


def _evaluate(out):
    return ev.evaluate_file(("x.pdf", RECORD, {"_reader": _Fake(out)}))


def test_harness_outcomes():
    perfect = dict(TRUTH, needs_review=[], processes=[{"code": "TAP_M4", "count_per_part": 4}, {"code": UNREG, "count_per_part": None}])
    row = _evaluate(perfect)
    assert all(row[f] == "CORRECT" for f in ev.ALL_FIELDS) and row["auto_confirmed"] and not row["dangerous"]

    row = _evaluate(dict(perfect, material="SECC"))  # wrong and silent
    assert row["material"] == "DANGEROUS" and row["dangerous"]
    row = _evaluate(dict(perfect, material="SECC", needs_review=["material"]))
    assert row["material"] == "WRONG_FLAGGED" and not row["dangerous"] and not row["auto_confirmed"]
    row = _evaluate(dict(perfect, needs_review=["surface_treatment"]))  # flag on a null value is not a review
    assert row["surface_treatment"] == "CORRECT" and row["auto_confirmed"]
    row = _evaluate(dict(perfect, surface_treatment="NONE"))  # "not written" is not "no treatment"
    assert row["surface_treatment"] == "DANGEROUS"
    row = _evaluate(dict(perfect, processes=[{"code": "TAP_M4", "count_per_part": 4}]))  # unregistered item dropped
    assert row["processes"] == "DANGEROUS"
    row = _evaluate(dict(perfect, processes=[{"code": "TAP_M4", "count_per_part": 6}, {"code": UNREG, "count_per_part": 2}]))
    assert row["processes"] == "DANGEROUS"
    row = _evaluate(dict(perfect, revision="REV. b", drawing_no="ＡＢ－２４－０００１"))
    assert row["revision"] == "CORRECT" and row["drawing_no"] == "CORRECT"


def test_harness_counts_a_crash_as_rejected():
    class Boom:
        def read(self, path):
            raise RuntimeError("no")
    row = ev.evaluate_file(("x.pdf", RECORD, {"_reader": Boom()}))
    assert all(row[f] == "REJECTED" for f in ev.ALL_FIELDS) and not row["auto_confirmed"]
    assert ev.gate([row])  # a crash never passes
