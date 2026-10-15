"""The jobs in three steps: prepare (database) -> extract (files only) -> finish (database). The extraction step
runs without the database (on AWS: a step without database rights), and every step's input and output is JSON."""
from __future__ import annotations

import json
import subprocess
import sys

from fastapi.testclient import TestClient
from sqlalchemy import select

import tests.test_platform as platform_tests
from src.db.models import DrawingRevision, Quote, QuoteEditLog
from src.services.extraction import Extractor, extract
from src.services.platform.drawings import finish_register, prepare_register
from src.services.platform.quotes import finish_estimate, finish_reanalysis, prepare_estimate, prepare_reanalysis
from src.services.storage import FileStore, LocalBlobStore

ROOT = platform_tests.ROOT


def roundtrip(value):
    return json.loads(json.dumps(value, ensure_ascii=False))


def test_the_extraction_module_does_not_load_the_database():
    code = ("import sys; import src.services.extraction as e; "
            "bad = [m for m in sys.modules if m.startswith(('src.db', 'src.services.platform', 'sqlalchemy'))]; "
            "print(bad); sys.exit(1 if bad else 0)")
    done = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True)
    assert done.returncode == 0, done.stdout + done.stderr


def test_extract_with_files_and_a_reader_only(tmp_path):
    files = FileStore(LocalBlobStore(tmp_path))
    step = files.save(platform_tests.DRAWING_STEP.read_bytes(), platform_tests.DRAWING_STEP.name)
    pdf = files.save(platform_tests.DRAWING_PDF.read_bytes(), platform_tests.DRAWING_PDF.name)
    stages = []
    ex = Extractor(files, read_pdf=lambda path: ({"material": "SPCC", "path_suffix": path.suffix}, [{"material": {"text": "SPCC"}}]),
                   progress=lambda **p: stages.append(p["stage"]))
    out = extract(ex, {"analysis": {"file_id": step["file_id"], "file_name": step["filename"], "max_dimension": True},
                       "pdf_file_id": pdf["file_id"], "pdf_text": True, "read": True})
    assert out == roundtrip(out)  # (this drawing has no text layer: a scan)
    assert out["analysis"]["status"] in ("success", "partial") and out["max_dimension_mm"] > 0
    assert out["pdf_text"] == "" and out["reading"]["reading"] == {"material": "SPCC", "path_suffix": ".pdf"}
    assert stages == ["CADデータの解析", "図面の読み取り"]

    broken = files.save(b"ISO-10303-21;\nbroken", "broken.step")
    out = extract(Extractor(files), {"analysis": {"file_id": broken["file_id"], "file_name": "broken.step"},
                                      "pdf_file_id": pdf["file_id"], "read": True})
    assert out["analysis"]["status"] == "error" and out["max_dimension_mm"] is None  # the analyzer says why
    out = extract(Extractor(files), {"analysis": {"file_id": "f_0000000000000000", "file_name": "gone.step"}})
    assert out["analysis"] == {"status": "error", "file_name": "gone.step", "message": "CADデータを解析できませんでした。"}
    out = extract(Extractor(files), {"pdf_file_id": pdf["file_id"], "read": True})
    assert out["reading"] is None and out["pdf_text"] is None  # no reader: nothing read; text not asked


def test_the_steps_passed_as_json_give_what_the_job_gave(tmp_path, _history_copy):
    """What a state machine would do: each step's output goes to the next one as JSON."""
    with TestClient(platform_tests.make_app(tmp_path / "storage", _history_copy)) as client:
        pf = client.app.state.platform
        drawing_id = platform_tests.register(client, "STEPS-1", step=platform_tests.DRAWING_STEP, pdf=platform_tests.DRAWING_PDF)
        q = platform_tests.estimate(client, drawing_id, k_factor=0.4)  # another K factor: analysed again
        with pf.session() as s:
            revision = s.scalars(select(DrawingRevision)).one()
            before = (revision.analysis, revision.max_dimension_mm, revision.pdf_text, revision.reading)
            quote = s.get(Quote, q["id"])
            quote_before = (quote.analysis, quote.result)

        payload = {"revision_id": revision.id}
        plan = roundtrip(prepare_register(pf, payload))
        plan["read"], plan["draft_reading"] = True, None  # read again, as at a first registration
        extracted = roundtrip(extract(pf.service.extractor(), plan))
        finish_register(pf, payload, plan, extracted)
        with pf.session() as s:
            again = s.get(DrawingRevision, revision.id)
            assert (again.analysis, again.max_dimension_mm, again.pdf_text, again.reading) == before

        payload = {"quote_id": q["id"]}
        plan = roundtrip(prepare_estimate(pf, payload))
        assert plan["analysis"]["k_factor"] == 0.4 and plan["read"] is False
        extracted = roundtrip(extract(pf.service.extractor(), plan))
        finish_estimate(pf, payload, plan, extracted, lambda i, **found: None, {})
        with pf.session() as s:
            quote = s.get(Quote, q["id"])
            assert (quote.analysis, quote.result) == quote_before


def test_changed_conditions_are_analysed_in_a_job_not_in_the_api(tmp_path, _history_copy):
    """A new K factor needs a new analysis: the API saves the conditions and answers at once (the amount waits for
    the analysis), the analysis runs as a job in the same three steps, and its amount goes to the edit history."""
    with TestClient(platform_tests.make_app(tmp_path / "storage", _history_copy)) as client:
        pf, queue = client.app.state.platform, client.app.state.queue
        q = platform_tests.estimate(client, platform_tests.register(client, "REAN-1", step=platform_tests.DRAWING_STEP),
                                    material="SPCC")
        started: list[str] = []
        queue.start = started.append  # hold the jobs: the API must not analyse by itself
        r = client.put(f"/api/estimates/{q['id']}", json={"version": q["version"], "inputs": {"k_factor": 0.4}})
        assert r.status_code == 200
        pending = r.json()
        assert pending["job"]["status"] == "queued" and started == [pending["job"]["job_id"]]
        assert not pending["result"]["issuable"] and "analysis" in [m["field"] for m in pending["result"]["missing"]]
        with pf.session() as s:
            assert s.get(Quote, q["id"]).analysis is None
            assert s.scalars(select(QuoteEditLog)).one().total_after is None

        # the steps as a state machine would run them, passed as JSON
        payload = roundtrip(pf.job(pending["job"]["job_id"])["input"])
        plan = roundtrip(prepare_reanalysis(pf, payload))
        assert plan["analysis"]["k_factor"] == 0.4 and plan["read"] is False
        extracted = roundtrip(extract(pf.service.extractor(), plan))
        subtotal = finish_reanalysis(pf, payload, plan, extracted)["subtotal"]
        queue.execute(pending["job"]["job_id"])  # the same job run by the queue gives the same
        assert pf.job(pending["job"]["job_id"])["result"]["subtotal"] == subtotal
        done = client.get(f"/api/estimates/{q['id']}").json()
        area = lambda d: d["analysis"]["blank_area_mm2"]  # noqa: E731 - the K factor changes the unfolded area
        assert done["result"]["issuable"] and area(done) != area(q)
        log = done["edit_log"][0]
        assert log["total_after"] == done["result"]["price"]["subtotal"] and "Kファクター" in log["summary"]

        # changed again before the job ran: the older job leaves the quote to the newer one
        r = client.put(f"/api/estimates/{q['id']}", json={"version": done["version"], "inputs": {"k_factor": 0.45}})
        old = roundtrip(pf.job(r.json()["job"]["job_id"])["input"])
        old_plan = roundtrip(prepare_reanalysis(pf, old))
        q2 = client.get(f"/api/estimates/{q['id']}").json()
        r = client.put(f"/api/estimates/{q['id']}", json={"version": q2["version"], "inputs": {"k_factor": 0.5}})
        assert finish_reanalysis(pf, old, old_plan, extract(pf.service.extractor(), old_plan)) == {
            "quote_id": q["id"], "superseded": True}
        newest = r.json()["job"]["job_id"]
        queue.execute(newest)
        assert pf.job(newest)["status"] == "done"
        latest = client.get(f"/api/estimates/{q['id']}").json()
        assert latest["job"]["status"] == "done" and area(latest) not in (area(q), area(done))
