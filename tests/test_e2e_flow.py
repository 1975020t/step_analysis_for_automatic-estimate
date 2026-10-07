"""The main flow in a real browser (Playwright, Chromium), against the API serving the built screens (web/dist):
home -> new estimate (drawing PDF + STEP, registered on the way) -> conditions (the reading fills the draft) ->
analysis -> estimate result (next action: the missing unit price) -> issue the quotation -> the case changes status
on the board -> accepted -> delivery note and invoice -> the review.

Needs the built screens (cd web && npm ci && npm run build); skipped without them. No LLM: the drawing reader
replays the recorded reading of pdf_data/pdf/Lv3_0043.pdf (DRAWING_READER=recorded:...).
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "web" / "dist"
PDF, STEP = ROOT / "pdf_data" / "pdf" / "Lv3_0043.pdf", ROOT / "pdf_data" / "step" / "Lv3_0043.step"
CHROMIUM = [Path("/opt/pw-browsers/chromium-1194/chrome-linux/chrome")]

pytestmark = pytest.mark.skipif(not (DIST / "index.html").exists(), reason="web/dist がありません（cd web && npm run build）")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def server(tmp_path, _history_copy):
    recorded = json.loads((ROOT / "docs" / "scripts" / "recorded_reading.json").read_text(encoding="utf-8"))
    readings = tmp_path / "readings.json"
    readings.write_text(json.dumps({"readings": {"Lv3_0043": recorded}}, ensure_ascii=False), encoding="utf-8")
    port = free_port()
    env = {**os.environ, "ESTIMATE_STORAGE_DIR": str(tmp_path / "storage"), "PAST_QUOTES_PATH": str(_history_copy),
           "QUOTE_LOG_PATH": str(tmp_path / "quote_log.csv"), "DRAWING_READER": f"recorded:{readings}",
           "ANALYSIS_ANTHROPIC_API_KEY": "", "ANTHROPIC_API_KEY": "", "DATABASE_URL": ""}
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "api.main:app", "--port", str(port)], cwd=ROOT, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
    for _ in range(300):
        try:
            urllib.request.urlopen(base + "/api/health", timeout=1)
            break
        except OSError:
            time.sleep(0.2)
    yield base
    proc.terminate()
    proc.wait(timeout=20)


def api(base: str, path: str) -> dict:
    return json.load(urllib.request.urlopen(base + path))


def launch(p):
    try:
        return p.chromium.launch()
    except Exception:  # noqa: BLE001 - the pre-installed browser of the development container
        return p.chromium.launch(executable_path=str(next(c for c in CHROMIUM if c.exists())))


def test_register_estimate_issue_progress_accept_documents_review(server):
    from playwright.sync_api import expect, sync_playwright

    won_before = api(server, "/api/review?months=3")["current"]["won"]
    with sync_playwright() as p:
        browser = launch(p)
        page = browser.new_page(viewport={"width": 1440, "height": 950}, locale="ja-JP")
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        expect.set_options(timeout=60_000)

        # 1. home: the To Do list and the phases; the menu's main button starts a new estimate
        page.goto(server + "/")
        expect(page.get_by_role("heading", name="To Doリスト")).to_be_visible()
        expect(page.get_by_test_id("phases")).to_contain_text("見積作成中")
        page.get_by_role("link", name="＋ 新規見積").click()

        # 2. step 1: a new drawing PDF + STEP of the same name (registered as one drawing on the way)
        page.get_by_test_id("file-input").set_input_files([str(PDF), str(STEP)])
        expect(page.get_by_test_id("selected-files")).to_contain_text("Lv3_0043.step")
        page.get_by_role("button", name="次へ：条件入力 →").click()

        # 3. step 2: the reading fills the draft (marked 読取 / 要確認); the customer is entered by hand
        expect(page.get_by_label("図番", exact=True)).to_have_value("DB-25-0367")
        expect(page.get_by_label("数量")).to_have_value("50")
        expect(page.get_by_label("材質")).to_have_value("SPHC")
        expect(page.get_by_text("要確認：改訂欄の値と本文の値が違います")).to_be_visible()
        expect(page.get_by_test_id("draft-processes")).to_contain_text("M10タップ")
        page.get_by_label("品名").fill("ベースブラケット")
        page.get_by_label("顧客").fill("株式会社サンプル機工")
        page.get_by_label("希望納期").fill("2026-12-24")
        page.get_by_role("button", name="解析・計算を実行 →").click()

        # 4. step 3, then the result: the next action is the unregistered M10 tap's unit price
        page.wait_for_url("**/progress**")
        page.wait_for_function("/^\\/estimates\\/\\d+$/.test(location.pathname)", timeout=120_000)
        expect(page.get_by_test_id("missing")).to_contain_text("M10タップ")
        views = page.get_by_test_id("shape-views")  # 3D and the flat pattern, always shown
        expect(views.locator("canvas")).to_be_visible()
        expect(views.get_by_test_id("flat-pattern").locator("path").first).to_be_visible()
        expect(page.get_by_role("button", name="見積書の発行へ →")).to_be_disabled()
        page.get_by_test_id("missing").get_by_role("button", name="入力 →").click()
        page.get_by_label("未登録の加工の単価").fill("120")
        page.get_by_role("button", name="保存して計算し直す").click()
        expect(page.get_by_test_id("missing")).to_have_count(0)
        total = page.get_by_test_id("total").inner_text()
        assert total.startswith("¥")

        # 5. issue the quotation from the next action
        page.get_by_role("button", name="見積書の発行へ →").click()
        page.wait_for_url("**/documents**")
        expect(page.get_by_test_id("document-preview").locator("img")).to_be_visible()
        page.locator("main").get_by_role("button", name="見積書を発行").click()
        expect(page.get_by_role("status")).to_contain_text("を発行しました")
        expect(page.get_by_test_id("issued")).to_contain_text("Q-2026-0001")
        quote = api(server, f"/api/estimates/{api(server, '/api/estimates')[0]['id']}")
        assert total == f"¥{quote['result']['price']['subtotal']:,}"  # the screen shows the API's amount

        # 6. the case is on the board (見積提出済, phase 回答待ち); → moves it to 受注
        page.goto(server + "/cases?view=kanban")
        card = page.locator("[data-status='見積提出済'] .kcard")
        expect(card).to_have_count(1)
        expect(card).to_contain_text("受注・失注の登録")
        card.get_by_role("button", name="次のステータスへ").click()
        expect(page.locator("[data-status='受注'] .kcard")).to_have_count(1)

        # 7. delivery note and invoice with the same amount (the next action of the accepted estimate)
        quote_id = api(server, "/api/estimates")[0]["id"]
        page.goto(f"{server}/estimates/{quote_id}")
        page.get_by_role("button", name="納品書の発行へ →").click()
        for label in ("納品書", "請求書"):
            page.get_by_role("radio", name=label).check()
            expect(page.get_by_test_id("not-issuable")).to_have_count(0)
            page.locator("main").get_by_role("button", name=f"{label}を発行").click()
            expect(page.get_by_role("status")).to_contain_text(f"{label}")
        issued = api(server, "/api/issued")
        assert sorted(d["kind"] for d in issued) == ["delivery", "invoice", "quote"]
        assert {d["total"] for d in issued} == {quote["result"]["price"]["total"]}  # screen = API = documents

        # 8. the review (実績分析) counts the accepted quote
        page.goto(server + "/review")
        page.get_by_role("tab", name="直近3か月").click()
        expect(page.get_by_test_id("review-figures")).to_contain_text(f"受注 {won_before + 1}件")
        assert not errors, errors
        browser.close()
