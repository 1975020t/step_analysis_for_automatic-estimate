"""Photograph the real demo screens for the documents (docs/images/screens/). No LLM API.

    python docs/scripts/capture_screens.py [--app-ref origin/main] [--api-only]

The app is photographed from a checkout of --app-ref (default origin/main; a temporary git worktree), because this
documents branch carries no code. The input data and the outputs are those of this branch.
Starts the Streamlit demo (docs/scripts/demo_app.py: app.py with a recorded drawing reading) and the API on
temporary storage and a temporary copy of the quote history (the committed history is not changed), drives
them with Playwright (Chromium) using the development data (pdf_data, dxf_data) and a fictional recipient,
and saves the screenshots. Also renders the example quotation PDFs (analysis/quote_document_example*.pdf).
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
from PIL import Image
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "docs" / "images" / "screens"
CHROMIUM = Path("/opt/pw-browsers/chromium-1194/chrome-linux/chrome")
STEP = ROOT / "pdf_data" / "step" / "Lv1_0047.step"          # a part quoted before (repeat in the history)
DRAWING_STEP = ROOT / "pdf_data" / "step" / "Lv3_0043.step"  # the part of the recorded drawing
DRAWING_PDF = ROOT / "pdf_data" / "pdf" / "Lv3_0043.pdf"
DXF = ROOT / "dxf_data" / "D1" / "Lv2_0013_D1.dxf"
MAIN_X = (340, 1360)  # the main column of the 1400 px wide page (the sidebar is left of it)


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


class Shots:
    def __init__(self, page):
        self.page = page

    def box(self, text: str, exact: bool = True, nth: int = 0):
        loc = self.page.get_by_text(text, exact=exact)
        return loc.nth(nth).bounding_box()

    def between(self, name: str, start: str, end: str | None = None, pad_top: int = 24, extra: int = 0,
                full_width: bool = False, start_exact: bool = True, end_exact: bool = True) -> None:
        a = self.box(start, start_exact)
        b = self.box(end, end_exact) if end else None
        top = max(0, a["y"] - pad_top)
        bottom = (b["y"] - 8 if b else a["y"] + a["height"]) + extra
        x0, x1 = (0, 1400) if full_width else MAIN_X
        self.page.screenshot(path=str(OUT / name), clip={"x": x0, "y": top, "width": x1 - x0, "height": bottom - top})
        print("saved", name)

    def wait_text(self, text: str, timeout: float = 120_000, exact: bool = False) -> None:
        self.page.get_by_text(text, exact=exact).first.wait_for(timeout=timeout)
        self.page.wait_for_timeout(1500)

    def pick(self, label_index: int, text: str) -> None:
        box = self.page.locator('[data-testid="stSelectbox"]').nth(label_index).locator("input")
        box.first.click(force=True)
        self.page.wait_for_timeout(500)
        self.page.keyboard.type(text)
        self.page.wait_for_timeout(500)
        self.page.keyboard.press("Enter")
        self.page.wait_for_timeout(2500)

    def fill(self, label: str, value: str) -> None:
        self.page.get_by_label(label, exact=True).fill(value)
        self.page.keyboard.press("Tab")
        self.page.wait_for_timeout(2000)

    def number(self, label: str, value: str) -> None:
        field = self.page.locator(f'input[aria-label="{label}"]')
        field.fill(value)
        field.press("Enter")
        self.page.wait_for_timeout(2500)


def run_demo(browser, port: int) -> None:
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    s = Shots(page)
    url = f"http://127.0.0.1:{port}/"

    # 1. start screen
    page.goto(url)
    s.wait_text("解析を実行")
    page.screenshot(path=str(OUT / "01_start.png"))
    print("saved 01_start.png")

    # 2. STEP: analysis, quote, customer, similar quotes, quotation, outcome
    page.set_viewport_size({"width": 1400, "height": 7000})
    page.locator("input[type=file]").nth(0).set_input_files(str(STEP))
    page.wait_for_timeout(1500)
    page.get_by_text("指定済み加工条件として扱う").click()
    page.wait_for_timeout(800)
    s.between("02_step_upload.png", "STEP・DXF板金解析・見積デモ", "解析を実行", pad_top=10, extra=60, full_width=True)
    page.get_by_role("button", name="解析を実行").click()
    s.wait_text("ファイル:")
    page.wait_for_timeout(6000)  # 3D preview
    s.between("03_step_result.png", "ファイル:", "解析根拠と処理段階", start_exact=False, pad_top=6)
    page.get_by_text("解析根拠と処理段階").click()
    page.wait_for_timeout(1500)
    s.between("04_step_evidence.png", "解析根拠と処理段階", "ルールベース見積", pad_top=10)
    page.get_by_text("解析根拠と処理段階").click()
    page.wait_for_timeout(1000)
    s.pick(0, "SPCC")
    s.number("数量", "100")
    s.pick(1, "三価クロメート（有色）")
    s.between("05_quote.png", "ルールベース見積", "顧客と部品")
    s.fill("宛先の会社名（必須）", "サンプル電機株式会社")
    s.fill("部署・担当者名（任意）", "購買部　山田 太郎")
    s.fill("品名", "取付板")
    s.fill("図番", "CS649-3372")
    page.wait_for_timeout(2000)
    s.between("06_customer.png", "顧客と部品", "類似見積（参考）")
    s.between("07_similar.png", "類似見積（参考）", "見積書を出力")
    s.between("07b_similar_top.png", "類似見積（参考）", "2. 【リピート】", start_exact=True, end_exact=False)
    page.get_by_text("この見積の全項目").first.click()
    page.wait_for_timeout(1500)
    s.between("08_similar_detail.png", "1. 【リピート】", "2. 【リピート】", start_exact=False, end_exact=False)
    page.get_by_text("この見積の全項目").first.click()
    page.wait_for_timeout(1000)
    page.get_by_text("社内用の内訳も出力する（別PDF、社外秘）").click()
    page.wait_for_timeout(1500)
    page.get_by_role("button", name="見積書PDFを作成").click()
    s.wait_text("ダウンロード:")
    s.between("09_export.png", "見積書を出力", "受注・失注の記録", extra=10)
    page.get_by_text("受注・失注の記録").click()
    page.wait_for_timeout(1500)
    s.between("10_outcome.png", "受注・失注の記録", "解析結果 JSON", pad_top=10)

    # 3. DXF (thickness)
    page.goto(url)
    s.wait_text("解析を実行")
    page.locator("input[type=file]").nth(0).set_input_files(str(DXF))
    page.wait_for_timeout(2000)
    s.between("11_dxf_input.png", "STEP・DXF板金解析・見積デモ", "解析を実行", pad_top=10, extra=110, full_width=True)
    s.number("板厚（mm）", "1")  # the thickness of this part (dxf_data/index.json); the title block says the same
    page.get_by_role("button", name="解析を実行").click()
    s.wait_text("ファイル:")
    page.wait_for_timeout(3000)
    s.between("12_dxf_result.png", "ファイル:", "解析根拠と処理段階", start_exact=False, pad_top=6)

    # 4. drawing PDF (recorded reading) + STEP, and the estimate (概算) notice
    page.goto(url)
    s.wait_text("解析を実行")
    page.locator("input[type=file]").nth(0).set_input_files(str(DRAWING_STEP))
    page.locator("input[type=file]").nth(1).set_input_files(str(DRAWING_PDF))
    page.wait_for_timeout(2000)
    page.get_by_text("指定済み加工条件として扱う").click()
    page.wait_for_timeout(800)
    s.between("13_pdf_upload.png", "STEP・DXF板金解析・見積デモ", "解析を実行", pad_top=10, extra=60, full_width=True)
    page.get_by_role("button", name="解析を実行").click()
    s.wait_text("図面から読み取った加工条件")
    page.wait_for_timeout(5000)
    s.between("14_pdf_conditions.png", "ルールベース見積", "原価の内訳（社内用。見積書には出ません）", end_exact=False, extra=10)
    s.fill("宛先の会社名（必須）", "サンプル電機株式会社")
    s.between("15_estimate_notice.png", "見積書を出力", "見積書PDFを作成", extra=50)
    # the user confirms the surface treatment: the item becomes 確定
    page.get_by_text("この値で確定").nth(1).click()
    page.wait_for_timeout(3000)
    s.between("16_pdf_confirmed.png", "ルールベース見積", "原価の内訳（社内用。見積書には出ません）", end_exact=False, extra=10)
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
    fonts = app_root / "static" / "fonts"
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


def render_documents() -> None:
    for name, src in (("30_quote_confirmed.png", "quote_document_example.pdf"),
                      ("31_quote_estimate.png", "quote_document_example_estimate.pdf"),
                      ("32_quote_internal.png", "quote_document_example_internal.pdf")):
        image = pdfium.PdfDocument(ROOT / "analysis" / src)[0].render(scale=1.6).to_pil()
        image.save(OUT / name, optimize=True)
        print("saved", name)


def trim(path: Path) -> None:
    """Cut the empty right margin of the main-column shots (Streamlit centres the content)."""
    image = Image.open(path).convert("RGB")
    image.save(path, optimize=True)


def app_checkout(ref: str, work: Path) -> Path:
    """A temporary worktree of the app at `ref`, with the demo wrapper and the recorded reading in its root."""
    app = work / "app"
    subprocess.run(["git", "worktree", "add", "--quiet", "--detach", str(app), ref], cwd=ROOT, check=True,
                   stdout=subprocess.DEVNULL)
    shutil.copyfile(Path(__file__).with_name("demo_app.py"), app / "_docs_demo_app.py")
    return app


def main() -> int:
    ref = sys.argv[sys.argv.index("--app-ref") + 1] if "--app-ref" in sys.argv else "origin/main"
    OUT.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="docshots_"))
    app_root = app_checkout(ref, work)
    shutil.copyfile(ROOT / "data" / "past_quotes" / "history.csv", work / "history.csv")
    env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "ANALYSIS_ANTHROPIC_API_KEY", "OPENAI_API_KEY")}
    env.update(PAST_QUOTES_PATH=str(work / "history.csv"), ESTIMATE_STORAGE_DIR=str(work / "storage"),
               QUOTE_LOG_PATH=str(work / "quote_log.csv"), PYTHONPATH=str(app_root), DOCS_APP_ROOT=str(app_root),
               DOCS_RECORDED_READING=str(Path(__file__).with_name("recorded_reading.json")))
    demo_port, api_port = free_port(), free_port()
    demo = subprocess.Popen([sys.executable, "-m", "streamlit", "run", "_docs_demo_app.py", "--server.headless", "true",
                             "--server.port", str(demo_port), "--browser.gatherUsageStats", "false"],
                            cwd=app_root, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    api = subprocess.Popen([sys.executable, "-m", "uvicorn", "api.main:app", "--port", str(api_port)], cwd=app_root, env=env,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        wait_http(demo_port)
        wait_http(api_port, "/api/health")
        with sync_playwright() as pw:
            browser = pw.chromium.launch(executable_path=str(CHROMIUM)) if CHROMIUM.exists() else pw.chromium.launch()
            if "--api-only" not in sys.argv:
                run_demo(browser, demo_port)
            run_api_docs(browser, api_port, app_root)
            browser.close()
        render_documents()
    finally:
        demo.terminate()
        api.terminate()
        demo.wait(timeout=30)
        api.wait(timeout=30)
        subprocess.run(["git", "worktree", "remove", "--force", str(app_root)], cwd=ROOT, check=False)
        shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
