"""納品書の発行とステータスの遷移の確認（docs/09_テスト報告書.md の 5.2）。

1つの案件のステータスを順に変え、各段階で 画面に返す発行の可否（blockers）・プレビュー・実際の発行・次のアクション
が食い違わないかを見る。LLM は呼ばない。

    python scripts/verify/check_delivery_status.py
"""
import os, sys, time, shutil, tempfile
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]; sys.path.insert(0, str(ROOT)); os.chdir(ROOT)
from fastapi.testclient import TestClient
from api.main import create_app
from src.services.settings import Settings
tmp = Path(tempfile.mkdtemp()); hist = tmp / "history.csv"
shutil.copy(ROOT / "data/past_quotes/history.csv", hist)
app = create_app(Settings(data_dir=ROOT / "data", storage_dir=tmp / "s", history_path=hist, quote_log_path=tmp / "log.csv"))

def wait(c, j):
    while (r := c.get(f"/api/jobs/{j}").json())["status"] not in ("done", "failed"): time.sleep(0.1)
    return r

with TestClient(app) as c:
    fid = c.post("/api/files", files={"file": ("a.step", (ROOT / "pdf_data/step/Lv0_0002.step").read_bytes())}).json()["file_id"]
    reg = c.post("/api/drawings/register", json={"items": [{"drawing_no": "T-1", "shape_file_id": fid}]}).json()[0]; wait(c, reg["job_id"])
    e = c.post("/api/estimates", json={"drawing_id": reg["drawing_id"], "customer": "顧客A", "quantity": 10, "material": "SPCC"}).json(); wait(c, e["job_id"])
    qid = e["estimate_id"]
    st = {s["name"]: s for s in c.get("/api/statuses").json()}
    def q(): return c.get(f"/api/estimates/{qid}").json()
    def move(name, **extra):
        d = q(); r = c.put(f"/api/cases/{d['case_id']}/status", json={"status_id": st[name]["id"], "version": d["case_version"], **extra})
        assert r.status_code == 200, r.text
    def check(label, expect_ok, issue=False):
        d = q(); blk = d["blockers"]["delivery"]
        prev = c.get(f"/api/estimates/{qid}/documents/preview.png?kind=delivery").status_code
        res = ""
        if issue:
            r = c.post(f"/api/estimates/{qid}/documents", json={"kind": "delivery"}); res = r.status_code
            if r.status_code == 201: res = f"201 {r.json()['number']} 税込{r.json()['total']}"
        ok = (not blk) == expect_ok and (prev == 200) == expect_ok and (not issue or str(res).startswith("201") == expect_ok)
        print(f"{'OK ' if ok else 'NG '} {label:28} status={d['status']['name']:6} outcome={d['outcome']:4} 画面の理由={blk or '発行可能'} preview={prev} 発行={res} 次={d['next_action']['label']}")
        return ok
    results = []
    results.append(check("1 見積書未発行のまま", False, True))
    move("受注"); results.append(check("2 未発行のまま受注にした", False, True))
    move("見積作成中")
    r = c.post(f"/api/estimates/{qid}/documents", json={"kind": "quote"}); quote_total = r.json()["total"]; print("   見積書発行:", r.status_code, r.json()["number"], "税込", quote_total)
    results.append(check("3 見積書発行→自動で見積提出済", False, True))
    move("見積確認中"); results.append(check("4 見積確認中に戻した", False, True))
    move("受注"); results.append(check("5 受注", True, True))
    for name in ("製造準備", "製造中", "検査", "出荷待ち", "出荷済", "完了"):
        move(name); results.append(check(f"6 {name}", True))
    move("失注", lost_reason="価格"); results.append(check("7 失注", False, True))
    move("保留"); results.append(check("8 保留", False))
    move("受注"); results.append(check("9 受注に戻した", True))
    # user-added statuses
    rows = c.get("/api/statuses").json(); body = [{k: r[k] for k in ("id", "name", "color", "phase", "visible")} for r in rows]
    body += [{"name": "外注加工中", "color": "blue", "phase": "production", "visible": True}, {"name": "納品済", "color": "grey", "phase": "closed", "visible": True}]
    assert c.put("/api/statuses", json={"statuses": body}).status_code == 200
    st = {s["name"]: s for s in c.get("/api/statuses").json()}
    move("外注加工中"); results.append(check("10 追加ステータス(製造・出荷)", True))
    move("納品済"); results.append(check("11 追加ステータス(完了分・役割なし)", True))
    issued = [d for d in c.get("/api/issued").json() if d["quote_id"] == qid]
    print("発行済み:", [(d["kind"], d["number"], d["total"]) for d in issued], "見積書と同額:", {d["total"] for d in issued} == {quote_total})
    hist_row = [r for r in c.get("/api/history?limit=500").json()["items"] if r.get("customer") == "顧客A"]
    print("履歴の結果:", [r.get("outcome") for r in hist_row])
    print("RESULT", sum(results), "/", len(results))
    sys.exit(0 if all(results) else 1)
