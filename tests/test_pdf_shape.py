"""Shape values from a drawing PDF alone (src/pdf_shape.py, src/pdf_flat_pattern.py, src/pdf_fold.py) and their use
in the screens' API. No Claude API call: a fake client returns fixed answers, the platform uses a fake estimator."""
from __future__ import annotations

import copy
import json
import time
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from api.main import create_app
from src.claude_api import ClaudeClient
from src.models import SheetMetalAnalysis
from src.pdf_flat_pattern import measure_flat_pattern
from src.pdf_fold import build_model, check_model, fold_scale
from src.pdf_shape import (REASON_CODE, PdfShapeEstimator, PdfShapeResult, RecordedShapeEstimator, _repair,
                           _schema_problem)
from src.services.platform.core import RecordedReader
from src.services.settings import Settings

ROOT = Path(__file__).resolve().parents[1]
VECTOR_PDF = ROOT / "pdf_data" / "pdf" / "Lv0_0001.pdf"   # dimensions 205.3 and 110.6 are on its text layer
RECORDED = ROOT / "docs" / "scripts" / "recorded_reading.json"


# ---------------------------------------------------------------- folding: the scale and the part described
def test_a_part_described_as_base_and_flanges_is_unfolded_and_checked_against_the_dimensions():
    model = {"base": {"width": 200, "depth": 100}, "holes": [{"shape": "round", "size": 6, "count": 4}],
             "flanges": [{"edge": "right", "length": 40, "angle": 90, "direction": "up"},
                         {"edge": "left", "length": 40, "angle": 90, "direction": "up"}]}
    built = build_model(model, 1.6)
    flat_w = 200 + 2 * (40 - 0.8)                       # each flange is drawn to the outside of its bend
    assert built["cut_length_mm"] == pytest.approx(2 * (flat_w + 100) + 4 * np.pi * 6, abs=0.2)
    assert built["blank_area_mm2"] == pytest.approx(flat_w * 100 - 4 * np.pi * 9, abs=0.5)
    assert check_model(built, [200, 100, 40], 1.6)["ok"]          # a U channel 200 x 100 x 40
    assert not check_model(built, [200, 100, 80], 1.6)["ok"]      # the drawing says otherwise: not used
    tab = build_model({"base": {"width": 200, "depth": 100}, "holes": [], "flanges": [
        {"edge": "back", "length": 30, "angle": 90, "direction": "down", "width": 50, "offset": 20,
         "then": [{"length": 20, "angle": 90, "direction": "up"}]}]}, 1.0)   # a Z-bent tab
    assert len(tab["panels"]) == 3 and check_model(tab, [200, 100 + 19.5, 30], 1.0)["ok"]


# ---------------------------------------------------------------- Claude's answer
def answer(**over) -> dict:
    raw = {"drawing_kind": "part", "overall_dimensions": [{"value": "205.3", "text": "205.3"}, {"value": 110.6, "text": "110.6"}],
           "flat_pattern": None, "views_region": None,
           "bends": [{"description": "フランジ"}], "holes": [{"description": "φ12"}, {"description": "φ3.4"}],
           "uncertain": []}
    raw.update(over)
    return raw


class FakeClient(ClaudeClient):
    def __init__(self, raws):
        super().__init__(model="test-model", mode="live", api_key="dummy")
        self.raws = list(raws)
        self.calls = 0

    def complete_json(self, system, user, schema, tool_name="report", max_tokens=2048):
        self.calls += 1
        self.usage.calls += 1
        self.usage.input_tokens += 1000
        return copy.deepcopy(self.raws.pop(0))


def test_lists_or_the_whole_answer_returned_as_a_string_are_repaired():
    raw = answer(bends=json.dumps([{"description": "a"}, {"description": "b"}]))
    assert _schema_problem(raw) is not None
    assert len(_repair(raw)["bends"]) == 2 and _schema_problem(_repair(raw)) is None
    nested = {"bends": json.dumps(answer())}
    assert _repair(nested)["overall_dimensions"][0]["value"] == "205.3"


PLATE = {"base": {"width": 205.3, "depth": 110.6}, "flanges": [],
         "holes": [{"shape": "round", "size": 12, "count": 1}, {"shape": "round", "size": 3.4, "count": 1}]}


def test_without_a_flat_pattern_the_part_claude_describes_is_used_only_when_it_folds_into_the_dimensions():
    client = FakeClient([answer(), PLATE])
    result = PdfShapeEstimator(client=client).estimate(VECTOR_PDF, thickness_mm=2.3)
    a = result.analysis
    assert a.status == "partial" and a.reason_code == REASON_CODE
    assert (a.bend_count, a.hole_count, a.thickness_mm) == (1, 2, 2.3)
    assert result.dimensions_mm == [205.3, 110.6]
    assert a.cut_length_mm == pytest.approx(2 * (205.3 + 110.6) + np.pi * (12 + 3.4), abs=0.1)
    assert a.blank_area_mm2 == pytest.approx(205.3 * 110.6 - np.pi * (36 + 1.7 ** 2), abs=0.5)
    assert a.metric_quality["cut_length_mm"].confidence == "low"
    assert a.metric_quality["bend_count"].confidence == "low"
    assert any("画像で確かめられませんでした" in w for w in a.warnings)
    assert client.calls == 2 and result.usage["llm_input_tokens"] == 2000
    # a described part that does not have the written dimensions is not used: the person enters the lengths
    wrong = {**PLATE, "base": {"width": 150, "depth": 110.6}}
    a = PdfShapeEstimator(client=FakeClient([answer(), wrong])).estimate(VECTOR_PDF, thickness_mm=2.3).analysis
    assert a.status == "partial" and a.cut_length_mm is None and a.blank_area_mm2 is None
    assert any("外形寸法と合いませんでした" in w for w in a.warnings)
    assert any("値を入力してください" in w for w in a.warnings)


def test_a_dimension_missing_from_the_text_layer_is_flagged_and_a_broken_answer_is_retried():
    client = FakeClient([{"bends": "?"}, answer(overall_dimensions=[{"value": 205.3}, {"value": 999.9}]),
                         {**PLATE, "base": {"width": 999.9, "depth": 205.3}}])
    a = PdfShapeEstimator(client=client).estimate(VECTOR_PDF, thickness_mm=2.3).analysis
    assert client.calls == 3 and a.status == "partial"
    assert any("図面の文字にない外形寸法" in w and "999.9" in w for w in a.warnings)
    assert a.metric_quality["cut_length_mm"].confidence == "low"


def test_without_overall_dimensions_or_for_an_assembly_nothing_is_estimated():
    for raw in (answer(overall_dimensions=[{"value": 205.3}]), answer(drawing_kind="assembly")):
        a = PdfShapeEstimator(client=FakeClient([raw])).estimate(VECTOR_PDF, thickness_mm=2.3).analysis
        assert a.status == "unsupported" and a.cut_length_mm is None and a.reason_code == REASON_CODE


def test_a_flat_pattern_is_counted_on_the_image_and_its_count_wins():
    # the flat pattern of Lv0_0001 (展開図（参考）) is at the right of the page; Claude's counts are deliberately wrong
    index = json.loads((ROOT / "pdf_data" / "index.json").read_text(encoding="utf-8"))
    truth = next(r["part_truth"] for r in index["drawings"] if r["file"] == "pdf/Lv0_0001.pdf")
    from src.pdf_shape import load_shape_pages

    page = load_shape_pages(VECTOR_PDF)[0]
    w, h = page.image_size
    raw = answer(bends=[], holes=[], flat_pattern={"page": 1, "box": {"x0": 0.60 * w, "y0": 0.33 * h, "x1": 0.91 * w, "y1": 0.58 * h}})
    recount = {"bends": [], "holes": []}   # asked only when the image measurement fails
    result = PdfShapeEstimator(client=FakeClient([raw, recount])).estimate(VECTOR_PDF, thickness_mm=2.3)
    if not (result.flat or {}).get("ok"):
        raise AssertionError(f"flat pattern not measured: {result.flat}")
    a = result.analysis
    assert (a.bend_count, a.hole_count) == (truth["bend_count"], truth["hole_count"])
    assert any("画像の計測" in w for w in a.warnings)
    # the cut length and the area are measured on the flat pattern, scaled by the written dimensions
    assert result.raw["_counts"]["fold"]["px_per_mm"] > 0
    assert a.cut_length_mm == pytest.approx(truth["cut_length_mm"], rel=0.03)
    assert a.blank_area_mm2 == pytest.approx(truth["blank_area_mm2"], rel=0.03)
    assert a.metric_quality["cut_length_mm"].confidence == "medium"


# ---------------------------------------------------------------- image measurement
def flat_page(dashed=(400, 600), solid=(), holes=((300, 300),), specks=0) -> np.ndarray:
    """A 100 x 100 mm page at 10 px/mm: a 60 mm square outline, dashed / solid bend lines, round holes."""
    g = np.full((1000, 1000), 255, np.uint8)
    g[200:203, 200:800] = g[797:800, 200:800] = 0
    g[200:800, 200:203] = g[200:800, 797:800] = 0
    for x in dashed:
        for y in range(205, 795, 20):
            g[y:y + 12, x:x + 2] = 0
    for y in solid:
        g[y:y + 2, 203:797] = 0
    yy, xx = np.ogrid[:1000, :1000]
    for cx, cy in holes:
        r = (xx - cx) ** 2 + (yy - cy) ** 2
        g[(r <= 15 ** 2) & (r >= 13 ** 2)] = 0
    rng = np.random.default_rng(0)
    for _ in range(specks):
        x, y = rng.integers(0, 1000, 2)
        g[y:y + 2, x:x + 2] = 0
    return g


BOX = {"x0": 200, "y0": 200, "x1": 800, "y1": 800}


@pytest.mark.parametrize("kw, bends, holes", [
    ({}, 2, 1),
    ({"dashed": ()}, 0, 1),
    ({"solid": (700,)}, 3, 1),                       # a solid bend line splits the outline into panels
    ({"holes": ((300, 300), (700, 300), (300, 500))}, 2, 3),
    ({"dashed": (), "holes": ()}, 0, 0),            # a plain rectangle
])
def test_bend_lines_and_holes_are_counted_on_the_image(kw, bends, holes):
    r = measure_flat_pattern(flat_page(**kw), 10.0, BOX)
    assert r["ok"] and (r["bends"], r["holes"]) == (bends, holes)


def test_lengths_are_measured_and_the_fold_finds_the_scale():
    r = measure_flat_pattern(flat_page(), 10.0, BOX)   # a 60 mm square, bend lines at 20 and 40 mm, one hole
    assert r["outer_px"] == pytest.approx(4 * 598.5, rel=0.01)       # along the middle of the drawn line
    assert r["holes_px"][0] == pytest.approx(2 * np.pi * 14, rel=0.03)
    assert r["area_px"] == pytest.approx(598.5 ** 2 - np.pi * 14 ** 2, rel=0.01)
    # the drawing is at 1:2 (5 px per mm of the page's 10 px/mm): folded at 90 degrees it is a U channel
    # 120 long, 40 wide, 40 high, which are the written dimensions
    scale = fold_scale(r, [120, 40, 40], 1.0)
    assert scale["px_per_mm"] == pytest.approx(5.0, rel=0.03) and not scale["ambiguous"]
    assert [abs(a) for a in scale["angles"]] == [90, 90]
    assert fold_scale(r, [120, 40, 10], 1.0) is None or fold_scale(r, [120, 40, 10], 1.0)["angles"] != [90, 90]
    plate = measure_flat_pattern(flat_page(dashed=()), 10.0, BOX)
    assert fold_scale(plate, [30, 30], 1.0)["px_per_mm"] == pytest.approx(20.0, rel=0.02)
    assert fold_scale(plate, [30, 60], 1.0) is None           # a square cannot be 30 x 60


def test_a_noisy_page_is_not_measured():
    r = measure_flat_pattern(flat_page(specks=2000), 10.0, BOX)
    assert not r["ok"] and "ノイズ" in r["reason"]
    assert not measure_flat_pattern(flat_page(), 10.0, {"x0": 0, "y0": 0, "x1": 2, "y1": 2})["ok"]


# ---------------------------------------------------------------- the screens' API
class FakeShapeEstimator:
    calls = 0

    def estimate(self, path, thickness_mm=None):
        FakeShapeEstimator.calls += 1
        a = SheetMetalAnalysis(status="partial", file_name=Path(path).name, thickness_mm=thickness_mm, bend_count=1,
                               hole_count=2, reason_code=REASON_CODE, reason_codes=[REASON_CODE],
                               message="図面PDFから推定した概算の形状", assumptions=[
                                   "図面PDFから測った値（展開図を外形寸法で縮尺を合わせて計測。CADデータがあれば置き換える）"],
                               cut_length_mm=680.2, blank_area_mm2=22520.0)
        return PdfShapeResult(a, [205.3, 110.6])


def make_client(tmp_path, history, factory):
    settings = Settings(data_dir=ROOT / "data", storage_dir=tmp_path / "storage", history_path=history,
                        quote_log_path=tmp_path / "storage" / "quote_log.csv")
    return TestClient(create_app(settings, reader_factory=lambda: RecordedReader(RECORDED),
                                 shape_estimator_factory=factory))


def wait(client, job_id: str) -> dict:
    end = time.time() + 120
    while time.time() < end:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in ("done", "failed"):
            return job
        time.sleep(0.1)
    raise AssertionError("job did not finish")


def upload(client, path: Path) -> str:
    r = client.post("/api/files", files={"file": (path.name, path.read_bytes())})
    assert r.status_code == 201, r.text
    return r.json()["file_id"]


def register_pdf_only(client, drawing_no: str) -> int:
    item = {"drawing_no": drawing_no, "name": "アングル", "customer": "株式会社テスト", "pdf_file_id": upload(client, VECTOR_PDF),
            "thickness_mm": 2.3}
    r = client.post("/api/drawings/register", json={"items": [item]})
    assert r.status_code == 202, r.text
    assert wait(client, r.json()[0]["job_id"])["status"] == "done"
    return r.json()[0]["drawing_id"]


def test_a_drawing_with_only_a_pdf_is_priced_from_the_estimated_shape(tmp_path, _history_copy):
    with make_client(tmp_path, _history_copy, FakeShapeEstimator) as c:
        drawing_id = register_pdf_only(c, "PS-100")
        d = c.get(f"/api/drawings/{drawing_id}").json()
        rev = d["revisions"][0]
        assert rev["analysis"]["reason_code"] == REASON_CODE and rev["metrics"]["bend_count"] == 1
        assert d["max_dimension_mm"] == 205.3

        r = c.post("/api/estimates", json={"drawing_id": drawing_id, "customer": "株式会社テスト", "quantity": 10,
                                           "material": "SPCC"})
        assert wait(c, r.json()["job_id"])["status"] == "done"
        q = c.get(f"/api/estimates/{r.json()['estimate_id']}").json()
        shape = q["result"]["shape"]
        assert {shape[k]["source"] for k in ("bend_count", "hole_count", "cut_length_mm", "blank_area_mm2")} == {"drawing"}
        assert not [m for m in q["result"]["missing"] if m["field"].startswith("shape")]
        assert any(h.get("field") == "shape" and "±20%" in h["message"] for h in q["result"]["hint_items"])
        assert q["analysis"]["reason_code"] == REASON_CODE

        # the recorded reading has a process without a price: drop it, then the amount is the rules' amount
        q = c.put(f"/api/estimates/{q['id']}", json={"version": q["version"], "inputs": {"custom_processes": []}}).json()
        assert q["result"]["issuable"] and q["result"]["final_price"] > 0
        # a value the person knows overrides the estimate
        r = c.put(f"/api/estimates/{q['id']}", json={"version": q["version"], "inputs": {"shape": {"bend_count": 3}}})
        assert r.status_code == 200, r.text
        changed = r.json()["result"]
        assert (changed["shape"]["bend_count"]["value"], changed["shape"]["bend_count"]["source"]) == (3, "input")
        assert changed["shape"]["hole_count"]["source"] == "drawing"
        assert changed["final_price"] > q["result"]["final_price"]


def test_without_an_estimator_the_values_are_entered_by_hand_and_the_endpoint_says_so(tmp_path, _history_copy):
    with make_client(tmp_path, _history_copy, None) as c:      # no API key in tests: not available
        assert c.get("/api/health").json()["pdf_shape_estimator"] is False
        drawing_id = register_pdf_only(c, "PS-101")
        assert c.get(f"/api/drawings/{drawing_id}").json()["revisions"][0]["analysis"] is None
        r = c.post("/api/drawings/shape-estimates", json={"file_id": upload(c, VECTOR_PDF), "thickness_mm": 2.3})
        assert r.status_code == 503 and r.json()["error"]["code"] == "SHAPE_ESTIMATOR_UNAVAILABLE"


def test_the_shape_estimate_endpoint_runs_a_job(tmp_path, _history_copy):
    with make_client(tmp_path, _history_copy, FakeShapeEstimator) as c:
        r = c.post("/api/drawings/shape-estimates", json={"file_id": upload(c, VECTOR_PDF), "thickness_mm": 2.3})
        assert r.status_code == 202, r.text
        job = wait(c, r.json()["job_id"])
        assert job["status"] == "done" and job["result"]["reason_code"] == REASON_CODE
        assert job["result"]["file_name"] == VECTOR_PDF.name and job["result"]["thickness_mm"] == 2.3


def test_recorded_estimates_replay_without_the_api(tmp_path):
    a = FakeShapeEstimator().estimate("Lv0_0001.pdf", 2.3).analysis
    path = tmp_path / "shapes.json"
    path.write_text(json.dumps({"shapes": {"Lv0_0001": {"analysis": a.model_dump(mode="json"), "dimensions_mm": [205.3, 110.6]}}}),
                    encoding="utf-8")
    replay = RecordedShapeEstimator(path).estimate("/x/upload_Lv0_0001.pdf", thickness_mm=1.6)
    assert replay.analysis.thickness_mm == 1.6 and replay.analysis.cut_length_mm == a.cut_length_mm
    with pytest.raises(ValueError):
        RecordedShapeEstimator(path).estimate("other.pdf")
