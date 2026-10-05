"""The API end to end with FastAPI's TestClient: every function, the job queue (fast acceptance, restart),
the limits and errors. Small STEP / DXF / drawing PDF from the development data; the drawing reader is a fake
(no LLM call)."""
from __future__ import annotations

import json
import re
import struct
import time
from pathlib import Path

import pypdfium2 as pdfium
import pytest
from fastapi.testclient import TestClient

from api.main import create_app
from src.services.estimate import EstimateService
from src.services.settings import Settings

ROOT = Path(__file__).resolve().parents[1]
STEP = ROOT / "pdf_data" / "step" / "Lv0_0002.step"
DXF = ROOT / "dxf_data" / "D0" / "Lv4_0024_D0.dxf"
PDF = ROOT / "pdf_data" / "pdf" / "Lv4_0042.pdf"

READING = {  # what the reader would return for PDF (fake: no LLM call)
    "drawing_no": "KS848-3189", "revision": "2", "material": "SPHC", "thickness_mm": 1.6, "quantity": None,
    "surface_treatment": None, "processes": [{"code": "COUNTERSINK", "count_per_part": 2}, {"code": "TAP_M4", "count_per_part": 4},
                                             {"code": "UNREGISTERED", "count_per_part": None}],
    "rush": True, "flags": ["tolerance", "inspection"], "needs_review": ["rush"],
    "review_reasons": {"rush": ["2回の読み取りが一致しない"]},
    "evidence": [{"processes": [{"text": "バーリングタップ M4 2ヶ所", "code": "UNREGISTERED"}]}],
}


class FakeReader:
    calls: list[str] = []

    def read(self, path):
        FakeReader.calls.append(str(path))
        return json.loads(json.dumps(READING))


def settings_for(tmp_path, **changes) -> Settings:
    base = Settings.from_env()  # conftest points history, log and storage at temporary copies
    values = dict(data_dir=ROOT / "data", storage_dir=base.storage_dir, history_path=base.history_path,
                  quote_log_path=base.quote_log_path, api_token="", max_upload_bytes=base.max_upload_bytes, job_workers=2)
    values.update(changes)
    return Settings(**values)


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(settings_for(tmp_path), reader_factory=FakeReader)) as c:
        yield c


def upload(client, path: Path, name: str | None = None) -> dict:
    response = client.post("/api/files", files={"file": (name or path.name, path.read_bytes())})
    assert response.status_code == 201, response.text
    return response.json()


def wait(client, job_id: str, timeout: float = 120) -> dict:
    end = time.time() + timeout
    while time.time() < end:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in ("done", "failed"):
            return job
        time.sleep(0.1)
    raise AssertionError("job did not finish")


def analyse(client, path=STEP, **body) -> dict:
    file = upload(client, path)
    job = client.post("/api/analyses", json={"file_id": file["file_id"], **body})
    assert job.status_code == 202, job.text
    done = wait(client, job.json()["job_id"])
    assert done["status"] == "done", done
    return {"file": file, "job": done}


CONDITION = {"material": "SPCC", "quantity": 100, "surface_treatment": "ZINC_CLEAR",
             "additional_processes": [{"process_code": "TAP_M4", "quantity": 4}]}


# ---------------------------------------------------------------- masters, docs
def test_health_masters_and_openapi(client):
    assert client.get("/api/health").json()["status"] == "ok"
    masters = client.get("/api/masters").json()
    assert {"materials", "processes", "surface_treatments", "pricing_policy", "company"} <= set(masters)
    assert any(m["material"] == "SPCC" for m in masters["materials"])
    assert masters["pricing_policy"]["tax_rate"] == "0.10" and "（仮）" in masters["company"]["name"]
    spec = client.get("/openapi.json").json()
    paths = set(spec["paths"])
    for path in ("/api/files", "/api/analyses", "/api/jobs/{job_id}", "/api/drawings/readings", "/api/quotes",
                 "/api/documents", "/api/documents/{quote_no}/{kind}.pdf", "/api/similar-quotes", "/api/history",
                 "/api/history/import", "/api/history/{quote_no}", "/api/history/{quote_no}/outcome",
                 "/api/files/{file_id}/model.glb", "/api/masters"):
        assert path in paths, path
    assert "QuoteResponse" in spec["components"]["schemas"] and "SheetMetalAnalysis" in spec["components"]["schemas"]
    assert client.get("/docs").status_code == 200


# ---------------------------------------------------------------- analysis
def test_step_analysis_quote_document_and_similar_quotes(client):
    done = analyse(client, k_factor_confirmed=True)
    result = done["job"]["result"]
    assert result["status"] == "success" and result["file_name"] == STEP.name and result["blank_area_mm2"] > 0
    local = EstimateService.analyze_bytes(STEP.read_bytes(), STEP.name, k_factor_confirmed=True)
    assert result["blank_area_mm2"] == pytest.approx(local.blank_area_mm2)  # the job does the same analysis

    job_id = done["job"]["job_id"]
    quote = client.post("/api/quotes", json={"analysis_job_id": job_id, "condition": CONDITION}).json()
    price = quote["price"]
    assert price["amount"] == price["unit_price"] * 100 and price["total"] == price["subtotal"] + price["tax"]
    assert price["tax"] == int(price["subtotal"] * 0.10) and not quote["is_estimate"]
    assert quote["quote"]["final_price"] <= price["subtotal"] < quote["quote"]["final_price"] + 100

    doc = client.post("/api/documents", json={"analysis_job_id": job_id, "condition": CONDITION,
                                              "recipient": {"company": "株式会社テスト"},
                                              "part": {"name": "ブラケット", "drawing_no": "API-001", "revision": "A"},
                                              "include_internal": True})
    assert doc.status_code == 201, doc.text
    doc = doc.json()
    assert re.fullmatch(r"Q\d{8}-\d{3}", doc["quote_no"]) and doc["title"] == "御見積書" and doc["total"] == price["total"]
    assert [f["kind"] for f in doc["files"]] == ["quote", "internal"]
    pdf = client.get(doc["files"][0]["url"])
    assert pdf.status_code == 200 and pdf.headers["content-type"] == "application/pdf"
    text = re.sub(r"\s+", "", pdfium.PdfDocument(pdf.content)[0].get_textpage().get_text_range())
    assert f"¥{price['total']:,}" in text and f"{price['unit_price']:,}" in text and "株式会社テスト御中" in text
    internal = client.get(doc["files"][1]["url"])
    assert internal.status_code == 200 and "社外秘" in pdfium.PdfDocument(internal.content)[0].get_textpage().get_text_range()

    again = client.post("/api/documents", json={"analysis_job_id": job_id, "condition": CONDITION,
                                                "recipient": {"company": "株式会社テスト"},
                                                "part": {"name": "ブラケット", "drawing_no": "API-001", "revision": "B"}}).json()
    assert again["quote_no"] != doc["quote_no"]  # numbered without duplicates

    similar = client.post("/api/similar-quotes", json={"analysis_job_id": job_id, "condition": CONDITION,
                                                       "customer": "株式会社テスト", "drawing_no": "API-001",
                                                       "revision": "C"}).json()
    assert similar["price"] == price and 1 <= len(similar["matches"]) <= 10
    first = similar["matches"][0]
    assert first["category"] == "リピート" and first["quote"]["quote_no"] in (doc["quote_no"], again["quote_no"])
    assert first["reasons"] and first["differences"] and first["leveled_unit"] == pytest.approx(price["unit_price"], rel=1e-3)


def test_dxf_analysis_needs_a_thickness(client):
    file = upload(client, DXF)
    missing = client.post("/api/analyses", json={"file_id": file["file_id"]})
    assert missing.status_code == 400 and missing.json()["error"]["code"] == "THICKNESS_REQUIRED"
    job = client.post("/api/analyses", json={"file_id": file["file_id"], "thickness_mm": 2.0}).json()
    done = wait(client, job["job_id"])
    assert done["status"] == "done" and done["result"]["thickness_mm"] == 2.0 and done["result"]["hole_count"] is not None


def test_acceptance_returns_at_once_even_for_a_slow_analysis(tmp_path, monkeypatch):
    original = EstimateService.analyze_bytes

    def slow_analysis(data, file_name, *args, **kwargs):
        time.sleep(3)
        return original(data, file_name, *args, **kwargs)

    monkeypatch.setattr(EstimateService, "analyze_bytes", staticmethod(slow_analysis))
    with TestClient(create_app(settings_for(tmp_path), reader_factory=FakeReader)) as client:
        file = upload(client, STEP)
        start = time.perf_counter()
        response = client.post("/api/analyses", json={"file_id": file["file_id"]})
        elapsed = time.perf_counter() - start
        assert response.status_code == 202 and elapsed < 0.5, elapsed
        assert client.get(f"/api/jobs/{response.json()['job_id']}").json()["status"] in ("queued", "running")
        assert wait(client, response.json()["job_id"])["status"] == "done"


def test_jobs_survive_a_restart(tmp_path):
    settings = settings_for(tmp_path)
    with TestClient(create_app(settings, reader_factory=FakeReader, start_jobs=False)) as stopped:
        file = upload(stopped, STEP)
        pending = stopped.post("/api/analyses", json={"file_id": file["file_id"]}).json()
        time.sleep(0.3)
        assert stopped.get(f"/api/jobs/{pending['job_id']}").json()["status"] == "queued"  # the server "stops" here
    with TestClient(create_app(settings, reader_factory=FakeReader)) as restarted:
        done = wait(restarted, pending["job_id"])  # picked up again after the restart
        assert done["status"] == "done" and done["result"]["file_name"] == STEP.name
    with TestClient(create_app(settings, reader_factory=FakeReader, start_jobs=False)) as again:
        kept = again.get(f"/api/jobs/{pending['job_id']}").json()
        assert kept["status"] == "done" and kept["result"] == done["result"]  # finished results stay readable


def test_failed_job_reports_a_safe_message(client):
    file = upload(client, STEP, name="broken.step")
    Path(client.app.state.settings.uploads_dir / file["file_id"] / "broken.step").write_bytes(b"ISO-10303-21;\nbroken")
    job = client.post("/api/analyses", json={"file_id": file["file_id"]}).json()
    done = wait(client, job["job_id"])
    assert done["status"] in ("done", "failed")
    text = json.dumps(done, ensure_ascii=False)
    assert str(client.app.state.settings.storage_dir) not in text and "Traceback" not in text


def test_api_screen_and_document_show_the_same_amounts(client):
    from streamlit.testing.v1 import AppTest

    analysis = EstimateService.analyze_bytes(STEP.read_bytes(), STEP.name, k_factor_confirmed=True)
    condition = {"material": "SPCC", "quantity": 100, "surface_treatment": "ZINC_CLEAR"}
    api = client.post("/api/quotes", json={"analysis": analysis.model_dump(mode="json"), "condition": condition}).json()["price"]

    app = AppTest.from_file(str(ROOT / "app.py"))
    app.session_state["analysis_result"] = analysis
    app.run(timeout=30)
    app.selectbox[0].select("SPCC").run(timeout=30)
    next(n for n in app.number_input if n.label == "数量").set_value(100).run(timeout=30)
    next(s for s in app.selectbox if s.label == "表面処理").select("ZINC_CLEAR").run(timeout=30)
    metrics = {m.label: m.value for m in app.metric}
    assert metrics["単価（1個）"] == f"¥{api['unit_price']:,}" and metrics["小計（税抜、100個）"] == f"¥{api['subtotal']:,}"
    assert metrics["消費税（10%）"] == f"¥{api['tax']:,}" and metrics["見積金額（税込）"] == f"¥{api['total']:,}"

    next(b for b in app.button if b.label.startswith("見積書を作成する")).click().run(timeout=30)  # the quotation page
    app.text_input(key="doc_customer").input("株式会社テスト").run(timeout=30)
    next(b for b in app.button if b.label == "見積書PDFを作成").click().run(timeout=30)
    assert not app.exception
    documents = sorted((client.app.state.settings.documents_dir).glob("*/quote.pdf"))
    assert len(documents) == 1  # the screen issued it through the same service (same storage)
    text = re.sub(r"\s+", "", pdfium.PdfDocument(documents[0].read_bytes())[0].get_textpage().get_text_range())
    for value in (f"¥{api['total']:,}", f"{api['unit_price']:,}", f"{api['subtotal']:,}", f"{api['tax']:,}"):
        assert value in text


# ---------------------------------------------------------------- drawing PDF
def test_drawing_reading_with_a_fake_reader_feeds_the_quote_and_the_document(client):
    FakeReader.calls.clear()
    pdf = upload(client, PDF)
    job = client.post("/api/drawings/readings", json={"file_id": pdf["file_id"]})
    assert job.status_code == 202
    done = wait(client, job.json()["job_id"])
    assert done["status"] == "done" and len(FakeReader.calls) == 1
    drawing = done["result"]["drawing"]
    assert "evidence" not in done["result"]["reading"] and drawing["drawing_no"] == "KS848-3189"
    assert drawing["unregistered_texts"] == ["バーリングタップ M4 2ヶ所"] and drawing["flags"] == ["tolerance", "inspection"]
    statuses = {i["field"]: i["status"] for i in drawing["items"]}
    assert statuses == {"material": "確定", "thickness_mm": "確定", "quantity": "記載なし", "surface_treatment": "記載なし",
                        "processes": "未登録", "rush": "要確認"}

    shape = analyse(client, DXF, thickness_mm=1.6)["job"]["job_id"]
    body = {"analysis_job_id": shape, "condition": {"material": "SPCC"}, "drawing": drawing}
    quote = client.post("/api/quotes", json=body).json()
    assert quote["is_estimate"] and quote["condition"]["material"] == "SPHC" and not quote["condition"]["rush"]
    reasons = " ".join(quote["estimate_reasons"])
    assert "数量：図面に記載なし" in reasons and "バーリングタップ M4 2ヶ所" in reasons and "特急" in reasons
    # the user confirms the rush and enters the quantity on the screen: those items become 確定
    for item in drawing["items"]:
        if item["field"] == "rush":
            item["status"] = "確定"
        if item["field"] == "quantity":
            item.update(value=50, status="確定")
    confirmed = client.post("/api/quotes", json=body).json()
    assert confirmed["condition"]["rush"] and confirmed["condition"]["quantity"] == 50
    assert any(line["code"] == "RUSH" for line in confirmed["quote"]["lines"])

    doc = client.post("/api/documents", json={**body, "recipient": {"company": "サンプル電機株式会社"}}).json()
    assert doc["title"] == "御見積書" and "is_estimate" not in doc  # issuing settles the conditions: never 概算
    text = re.sub(r"\s+", "", pdfium.PdfDocument(client.get(doc["files"][0]["url"]).content)[0].get_textpage().get_text_range())
    assert "概算" not in text and "検査成績書" in text
    assert "追加加工「バーリングタップM42ヶ所」はマスター未登録のため、本見積に含めず別途見積とします。" in text
    assert f"¥{confirmed['price']['total']:,}" in text

    # a quotation settles every item as it is: the rush 要確認 is quoted; a quantity must be known
    as_read = client.get(f"/api/jobs/{job.json()['job_id']}").json()["result"]["drawing"]
    unsettled = {"analysis_job_id": shape, "condition": {"material": "SPCC"}, "drawing": as_read}
    refused = client.post("/api/documents", json={**unsettled, "recipient": {"company": "サンプル電機株式会社"}})
    assert refused.status_code == 400 and refused.json()["error"]["code"] == "QUANTITY_REQUIRED"
    unsettled["condition"]["quantity"] = 50
    doc = client.post("/api/documents", json={**unsettled, "recipient": {"company": "サンプル電機株式会社"}}).json()
    assert doc["total"] == confirmed["price"]["total"]


def test_drawing_reading_is_refused_clearly_without_an_api_key(tmp_path, monkeypatch):
    for key in ("ANALYSIS_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    with TestClient(create_app(settings_for(tmp_path))) as client:  # the real reader, but no key
        assert client.get("/api/health").json()["drawing_reader"] is False
        pdf = upload(client, PDF)
        response = client.post("/api/drawings/readings", json={"file_id": pdf["file_id"]})
        assert response.status_code == 503 and response.json()["error"]["code"] == "DRAWING_READER_UNAVAILABLE"


# ---------------------------------------------------------------- history
def test_history_import_list_detail_and_outcome(client, tmp_path):
    csv_text = "得意先,見積No,日付,図番,材料,板厚,個数,見積単価,受注結果\n株式会社API,A-1,2024-05-10,TX-9,SUS304,1.5,20,1280,\n"
    response = client.post("/api/history/import", files={"file": ("old.csv", csv_text.encode("cp932"))},
                           data={"mapping": json.dumps({"unit_price": "見積単価"})})
    assert response.status_code == 200, response.text
    assert response.json() == {"read": 1, "added": 1, "skipped": 0, "problems": []}
    assert client.post("/api/history/import", files={"file": ("old.csv", csv_text.encode("cp932"))},
                       data={"mapping": json.dumps({"unit_price": "見積単価"})}).json()["skipped"] == 1
    page = client.get("/api/history", params={"customer": "株式会社API"}).json()
    assert page["total"] == 1 and page["items"][0]["unit_price"] == 1280 and page["items"][0]["material_code"] == "SUS304"
    detail = client.get("/api/history/A-1").json()
    assert detail[0]["original"]["得意先"] == "株式会社API"
    assert client.put("/api/history/A-1/outcome", json={"outcome": "受注"}).json()["outcome"] == "受注"
    assert client.get("/api/history/A-1").json()[0]["outcome"] == "受注"
    assert client.put("/api/history/A-1/outcome", json={"outcome": "保留"}).status_code == 422
    assert client.get("/api/history/NOPE").status_code == 404
    bad = client.post("/api/history/import", files={"file": ("old.csv", csv_text.encode())}, data={"mapping": '{"x": "y"}'})
    assert bad.status_code == 400 and "未知の項目名" in bad.json()["error"]["message"]
    assert client.get("/api/history", params={"limit": 3}).json()["total"] > 2000


# ---------------------------------------------------------------- 3D view
def test_step_is_returned_as_glb(client):
    file = upload(client, STEP)
    response = client.get(f"/api/files/{file['file_id']}/model.glb")
    assert response.status_code == 200 and response.headers["content-type"] == "model/gltf-binary"
    data = response.content
    magic, version, length = struct.unpack("<III", data[:12])
    assert magic == 0x46546C67 and version == 2 and length == len(data)
    json_length, kind = struct.unpack("<II", data[12:20])
    document = json.loads(data[20:20 + json_length])
    assert kind == 0x4E4F534A and document["accessors"][0]["count"] > 10 and document["meshes"]
    dxf = upload(client, DXF)
    assert client.get(f"/api/files/{dxf['file_id']}/model.glb").status_code == 400


# ---------------------------------------------------------------- limits, token, errors
def test_upload_limits(tmp_path):
    with TestClient(create_app(settings_for(tmp_path, max_upload_bytes=30_000), reader_factory=FakeReader)) as client:
        assert client.post("/api/files", files={"file": ("a.exe", b"MZ...")}).status_code == 415
        assert client.post("/api/files", files={"file": ("a.step", b"%PDF-1.7 not a step")}).status_code == 415
        assert client.post("/api/files", files={"file": ("a.pdf", b"")}).status_code == 400
        too_big = client.post("/api/files", files={"file": ("big.dxf", DXF.read_bytes())})
        assert too_big.status_code == 413 and too_big.json()["error"]["code"] == "TOO_LARGE"
        assert client.post("/api/files", files={"file": ("../../etc/p.step", STEP.read_bytes())}).json()["filename"] == "p.step"
        assert client.post("/api/analyses", json={"file_id": "../../etc"}).status_code == 404
        assert client.get("/api/jobs/j_nothere").status_code == 404
        assert client.get("/api/documents/..%2F..%2Fx/quote.pdf").status_code == 404


def test_token_is_required_when_set(tmp_path):
    with TestClient(create_app(settings_for(tmp_path, api_token="s3cret"), reader_factory=FakeReader)) as client:
        assert client.get("/api/health").status_code == 200  # health stays open for probes
        denied = client.get("/api/masters")
        assert denied.status_code == 401 and "s3cret" not in denied.text
        assert client.get("/api/masters", headers={"Authorization": "Bearer wrong"}).status_code == 401
        assert client.get("/api/masters", headers={"Authorization": "Bearer s3cret"}).status_code == 200
        assert client.get("/api/masters", headers={"X-API-Token": "s3cret"}).status_code == 200


def test_errors_do_not_leak_internal_paths(tmp_path, monkeypatch):
    app = create_app(settings_for(tmp_path), reader_factory=FakeReader)

    def boom():
        raise RuntimeError(f"failed at {tmp_path}/secret/path with key sk-ant-xxxx")

    monkeypatch.setattr(app.state.service, "masters_payload", boom)
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/api/masters")
        assert response.status_code == 500
        assert "secret" not in response.text and "sk-ant" not in response.text and str(tmp_path) not in response.text
        bad = client.post("/api/quotes", json={"analysis": {"status": "success"}, "condition": {"material": "SPCC"}})
        assert bad.status_code == 422 and bad.json()["error"]["code"] == "INVALID_INPUT"
        unknown = client.post("/api/quotes", json={"analysis_job_id": "j_0000000000000000", "condition": {"material": "SPCC"}})
        assert unknown.status_code == 404


def test_settings_from_environment(tmp_path):
    s = Settings.from_env({"ESTIMATE_DATA_DIR": "/srv/masters", "ESTIMATE_STORAGE_DIR": str(tmp_path), "PAST_QUOTES_PATH": "/srv/h.csv",
                           "API_TOKEN": " t ", "MAX_UPLOAD_MB": "5", "JOB_WORKERS": "4"})
    assert s.data_dir == Path("/srv/masters") and s.uploads_dir == tmp_path / "uploads" and s.jobs_dir == tmp_path / "jobs"
    assert s.quote_log_path == tmp_path / "quote_log.csv" and s.history_path == Path("/srv/h.csv")
    assert s.api_token == "t" and s.max_upload_bytes == 5 * 1024 * 1024 and s.job_workers == 4
    default = Settings.from_env({})
    assert default.history_path == Path("data/past_quotes/history.csv") and default.storage_dir == Path("output")
