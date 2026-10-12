"""CADデータの解析を複数のスレッドで同時に動かし、3D表示の変換とも並べて、結果が1回ずつの解析と同じかを確かめる
（docs/09_テスト報告書.md の 5.4）。

    python scripts/verify/analysis_threads.py [1スレッドの回数 12] [スレッド数 6]
"""
import os, sys, threading, collections
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]; sys.path.insert(0, str(ROOT)); os.chdir(ROOT)
from src.services.estimate import EstimateService
from src.services.gltf import step_to_glb
files = sorted(Path("pdf_data/step").glob("*.step"))[:40]
res = collections.Counter(); lock = threading.Lock(); bad = []
base = {}
for p in files[:12]:
    a = EstimateService.analyze_bytes(p.read_bytes(), p.name, None); base[p.name] = (a.status, a.hole_count, a.bend_count, round(a.blank_area_mm2 or 0))
def work(i, n, glb):
    for j in range(n):
        p = files[(i * 3 + j) % 12]
        try:
            if glb and j % 2:
                step_to_glb(p); key = "glb ok"
            else:
                a = EstimateService.analyze_bytes(p.read_bytes(), p.name, None)
                got = (a.status, a.hole_count, a.bend_count, round(a.blank_area_mm2 or 0))
                key = "same" if got == base[p.name] else "DIFF"
                if key == "DIFF": bad.append((p.name, base[p.name], got, a.reason_code, a.message))
        except Exception as e:
            key = f"EXC {type(e).__name__}"; bad.append((p.name, repr(e)[:150]))
        with lock: res[key] += 1
ts = [threading.Thread(target=work, args=(i, (int(sys.argv[1]) if len(sys.argv) > 1 else 12), i % 2 == 0)) for i in range((int(sys.argv[2]) if len(sys.argv) > 2 else 6))]
[t.start() for t in ts]; [t.join() for t in ts]
print(dict(res)); [print(b) for b in bad[:10]]
sys.exit(1 if bad else 0)
