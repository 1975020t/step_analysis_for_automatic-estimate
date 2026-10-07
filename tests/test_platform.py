"""The screens' API (database): the amounts (same as the existing calculation, the same on screen, API and the
issued PDFs), when a quotation can be issued, the similarity rule, data kept across restarts, and saves that
would overwrite someone else's. No LLM: the drawing reader replays a recorded reading."""
from __future__ import annotations

import hashlib
import re
import time
from pathlib import Path

import pypdfium2 as pdfium
import pytest
from fastapi.testclient import TestClient

from api.main import create_app
from src.master_loader import MasterLoader
from src.db.models import Quote
from src.models import QuoteCondition, SheetMetalAnalysis
from src.past_quotes import HistoryStore
from src.quote_document import price_summary
from src.quote_engine import QuoteEngine
from src.services.estimate import EstimateService
from src.services.platform.core import RecordedReader
from src.services.schemas import AdditionalProcessInput, ConditionInput
from src.services.settings import Settings

ROOT = Path(__file__).resolve().parents[1]
STEP = ROOT / "pdf_data" / "step" / "Lv0_0002.step"
DRAWING_PDF, DRAWING_STEP = ROOT / "pdf_data" / "pdf" / "Lv3_0043.pdf", ROOT / "pdf_data" / "step" / "Lv3_0043.step"
RECORDED = ROOT / "docs" / "scripts" / "recorded_reading.json"
CSV_MASTERS = MasterLoader(ROOT / "data")


def make_app(storage: Path, history: Path):
    settings = Settings(data_dir=ROOT / "data", storage_dir=storage, history_path=history,
                        quote_log_path=storage / "quote_log.csv")
    return create_app(settings, reader_factory=lambda: RecordedReader(RECORDED))


@pytest.fixture
def client(tmp_path, _history_copy):
    with TestClient(make_app(tmp_path / "storage", _history_copy)) as c:
        yield c


def wait(client, job_id: str) -> dict:
    end = time.time() + 180
    while time.time() < end:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in ("done", "failed"):
            return job
        time.sleep(0.1)
    raise AssertionError("job did not finish")


def upload(client, path: Path, data: bytes | None = None) -> str:
    r = client.post("/api/files", files={"file": (path.name, data if data is not None else path.read_bytes())})
    assert r.status_code == 201, r.text
    return r.json()["file_id"]


def register(client, drawing_no: str, step: Path | None = STEP, pdf: Path | None = None, step_bytes: bytes | None = None,
             **extra) -> int:
    item = {"drawing_no": drawing_no, "name": "ブラケット", "customer": "株式会社テスト", **extra}
    if step is not None:
        item["shape_file_id"] = upload(client, step, step_bytes)
    if pdf is not None:
        item["pdf_file_id"] = upload(client, pdf)
    r = client.post("/api/drawings/register", json={"items": [item]})
    assert r.status_code == 202, r.text
    assert wait(client, r.json()[0]["job_id"])["status"] == "done"
    return r.json()[0]["drawing_id"]


def estimate(client, drawing_id: int, **body) -> dict:
    r = client.post("/api/estimates", json={"drawing_id": drawing_id, "customer": "株式会社テスト", "quantity": 100, **body})
    assert r.status_code == 202, r.text
    assert wait(client, r.json()["job_id"])["status"] == "done"
    return client.get(f"/api/estimates/{r.json()['estimate_id']}").json()


def change(client, q: dict, **inputs) -> dict:
    r = client.put(f"/api/estimates/{q['id']}", json={"version": q["version"], "inputs": inputs})
    assert r.status_code == 200, r.text
    return r.json()


def issue(client, q: dict, kind: str = "quote"):
    return client.post(f"/api/estimates/{q['id']}/documents", json={"kind": kind})


def pdf_text(client, url: str) -> str:
    data = client.get(url).content
    return re.sub(r"\s+", "", pdfium.PdfDocument(data)[0].get_textpage().get_text_range())


# ---------------------------------------------------------------- amounts
def test_amounts_are_those_of_the_existing_calculation_and_the_same_on_screen_api_and_documents(client):
    q = estimate(client, register(client, "AM-001"), material="SPCC", surface_treatment="ZINC_CLEAR")
    q = change(client, q, processes=[{"code": "TAP_M4", "quantity": 4}])
    price = q["result"]["price"]

    # the existing calculation (main): EstimateService with the CSV masters, same analysis and conditions
    with client.app.state.platform.session() as s:
        analysis = SheetMetalAnalysis.model_validate(s.get(Quote, q["id"]).analysis)
    main = EstimateService(Settings(data_dir=ROOT / "data")).quote(analysis, ConditionInput(
        material="SPCC", quantity=100, surface_treatment="ZINC_CLEAR",
        additional_processes=[AdditionalProcessInput(process_code="TAP_M4", quantity=4)]))
    assert price == main.price_out().model_dump()
    api = client.post("/api/quotes", json={"analysis": analysis.model_dump(mode="json"), "condition": {
        "material": "SPCC", "quantity": 100, "surface_treatment": "ZINC_CLEAR",
        "additional_processes": [{"process_code": "TAP_M4", "quantity": 4}]}}).json()["price"]
    assert api == price

    doc = issue(client, q)
    assert doc.status_code == 201, doc.text
    doc = doc.json()
    assert (doc["unit_price"], doc["subtotal"], doc["tax"], doc["total"]) == (price["unit_price"], price["subtotal"], price["tax"], price["total"])
    text = pdf_text(client, doc["url"])
    for value in (f"¥{price['total']:,}", f"{price['unit_price']:,}", f"{price['subtotal']:,}", f"{price['tax']:,}"):
        assert value in text

    cases = client.get("/api/cases").json()
    case = next(c for c in cases["cases"] if c["quote_id"] == q["id"])
    assert case["status"]["name"] == "見積提出済"  # issuing moves the case on
    assert issue(client, q, "invoice").status_code == 409  # not accepted yet
    won = next(s for s in cases["statuses"] if s["role"] == "won")
    assert client.put(f"/api/cases/{case['id']}/status", json={"status_id": won["id"], "version": case["version"]}).status_code == 200
    for kind in ("delivery", "invoice"):
        d = issue(client, q, kind)
        assert d.status_code == 201, d.text
        d = d.json()
        assert (d["subtotal"], d["tax"], d["total"]) == (price["subtotal"], price["tax"], price["total"])
        text = pdf_text(client, d["url"])
        assert f"¥{price['total']:,}" in text
        if kind == "invoice":
            assert "登録番号" in text and "10%対象" in text  # qualified invoice: registration number, total per rate
    review = client.get("/api/review?months=3").json()["current"]
    assert review["won"] >= 1 and review["won_total"] >= price["subtotal"]  # the accepted quote is in the review


# ---------------------------------------------------------------- issuing
def test_a_quotation_is_issued_only_when_every_amount_item_is_entered(client):
    # (1) material not chosen (the drawing has none), then chosen. The analysis is an estimate (k factor not
    # given) and the finish / rush are "none": none of that blocks.
    q = estimate(client, register(client, "IS-001", step=DRAWING_STEP))
    assert [m["field"] for m in q["result"]["missing"]] == ["material"] and q["result"]["price"] is None
    refused = issue(client, q)
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "NOT_ISSUABLE"
    assert refused.json()["error"]["details"][0]["field"] == "material"
    q = change(client, q, material="SPCC")
    assert q["result"]["issuable"] and any("概算" in h for h in q["result"]["hints"])
    assert q["inputs"]["surface_treatment"] == "NONE" and q["inputs"]["rush"] is False

    # (4) quantity missing, then entered
    q = change(client, q, quantity=None)
    assert [m["field"] for m in q["result"]["missing"]] == ["quantity"] and issue(client, q).status_code == 409
    q = change(client, q, quantity=10)
    assert q["result"]["issuable"]

    # (2) material / finish outside the master without a unit price, then with one; used for this quote only
    q = change(client, q, material=None, custom_material={"name": "SPCC-特殊"})
    assert [m["field"] for m in q["result"]["missing"]] == ["custom_material"] and issue(client, q).status_code == 409
    q = change(client, q, custom_material={"name": "SPCC-特殊", "price_per_kg": 200, "density_kg_m3": 7870})
    q = change(client, q, surface_treatment=None, custom_finish={"name": "特殊めっき"})
    assert [m["field"] for m in q["result"]["missing"]] == ["custom_finish"] and issue(client, q).status_code == 409
    q = change(client, q, custom_finish={"name": "特殊めっき", "unit_price": 75})
    assert q["result"]["issuable"]
    lines = {l["name"]: l for l in q["result"]["lines"]}
    assert lines["材料費（SPCC-特殊）"]["unit_price"] == 200 and lines["表面処理（特殊めっき）"]["amount"] == 75 * 10
    assert "SPCC-特殊" not in client.app.state.platform.masters().materials  # never registered automatically
    assert issue(client, q).status_code == 201

    # (2) a process on the drawing that is not in the master (recorded reading: M10タップ 6ヶ所), and a reading
    # to check (surface treatment 要確認): the price is missing until entered; the 要確認 is a hint only
    q = estimate(client, register(client, "DB-25-0367", step=DRAWING_STEP, pdf=DRAWING_PDF), quantity=50)
    assert q["inputs"]["material"] == "SPHC" and q["inputs"]["surface_treatment"] == "POWDER_COAT"
    assert [m["field"] for m in q["result"]["missing"]] == ["custom_processes.0"] and issue(client, q).status_code == 409
    assert any("要確認" in h for h in q["result"]["hints"])
    custom = q["inputs"]["custom_processes"]
    assert custom[0]["name"] == "M10タップ" and custom[0]["quantity"] == 6
    q = change(client, q, custom_processes=[{**custom[0], "unit_price": 120}])
    assert q["result"]["issuable"] and issue(client, q).status_code == 201
    assert next(l for l in q["result"]["lines"] if l["name"] == "M10タップ")["amount"] == 6 * 120 * 50

    # (3) the shape cannot be analysed: the five values are entered, and the price is the rules' price of them
    broken = b"ISO-10303-21;\nHEADER;\nENDSEC;\nDATA;\n#1=NOTHING();\nENDSEC;\nEND-ISO-10303-21;\n"
    q = estimate(client, register(client, "IS-003", step=Path("broken.step"), step_bytes=broken), material="SECC")
    assert {m["field"] for m in q["result"]["missing"]} == {"shape.thickness_mm", "shape.blank_area_mm2", "shape.cut_length_mm",
                                                           "shape.hole_count", "shape.bend_count"}
    assert issue(client, q).status_code == 409
    values = {"thickness_mm": 1.6, "blank_area_mm2": 12000.0, "cut_length_mm": 640.0, "hole_count": 3, "bend_count": 2}
    q = change(client, q, shape=values)
    assert q["result"]["issuable"]
    expected = QuoteEngine(CSV_MASTERS).calculate(SheetMetalAnalysis(status="success", file_name="x", **values),
                                                  QuoteCondition(material="SECC", quantity=100))
    assert q["result"]["price"]["subtotal"] == price_summary(expected, 100, 0.10).subtotal
    assert not any("手入力" in l["name"] or "入力" in l["name"] for l in q["result"]["lines"])
    assert issue(client, q).status_code == 201


# ---------------------------------------------------------------- similar
def test_similar_drawings_and_similar_quotes_follow_one_rule_on_both_screens(client):
    a = register(client, "SM-001", material="SPHC")
    b = register(client, "SM-002", step=DRAWING_STEP, material="SPHC")
    q = estimate(client, b, material="SPHC")
    drawing = client.get(f"/api/drawings/{b}").json()
    from_list = client.get(f"/api/drawings/{b}/similar").json()
    assert from_list == q["similar"] and from_list  # the drawing list and the estimate show the same results
    bends, holes = drawing["metrics"]["bend_count"], drawing["metrics"]["hole_count"]
    keys = []
    for s in from_list:
        assert s["material"] == "SPHC" and abs(s["bends"] - bends) <= 1 and abs(s["holes"] - holes) <= 2
        keys.append(abs(s["bends"] - bends) + abs(s["holes"] - holes))
    assert keys == sorted(keys) and all(s["drawing_id"] != b for s in from_list)
    other = client.get(f"/api/drawings/{a}").json()["metrics"]
    if abs(other["bend_count"] - bends) > 1 or abs(other["hole_count"] - holes) > 2:
        assert all(s["drawing_id"] != a for s in from_list)
    pdf_only = register(client, "SM-003", step=None, pdf=DRAWING_PDF)
    assert client.get(f"/api/drawings/{pdf_only}/similar").json() == []  # no shape file: not searched


# ---------------------------------------------------------------- data
def test_data_is_kept_across_restarts_and_the_csv_files_are_the_initial_data(tmp_path, _history_copy):
    digests = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (ROOT / "data").glob("*.csv")}
    storage = tmp_path / "storage"
    with TestClient(make_app(storage, _history_copy)) as c:
        assert c.get("/api/history?limit=1").json()["total"] == len(HistoryStore(_history_copy).load())
        rows = {r["code"]: r for r in c.get("/api/master-tables/materials").json()}
        assert {k: rows[k]["price_per_kg"] for k in rows} == {k: v["price_per_kg"] for k, v in CSV_MASTERS.materials.items()}
        q = estimate(c, register(c, "PS-001"), material="SPCC")
        r = c.put("/api/master-tables/materials/SPCC", json={"values": {**{k: rows["SPCC"][k] for k in (
            "display_name", "density_kg_m3", "waste_factor", "charge_scope", "aliases")}, "price_per_kg": "170"},
            "version": rows["SPCC"]["version"]})
        assert r.status_code == 200, r.text
    with TestClient(make_app(storage, _history_copy)) as c:  # the API started again on the same database
        assert [d["drawing_no"] for d in c.get("/api/drawings").json()] == ["PS-001"]
        again = c.get(f"/api/estimates/{q['id']}").json()
        assert again["number"] == q["number"] and again["inputs"]["material"] == "SPCC"
        assert c.get("/api/master-tables/materials").json()[[r["code"] for r in c.get("/api/master-tables/materials").json()].index("SPCC")]["price_per_kg"] == "170"
        assert again["result"]["price"]["subtotal"] > q["result"]["price"]["subtotal"]  # the edited master is used
    # the evaluation and the existing tests keep reading the CSV files: the screen's master edit is not in them
    assert digests == {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (ROOT / "data").glob("*.csv")}
    assert MasterLoader(ROOT / "data").materials["SPCC"]["price_per_kg"] == "160"


def test_a_save_based_on_an_older_version_is_refused_not_overwritten(client):
    q = estimate(client, register(client, "CC-001"), material="SPCC")
    first = client.put(f"/api/estimates/{q['id']}", json={"version": q["version"], "inputs": {"quantity": 20}})
    second = client.put(f"/api/estimates/{q['id']}", json={"version": q["version"], "inputs": {"quantity": 30}})
    assert first.status_code == 200 and second.status_code == 409 and second.json()["error"]["code"] == "CONFLICT"
    assert client.get(f"/api/estimates/{q['id']}").json()["inputs"]["quantity"] == 20
    cases = client.get("/api/cases").json()
    case = cases["cases"][0]
    to = [s for s in cases["statuses"] if s["name"] == "見積確認中"][0]
    assert client.put(f"/api/cases/{case['id']}/status", json={"status_id": to["id"], "version": case["version"]}).status_code == 200
    stale = client.put(f"/api/cases/{case['id']}/status", json={"status_id": to["id"] + 1, "version": case["version"]})
    assert stale.status_code == 409
    log = client.get(f"/api/cases/{case['id']}/history").json()
    assert [x["status_name"] for x in log] == ["見積作成中", "見積確認中"]


# ---------------------------------------------------------------- phases, To Do list and next action
def test_phases_to_do_and_next_action_are_decided_by_the_api(client):
    phase = {s["name"]: s["phase"] for s in client.get("/api/statuses").json()}
    assert phase == {"見積依頼受付": "drafting", "見積前": "drafting", "見積作成中": "drafting", "見積確認中": "checking",
                     "見積提出済": "waiting", "受注": "production", "製造準備": "production", "製造中": "production",
                     "検査": "production", "出荷待ち": "production", "出荷済": "closed", "完了": "closed",
                     "失注": "closed", "保留": "closed"}

    # a missing item puts the case on the To Do list with the input as next action; entering it takes it off
    q = estimate(client, register(client, "TD-001", step=DRAWING_STEP))
    home = client.get("/api/home").json()
    row = next(c for c in home["todo"] if c["quote_id"] == q["id"])
    assert [t["kind"] for t in row["todo"]] == ["missing"] and row["next_action"]["kind"] == "input"
    assert next(p for p in home["phases"] if p["key"] == "drafting")["attention"]["count"] == 1
    q = change(client, q, material="SPCC")
    assert q["next_action"]["kind"] == "issue" and q["flow_step"] == 4
    assert not any(c["quote_id"] == q["id"] for c in client.get("/api/home").json()["todo"])

    # issued: phase 回答待ち, next the outcome; accepted: the delivery note, then the invoice
    assert issue(client, q).status_code == 201
    q = client.get(f"/api/estimates/{q['id']}").json()
    assert q["phase"] == "waiting" and q["next_action"]["kind"] == "outcome" and q["flow_step"] == 6
    won = q["outcome_statuses"]["won"]
    assert client.put(f"/api/cases/{q['case_id']}/status", json={"status_id": won["id"], "version": q["case_version"]}).status_code == 200
    cases = client.get("/api/cases?phase=production").json()
    row = next(c for c in cases["cases"] if c["quote_id"] == q["id"])
    assert row["next_action"] == {**row["next_action"], "kind": "documents", "doc_kind": "delivery"}
    assert all(c["phase"] == "production" for c in cases["cases"])

    # a renamed or added status keeps the phase it is given; an unknown phase is refused
    rows = client.get("/api/statuses").json()
    rows[3]["name"] = "社内確認中"
    body = [{k: r[k] for k in ("id", "name", "color", "phase", "visible")} for r in rows]
    body.insert(5, {"name": "回答督促中", "color": "blue", "phase": "waiting", "visible": True})
    saved = {s["name"]: s["phase"] for s in client.put("/api/statuses", json={"statuses": body}).json()}
    assert saved["社内確認中"] == "checking" and saved["回答督促中"] == "waiting"
    body[0]["phase"] = "somewhere"
    assert client.put("/api/statuses", json={"statuses": body}).status_code == 400


def test_the_draft_of_a_new_estimate_marks_where_each_value_comes_from(client):
    pdf = upload(client, DRAWING_PDF)
    job = client.post("/api/drawings/readings", json={"file_id": pdf}).json()["job_id"]
    assert wait(client, job)["status"] == "done"
    d = client.get(f"/api/estimate-draft?reading_job_id={job}&file_name=Lv3_0043.pdf").json()
    f = d["fields"]
    assert (f["drawing_no"]["value"], f["drawing_no"]["mark"]) == ("DB-25-0367", "read")
    assert (f["material"]["value"], f["material"]["mark"]) == ("SPHC", "read")
    assert (f["quantity"]["value"], f["quantity"]["mark"]) == (50, "read")
    assert f["surface_treatment"]["mark"] == "review" and f["surface_treatment"]["note"]  # with the reason
    assert f["processes"]["mark"] == "unregistered" and "M10タップ" in f["processes"]["note"]
    assert f["processes"]["custom_processes"][0]["name"] == "M10タップ"  # its unit price is entered later

    # a drawing without a reading: nothing is guessed
    plain = client.get(f"/api/estimate-draft?drawing_id={register(client, 'DR-001')}").json()["fields"]
    assert plain["material"]["value"] is None and plain["quantity"]["value"] is None
    assert plain["drawing_no"] == {"value": "DR-001", "mark": "drawing", "note": ""}


def test_the_phase_migration_keeps_the_meaning_of_existing_statuses(tmp_path):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, text

    url = f"sqlite:///{tmp_path / 'old.db'}"
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "0001")
    engine = create_engine(url)
    with engine.begin() as c:  # a database of the previous version, with a renamed and an added status
        for i, (name, group, role) in enumerate([("依頼受付", "見積・受注", ""), ("見積確認中", "見積・受注", ""),
                                                 ("見積提出済", "見積・受注", "issued"), ("塗装待ち", "製造・出荷", ""),
                                                 ("失注", "保留・失注", "lost")]):
            c.execute(text("INSERT INTO case_statuses (name, sort, color, group_name, visible, role, version) "
                           "VALUES (:n, :s, 'blue', :g, 1, :r, 1)"), {"n": name, "s": i, "g": group, "r": role})
    command.upgrade(config, "head")
    with engine.begin() as c:
        got = dict(c.execute(text("SELECT name, phase FROM case_statuses")).all())
    assert got == {"依頼受付": "drafting", "見積確認中": "checking", "見積提出済": "waiting", "塗装待ち": "production",
                   "失注": "closed"}
