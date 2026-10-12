"""見積の作成を同時に何度も流す負荷試験（docs/09_テスト報告書.md の 5.4）。

図面の登録（図面PDF＋STEP か STEP）→ すぐに見積の作成、を並行して N 回流し、受付の失敗・サーバーエラー・
解析不可を数える。比べたい別の作業コピーがあれば --root で指定する（例：修正前のコード）。LLM は呼ばない。

    python scripts/verify/stress_estimates.py [件数 40] [同時数 4] [--root 別の作業コピー] [--traceback]
"""
import os, sys, time, shutil, tempfile, threading, collections, json
from pathlib import Path
DATA = Path(__file__).resolve().parents[2]
args = [a for a in sys.argv[1:] if a.isdigit()]
ROOT = Path(sys.argv[sys.argv.index("--root") + 1]).resolve() if "--root" in sys.argv else DATA
sys.path.insert(0, str(ROOT)); os.chdir(ROOT)
from fastapi.testclient import TestClient
from api.main import create_app
from src.services.settings import Settings
from src.services.platform.core import RecordedReader
tmp = Path(tempfile.mkdtemp()); hist = tmp / "h.csv"; shutil.copy(ROOT / "data/past_quotes/history.csv", hist)
app = create_app(Settings(data_dir=ROOT / "data", storage_dir=tmp / "s", history_path=hist, quote_log_path=tmp / "l.csv"),
                 reader_factory=lambda: RecordedReader(DATA / "docs/scripts/recorded_reading.json"))
res = collections.Counter(); errs = []; lock = threading.Lock()
def rec(k, detail=None):
    with lock:
        res[k] += 1
        if detail and len(errs) < 12: errs.append(detail)
def wait(c, j):
    for _ in range(3000):
        r = c.get(f"/api/jobs/{j}").json()
        if r["status"] in ("done", "failed"): return r
        time.sleep(0.05)
    return {"status": "timeout"}
STEPS = ["Lv3_0043", "Lv4_0036", "Lv2_0041", "Lv1_0009"]
def flow(c, i):
    stem = STEPS[i % 4]
    item = {"drawing_no": f"S-{i}", "customer": "顧客", "shape_file_id": c.post("/api/files", files={"file": (f"{stem}.step", (DATA / f"pdf_data/step/{stem}.step").read_bytes())}).json()["file_id"]}
    if stem == "Lv3_0043":
        item["pdf_file_id"] = c.post("/api/files", files={"file": (f"{stem}.pdf", (DATA / f"pdf_data/pdf/{stem}.pdf").read_bytes())}).json()["file_id"]
    r = c.post("/api/drawings/register", json={"items": [item]})
    if r.status_code != 202: return rec(f"register HTTP {r.status_code}", r.text[:150])
    reg = r.json()[0]
    # like clicking 「この図面で見積を作成」 right after registering (no waiting for the registration job)
    r = c.post("/api/estimates", json={"drawing_id": reg["drawing_id"], "customer": "顧客", "quantity": 10, "material": "SPCC"})
    if r.status_code != 202: return rec(f"estimate HTTP {r.status_code}", r.text[:150])
    j = wait(c, r.json()["job_id"])
    if j["status"] != "done": return rec(f"estimate job {j['status']}", f"{stem}: {j.get('error')}")
    d = c.get(f"/api/estimates/{r.json()['estimate_id']}")
    if d.status_code != 200: return rec(f"detail HTTP {d.status_code}", d.text[:150])
    d = d.json()
    state = (d.get("analysis") or {}).get("state")
    if state == "解析不可": return rec("analysis failed", f"{stem}: {(d.get('analysis') or {}).get('failure')}")
    if not d["result"]["issuable"]: return rec("not issuable", f"{stem}: {[m['message'] for m in d['result']['missing']]}")
    rec("ok")
n, par = (int(args[0]) if args else 40), (int(args[1]) if len(args) > 1 else 4)
with TestClient(app, raise_server_exceptions="--traceback" in sys.argv) as c:
    idx = iter(range(n)); il = threading.Lock()
    def worker():
        while True:
            with il:
                i = next(idx, None)
            if i is None: return
            try: flow(c, i)
            except Exception as e: rec("EXC", repr(e)[:150])
    ts = [threading.Thread(target=worker) for _ in range(par)]; [t.start() for t in ts]; [t.join() for t in ts]
print(dict(res)); [print(" ", e) for e in errs]
sys.exit(1 if set(res) - {"ok", "not issuable"} else 0)
