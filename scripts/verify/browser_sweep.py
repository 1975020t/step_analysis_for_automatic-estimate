"""画面のブラウザ巡回（docs/09_テスト報告書.md の 5.3）。お試しデータで起動した API（画面を配信）に対して、全画面を開き、
主な操作（条件修正・同時保存の拒否・カンバン・失注・設定の各画面・CSV取り込み・DXFの登録から見積まで）を行い、
画面のエラー（JavaScript）と失敗した通信（500）を記録する。

    python scripts/seed_demo.py --storage output/demo --fresh
    （ESTIMATE_STORAGE_DIR=output/demo PAST_QUOTES_PATH=output/demo/history.csv DRAWING_READER=recorded:output/demo/readings.json で）
    uvicorn api.main:app --port 8000
    python scripts/verify/browser_sweep.py [http://127.0.0.1:8000]
"""
import json, os, sys, urllib.request, traceback
from pathlib import Path
os.chdir(Path(__file__).resolve().parents[2])
from playwright.sync_api import sync_playwright, expect
base = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
OUT = str(Path("output") / "browser_sweep_")
api = lambda p: json.load(urllib.request.urlopen(base + p))
problems = []
def step(name, fn):
    try:
        fn(); print("ok ", name)
    except Exception as e:
        problems.append(f"{name}: {str(e).splitlines()[0][:200]}"); print("NG ", name, str(e).splitlines()[0][:200])
        pg.screenshot(path=OUT + name.replace(" ", "_").replace("/", "_") + ".png", full_page=True)
with sync_playwright() as p:
    chrome = Path("/opt/pw-browsers/chromium-1194/chrome-linux/chrome")
    b = p.chromium.launch(executable_path=str(chrome)) if chrome.exists() else p.chromium.launch()
    pg = b.new_page(viewport={"width": 1440, "height": 950}, locale="ja-JP")
    pg.on("pageerror", lambda e: problems.append(f"JS error on {pg.url}: {e}"))
    pg.on("console", lambda m: m.type == "error" and problems.append(f"console error on {pg.url}: {m.text[:160]}"))
    pg.on("response", lambda r: r.status >= 500 and problems.append(f"HTTP {r.status} {r.url}"))
    expect.set_options(timeout=20_000)
    pg.goto(base + "/settings/staff"); pg.evaluate("localStorage.setItem('actor','佐藤')")
    est = {e["drawing_no"]: e for e in api("/api/estimates")}
    drw = {d["drawing_no"]: d for d in api("/api/drawings")}
    for path in (["/", "/cases", "/cases?view=kanban", "/cases?phase=closed", "/drawings", "/drawings?mode=similar", "/drawings?mode=text",
                 "/drawings/register", "/estimates/new", f"/estimates/new?drawing={drw['DB-25-0367']['id']}", "/documents", "/review",
                 "/settings", "/settings/masters", "/settings/logic", "/settings/templates", "/settings/statuses", "/settings/categories",
                 "/settings/staff", "/settings/import", "/settings/partners", "/search?q=DB", "/estimates", "/partners", "/nowhere"]
                 + [f"/estimates/{e['id']}" for e in est.values()] + [f"/drawings/{d['id']}" for d in drw.values()]):
        step(f"open {path}", lambda: (pg.goto(base + path), pg.wait_for_load_state("networkidle")))
    q1 = est["DB-25-0367"]["id"]
    def tabs():
        pg.goto(f"{base}/estimates/{q1}")
        for t in ["条件修正", "CADデータ", "類似実績", "修正履歴", "金額内訳"]:
            pg.get_by_role("tab", name=t, exact=False).first.click(); pg.wait_for_timeout(200)
        pg.get_by_label("顧客提示モード").check(); expect(pg.get_by_test_id("cost-lines")).to_have_count(0); pg.get_by_label("顧客提示モード").uncheck()
    step("estimate tabs + customer mode", tabs)
    def chat():
        q = est["AB-22-3449"]["id"]; pg.goto(f"{base}/estimates/{q}")
        pg.get_by_label("チャット").fill("数量を7個に"); pg.get_by_role("button", name="送る").click()
        expect(pg.locator(".chat")).to_contain_text("数量を7個に"); assert api(f"/api/estimates/{q}")["inputs"]["quantity"] == 7
    step("chat changes quantity", chat)
    def conflict():
        q = est["AB-22-3449"]["id"]; pg.goto(f"{base}/estimates/{q}?tab=conditions")
        d = api(f"/api/estimates/{q}")
        req = urllib.request.Request(f"{base}/api/estimates/{q}", data=json.dumps({"version": d["version"], "inputs": {"quantity": 8}}).encode(), method="PUT", headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req)
        pg.get_by_label("数量", exact=True).fill("9"); pg.get_by_role("button", name="保存して計算し直す").click()
        expect(pg.get_by_role("status")).to_contain_text("ほかの人"); assert api(f"/api/estimates/{q}")["inputs"]["quantity"] == 8
    step("conflict is refused", conflict)
    def fill_missing():
        q = est["AB-22-3449"]["id"]; pg.goto(f"{base}/estimates/{q}")
        pg.get_by_test_id("missing").get_by_role("button", name="入力 →").first.click()
        pg.get_by_label("材質", exact=True).select_option("SPCC"); pg.get_by_role("button", name="保存して計算し直す").click()
        expect(pg.get_by_test_id("missing")).to_have_count(0); expect(pg.get_by_test_id("next-issue")).to_be_visible()
    step("fill missing material", fill_missing)
    def custom_material():
        q = est["KA421-2456"]["id"]; pg.goto(f"{base}/estimates/{q}?tab=conditions")
        pg.get_by_label("寸法 曲げ数").fill("2"); pg.get_by_role("button", name="保存して計算し直す").click()
        expect(pg.get_by_test_id("missing")).to_have_count(0)
        pg.get_by_label("材質", exact=True).select_option("__custom"); pg.get_by_label("材質の名前").fill("特殊鋼")
        pg.get_by_role("button", name="保存して計算し直す").click(); expect(pg.get_by_test_id("missing")).to_contain_text("特殊鋼")
        pg.get_by_label("kg単価").fill("300"); pg.get_by_label("密度").fill("7800"); pg.get_by_role("button", name="保存して計算し直す").click()
        expect(pg.get_by_test_id("missing")).to_have_count(0)
        pg.get_by_role("button", name="＋ マスタにない加工").click(); pg.get_by_label("未登録の加工の名前").fill("刻印")
        pg.get_by_role("button", name="保存して計算し直す").click(); expect(pg.get_by_test_id("missing")).to_contain_text("刻印")
    step("custom material/process", custom_material)
    def docs_switch():
        pg.goto(f"{base}/documents")
        pg.get_by_label("対象の案件").select_option(str(est["DB-25-0367"]["id"])); pg.wait_for_timeout(600)
        t1 = pg.get_by_label("テンプレート").input_value()
        pg.get_by_label("対象の案件").select_option(str(est["BR448-5321"]["id"])); pg.wait_for_timeout(600)
        print("   templates", t1, pg.get_by_label("テンプレート").input_value())
        pg.get_by_role("radio", name="納品書").check(); expect(pg.get_by_test_id("not-issuable")).to_be_visible()
    step("documents switching", docs_switch)
    def kanban():
        pg.goto(f"{base}/cases?view=kanban")
        card = pg.locator("[data-status='見積前'] .kcard").first
        card.drag_to(pg.locator("[data-status='見積作成中']"))
        expect(pg.get_by_role("status")).to_contain_text("見積作成中")
    step("kanban drag", kanban)
    def list_lost():
        pg.goto(f"{base}/cases")
        row = pg.get_by_test_id("case-Q-2026-0002")
        row.get_by_label("ステータス").select_option(label="失注")
        pg.get_by_label("失注の理由").select_option("納期"); pg.get_by_role("dialog").get_by_role("button", name="失注を登録").click()
        expect(pg.get_by_role("status")).to_contain_text("失注")
    step("list lost with reason", list_lost)
    def staff():
        pg.goto(f"{base}/settings/staff"); pg.wait_for_timeout(500)
        pg.screenshot(path=OUT + "staff.png")
    step("staff page", staff)
    def masters():
        pg.goto(f"{base}/settings/masters?tab=pricing_policy"); pg.get_by_role("button", name="編集").first.click()
        pg.get_by_label("値").fill("30"); pg.get_by_role("button", name="保存", exact=True).click(); expect(pg.get_by_role("status")).to_contain_text("保存")
        assert api("/api/meta")["policy"]["margin_rate"] == 0.3, api("/api/meta")["policy"]
        pg.get_by_role("button", name="編集").first.click(); pg.get_by_label("値").fill("25"); pg.get_by_role("button", name="保存", exact=True).click()
        expect(pg.get_by_role("status")).to_contain_text("保存")
    step("masters edit rate", masters)
    def drawing_detail():
        pg.goto(f"{base}/drawings/{drw['DB-25-0367']['id']}")
        for t in ["改訂履歴", "使用先", "関連書類", "注意／不具合メモ", "図面情報"]:
            loc = pg.get_by_role("tab", name=t, exact=False)
            if loc.count(): loc.first.click(); pg.wait_for_timeout(150)
        pg.get_by_role("button", name="類似形状の実績").click()
    step("drawing detail tabs", drawing_detail)
    def search_add():
        pg.goto(f"{base}/search?add=1"); expect(pg.get_by_role("dialog")).to_be_visible()
    step("search add", search_add)
    def home_links():
        pg.goto(base + "/")
        for i in range(pg.get_by_test_id("todo").get_by_role("link").count()):
            pg.goto(base + "/"); pg.get_by_test_id("todo").get_by_role("link").nth(i).click(); pg.wait_for_load_state("networkidle")
            assert "/estimates/" in pg.url or "/documents" in pg.url, pg.url
    step("home todo links", home_links)
    def statuses_add():
        pg.goto(f"{base}/settings/statuses"); pg.get_by_role("button", name="＋ ステータスを追加").click()
        pg.get_by_role("button", name="変更を保存").click(); expect(pg.get_by_role("alert")).to_contain_text("名前")
        pg.get_by_label("ステータス名").last.fill("外注中"); pg.get_by_label("フェーズ").last.select_option("production")
        pg.get_by_role("button", name="変更を保存").click(); expect(pg.get_by_role("status")).to_contain_text("保存")
        assert any(s["name"] == "外注中" and s["phase"] == "production" for s in api("/api/statuses"))
    step("statuses add (settings)", statuses_add)
    def staff_swap():
        pg.goto(f"{base}/settings/staff"); pg.get_by_label("担当者名").first.wait_for(); n = pg.get_by_label("担当者名").count()
        names = [pg.get_by_label("担当者名").nth(i).input_value() for i in range(n)]
        pg.get_by_label("担当者名").nth(0).fill(names[1]); pg.get_by_label("担当者名").nth(1).fill(names[0])
        pg.get_by_role("button", name="保存", exact=True).click(); expect(pg.get_by_role("status")).to_contain_text("保存")
        got = [s["name"] for s in api("/api/staff") if s["active"]][:2]; assert got == [names[1], names[0]], got
        pg.get_by_role("button", name="＋ 担当者を追加").click(); pg.get_by_label("担当者名").last.fill("田中")
        pg.get_by_role("button", name="保存", exact=True).click(); expect(pg.get_by_role("status")).to_contain_text("保存")
    step("staff swap + add", staff_swap)
    def templates():
        pg.goto(f"{base}/settings/templates"); pg.get_by_role("button", name="＋ テンプレートを追加").click(); pg.wait_for_timeout(500)
        pg.get_by_role("button", name="保存", exact=True).first.click(); pg.wait_for_timeout(800)
    step("templates add", templates)
    def categories():
        pg.goto(f"{base}/settings/categories"); pg.get_by_role("button", name="＋ 項目を追加").click(); pg.get_by_label("項目名").last.fill("板取り数")
        pg.get_by_label("入力の種類").last.select_option("number_range"); pg.get_by_role("button", name="項目を保存").click()
        pg.wait_for_timeout(800); assert any(a["label"] == "板取り数" for a in api("/api/attributes"))
    step("attributes add", categories)
    def partners():
        pg.goto(f"{base}/settings/partners"); pg.get_by_role("button", name="＋ 取引先を追加").click()
        pg.get_by_label("取引先名").fill("協力会社F"); pg.get_by_role("dialog").get_by_role("button", name="保存").click()
        expect(pg.get_by_role("status")).to_contain_text("保存")
    step("partners add", partners)
    def import_csv():
        pg.goto(f"{base}/settings/import")
        csv = "見積番号,見積日,顧客名,図番,数量,単価,結果\nZ-9,2026/09/01,顧客Z,ZZ-9,10,1000,受注\n"
        pg.get_by_label("CSVファイル").set_input_files({"name": "a.csv", "mimeType": "text/csv", "buffer": csv.encode()})
        pg.get_by_role("button", name="取り込む").click(); expect(pg.get_by_role("status")).to_contain_text("取り込みました")
    step("import csv", import_csv)
    def register():
        pg.goto(f"{base}/drawings/register")
        pg.get_by_test_id("file-input").set_input_files(["dxf_data/D0/Lv0_0002_D0.dxf"])
        pg.get_by_label("板厚").fill("2"); pg.get_by_label("品名").fill("DXF部品")
        pg.get_by_role("button", name="確認済み 1 件を登録").click(); expect(pg.get_by_role("link", name="新規見積 →")).to_be_visible()
        pg.get_by_role("link", name="新規見積 →").click(); pg.get_by_label("顧客").fill("顧客D"); pg.get_by_label("数量").fill("5")
        expect(pg.get_by_label("板厚")).to_have_value("2")
        pg.get_by_role("button", name="解析・計算を実行 →").click()
        pg.wait_for_function("/^\\/estimates\\/\\d+$/.test(location.pathname)", timeout=120_000)
        pg.wait_for_timeout(1000); pg.screenshot(path=OUT + "dxf_result.png", full_page=True)
    step("register DXF then estimate", register)
    print("\n== PROBLEMS ==")
    for x in problems: print(x)
    b.close()
    expected = ("status of 409", "status of 422")  # the refused conflicting save and the blank status name are checked on purpose
    sys.exit(1 if [x for x in problems if not any(e in x for e in expected)] else 0)
