"""帳票（見積書・納品書・請求書）を同時に発行する負荷試験（docs/09_テスト報告書.md の 5.4）。

8件の見積で、見積書を2回ずつ・受注・納品書・請求書を、それぞれ同時に発行し、エラーと番号の重複を数える。

    python scripts/verify/stress_documents.py [--root 別の作業コピー]
"""
import os, sys, time, shutil, tempfile, threading, collections
from pathlib import Path
DATA = Path(__file__).resolve().parents[2]
ROOT = Path(sys.argv[sys.argv.index("--root") + 1]).resolve() if "--root" in sys.argv else DATA
sys.path.insert(0, str(ROOT)); os.chdir(ROOT)
from fastapi.testclient import TestClient
from api.main import create_app
from src.services.settings import Settings
tmp = Path(tempfile.mkdtemp()); hist = tmp / "h.csv"; shutil.copy(ROOT / "data/past_quotes/history.csv", hist)
app = create_app(Settings(data_dir=ROOT / "data", storage_dir=tmp / "s", history_path=hist, quote_log_path=tmp / "l.csv"))
def wait(c, j):
    while (r := c.get(f"/api/jobs/{j}").json())["status"] not in ("done", "failed"): time.sleep(0.05)
res = collections.Counter(); errs = []
with TestClient(app, raise_server_exceptions=False) as c:
    fid = c.post("/api/files", files={"file": ("a.step", (DATA / "pdf_data/step/Lv4_0036.step").read_bytes())}).json()["file_id"]
    reg = c.post("/api/drawings/register", json={"items": [{"drawing_no": "D", "shape_file_id": fid}]}).json()[0]; wait(c, reg["job_id"])
    st = {s["name"]: s["id"] for s in c.get("/api/statuses").json()}
    ids = []
    for i in range(8):
        e = c.post("/api/estimates", json={"drawing_id": reg["drawing_id"], "customer": f"顧客{i}", "quantity": 5, "material": "SPCC"}).json(); wait(c, e["job_id"])
        ids.append(e["estimate_id"])
    def go(fn):
        ts = [threading.Thread(target=fn, args=(q,)) for q in ids]; [t.start() for t in ts]; [t.join() for t in ts]
    def issue(kind):
        def f(q):
            r = c.post(f"/api/estimates/{q}/documents", json={"kind": kind}); res[f"{kind} {r.status_code}"] += 1
            if r.status_code >= 500: errs.append(r.text[:120])
        return f
    go(issue("quote")); go(issue("quote"))  # every quotation twice: Q-…, then Q-…-2
    def won(q):
        d = c.get(f"/api/estimates/{q}").json()
        r = c.put(f"/api/cases/{d['case_id']}/status", json={"status_id": st["受注"], "version": d["case_version"]}); res[f"won {r.status_code}"] += 1
    go(won); go(issue("delivery")); go(issue("invoice"))
    nums = [d["number"] for d in c.get("/api/issued?limit=500").json()]
    print(dict(res)); print("documents", len(nums), "unique numbers", len(set(nums)), sorted(n for n in nums if n.startswith(("D-", "I-")))[:4], [n for n in nums if n.endswith("-2")][:3])
    [print(" ", e) for e in errs[:5]]
    sys.exit(1 if errs or len(nums) != len(set(nums)) else 0)
