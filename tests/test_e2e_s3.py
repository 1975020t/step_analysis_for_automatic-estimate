"""Files straight between the browser and S3, in a real browser (Playwright, Chromium): the screens send the drawing PDF
and the STEP to the bucket with the form the API hands out, and fetch a large file through the short-lived link the
API redirects to. No AWS: a moto server stands in for S3 (with the CORS rule the bucket needs, deploy/aws/README.md).
Needs the built screens (cd web && npm ci && npm run build); skipped without them.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request

import boto3
import pytest

from tests.test_e2e_flow import DIST, PDF, ROOT, STEP, free_port, launch

pytestmark = pytest.mark.skipif(not (DIST / "index.html").exists(), reason="web/dist がありません（cd web && npm run build）")

BUCKET = "estimate-e2e"
AWS_ENV = {"AWS_ACCESS_KEY_ID": "testing", "AWS_SECRET_ACCESS_KEY": "testing", "AWS_DEFAULT_REGION": "ap-northeast-1",
           "AWS_SESSION_TOKEN": "testing"}


@pytest.fixture
def s3_server():
    from moto.server import ThreadedMotoServer

    port = free_port()
    server = ThreadedMotoServer(ip_address="127.0.0.1", port=port, verbose=False)
    server.start()
    yield f"http://127.0.0.1:{port}"
    server.stop()


@pytest.fixture
def server(tmp_path, _history_copy, s3_server):
    recorded = json.loads((ROOT / "docs" / "scripts" / "recorded_reading.json").read_text(encoding="utf-8"))
    readings = tmp_path / "readings.json"
    readings.write_text(json.dumps({"readings": {"Lv3_0043": recorded}}, ensure_ascii=False), encoding="utf-8")
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    s3 = boto3.client("s3", endpoint_url=s3_server, region_name="ap-northeast-1", aws_access_key_id="testing",
                      aws_secret_access_key="testing")
    s3.create_bucket(Bucket=BUCKET, CreateBucketConfiguration={"LocationConstraint": "ap-northeast-1"})
    s3.put_bucket_cors(Bucket=BUCKET, CORSConfiguration={"CORSRules": [
        {"AllowedOrigins": [base], "AllowedMethods": ["GET", "POST"], "AllowedHeaders": ["*"], "MaxAgeSeconds": 600}]})
    env = {**os.environ, **AWS_ENV, "ESTIMATE_STORAGE_DIR": str(tmp_path / "storage"), "PAST_QUOTES_PATH": str(_history_copy),
           "QUOTE_LOG_PATH": str(tmp_path / "quote_log.csv"), "DRAWING_READER": f"recorded:{readings}",
           "ANALYSIS_ANTHROPIC_API_KEY": "", "ANTHROPIC_API_KEY": "", "DATABASE_URL": "",
           "ESTIMATE_STORAGE_URL": f"s3://{BUCKET}/app", "AWS_ENDPOINT_URL_S3": s3_server, "DIRECT_DOWNLOAD_MB": "0.01",
           "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost"}
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "api.main:app", "--port", str(port)], cwd=ROOT, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(300):
        try:
            urllib.request.urlopen(base + "/api/health", timeout=1)
            break
        except OSError:
            time.sleep(0.2)
    yield base, s3
    proc.terminate()
    proc.wait(timeout=20)


def test_the_screens_send_and_fetch_files_straight_with_s3(server, s3_server):
    from playwright.sync_api import expect, sync_playwright

    base, s3 = server
    with sync_playwright() as p:
        browser = launch(p)
        page = browser.new_page(viewport={"width": 1440, "height": 950}, locale="ja-JP")
        errors: list[str] = []
        sent: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("request", lambda r: sent.append(f"{r.method} {r.url}"))
        expect.set_options(timeout=60_000)

        page.goto(base + "/estimates/new")
        page.get_by_test_id("file-input").set_input_files([str(PDF), str(STEP)])
        expect(page.get_by_test_id("selected-files")).to_contain_text("Lv3_0043.step")
        page.get_by_role("button", name="次へ：条件入力 →").click()
        expect(page.get_by_label("図番", exact=True)).to_have_value("DB-25-0367")  # the reading of the PDF sent to S3
        page.get_by_label("品名").fill("ベースブラケット")
        page.get_by_label("顧客").fill("株式会社サンプル機工")
        page.get_by_role("button", name="解析・計算を実行 →").click()
        page.wait_for_function("/^\\/estimates\\/\\d+$/.test(location.pathname)", timeout=120_000)
        views = page.get_by_test_id("shape-views")
        expect(views.locator("canvas")).to_be_visible()  # the 3D view file, fetched from S3 through the redirect

        # the drawing PDF opens from S3 too (larger than DIRECT_DOWNLOAD_MB here)
        with page.expect_popup() as popup:
            page.get_by_role("button", name="図面PDFを表示").click()
        popup.value.wait_for_load_state()
        assert not errors

    to_s3 = [s for s in sent if s.startswith(f"POST {s3_server}/{BUCKET}")]
    assert len(to_s3) == 2  # the PDF and the STEP, not through the API
    assert not any(s.startswith(f"POST {base}/api/files") for s in sent)
    assert any(s.startswith(f"GET {s3_server}/{BUCKET}/app/viewer/") for s in sent)
    assert any(s.startswith(f"GET {s3_server}/{BUCKET}/app/uploads/") and ".pdf" in s for s in sent)
    keys = [o["Key"] for o in s3.list_objects_v2(Bucket=BUCKET)["Contents"]]
    assert sum(k.startswith("app/uploads/") and k.endswith("meta.json") for k in keys) == 2
