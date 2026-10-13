"""On AWS: the jobs as executions of a state machine (prepare -> extract -> finish, fail on errors), files sent and
fetched straight from S3, screens on another domain. No AWS: the state machine is played here (each step's input
and output goes through JSON, in a thread of its own), moto stands in for S3."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import threading
import urllib.parse

import boto3
import pytest
from fastapi.testclient import TestClient
from moto import mock_aws

import api.aws_lambda as aws_lambda
import src.services.aws_extract as aws_extract
import tests.test_api as api_tests
import tests.test_platform as platform_tests
from api.main import create_app
from src.services.estimate import EstimateService, ServiceError
from src.services.platform.core import RecordedReader
from src.services.settings import Settings

ROOT = platform_tests.ROOT
ARN = "arn:aws:states:ap-northeast-1:123456789012:stateMachine:estimate-jobs"
BUCKET = "estimate-aws"


def roundtrip(value):
    return json.loads(json.dumps(value, ensure_ascii=False))


ASL = json.loads((ROOT / "deploy" / "aws" / "jobs_state_machine.asl.json").read_text(encoding="utf-8"))
FUNCTIONS = {"${PrepareFunctionArn}": "prepare", "${ExtractFunctionArn}": "extract", "${FinishFunctionArn}": "finish",
             "${FailFunctionArn}": "fail"}


class StateMachine:
    """deploy/aws/jobs_state_machine.asl.json, played in a thread per execution: each Task invokes its function with
    the state as the payload and goes on with the function's answer; an error goes to the Catch with $.error."""

    def __init__(self) -> None:
        self.names: list[str] = []
        self.threads: list[threading.Thread] = []
        self.failed_steps: list[str] = []
        self.ends: list[str] = []

    def start_execution(self, stateMachineArn: str, name: str, input: str):  # noqa: N803 - boto3's names
        assert stateMachineArn == ARN and name not in self.names
        self.names.append(name)
        thread = threading.Thread(target=self.run, args=(json.loads(input),))
        thread.start()
        self.threads.append(thread)
        return {"executionArn": f"{ARN}:{name}"}

    @staticmethod
    def function(arn: str):
        step = FUNCTIONS[arn]
        return aws_extract.handler if step == "extract" else getattr(aws_lambda, step)

    def run(self, event: dict) -> None:
        name, state = ASL["StartAt"], roundtrip(event)
        while True:
            spec = ASL["States"][name]
            if spec["Type"] == "Fail":
                self.ends.append(spec["Error"])
                return
            assert spec["Resource"] == "arn:aws:states:::lambda:invoke" and spec["Parameters"]["Payload.$"] == "$"
            assert spec["OutputPath"] == "$.Payload"
            try:
                state = roundtrip(self.function(spec["Parameters"]["FunctionName"])(roundtrip(state)))
            except Exception as exc:  # what Lambda reports for an uncaught exception, to the Catch
                (catch,) = spec["Catch"]
                assert catch["ErrorEquals"] == ["States.ALL"] and catch["ResultPath"] == "$.error"
                self.failed_steps.append(type(exc).__name__)
                cause = json.dumps({"errorMessage": str(exc), "errorType": type(exc).__name__})
                state, name = {**state, "error": {"Error": type(exc).__name__, "Cause": cause}}, catch["Next"]
                continue
            if spec.get("End"):
                self.ends.append("done")
                return
            name = spec["Next"]

    def join(self) -> None:
        for thread in self.threads:
            thread.join(120)


def on_aws(settings: Settings, reader_factory):
    """The API with JOBS_STATE_MACHINE_ARN, and the step functions using the same database and files."""
    settings = Settings(**{**settings.__dict__, "jobs_state_machine": ARN})
    app = create_app(settings, reader_factory=reader_factory)
    machine = StateMachine()
    return app, machine, settings


@pytest.fixture
def aws_client(tmp_path, _history_copy, monkeypatch):
    storage = tmp_path / "storage"
    settings = Settings(data_dir=ROOT / "data", storage_dir=storage, history_path=_history_copy,
                        quote_log_path=storage / "quote_log.csv")
    reader = lambda: RecordedReader(platform_tests.RECORDED)  # noqa: E731
    app, machine, settings = on_aws(settings, reader)
    with TestClient(app) as client:
        client.app.state.queue._client = machine
        monkeypatch.setattr(aws_lambda, "_platform", app.state.platform)
        monkeypatch.setattr(aws_extract, "_service", EstimateService(settings, reader_factory=reader))
        client.machine = machine
        yield client
        machine.join()


def test_the_extract_function_does_not_load_the_database():
    code = ("import sys; import src.services.aws_extract, src.services.extraction, src.services.estimate; "
            "bad = [m for m in sys.modules if m.startswith(('src.db', 'src.services.platform', 'sqlalchemy', 'api'))]; "
            "print(bad); sys.exit(1 if bad else 0)")
    done = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True)
    assert done.returncode == 0, done.stdout + done.stderr


def test_screen_jobs_run_as_executions_and_give_what_the_threads_give(aws_client, tmp_path, _history_copy):
    client = aws_client
    drawing_id = platform_tests.register(client, "AWS-1", step=platform_tests.DRAWING_STEP, pdf=platform_tests.DRAWING_PDF)
    q = platform_tests.estimate(client, drawing_id)
    changed = platform_tests.change(client, q, k_factor=0.4, k_factor_confirmed=True)  # analysed again (reanalysis)
    assert client.machine.failed_steps == [] and len(client.machine.names) == 3
    jobs = [client.get(f"/api/jobs/{name}").json() for name in client.machine.names]
    assert [j["kind"] for j in jobs] == ["register", "estimate", "reanalysis"]
    assert all(j["status"] == "done" and j["started_at"] and j["finished_at"] for j in jobs)
    assert jobs[1]["progress"]["stage"] == "完了"  # the progress screen's last stage

    # the same drawing and conditions with the jobs in threads of one process: the same results
    with TestClient(platform_tests.make_app(tmp_path / "local", _history_copy)) as local:
        local_drawing = platform_tests.register(local, "AWS-1", step=platform_tests.DRAWING_STEP, pdf=platform_tests.DRAWING_PDF)
        local_q = platform_tests.estimate(local, local_drawing)
        local_changed = platform_tests.change(local, local_q, k_factor=0.4, k_factor_confirmed=True)
    for a, b in ((q, local_q), (changed, local_changed)):
        assert a["result"]["price"] == b["result"]["price"] and a["result"]["missing"] == b["result"]["missing"]
        assert a["analysis"]["blank_area_mm2"] == b["analysis"]["blank_area_mm2"]
    assert changed["analysis"]["blank_area_mm2"] != q["analysis"]["blank_area_mm2"]

    # the registration made the 3D view file (the API on AWS has no CadQuery to make it)
    revision = client.get(f"/api/drawings/{drawing_id}").json()["revisions"][0]
    glb = client.app.state.settings.storage_dir / "viewer" / f"{revision['shape_file_id']}.glb"
    assert glb.is_file() and client.get(f"/api/files/{revision['shape_file_id']}/model.glb").content == glb.read_bytes()


def test_api_jobs_and_failures_on_aws(tmp_path, monkeypatch):
    settings = api_tests.settings_for(tmp_path)
    app, machine, settings = on_aws(settings, api_tests.FakeReader)
    with TestClient(app) as client:
        app.state.queue._client = machine
        monkeypatch.setattr(aws_lambda, "_platform", app.state.platform)
        monkeypatch.setattr(aws_extract, "_service", EstimateService(settings, reader_factory=api_tests.FakeReader))
        api_tests.test_step_analysis_quote_document_and_similar_quotes(client)
        api_tests.test_drawing_reading_with_a_fake_reader_feeds_the_quote_and_the_document(client)

        # a message meant for the user (JobError) is shown; anything else is the safe message
        def refuse(*args, **kwargs):
            raise ServiceError("この形状は解析できません。")

        monkeypatch.setattr(EstimateService, "analyze_bytes", staticmethod(refuse))
        dxf = api_tests.upload(client, api_tests.DXF)
        job = client.post("/api/analyses", json={"file_id": dxf["file_id"], "thickness_mm": 1.6}).json()
        failed = api_tests.wait(client, job["job_id"])
        assert failed["status"] == "failed" and failed["error"] == "この形状は解析できません。"
        monkeypatch.setattr(EstimateService, "extract_drawing_job", lambda self, payload: 1 / 0)
        pdf = api_tests.upload(client, api_tests.PDF)
        job = client.post("/api/drawings/readings", json={"file_id": pdf["file_id"]}).json()
        failed = api_tests.wait(client, job["job_id"])
        assert failed["status"] == "failed" and failed["error"] == "処理中にエラーが発生しました。入力ファイルを確認してください。"
        machine.join()
        assert machine.failed_steps == ["JobError", "ZeroDivisionError"] and machine.ends.count("JobFailed") == 2

        # an execution that cannot start leaves the job failed, not queued forever
        class Refusing:
            def start_execution(self, **_):
                raise RuntimeError("AccessDenied")

        app.state.queue._client = Refusing()
        job = client.post("/api/analyses", json={"file_id": dxf["file_id"], "thickness_mm": 1.6}).json()
        assert client.get(f"/api/jobs/{job['job_id']}").json()["status"] == "failed"
        machine.join()


def test_the_api_side_runs_without_cadquery(tmp_path, _history_copy):
    """On AWS the API's Lambda has no CadQuery: everything but the extraction step works without it. The extraction
    step runs in another process (as the container would) with CadQuery."""
    script = textwrap.dedent(f"""
        import importlib.abc, json, subprocess, sys
        HEAVY = ("cadquery", "OCP", "vtkmodules", "casadi")
        asked = []

        class Block(importlib.abc.MetaPathFinder):
            def find_spec(self, name, path=None, target=None):
                if name.split(".")[0] in HEAVY:
                    asked.append(name)
                    raise ImportError(name)

        sys.meta_path.insert(0, Block())
        from fastapi.testclient import TestClient
        import api.aws_lambda as aws_lambda
        import tests.test_platform as pt
        from api.main import create_app

        class Recorder:
            names = []
            def start_execution(self, stateMachineArn, name, input):
                self.names.append(name)

        def run(job_id):
            event = aws_lambda.prepare({{"job_id": job_id}})
            done = subprocess.run([sys.executable, "-c", "import json, sys; import src.services.aws_extract as e; "
                                   "print(json.dumps(e.handler(json.loads(sys.argv[1]))))", json.dumps(event)],
                                  capture_output=True, text=True)
            assert done.returncode == 0, done.stderr
            aws_lambda.finish(json.loads(done.stdout.strip().splitlines()[-1]))

        app = create_app()
        with TestClient(app) as client:
            app.state.queue._client = recorder = Recorder()
            aws_lambda._platform = app.state.platform
            item = {{"drawing_no": "NO-CQ", "name": "ブラケット", "customer": "株式会社テスト",
                     "shape_file_id": pt.upload(client, pt.DRAWING_STEP), "pdf_file_id": pt.upload(client, pt.DRAWING_PDF)}}
            registered = client.post("/api/drawings/register", json={{"items": [item]}}).json()[0]
            run(registered["job_id"])
            r = client.post("/api/estimates", json={{"drawing_id": registered["drawing_id"], "customer": "株式会社テスト",
                                                     "quantity": 100, "material": "SPCC"}})
            run(r.json()["job_id"])
            q = client.get(f"/api/estimates/{{r.json()['estimate_id']}}").json()
            assert q["result"]["price"], q["result"]
            revision = client.get(f"/api/drawings/{{registered['drawing_id']}}").json()["revisions"][0]
            assert client.get(f"/api/files/{{revision['shape_file_id']}}/model.glb").status_code == 200
            assert client.get(f"/api/revisions/{{revision['id']}}/preview.png?width=300").status_code == 200
            assert all(client.get(f"/api/jobs/{{n}}").json()["status"] == "done" for n in recorder.names)
        print("ASKED", json.dumps(sorted(set(asked))))
    """)
    storage = tmp_path / "storage"
    env = {**os.environ, "ESTIMATE_STORAGE_DIR": str(storage), "ESTIMATE_DATA_DIR": str(ROOT / "data"),
           "PAST_QUOTES_PATH": str(_history_copy), "QUOTE_LOG_PATH": str(storage / "quote_log.csv"),
           "JOBS_STATE_MACHINE_ARN": ARN, "DATABASE_URL": "", "ANALYSIS_ANTHROPIC_API_KEY": "", "ANTHROPIC_API_KEY": "",
           "PYTHONPATH": str(ROOT)}
    done = subprocess.run([sys.executable, "-c", script], cwd=ROOT, env=env, capture_output=True, text=True, timeout=600)
    assert done.returncode == 0, done.stdout[-3000:] + done.stderr[-3000:]
    assert "ASKED []" in done.stdout, done.stdout[-2000:]


# ---------------------------------------------------------------- files straight to and from S3
@pytest.fixture
def s3_client(tmp_path, _history_copy):
    with mock_aws():
        s3 = boto3.client("s3", region_name="ap-northeast-1")
        s3.create_bucket(Bucket=BUCKET, CreateBucketConfiguration={"LocationConstraint": "ap-northeast-1"})
        storage = tmp_path / "storage"
        settings = Settings(data_dir=ROOT / "data", storage_dir=storage, history_path=_history_copy,
                            quote_log_path=storage / "quote_log.csv", storage_url=f"s3://{BUCKET}/app")
        with TestClient(create_app(settings, reader_factory=lambda: RecordedReader(platform_tests.RECORDED))) as client:
            client.s3 = s3
            yield client


def post_form(s3, form: dict, data: bytes, filename: str) -> int:
    """What the browser does with the form: post it to the bucket (moto answers the request)."""
    import requests

    response = requests.post(form["url"], data=form["fields"], files={"file": (filename, data)})
    return response.status_code


def test_files_are_sent_straight_to_s3_and_taken_in(s3_client):
    client = s3_client
    data = platform_tests.DRAWING_STEP.read_bytes()
    form = client.post("/api/uploads", json={"filename": "部品 A.step", "size": len(data)}).json()
    assert form["direct"] and form["file_id"].startswith("f_") and form["url"]
    assert form["fields"]["key"] == f"app/uploads/{form['file_id']}/{form['filename']}"
    assert post_form(client.s3, form, data, form["filename"]) in (200, 204)
    done = client.post(f"/api/uploads/{form['file_id']}/complete", json={"filename": "部品 A.step"})
    assert done.status_code == 201, done.text
    assert done.json() == {"file_id": form["file_id"], "filename": form["filename"], "kind": "step", "size": len(data)}
    again = client.post(f"/api/uploads/{form['file_id']}/complete", json={"filename": "部品 A.step"})
    assert again.status_code == 404  # taken in once

    # the file is used like an uploaded one
    r = client.post("/api/drawings/register", json={"items": [{"drawing_no": "S3-DIRECT", "name": "ブラケット",
                                                                "customer": "株式会社テスト", "shape_file_id": form["file_id"]}]})
    assert platform_tests.wait(client, r.json()[0]["job_id"])["status"] == "done"

    # refused: another type, too large, not sent, content not matching the name
    assert client.post("/api/uploads", json={"filename": "a.exe", "size": 10}).status_code == 415
    too_big = client.app.state.settings.max_upload_bytes + 1
    assert client.post("/api/uploads", json={"filename": "a.step", "size": too_big}).status_code == 413
    form = client.post("/api/uploads", json={"filename": "b.pdf", "size": 5}).json()
    assert client.post(f"/api/uploads/{form['file_id']}/complete", json={"filename": "b.pdf"}).status_code == 404
    assert post_form(client.s3, form, b"not a pdf", form["filename"]) in (200, 204)
    refused = client.post(f"/api/uploads/{form['file_id']}/complete", json={"filename": "b.pdf"})
    assert refused.status_code == 415 and refused.json()["error"]["code"] == "UNSUPPORTED_TYPE"
    assert client.post("/api/uploads/f_nothex/complete", json={"filename": "b.pdf"}).status_code == 404


def test_large_files_are_fetched_straight_from_s3(s3_client, monkeypatch):
    client = s3_client
    file_id = platform_tests.upload(client, platform_tests.DRAWING_PDF)
    small = client.get(f"/api/files/{file_id}/raw")
    assert small.status_code == 200 and small.content.startswith(b"%PDF")
    object.__setattr__(client.app.state.settings, "direct_download_bytes", 10)
    moved = client.get(f"/api/files/{file_id}/raw", follow_redirects=False)
    assert moved.status_code == 307
    url = urllib.parse.urlparse(moved.headers["location"])
    assert url.path.endswith(f"/uploads/{file_id}/{platform_tests.DRAWING_PDF.name}") and "Signature" in url.query
    import requests

    assert requests.get(moved.headers["location"]).content == platform_tests.DRAWING_PDF.read_bytes()
    step_id = platform_tests.upload(client, platform_tests.DRAWING_STEP)
    glb = client.get(f"/api/files/{step_id}/model.glb", follow_redirects=False)
    assert glb.status_code == 307 and "/viewer/" in glb.headers["location"]


def test_a_folder_store_answers_that_files_go_through_the_api(tmp_path):
    with TestClient(create_app(api_tests.settings_for(tmp_path))) as client:
        assert client.post("/api/uploads", json={"filename": "a.step", "size": 10}).json() == {
            "direct": False, "file_id": None, "filename": None, "url": None, "fields": {}}


# ---------------------------------------------------------------- screens on another domain
def test_screens_on_another_domain_may_call_the_api_only_when_allowed(tmp_path):
    origin = "https://app.example.com"
    with TestClient(create_app(api_tests.settings_for(tmp_path, cors_origins=(origin,)))) as client:
        pre = client.options("/api/health", headers={"Origin": origin, "Access-Control-Request-Method": "GET",
                                                      "Access-Control-Request-Headers": "authorization"})
        assert pre.status_code == 200 and pre.headers["access-control-allow-origin"] == origin
        assert client.get("/api/health", headers={"Origin": origin}).headers["access-control-allow-origin"] == origin
        other = client.get("/api/health", headers={"Origin": "https://evil.example.com"})
        assert "access-control-allow-origin" not in other.headers
    with TestClient(create_app(api_tests.settings_for(tmp_path))) as client:
        assert "access-control-allow-origin" not in client.get("/api/health", headers={"Origin": origin}).headers
    assert Settings.from_env({"CORS_ORIGINS": "https://a.example.com/, https://b.example.com"}).cors_origins == (
        "https://a.example.com", "https://b.example.com")


# ---------------------------------------------------------------- the API behind API Gateway
def test_the_api_answers_an_api_gateway_event(tmp_path, monkeypatch):
    for key, value in {"ESTIMATE_STORAGE_DIR": str(tmp_path / "s"), "ESTIMATE_DATA_DIR": str(ROOT / "data"),
                       "JOBS_STATE_MACHINE_ARN": ARN, "DATABASE_URL": ""}.items():
        monkeypatch.setenv(key, value)
    import api.main

    monkeypatch.setattr(api.main, "app", create_app())
    monkeypatch.setattr(aws_lambda, "_api", None)
    event = {"version": "2.0", "routeKey": "$default", "rawPath": "/api/health", "rawQueryString": "",
             "headers": {"host": "x.execute-api.ap-northeast-1.amazonaws.com"},
             "requestContext": {"http": {"method": "GET", "path": "/api/health", "protocol": "HTTP/1.1",
                                         "sourceIp": "203.0.113.1", "userAgent": "test"},
                                "stage": "$default", "requestId": "r1", "accountId": "1", "apiId": "x",
                                "domainName": "x.execute-api.ap-northeast-1.amazonaws.com", "domainPrefix": "x",
                                "time": "13/Oct/2026:00:00:00 +0000", "timeEpoch": 0},
             "isBase64Encoded": False}
    out = aws_lambda.api(event, None)
    assert out["statusCode"] == 200 and json.loads(out["body"])["status"] == "ok"
    from src.services.stepfunctions import StepFunctionsQueue

    assert isinstance(api.main.app.state.queue, StepFunctionsQueue)
    monkeypatch.setattr(aws_lambda, "_api", None)
    monkeypatch.setattr(api.main, "app", create_app(Settings.from_env({**os.environ, "JOBS_STATE_MACHINE_ARN": ""})))
    with pytest.raises(RuntimeError):
        aws_lambda.api(event, None)


def test_the_database_is_made_ready_once_per_deployment(tmp_path, monkeypatch):
    monkeypatch.setenv("ESTIMATE_STORAGE_DIR", str(tmp_path / "s"))
    monkeypatch.setenv("ESTIMATE_DATA_DIR", str(ROOT / "data"))
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setattr(aws_lambda, "_platform", None)
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    head = ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini"))).get_current_head()
    assert aws_lambda.migrate({}, None) == {"database": "ready", "version": head}
    assert aws_lambda.migrate({}, None) == {"database": "ready", "version": head}  # again: nothing to do
