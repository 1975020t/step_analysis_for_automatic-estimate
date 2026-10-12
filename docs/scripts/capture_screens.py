"""Photograph the real screens for the documents (docs/images/screens/). No LLM API.

    cd web && npm ci && npm run build && cd ..
    python docs/scripts/capture_screens.py [--api-only]

Fills a temporary storage with the demo data (scripts/seed_demo.py: fictional customers, drawings and shapes
of the development data, the recorded reading of pdf_data/pdf/Lv3_0043.pdf), starts the API serving the built
screens (web/dist) on it, and photographs every screen with Playwright (Chromium). Also photographs the API
documentation (Swagger UI) and renders the example documents (quotation, delivery note, invoice). The committed
history and the masters are not changed (the demo works on copies).
"""
from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pypdfium2 as pdfium
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "docs" / "images" / "screens"
CHROMIUM = Path("/opt/pw-browsers/chromium-1194/chrome-linux/chrome")
VIEW = {"width": 1440, "height": 900}


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_http(port: int, path: str = "/", timeout: float = 90) -> None:
    import urllib.request

    end = time.time() + timeout
    while time.time() < end:
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=2)
            return
        except Exception:  # noqa: BLE001
            time.sleep(0.5)
    raise RuntimeError(f"server on {port} did not start")


def api(port: int, path: str):
    import json
    import urllib.request

    return json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}{path}"))


def shot(page, name: str, max_height: int = 1900) -> None:
    page.wait_for_timeout(1800)
    page.evaluate("document.activeElement && document.activeElement.blur(); window.scrollTo(0, 0)")
    page.wait_for_timeout(200)
    height = min(max_height, page.evaluate("document.documentElement.scrollHeight"))
    page.screenshot(path=str(OUT / name), clip={"x": 0, "y": 0, "width": VIEW["width"], "height": height}, full_page=True)
    print("saved", name)


def loaded(page) -> None:
    """Wait until the 3D view (and the rest of the page) has finished loading."""
    page.wait_for_function("!document.querySelector('main').innerText.includes('読み込み中')", timeout=60_000)


def run_screens(browser, port: int) -> None:
    base = f"http://127.0.0.1:{port}"
    page = browser.new_page(viewport=VIEW, locale="ja-JP")
    page.goto(base + "/settings/staff")
    page.evaluate("localStorage.setItem('actor', '佐藤')")  # the operator of this PC (shown at the bottom of the menu)
    estimates = api(port, "/api/estimates")
    drawings = api(port, "/api/drawings")
    by_no = {e["drawing_no"]: e for e in estimates}
    main = by_no["DB-25-0367"]
    drawing = next(d for d in drawings if d["drawing_no"] == "DB-25-0367")
    files = [str(ROOT / "pdf_data" / "pdf" / "Lv3_0043.pdf"), str(ROOT / "pdf_data" / "step" / "Lv3_0043.step")]

    page.goto(base + "/")  # A home
    shot(page, "01_home.png", 900)

    page.goto(base + "/drawings/register")  # the drawing-only registration: the reading fills the draft
    page.get_by_test_id("file-input").set_input_files(files)
    page.get_by_text("読み取り結果を下書きに入れました").wait_for(timeout=60_000)
    page.get_by_label("品名").fill("ベースブラケット")
    page.get_by_label("顧客").fill("株式会社サンプル機工")
    page.get_by_label("改訂").fill("B")
    shot(page, "02_register.png", 900)

    page.goto(base + "/estimates/new")  # B step 1: a drawing PDF + STEP of a new drawing
    page.get_by_test_id("file-input").set_input_files(files)
    page.get_by_test_id("selected-files").wait_for()
    shot(page, "03_new_estimate.png", 900)
    page.get_by_role("button", name="次へ：条件入力 →").click()  # C step 2: the reading fills the draft
    page.get_by_label("図番", exact=True).wait_for(timeout=60_000)
    page.wait_for_function("document.querySelector('[aria-label=図番]').value !== ''")
    page.get_by_label("品名").fill("ベースブラケット")
    page.get_by_label("顧客").fill("株式会社サンプル機工")
    page.get_by_label("希望納期").fill("2026-11-20")
    page.get_by_label("担当").select_option("佐藤")
    shot(page, "03_new_estimate_conditions.png", 1300)

    page.goto(f"{base}/estimates/new?drawing={drawing['id']}")  # step 3: photographed while the stages run
    page.get_by_label("数量").wait_for()
    page.get_by_label("数量").fill("100")
    page.get_by_label("Kファクター").fill("0.4")  # a K factor of the company: the shape is analysed again
    page.get_by_role("button", name="解析・計算を実行 →").click()
    page.wait_for_url("**/progress**")
    page.wait_for_timeout(400)
    page.screenshot(path=str(OUT / "04_progress.png"))
    print("saved 04_progress.png")
    page.wait_for_function("/^\\/estimates\\/\\d+$/.test(location.pathname)", timeout=120_000)

    for name, path, height in (
            ("05_estimate.png", f"/estimates/{by_no['BP475-8564']['id']}", 1500),
            ("05_estimate_missing.png", f"/estimates/{by_no['AB-22-3449']['id']}", 1000),
            ("05_estimate_outcome.png", f"/estimates/{by_no['BR448-5321']['id']}", 820),
            ("05_estimate_conditions.png", f"/estimates/{main['id']}?tab=conditions", 1900),
            ("05_estimate_shape_input.png", f"/estimates/{by_no['KA421-2456']['id']}?tab=conditions", 1900),
            ("05_estimate_shape.png", f"/estimates/{main['id']}?tab=shape", 1250),
            ("06_cases.png", "/cases", 900), ("06_cases_kanban.png", "/cases?view=kanban", 900),
            ("07_drawings.png", "/drawings", 1300), ("08_drawing.png", f"/drawings/{drawing['id']}", 1500),
            ("12_categories.png", "/settings/categories", 1000), ("13_search.png", "/search?q=DB-25-0367", 900),
            ("14_review.png", "/review", 900), ("15_partners.png", "/settings/partners", 800),
            ("16_documents.png", f"/documents?quote={main['id']}&kind=invoice", 1500),
            ("16_documents_blocked.png", f"/documents?quote={by_no['AB-22-3449']['id']}&kind=quote", 900),
            ("17_masters.png", "/settings/masters", 800), ("18_logic.png", "/settings/logic", 800),
            ("19_templates.png", "/settings/templates", 1000), ("20_statuses.png", "/settings/statuses", 1200),
            ("21_staff.png", "/settings/staff", 700), ("22_import.png", "/settings/import", 700),
            ("23_settings.png", "/settings", 900)):
        page.goto(base + path)
        if name.startswith("05_"):
            loaded(page)
        if name == "05_estimate_shape_input.png":
            page.get_by_text("寸法の値（CADデータから").wait_for()
        shot(page, name, height)
    page.goto(f"{base}/estimates/{main['id']}")
    loaded(page)
    page.get_by_label("顧客提示モード").check()
    shot(page, "05_estimate_customer.png", 1250)
    page.close()


SWAGGER = ROOT / "docs" / "scripts" / "node_modules" / "swagger-ui-dist"


def serve_swagger_locally(route) -> None:
    """/docs loads Swagger UI from a CDN; answer those requests from the npm copy (cd docs/scripts && npm install)."""
    name = route.request.url.rsplit("/", 1)[-1].split("?")[0]
    path = SWAGGER / name
    if path.exists():
        kind = "text/css" if name.endswith(".css") else "application/javascript" if name.endswith(".js") else "image/png"
        route.fulfill(status=200, body=path.read_bytes(), headers={"Content-Type": kind})
    else:
        route.fulfill(status=204, body=b"")


def serve_bytes(body: bytes, kind: str):
    return lambda route: route.fulfill(status=200, body=body, headers={"Content-Type": kind})


UI_FONT_CSS = """@font-face { font-family: "Noto Sans JP"; src: url("/_docs_font/regular.woff2"); font-weight: 400; }
@font-face { font-family: "Noto Sans JP"; src: url("/_docs_font/bold.woff2"); font-weight: 700; }
body, .swagger-ui, .swagger-ui * { font-family: "Noto Sans JP", sans-serif !important; }"""


def run_api_docs(browser, port: int, app_root: Path) -> None:
    """Swagger UI has no Japanese font of its own: give it the demo's (static/fonts), as a Japanese PC would show
    Japanese glyphs rather than the Chinese fallback of this Linux machine."""
    page = browser.new_page(viewport={"width": 1300, "height": 2400})
    fonts = ROOT / "static" / "fonts"
    for name, file in (("regular", "NotoSansJP-Regular.woff2"), ("bold", "NotoSansJP-Bold.woff2")):
        if (fonts / file).exists():
            body = (fonts / file).read_bytes()
            page.route(f"**/_docs_font/{name}.woff2", serve_bytes(body, "font/woff2"))
    page.route("https://cdn.jsdelivr.net/**", serve_swagger_locally)
    page.route("https://fastapi.tiangolo.com/**", lambda route: route.fulfill(status=204, body=b""))
    page.goto(f"http://127.0.0.1:{port}/docs")
    page.get_by_text("/api/analyses").first.wait_for(timeout=60_000)
    if (fonts / "NotoSansJP-Regular.woff2").exists():
        page.add_style_tag(content=UI_FONT_CSS)
        page.evaluate("document.fonts.ready")
    page.wait_for_timeout(2000)
    page.screenshot(path=str(OUT / "20_api_docs.png"), clip={"x": 0, "y": 0, "width": 1300, "height": 1900})
    print("saved 20_api_docs.png")
    page.get_by_text("/api/quotes").first.click()
    page.wait_for_timeout(1500)
    a = page.get_by_text("/api/quotes").first.bounding_box()
    page.screenshot(path=str(OUT / "21_api_quote.png"), clip={"x": 0, "y": max(0, a["y"] - 20), "width": 1300, "height": 1100})
    print("saved 21_api_quote.png")
    page.close()


def render_documents(port: int) -> None:
    for name, src in (("30_quote_confirmed.png", "quote_document_example.pdf"),
                      ("31_quote_separate.png", "quote_document_example_separate.pdf"),
                      ("32_quote_internal.png", "quote_document_example_internal.pdf")):
        image = pdfium.PdfDocument(ROOT / "analysis" / src)[0].render(scale=1.6).to_pil()
        image.save(OUT / name, optimize=True)
        print("saved", name)
    import urllib.request

    issued = api(port, "/api/issued?limit=100")
    for kind, name in (("quote", "33_issued_quote.png"), ("delivery", "34_delivery.png"), ("invoice", "35_invoice.png")):
        doc = next(d for d in issued if d["kind"] == kind and d["drawing_no"] == "DB-25-0367")
        data = urllib.request.urlopen(f"http://127.0.0.1:{port}{doc['url']}").read()
        pdfium.PdfDocument(data)[0].render(scale=1.6).to_pil().save(OUT / name, optimize=True)
        print("saved", name)


def main() -> int:
    if not (ROOT / "web" / "dist" / "index.html").exists():
        print("web/dist がありません。先に cd web && npm ci && npm run build を実行してください。")
        return 1
    OUT.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="docshots_"))
    storage = work / "storage"
    env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "ANALYSIS_ANTHROPIC_API_KEY", "OPENAI_API_KEY",
                                                              "DATABASE_URL")}
    subprocess.run([sys.executable, str(ROOT / "scripts" / "seed_demo.py"), "--storage", str(storage), "--fresh"],
                   cwd=ROOT, env=env, check=True)
    env.update(ESTIMATE_STORAGE_DIR=str(storage), PAST_QUOTES_PATH=str(storage / "history.csv"),
               QUOTE_LOG_PATH=str(storage / "quote_log.csv"), DRAWING_READER=f"recorded:{storage / 'readings.json'}")
    port = free_port()
    server = subprocess.Popen([sys.executable, "-m", "uvicorn", "api.main:app", "--port", str(port)], cwd=ROOT, env=env,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        wait_http(port, "/api/health")
        with sync_playwright() as pw:
            browser = pw.chromium.launch(executable_path=str(CHROMIUM)) if CHROMIUM.exists() else pw.chromium.launch()
            if "--api-only" not in sys.argv:
                run_screens(browser, port)
            run_api_docs(browser, port, ROOT)
            browser.close()
        render_documents(port)
    finally:
        server.terminate()
        server.wait(timeout=30)
        shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
