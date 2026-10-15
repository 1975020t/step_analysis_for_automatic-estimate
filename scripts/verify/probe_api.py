"""画面用 API の総当たり確認（docs/09_テスト報告書.md の 5.3）。

正しい操作と、空欄・負の数・存在しない番号・壊れたファイル・古い版での保存などの誤った入力を、ほぼすべての
エンドポイントに送り、サーバーエラー（500）や期待と違う応答を BUG として数える。LLM は呼ばない（記録済みの読み取り）。

    python scripts/verify/probe_api.py
"""
import os, sys, time, shutil, tempfile, json, io
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]; sys.path.insert(0, str(ROOT)); os.chdir(ROOT)
from fastapi.testclient import TestClient
from api.main import create_app
from src.services.settings import Settings
from src.services.platform.core import RecordedReader
tmp = Path(tempfile.mkdtemp()); hist = tmp / "history.csv"; shutil.copy(ROOT / "data/past_quotes/history.csv", hist)
app = create_app(Settings(data_dir=ROOT / "data", storage_dir=tmp / "s", history_path=hist, quote_log_path=tmp / "log.csv"),
                 reader_factory=lambda: RecordedReader(ROOT / "docs/scripts/recorded_reading.json"))
bugs = []
checks = [0]
def note(label, r, ok):
    checks[0] += 1
    good = ok(r) if callable(ok) else r.status_code in (ok if isinstance(ok, tuple) else (ok,))
    if r.status_code >= 500 or not good:
        bugs.append(f"{label}: {r.status_code} {r.text[:200]}")
        print("BUG", label, r.status_code, r.text[:200])
    return r
def wait(c, j):
    for _ in range(1500):
        r = c.get(f"/api/jobs/{j}").json()
        if r["status"] in ("done", "failed"): return r
        time.sleep(0.1)
def up(c, path, data=None, name=None):
    return c.post("/api/files", files={"file": (name or path.name, data if data is not None else path.read_bytes())})
STEP = ROOT / "pdf_data/step/Lv0_0002.step"; PDF = ROOT / "pdf_data/pdf/Lv3_0043.pdf"; STEP43 = ROOT / "pdf_data/step/Lv3_0043.step"
DXF = ROOT / "dxf_data/D1/Lv2_0013_D1.dxf"
with TestClient(app, raise_server_exceptions=False) as c:
    # ---- files
    note("upload txt", up(c, STEP, b"hello", "a.txt"), (400, 415))
    note("upload fake step", up(c, STEP, b"not a step", "x.step"), (400, 415, 201))
    note("upload empty", up(c, STEP, b"", "e.pdf"), (400, 415))
    sid = up(c, STEP).json()["file_id"]; pid = up(c, PDF).json()["file_id"]; s43 = up(c, STEP43).json()["file_id"]; did = up(c, DXF).json()["file_id"]
    # ---- register
    note("register empty", c.post("/api/drawings/register", json={"items": []}), 400)
    note("register no files", c.post("/api/drawings/register", json={"items": [{"drawing_no": "X"}]}), 400)
    note("register blank no", c.post("/api/drawings/register", json={"items": [{"drawing_no": "", "shape_file_id": sid}]}), 422)
    note("register bad file id", c.post("/api/drawings/register", json={"items": [{"drawing_no": "X", "shape_file_id": "nope"}]}), 404)
    note("register pdf as shape", c.post("/api/drawings/register", json={"items": [{"drawing_no": "X", "shape_file_id": pid}]}), 400)
    note("register bad category", c.post("/api/drawings/register", json={"items": [{"drawing_no": "X1", "shape_file_id": sid, "category_ids": [99999]}]}), (202, 400))
    note("register new_revision_of missing", c.post("/api/drawings/register", json={"items": [{"drawing_no": "X", "shape_file_id": sid, "new_revision_of": 9999}]}), 404)
    r = c.post("/api/drawings/register", json={"items": [{"drawing_no": "A-1", "name": "部品A", "customer": "顧客A", "shape_file_id": sid, "material": "SPCC"},
                                                        {"drawing_no": "DB-25-0367", "pdf_file_id": pid, "shape_file_id": s43, "customer": "顧客B"},
                                                        {"drawing_no": "D-1", "shape_file_id": did},
                                                        {"drawing_no": "P-1", "pdf_file_id": pid}]})
    regs = r.json(); [wait(c, x["job_id"]) for x in regs]
    A, B, D, P = [x["drawing_id"] for x in regs]
    # ---- drawing list filters
    for qs in ["keyword=A-1", "dim_min=abc", "dim_max=10", "date_from=2026-01-01", "category_id=abc", "attrs=notjson", "attrs=%7B%22x%22%3A%7B%22min%22%3A%22a%22%7D%7D", "text=SPHC", "status=未作成", "material=SPCC"]:
        note(f"drawings?{qs}", c.get(f"/api/drawings?{qs}"), (200, 400, 422))
    note("drawing 404", c.get("/api/drawings/9999"), 404)
    note("similar of pdf-only", c.get(f"/api/drawings/{P}/similar"), lambda r: r.status_code == 200 and r.json() == [])
    note("similar 404", c.get("/api/drawings/9999/similar"), 404)
    d = c.get(f"/api/drawings/{A}").json()
    note("drawing update", c.put(f"/api/drawings/{A}", json={"version": d["version"], "name": "部品A改"}), 200)
    note("drawing update stale", c.put(f"/api/drawings/{A}", json={"version": d["version"], "name": "x"}), 409)
    d = c.get(f"/api/drawings/{A}").json()
    note("drawing update blank no", c.put(f"/api/drawings/{A}", json={"version": d["version"], "drawing_no": ""}), (400, 422))
    note("drawing update bad cat", c.put(f"/api/drawings/{A}", json={"version": c.get(f"/api/drawings/{A}").json()["version"], "category_ids": [9999]}), (200, 400))
    note("current rev wrong", c.put(f"/api/drawings/{A}/current-revision?revision_id=9999"), (400, 404))
    note("current rev other drawing", c.put(f"/api/drawings/{A}/current-revision?revision_id={c.get(f'/api/drawings/{B}').json()['revision_id']}"), (400, 404))
    note("preview no pdf", c.get(f"/api/revisions/{c.get(f'/api/drawings/{A}').json()['revision_id']}/preview.png"), 404)
    note("preview 404", c.get("/api/revisions/9999/preview.png"), 404)
    note("memo", c.post(f"/api/drawings/{A}/memos", json={"kind": "注意", "text": "テスト"}), 200)
    note("memo empty", c.post(f"/api/drawings/{A}/memos", json={"kind": "注意", "text": ""}), (400, 422))
    note("memo 404", c.post("/api/drawings/9999/memos", json={"kind": "注意", "text": "x"}), 404)
    note("note out of range", c.post(f"/api/revisions/{c.get(f'/api/drawings/{B}').json()['revision_id']}/notes", json={"x": 5, "y": -1, "text": "x"}), (200, 400, 422))
    note("note 404", c.post("/api/revisions/9999/notes", json={"x": 0.5, "y": 0.5, "text": "x"}), 404)
    note("delete note 404", c.delete("/api/notes/9999"), (200, 404))
    note("same number", c.get("/api/drawings/same-number?drawing_no=DB-25-0367"), 200)
    # ---- drafts
    note("draft drawing", c.get(f"/api/estimate-draft?drawing_id={B}"), 200)
    note("draft 404", c.get("/api/estimate-draft?drawing_id=9999"), 404)
    note("draft bad job", c.get("/api/estimate-draft?reading_job_id=nope"), 404)
    note("draft nothing", c.get("/api/estimate-draft"), 200)
    note("draft analysis job as reading", c.get(f"/api/estimate-draft?reading_job_id={regs[0]['job_id']}"), (200, 400))
    # ---- estimates
    note("estimate no customer", c.post("/api/estimates", json={"drawing_id": A, "customer": "", "quantity": 1}), 422)
    note("estimate qty 0", c.post("/api/estimates", json={"drawing_id": A, "customer": "c", "quantity": 0}), 422)
    note("estimate 404 drawing", c.post("/api/estimates", json={"drawing_id": 9999, "customer": "c", "quantity": 1}), 404)
    note("estimate bad revision", c.post("/api/estimates", json={"drawing_id": A, "revision_id": 9999, "customer": "c", "quantity": 1}), 404)
    note("estimate bad material", c.post("/api/estimates", json={"drawing_id": A, "customer": "c", "quantity": 1, "material": "NOPE"}), (202, 400))
    note("estimate bad process", c.post("/api/estimates", json={"drawing_id": A, "customer": "c", "quantity": 1, "processes": [{"code": "NOPE", "quantity": 1}]}), (202, 400))
    note("estimate bad custom proc", c.post("/api/estimates", json={"drawing_id": A, "customer": "c", "quantity": 1, "custom_processes": [{"name": ""}]}), (400, 422))
    note("estimate bad due", c.post("/api/estimates", json={"drawing_id": A, "customer": "c", "quantity": 1, "due_date": "2026-13-40"}), 422)
    note("estimate other revision of other drawing", c.post("/api/estimates", json={"drawing_id": A, "revision_id": c.get(f"/api/drawings/{B}").json()["revision_id"], "customer": "c", "quantity": 1}), (400, 404))
    def est(**b):
        r = c.post("/api/estimates", json={"customer": "顧客A", "quantity": 10, **b}); assert r.status_code == 202, r.text
        j = wait(c, r.json()["job_id"])
        if j["status"] != "done": bugs.append(f"estimate job failed {b}: {j.get('error')}"); print("BUG job", j)
        return c.get(f"/api/estimates/{r.json()['estimate_id']}").json()
    qa = est(drawing_id=A, material="SPCC")
    qb = est(drawing_id=B)
    qd = est(drawing_id=D, material="SPCC")  # DXF without thickness
    qd2 = est(drawing_id=D, material="SPCC", thickness_mm=1.6)
    qp = est(drawing_id=P)
    qbad = est(drawing_id=A, material="NOPE", processes=[{"code": "NOPE", "quantity": 1}])
    print("A issuable", qa["result"]["issuable"], "B missing", [m["field"] for m in qb["result"]["missing"]], "D missing", [m["field"] for m in qd["result"]["missing"]],
          "D2", qd2["result"]["issuable"], qd2["analysis"] and qd2["analysis"]["state"], "P", [m["field"] for m in qp["result"]["missing"]], "bad", [m["field"] for m in qbad["result"]["missing"]])
    note("estimates list", c.get("/api/estimates"), 200)
    note("estimate 404", c.get("/api/estimates/9999"), 404)
    def put(q, inputs, **kw): return c.put(f"/api/estimates/{q['id']}", json={"version": q["version"], "inputs": inputs, **kw})
    note("update stale", put({**qa, "version": 0}, {"quantity": 5}), 409)
    note("update qty neg", put(qa, {"quantity": -5}), 400)
    note("update qty str", put(qa, {"quantity": "abc"}), 400)
    note("update bad shape", put(qa, {"shape": {"hole_count": -1}}), 400)
    note("update unknown field", put(qa, {"bogus": 1}), (200, 400))
    qa = c.get(f"/api/estimates/{qa['id']}").json()
    note("update blank customer", put(qa, {}, customer="  "), 400)
    note("update k out of range", put(qa, {"k_factor": 2}), 400)
    r = note("update k", put(qa, {"k_factor": 0.4, "k_factor_confirmed": True}), 200)
    qa = r.json()
    if qa.get("job") and qa["job"]["status"] in ("queued", "running"):  # analysed again as a job
        note("update k analysed", c.get(f"/api/jobs/{qa['job']['job_id']}"), lambda _: wait(c, qa["job"]["job_id"])["status"] == "done")
        qa = c.get(f"/api/estimates/{qa['id']}").json()
    print("A after k", qa["analysis"]["state"], qa["result"]["price"] and qa["result"]["price"]["subtotal"])
    r = note("update rush", put(qa, {"rush": True}), 200); qa = r.json()
    r = note("update due clears", put(qa, {}, due_date=None), 200); qa = r.json()
    note("chat empty", c.post(f"/api/estimates/{qa['id']}/chat", json={"message": " ", "version": qa["version"]}), 400)
    r = note("chat qty", c.post(f"/api/estimates/{qa['id']}/chat", json={"message": "数量を200に", "version": qa["version"]}), 200)
    if r.status_code == 200: print("chat qty ->", r.json()["inputs"]["quantity"]); qa = r.json()
    note("chat stale", c.post(f"/api/estimates/{qa['id']}/chat", json={"message": "数量を300に", "version": 0}), 409)
    # ---- documents
    note("issue bad kind", c.post(f"/api/estimates/{qa['id']}/documents", json={"kind": "foo"}), (400, 422))
    note("preview bad kind", c.get(f"/api/estimates/{qa['id']}/documents/preview.png?kind=foo"), (400, 422))
    note("issue missing", c.post(f"/api/estimates/{qb['id']}/documents", json={"kind": "quote"}), 409)
    note("preview missing", c.get(f"/api/estimates/{qb['id']}/documents/preview.png?kind=quote"), 409)
    note("issue bad template", c.post(f"/api/estimates/{qa['id']}/documents", json={"kind": "quote", "template_id": 9999}), (400, 404))
    r1 = note("issue quote", c.post(f"/api/estimates/{qa['id']}/documents", json={"kind": "quote", "person": "山田", "remarks": "テスト"}), 201)
    r2 = note("reissue quote", c.post(f"/api/estimates/{qa['id']}/documents", json={"kind": "quote"}), 201)
    print("quote numbers", r1.json().get("number"), r2.json().get("number"))
    note("issued list", c.get("/api/issued?kind=quote"), 200)
    note("issued file", c.get(f"/api/issued/{r1.json()['id']}/file.pdf"), 200)
    note("issued file 404", c.get("/api/issued/9999/file.pdf"), 404)
    # history duplicates after reissue
    h = c.get("/api/history?customer=顧客A").json()
    rows = [x for x in h["items"] if x.get("customer") == "顧客A"]
    print("history rows for 顧客A", len(rows), [x.get("quote_no") for x in rows])
    # change after issuing then delivery uses old amounts?
    qa = c.get(f"/api/estimates/{qa['id']}").json()
    st = {s["name"]: s for s in c.get("/api/statuses").json()}
    note("move lost no reason", c.put(f"/api/cases/{qa['case_id']}/status", json={"status_id": st["失注"]["id"], "version": qa["case_version"]}), 400)
    note("move bad status", c.put(f"/api/cases/{qa['case_id']}/status", json={"status_id": 9999, "version": qa["case_version"]}), 404)
    note("move case 404", c.put("/api/cases/9999/status", json={"status_id": st["受注"]["id"], "version": 1}), 404)
    note("move competitor neg", c.put(f"/api/cases/{qa['case_id']}/status", json={"status_id": st["失注"]["id"], "version": qa["case_version"], "lost_reason": "価格", "competitor_price": -1}), 422)
    note("won", c.put(f"/api/cases/{qa['case_id']}/status", json={"status_id": st["受注"]["id"], "version": qa["case_version"]}), 200)
    qa = c.get(f"/api/estimates/{qa['id']}").json()
    r = note("change after won", put(qa, {"quantity": 999}), 200)
    qa = c.get(f"/api/estimates/{qa['id']}").json()
    dl = note("delivery after change", c.post(f"/api/estimates/{qa['id']}/documents", json={"kind": "delivery"}), 201)
    print("delivery qty/total", dl.json().get("quantity"), dl.json().get("total"), "| estimate now qty", qa["inputs"]["quantity"], qa["result"]["price"]["total"], "| last quote doc total", r2.json()["total"])
    note("case history", c.get(f"/api/cases/{qa['case_id']}/history"), 200)
    for ph in ["", "active", "drafting", "closed", "bogus"]:
        note(f"cases phase={ph}", c.get(f"/api/cases?phase={ph}"), 200)
    note("home", c.get("/api/home"), 200)
    for m in [1, 3, 36, 0, 37]:
        note(f"review {m}", c.get(f"/api/review?months={m}"), (200, 422))
    # ---- statuses
    rows = c.get("/api/statuses").json()
    body = [{k: r[k] for k in ("id", "name", "color", "phase", "visible")} for r in rows]
    note("statuses dup", c.put("/api/statuses", json={"statuses": body + [{"name": rows[0]["name"], "phase": "drafting"}]}), 400)
    note("statuses blank", c.put("/api/statuses", json={"statuses": body + [{"name": " ", "phase": "drafting"}]}), (400, 422))
    note("statuses delete used", c.put("/api/statuses", json={"statuses": [b for b in body if b["name"] != "受注"]}), 409)
    note("statuses bad color", c.put("/api/statuses", json={"statuses": [{**body[0], "color": "red"}] + body[1:]}), 400)
    note("statuses empty", c.put("/api/statuses", json={"statuses": []}), (400, 409))
    swap = [dict(b) for b in body]; swap[0]["name"], swap[1]["name"] = swap[1]["name"], swap[0]["name"]
    note("statuses swap names", c.put("/api/statuses", json={"statuses": swap}), (200,))
    note("statuses restore", c.put("/api/statuses", json={"statuses": body}), 200)
    # ---- staff
    staff = c.get("/api/staff").json()
    note("staff dup", c.put("/api/staff", json={"staff": [{"name": "A"}, {"name": "A"}]}), 400)
    sw = [dict(s) for s in staff]; sw[0]["name"], sw[1]["name"] = sw[1]["name"], sw[0]["name"]
    note("staff swap names", c.put("/api/staff", json={"staff": sw}), 200)
    note("staff remove one", c.put("/api/staff", json={"staff": staff[1:]}), 200)
    note("staff re-add removed name", c.put("/api/staff", json={"staff": staff[1:] + [{"name": staff[0]["name"]}]}), 200)
    note("staff rename to inactive", c.put("/api/staff", json={"staff": [{"id": staff[1]["id"], "name": "臨時"}] + staff[2:]}), 200)
    note("staff rename onto inactive name", c.put("/api/staff", json={"staff": [{"id": staff[2]["id"], "name": "臨時"}]}), (200, 400))
    print("staff now", c.get("/api/staff").json())
    # ---- categories / attributes / templates / partners
    note("cat blank", c.post("/api/categories", json={"name": ""}), 400)
    note("cat parent 404", c.post("/api/categories", json={"name": "x", "parent_id": 9999}), 404)
    cats = c.post("/api/categories", json={"name": "新グループ"}).json()
    g = [x for x in cats if x["name"] == "新グループ"][0]
    cats = c.post("/api/categories", json={"name": "子", "parent_id": g["id"]}).json()
    child = [x for x in cats if x["id"] == g["id"]][0]["children"][0]
    note("cat grandchild", c.post("/api/categories", json={"name": "孫", "parent_id": child["id"]}), (200, 400))
    note("cat rename 404", c.put("/api/categories/9999", json={"name": "x"}), 404)
    note("cat delete group", c.delete(f"/api/categories/{g['id']}"), 200)
    attrs = c.get("/api/attributes").json()
    body = [{"id": a["id"], "label": a["label"], "input_type": a["input_type"], "options": [o if isinstance(o, str) else o["value"] for o in a["options"]], "unit": a["unit"], "searchable": a["searchable"]} for a in attrs]
    note("attrs save same", c.put("/api/attributes", json={"attributes": body}), 200)
    note("attrs bad type", c.put("/api/attributes", json={"attributes": body + [{"label": "x", "input_type": "zzz"}]}), 400)
    note("attrs delete builtin", c.put("/api/attributes", json={"attributes": body[1:]}), 400)
    r = note("attrs add", c.put("/api/attributes", json={"attributes": body + [{"label": "板取り", "input_type": "number_range", "unit": "枚"}]}), 200)
    newkey = [a for a in r.json() if a["label"] == "板取り"][0]["key"]
    dd = c.get(f"/api/drawings/{A}").json()
    note("drawing attrs set", c.put(f"/api/drawings/{A}", json={"version": dd["version"], "attrs": {newkey: "abc"}}), (200, 400))
    note("drawings attr range filter", c.get("/api/drawings?attrs=" + json.dumps({newkey: {"min": "1"}})), 200)
    tpls = c.get("/api/templates").json()
    note("tpl bad key", c.post("/api/templates", json={"name": "x", "options": {"zzz": True}}), 400)
    note("tpl delete default", c.delete(f"/api/templates/{[t for t in tpls if t['is_default']][0]['id']}"), 400)
    note("tpl 404", c.put("/api/templates/9999", json={"name": "x"}), 404)
    note("tpl preview", c.get(f"/api/templates/{tpls[0]['id']}/preview.png"), 200)
    note("tpl preview 404", c.get("/api/templates/9999/preview.png"), (404,))
    note("partner bad kind", c.post("/api/partners", json={"name": "x", "kind": "z"}), 400)
    pr = note("partner add", c.post("/api/partners", json={"name": "協力X"}), 201).json()
    note("partner stale", c.put(f"/api/partners/{pr['id']}", json={"name": "y", "version": 99}), 409)
    note("partner no version", c.put(f"/api/partners/{pr['id']}", json={"name": "y"}), (200, 409, 400))
    # ---- masters
    mats = c.get("/api/master-tables/materials").json()
    m0 = mats[0]; vals = {k: m0[k] for k in ("display_name", "density_kg_m3", "price_per_kg", "waste_factor", "charge_scope", "aliases")}
    note("master bad table", c.get("/api/master-tables/zzz"), 404)
    note("master neg", c.put(f"/api/master-tables/materials/{m0['code']}", json={"values": {**vals, "price_per_kg": "-1"}, "version": m0["version"]}), 400)
    note("master stale", c.put(f"/api/master-tables/materials/{m0['code']}", json={"values": vals, "version": 0}), 409)
    note("master dup", c.post("/api/master-tables/materials", json={"code": m0["code"], "values": vals}), 409)
    note("master missing cols", c.post("/api/master-tables/materials", json={"code": "NEWM", "values": {"display_name": "x"}}), 400)
    procs = c.get("/api/master-tables/processes").json(); p0 = procs[-1]
    pv = {k: p0[k] for k in ("display_name", "calculation_type", "unit_price", "unit", "charge_scope", "aliases")}
    r = note("master bad calc type", c.put(f"/api/master-tables/processes/{p0['code']}", json={"values": {**pv, "calculation_type": "zzz"}, "version": p0["version"]}), 400)
    r = note("master bad unit", c.put(f"/api/master-tables/processes/{p0['code']}", json={"values": {**pv, "unit": "zzz"}, "version": c.get("/api/master-tables/processes").json()[-1]["version"]}), 400)
    note("estimate after master edits", c.get(f"/api/estimates/{qa['id']}"), 200)
    note("meta after master edits", c.get("/api/meta"), 200)
    pol = c.get("/api/master-tables/pricing_policy").json()
    t = [x for x in pol if x["code"] == "tax_rate"][0]
    note("policy tax 10 (percent mistake)", c.put("/api/master-tables/pricing_policy/tax_rate", json={"values": {"value": "10", "description": t["description"]}, "version": t["version"]}), (400,))
    note("policy add row", c.post("/api/master-tables/pricing_policy", json={"code": "foo", "values": {"value": "1"}}), (400, 201))
    note("logic", c.get("/api/logic"), 200)
    # ---- search / library / import
    for q in ["", "DB-25", "a b", "%", "(", "\\"]:
        note(f"search {q!r}", c.get(f"/api/search?q={q}"), 200)
    note("library bad kind", c.post("/api/library", files={"file": ("a.pdf", PDF.read_bytes())}, data={"kind": "zzz"}), 400)
    note("library bad ids", c.post("/api/library", files={"file": ("a.pdf", PDF.read_bytes())}, data={"kind": "仕様書", "drawing_ids": "x"}), 400)
    note("library txt", c.post("/api/library", files={"file": ("a.txt", b"abc")}, data={"kind": "仕様書"}), (400, 415))
    note("library fake xlsx", c.post("/api/library", files={"file": ("a.xlsx", b"abc")}, data={"kind": "仕様書"}), (400, 415))
    lib = note("library pdf", c.post("/api/library", files={"file": ("a.pdf", PDF.read_bytes())}, data={"kind": "仕様書", "drawing_ids": json.dumps([A, 9999])}), (201, 400))
    note("library list", c.get("/api/library"), 200)
    note("library delete 404", c.delete("/api/library/9999"), (200, 404))
    note("history columns bad", c.post("/api/history/columns", files={"file": ("a.csv", b"\xff\xfe\x00garbage")}), (200, 400))
    note("history columns empty", c.post("/api/history/columns", files={"file": ("a.csv", b"")}), (200, 400))
    csv = "見積番号,見積日,顧客名,図番,数量,単価,結果\nZ-1,2026/09/01,顧客Z,ZZ-1,10,1000,受注\nZ-2,不正な日付,顧客Z,ZZ-2,10,abc,受注\n".encode("utf-8")
    note("history import", c.post("/api/history/import", files={"file": ("a.csv", csv)}), 200)
    note("history import again", c.post("/api/history/import", files={"file": ("a.csv", csv)}), 200)
    note("history import bad mapping", c.post("/api/history/import", files={"file": ("a.csv", csv)}, data={"mapping": "notjson"}), (400, 422))
    note("review after import", c.get("/api/review?months=12"), 200)
    # ---- legacy api
    note("legacy analyses", c.post("/api/analyses", json={"file_id": sid}), 202)
    note("legacy quotes bad", c.post("/api/quotes", json={"analysis_job_id": "nope", "condition": {"material": "SPCC", "quantity": 1}}), (400, 404))
    note("glb", c.get(f"/api/files/{sid}/model.glb"), 200)
    note("glb of pdf", c.get(f"/api/files/{pid}/model.glb"), (400, 404, 415))
    note("raw 404", c.get("/api/files/nope/raw"), 404)
    note("spa route", c.get("/cases"), (200, 404))
print(f"checks: {checks[0]}  bugs: {len(bugs)}")
print("\n".join(["", "== BUGS =="] + bugs))
# ---- follow-up checks of the fixes
with TestClient(app, raise_server_exceptions=False) as c:
    qd = c.get("/api/estimates").json()
    for dx in [e for e in qd if e["drawing_no"] == "D-1"]:
        d = c.get(f"/api/estimates/{dx['id']}").json()
        print("DXF missing:", d["inputs"]["thickness_mm"], [(m["field"], m["message"]) for m in d["result"]["missing"]], d["next_action"]["kind"])
        if d["inputs"]["thickness_mm"] is None:
            r = c.put(f"/api/estimates/{d['id']}", json={"version": d["version"], "inputs": {"thickness_mm": 1.6}})
            if r.json().get("job"):
                wait(c, r.json()["job"]["job_id"])
            r = c.get(f"/api/estimates/{d['id']}")
            print("  after thickness:", r.json()["result"]["issuable"], r.json()["analysis"]["state"])
    a = [e for e in qd if e["drawing_no"] == "A-1"]
    for e in a:
        d = c.get(f"/api/estimates/{e['id']}").json()
        if d["documents"]:
            print("notice:", d["notices"]["delivery"])
    print("staff:", c.get("/api/staff").json())
    print("chat:", end=" ")
    d = c.get(f"/api/estimates/{a[0]['id']}").json()
    r = c.post(f"/api/estimates/{d['id']}/chat", json={"message": "数量を200に", "version": d["version"]})
    print(r.status_code, r.json()["inputs"]["quantity"], r.json()["chat"][-1]["content"][:60])
sys.exit(1 if bugs else 0)
