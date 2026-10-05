"""Fill a storage folder with demo data through the API (fictional customers; drawings and shapes from the
development data pdf_data / dxf_data). No Claude API call: the one drawing reading is the recorded one
(docs/scripts/recorded_reading.json); the other drawings are registered with their conditions typed in.

    python scripts/seed_demo.py --storage output/demo
    ESTIMATE_STORAGE_DIR=output/demo DRAWING_READER=recorded:output/demo/readings.json uvicorn api.main:app

Used for the screenshots of the documents (docs/scripts/capture_screens.py). Dates are moved back a little so
that the progress warnings (納期超過, 回答待ち, 更新なし) show.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import shutil
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PDF, STEP, DXF = ROOT / "pdf_data" / "pdf", ROOT / "pdf_data" / "step", ROOT / "dxf_data"
RECORDED = ROOT / "docs" / "scripts" / "recorded_reading.json"

# (pdf stem or None, shape path or None, drawing no, rev, name, customer, material, quantity, finish, plan)
DRAWINGS = [
    ("Lv3_0043", STEP / "Lv3_0043.step", "DB-25-0367", "A", "ベースブラケット", "株式会社サンプル機工", None, 50, None, "won"),
    ("Lv3_0027", STEP / "Lv3_0027.step", "BR448-5321", "1", "取付ブラケット", "サンプル電機株式会社", "SECC", 20, "NONE", "issued"),
    ("Lv1_0009", STEP / "Lv1_0009.step", "BP475-8564", "C", "制御盤パネル", "サンプルロボティクス株式会社", "SPHC", 10, "POWDER_COAT", "checking"),
    ("Lv2_0041", STEP / "Lv2_0041.step", "AB-22-3449", "", "カバー", "株式会社サンプル計測", None, 3, "NONE", "drafting"),
    ("Lv4_0036", STEP / "Lv4_0036.step", "DS979-4363", "A", "ステー", "サンプル通信株式会社", "SPHC", 20, "NONE", "lost"),
    ("Lv0_0046", STEP / "Lv0_0046.step", "A61877-17", "", "放熱板", "サンプル医療機器株式会社", "AL5052", 500, "ANODIZE_BLACK", "production"),
    ("Lv3_0050", STEP / "Lv3_0050.step", "DX269-1807", "", "ダクトカバー", "サンプル住設株式会社", "SUS304", 50, "POWDER_COAT", "request"),
    (None, DXF / "D1" / "Lv2_0013_D1.dxf", "FL-0930", "A", "固定プレート", "有限会社サンプル製作所", "SPCC", 30, "ZINC_CLEAR", "before"),
    ("Lv1_0032", None, "KA421-2456", "", "スペーサー", "株式会社サンプル機工", "SPCC", 100, "NONE", "drafting"),
]
STATUS_OF = {"won": "製造中", "issued": "見積提出済", "checking": "見積確認中", "drafting": "見積作成中", "lost": "失注",
             "production": "製造準備", "request": "見積依頼受付", "before": "見積前"}


def readings_file(storage: Path) -> Path:
    path = storage / "readings.json"
    reading = {k: v for k, v in json.loads(RECORDED.read_text(encoding="utf-8")).items() if not k.startswith("_")}
    path.write_text(json.dumps({"readings": {"Lv3_0043": reading}}, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def wait(client, job_id: str) -> dict:
    for _ in range(1200):
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in ("done", "failed"):
            return job
        time.sleep(0.1)
    raise RuntimeError("job did not finish")


def ok(response, status=(200, 201, 202)):
    if response.status_code not in status:
        raise RuntimeError(f"{response.request.method} {response.request.url}: {response.status_code} {response.text}")
    return response.json()


def document_files() -> list[tuple[str, bytes, str, str]]:
    """Two small searchable documents (fictional): an Excel daily report and a PDF in-house standard."""
    from openpyxl import Workbook
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    from src.quote_pdf import FONT, register_font

    book = Workbook()
    sheet = book.active
    sheet.append(["日付", "図番", "作業", "実績"])
    sheet.append(["2026/09/14", "DB-25-0367", "曲げ 5か所・TIG溶接", "段取り 0.8h"])
    sheet.append(["2026/09/15", "BR448-5321", "レーザー切断・ピアス", "加工 0.4h"])
    xlsx = io.BytesIO()
    book.save(xlsx)
    register_font()
    pdf = io.BytesIO()
    c = canvas.Canvas(pdf, pagesize=A4, invariant=1)
    c.setFont(FONT, 14)
    c.drawString(60, 780, "社内規格 KS-12 板金の曲げR")
    c.setFont(FONT, 10)
    for i, line in enumerate(["SPCC・SPHC t1.6以下：内R＝板厚", "SUS304：内R＝板厚×1.5（割れ防止）",
                              "A5052：内R＝板厚×2", "TIG溶接後の歪み取りは別工程とする"]):
        c.drawString(60, 750 - i * 18, line)
    c.showPage()
    c.save()
    return [("作業日報_2026-09.xlsx", xlsx.getvalue(), "作業日報", "作業日報 2026年9月 板金課"),
            ("社内規格_KS-12_曲げR.pdf", pdf.getvalue(), "社内規格", "社内規格 KS-12 板金の曲げR")]


def seed_demo(client, platform, actor: str = "佐藤") -> dict:
    headers = {"X-Actor": "%E4%BD%90%E8%97%A4"}  # 佐藤
    meta = ok(client.get("/api/meta"))
    status = {s["name"]: s for s in meta["statuses"]}
    created = []
    for i, (pdf, shape, number, rev, name, customer, material, qty, finish, plan) in enumerate(DRAWINGS):
        item = {"drawing_no": number, "revision": rev, "name": name, "customer": customer}
        if pdf:
            up = ok(client.post("/api/files", files={"file": (f"{pdf}.pdf", (PDF / f"{pdf}.pdf").read_bytes())}))
            item["pdf_file_id"] = up["file_id"]
            if pdf == "Lv3_0043":
                job = ok(client.post("/api/drawings/readings", json={"file_id": up["file_id"]}))
                wait(client, job["job_id"])
                item["reading_job_id"] = job["job_id"]
        if shape:
            up = ok(client.post("/api/files", files={"file": (shape.name, shape.read_bytes())}))
            item["shape_file_id"] = up["file_id"]
            if shape.suffix == ".dxf":
                item["thickness_mm"] = 1.6
        if material:
            item["material"] = material
        if finish and finish != "NONE":
            item["surface_treatment"] = finish
        groups = {c["name"]: c for c in meta["categories"]}
        kinds = groups.get("部品種別", {}).get("children", [])
        if kinds:
            item["category_ids"] = [kinds[i % len(kinds)]["id"]]
        reg = ok(client.post("/api/drawings/register", json={"items": [item]}, headers=headers))[0]
        wait(client, reg["job_id"])
        created.append((reg, plan, material, qty, finish, customer, name))

    due = date.today()
    out = []
    for n, (reg, plan, material, qty, finish, customer, name) in enumerate(created):
        body = {"drawing_id": reg["drawing_id"], "customer": customer, "title": f"{name} 製作", "quantity": qty,
                "due_date": (due + timedelta(days=(-2 if plan == "request" else 2 if plan == "checking" else 12 + n * 3))).isoformat(),
                "staff": ["佐藤", "鈴木", "高橋"][n % 3]}
        if material and plan != "drafting":
            body["material"] = material
        if finish:
            body["surface_treatment"] = finish
        est = ok(client.post("/api/estimates", json=body, headers=headers))
        wait(client, est["job_id"])
        q = ok(client.get(f"/api/estimates/{est['estimate_id']}"))
        if plan == "won":  # the unregistered M10 tap: a unit price for this quote only
            cp = q["inputs"]["custom_processes"]
            for p in cp:
                p["unit_price"] = 120
                p["quantity"] = p["quantity"] or 6
            q = ok(client.put(f"/api/estimates/{q['id']}", json={"version": q["version"], "inputs": {"custom_processes": cp}}, headers=headers))
        if plan == "drafting" and q["drawing"] and q["drawing"]["drawing_no"] == "KA421-2456":
            q = ok(client.put(f"/api/estimates/{q['id']}", json={"version": q["version"], "inputs": {
                "shape": {"thickness_mm": 1.2, "blank_area_mm2": 5200, "cut_length_mm": 420, "hole_count": 4}}}, headers=headers))
        if plan in ("won", "issued", "lost", "production"):
            ok(client.post(f"/api/estimates/{q['id']}/documents", json={"kind": "quote"}, headers=headers))
        case = next(c for c in ok(client.get("/api/cases"))["cases"] if c["quote_id"] == q["id"])
        target = status[STATUS_OF[plan]]
        if plan in ("won", "production"):
            case = ok(client.put(f"/api/cases/{case['id']}/status", json={"status_id": status["受注"]["id"], "version": case["version"]}, headers=headers))
            if plan == "won":
                for kind in ("delivery", "invoice"):
                    ok(client.post(f"/api/estimates/{q['id']}/documents", json={"kind": kind}, headers=headers))
        case = next(c for c in ok(client.get("/api/cases"))["cases"] if c["quote_id"] == q["id"])
        if case["status"]["id"] != target["id"]:
            extra = {"lost_reason": "価格", "competitor_price": 21000} if plan == "lost" else {}
            ok(client.put(f"/api/cases/{case['id']}/status", json={"status_id": target["id"], "version": case["version"], **extra}, headers=headers))
        out.append(q["id"])

    for filename, data, kind, title in document_files():
        drawings = [d["id"] for d in ok(client.get("/api/drawings")) if d["drawing_no"] in ("DB-25-0367", "BR448-5321")]
        ok(client.post("/api/library", files={"file": (filename, data)},
                       data={"kind": kind, "title": title, "drawing_ids": json.dumps(drawings)}, headers=headers))
    age(platform)
    return {"estimates": out}


def age(platform) -> None:
    """Move the demo's timestamps back so the warnings show (answer waiting, no update for days)."""
    from sqlalchemy import select

    from src.db.models import Case, Drawing, Quote

    with platform.session() as s:
        for i, case in enumerate(s.scalars(select(Case).order_by(Case.id))):
            back = timedelta(days=[1, 7, 4, 1, 9, 5, 2, 6, 3][i % 9], hours=i)
            case.created_at -= back + timedelta(days=2)
            case.updated_at -= back
            case.status_changed_at -= back
        for i, quote in enumerate(s.scalars(select(Quote).order_by(Quote.id))):
            quote.updated_at -= timedelta(days=[1, 7, 4, 1, 9, 5, 2, 6, 3][i % 9])
        for i, drawing in enumerate(s.scalars(select(Drawing).order_by(Drawing.id))):
            drawing.created_at -= timedelta(days=20 - i)
        s.commit()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--storage", default="output/demo")
    parser.add_argument("--fresh", action="store_true", help="delete the storage folder first")
    args = parser.parse_args()
    storage = Path(args.storage)
    if args.fresh and storage.exists():
        shutil.rmtree(storage)
    storage.mkdir(parents=True, exist_ok=True)
    history = storage / "history.csv"
    if not history.exists():
        shutil.copyfile(ROOT / "data" / "past_quotes" / "history.csv", history)
    os.environ.update({"ESTIMATE_STORAGE_DIR": str(storage), "PAST_QUOTES_PATH": str(history),
                       "QUOTE_LOG_PATH": str(storage / "quote_log.csv")})
    os.environ.pop("DATABASE_URL", None) if not os.getenv("DATABASE_URL") else None
    from fastapi.testclient import TestClient

    from api.main import create_app
    from src.services.platform.core import RecordedReader
    from src.services.settings import Settings

    path = readings_file(storage)
    app = create_app(Settings.from_env(), reader_factory=lambda: RecordedReader(path))
    with TestClient(app) as client:
        result = seed_demo(client, app.state.platform)
    print(f"demo data in {storage}: {len(result['estimates'])} estimates")
    print(f"start:  ESTIMATE_STORAGE_DIR={storage} PAST_QUOTES_PATH={history} DRAWING_READER=recorded:{path} "
          "uvicorn api.main:app --port 8000")


if __name__ == "__main__":
    main()
