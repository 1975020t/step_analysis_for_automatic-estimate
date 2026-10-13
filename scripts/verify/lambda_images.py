"""The Lambda images on this machine, as AWS would run them: Lambda's runtime interface emulator (aws-lambda-rie) in
front of each function, the state machine of deploy/aws/jobs_state_machine.asl.json played by a stand-in for Step
Functions, API Gateway's events built here. One drawing (PDF + STEP) is registered and estimated through the API.

    docker build -f Dockerfile.lambda --target api -t estimate-api .
    docker build -f Dockerfile.lambda --target extract -t estimate-extract .
    python scripts/verify/lambda_images.py --rie <path to aws-lambda-rie>

aws-lambda-rie: https://github.com/aws/aws-lambda-runtime-interface-emulator (releases), or /usr/local/bin/aws-lambda-rie
of an AWS Lambda base image. Files and the database are a shared folder (SQLite) here; on AWS they are S3 and Aurora.
No AWS account and no Claude API call (the drawing is not read: no API key).
"""
from __future__ import annotations

import argparse
import base64
import json
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ASL = json.loads((ROOT / "deploy" / "aws" / "jobs_state_machine.asl.json").read_text(encoding="utf-8"))
ARN = "arn:aws:states:ap-northeast-1:123456789012:stateMachine:estimate-jobs"
PDF, STEP = ROOT / "pdf_data" / "pdf" / "Lv3_0043.pdf", ROOT / "pdf_data" / "step" / "Lv3_0043.step"
FUNCTIONS = {  # name: (image, handler, the placeholder in the state machine)
    "api": ("api", "api.aws_lambda.api", None),
    "prepare": ("api", "api.aws_lambda.prepare", "${PrepareFunctionArn}"),
    "extract": ("extract", "src.services.aws_extract.handler", "${ExtractFunctionArn}"),
    "finish": ("api", "api.aws_lambda.finish", "${FinishFunctionArn}"),
    "fail": ("api", "api.aws_lambda.fail", "${FailFunctionArn}"),
}


def invoke(port: int, event: dict, timeout: float = 900) -> dict:
    request = urllib.request.Request(f"http://127.0.0.1:{port}/2015-03-31/functions/function/invocations",
                                     data=json.dumps(event).encode(), method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read() or b"null")


class StepFunctions:
    """StartExecution of the Step Functions API (JSON 1.0); each execution runs the state machine in a thread."""

    def __init__(self, ports: dict[str, int]) -> None:
        self.ports = {FUNCTIONS[name][2]: port for name, port in ports.items() if FUNCTIONS[name][2]}
        self.ends: dict[str, str] = {}
        self.steps: dict[str, list[str]] = {}

    def run(self, name: str, state: dict) -> None:
        step, self.steps[name] = ASL["StartAt"], []
        while True:
            spec = ASL["States"][step]
            if spec["Type"] == "Fail":
                self.ends[name] = spec["Error"]
                return
            self.steps[name].append(step)
            out = invoke(self.ports[spec["Parameters"]["FunctionName"]], state)
            if isinstance(out, dict) and "errorType" in out:  # an uncaught exception of the function
                cause = json.dumps({"errorMessage": out.get("errorMessage"), "errorType": out["errorType"]})
                state, step = {**state, "error": {"Error": out["errorType"], "Cause": cause}}, spec["Catch"][0]["Next"]
                continue
            state = out
            if spec.get("End"):
                self.ends[name] = "done"
                return
            step = spec["Next"]

    def serve(self) -> int:
        machine = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                assert self.headers["X-Amz-Target"].endswith("StartExecution") and body["stateMachineArn"] == ARN
                threading.Thread(target=machine.run, args=(body["name"], json.loads(body["input"]))).start()
                out = json.dumps({"executionArn": f"{ARN}:{body['name']}", "startDate": time.time()}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/x-amz-json-1.0")
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("0.0.0.0", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return server.server_address[1]


def http_event(method: str, path: str, body: bytes = b"", content_type: str = "") -> dict:
    """An API Gateway HTTP API event (payload 2.0)."""
    raw_path, _, query = path.partition("?")
    headers = {"host": "x.execute-api.ap-northeast-1.amazonaws.com"}
    if content_type:
        headers["content-type"] = content_type
    return {"version": "2.0", "routeKey": "$default", "rawPath": raw_path, "rawQueryString": query, "headers": headers,
            "requestContext": {"http": {"method": method, "path": raw_path, "protocol": "HTTP/1.1",
                                        "sourceIp": "203.0.113.1", "userAgent": "verify"},
                               "stage": "$default", "requestId": uuid.uuid4().hex, "accountId": "123456789012",
                               "apiId": "x", "domainName": headers["host"], "domainPrefix": "x",
                               "time": "13/Oct/2026:00:00:00 +0000", "timeEpoch": 0},
            "body": base64.b64encode(body).decode(), "isBase64Encoded": True}


class Api:
    def __init__(self, port: int) -> None:
        self.port = port

    def call(self, method: str, path: str, body=None, file: Path | None = None) -> tuple[int, bytes]:
        data, kind = b"", ""
        if file is not None:
            boundary = uuid.uuid4().hex
            data = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{file.name}\"\r\n"
                    f"Content-Type: application/octet-stream\r\n\r\n").encode() + file.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
            kind = f"multipart/form-data; boundary={boundary}"
        elif body is not None:
            data, kind = json.dumps(body).encode(), "application/json"
        out = invoke(self.port, http_event(method, path, data, kind))
        content = base64.b64decode(out.get("body") or "") if out.get("isBase64Encoded") else (out.get("body") or "").encode()
        return out["statusCode"], content

    def json(self, method: str, path: str, body=None, file: Path | None = None, expect: int = 200):
        status, content = self.call(method, path, body, file)
        if status != expect:
            raise AssertionError(f"{method} {path}: {status} {content[:300]!r}")
        return json.loads(content)

    def wait(self, job_id: str) -> dict:
        for _ in range(900):
            job = self.json("GET", f"/api/jobs/{job_id}")
            if job["status"] in ("done", "failed"):
                return job
            time.sleep(1)
        raise AssertionError("the job did not finish")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rie", required=True, help="aws-lambda-rie（Lambda の実行環境の代わり）")
    ap.add_argument("--api-image", default="estimate-api")
    ap.add_argument("--extract-image", default="estimate-extract")
    ap.add_argument("--base-port", type=int, default=9301)
    args = ap.parse_args()

    work = Path(tempfile.mkdtemp(prefix="lambda_images_"))
    (work / "rie").mkdir()
    shutil.copy(args.rie, work / "rie" / "aws-lambda-rie")
    (work / "data").mkdir()
    shutil.copy(ROOT / "data" / "past_quotes" / "history.csv", work / "history.csv")
    ports = {name: args.base_port + i for i, name in enumerate(FUNCTIONS)}
    machine = StepFunctions(ports)
    sfn_port = machine.serve()
    names = []
    try:
        for name, (image, handler, _) in FUNCTIONS.items():
            container = f"verify-{name}-{uuid.uuid4().hex[:6]}"
            names.append(container)
            subprocess.run(
                ["docker", "run", "-d", "--name", container, "-p", f"{ports[name]}:8080", "--read-only", "--tmpfs", "/tmp",
                 "--add-host", "host.docker.internal:host-gateway",
                 "-v", f"{work / 'rie'}:/rie:ro", "-v", f"{work / 'data'}:/data", "-v", f"{work}/history.csv:/history.csv:ro",
                 "-e", "ESTIMATE_STORAGE_DIR=/data", "-e", "PAST_QUOTES_PATH=/history.csv",
                 "-e", f"JOBS_STATE_MACHINE_ARN={ARN}", "-e", "AWS_DEFAULT_REGION=ap-northeast-1",
                 "-e", "AWS_ACCESS_KEY_ID=verify", "-e", "AWS_SECRET_ACCESS_KEY=verify",
                 "-e", f"AWS_ENDPOINT_URL_SFN=http://host.docker.internal:{sfn_port}",
                 "--entrypoint", "/rie/aws-lambda-rie", f"{args.api_image if image == 'api' else args.extract_image}",
                 "python", "-m", "awslambdaric", handler], check=True, capture_output=True)
        for port in ports.values():
            for _ in range(120):
                try:
                    urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=1)
                except urllib.error.HTTPError:
                    break  # the emulator answers
                except OSError:
                    time.sleep(0.5)
        api = Api(ports["api"])
        checks: list[tuple[str, bool]] = []
        started = time.time()
        step = api.json("POST", "/api/files", file=STEP, expect=201)
        pdf = api.json("POST", "/api/files", file=PDF, expect=201)
        item = {"drawing_no": "LAMBDA-1", "name": "ブラケット", "customer": "株式会社テスト", "shape_file_id": step["file_id"],
                "pdf_file_id": pdf["file_id"]}
        registered = api.json("POST", "/api/drawings/register", {"items": [item]}, expect=202)[0]
        job = api.wait(registered["job_id"])
        checks.append(("登録の受付が終わる（準備→抽出→保存）", job["status"] == "done"))
        drawing = api.json("GET", f"/api/drawings/{registered['drawing_id']}")
        revision = drawing["revisions"][0]
        checks.append(("形状が解析されている", (revision.get("analysis") or {}).get("status") in ("success", "partial")))
        status, glb = api.call("GET", f"/api/files/{step['file_id']}/model.glb")
        checks.append(("3D表示のファイルを API が返す（抽出の段で作ったもの）", status == 200 and glb[:4] == b"glTF"))
        status, png = api.call("GET", f"/api/revisions/{revision['id']}/preview.png?width=300")
        checks.append(("図面PDFのプレビューを API が返す", status == 200 and png[:4] == b"\x89PNG"))
        made = api.json("POST", "/api/estimates", {"drawing_id": registered["drawing_id"], "customer": "株式会社テスト",
                                                   "quantity": 100, "material": "SPCC"}, expect=202)
        job = api.wait(made["job_id"])
        checks.append(("見積の受付が終わる", job["status"] == "done"))
        quote = api.json("GET", f"/api/estimates/{made['estimate_id']}")
        subtotal = ((quote.get("result") or {}).get("price") or {}).get("subtotal")
        checks.append((f"金額が出る（小計 {subtotal}）", bool(subtotal)))
        checks.append(("どの受付も 準備→抽出→保存 の順に動いた",
                       all(s == ["Prepare", "Extract", "Finish"] for s in machine.steps.values()) and len(machine.steps) == 2))
        for label, ok in checks:
            print(("OK  " if ok else "NG  ") + label)
        print(f"所要 {time.time() - started:.0f} 秒（最初の呼び出しの起動を含む）")
        return 0 if all(ok for _, ok in checks) else 1
    finally:
        for container in names:
            logs = subprocess.run(["docker", "logs", container], capture_output=True, text=True)
            (work / f"{container}.log").write_text(logs.stdout + logs.stderr, encoding="utf-8")
            subprocess.run(["docker", "rm", "-f", container], capture_output=True)
        print(f"ログ: {work}")


if __name__ == "__main__":
    sys.exit(main())
