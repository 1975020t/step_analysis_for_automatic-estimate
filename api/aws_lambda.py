"""AWS Lambda entry points on the API side (the zip package, with the database; no CadQuery).

  api.aws_lambda.api        the API behind API Gateway (HTTP API, payload 2.0): the same FastAPI app as uvicorn
  api.aws_lambda.prepare    step 1 of a job (Step Functions): marks it running, writes the plan
  api.aws_lambda.finish     step 3: matching, amounts, similar results, saved; the job is done
  api.aws_lambda.fail       on any error of the three steps: the job is failed
  api.aws_lambda.migrate    once per deployment, before the others: the database's tables and initial data (each
                            function would do it on its first call, but several at once on an empty database collide)
The step between them, the extraction, is src/services/aws_extract.py:handler (the container with CadQuery).
The state machine is deploy/aws/jobs_state_machine.asl.json; the settings are the API's (DATABASE_URL,
ESTIMATE_STORAGE_URL, COGNITO_*, JOBS_STATE_MACHINE_ARN, ...), see deploy/aws/README.md.

Each step's event and output is {"job_id", "tenant_id", "kind"}; the plan and the results are kept in the file
store (jobs/<job_id>/plan.json and extracted.json) because a Step Functions state holds 256KB at most.
"""
from __future__ import annotations

import json

from src.services.jobs import DONE, FAILED, FAILED_MESSAGE, RUNNING, _now, job_context
from src.tenancy import DEFAULT_TENANT, tenant_scope

_api = None
_platform = None


def api(event, context):
    """API Gateway -> the FastAPI app (Mangum). The jobs it accepts start executions of the state machine."""
    global _api
    if _api is None:
        from mangum import Mangum

        from api.main import app, start_queue

        if not app.state.settings.jobs_state_machine:
            raise RuntimeError("JOBS_STATE_MACHINE_ARN を設定してください（Lambda では処理をスレッドで動かせません）")
        start_queue(app)
        _api = Mangum(app, lifespan="off")
    return _api(event, context)


def platform():
    global _platform
    if _platform is None:
        from src.services.platform.core import Platform
        from src.services.settings import Settings

        _platform = Platform(Settings.from_env())
    return _platform


def migrate(event=None, context=None) -> dict:
    """Tables up to date and the initial data in (what every function does on its first call)."""
    from sqlalchemy import text

    pf = platform()
    with pf.sessions() as s:
        version = s.execute(text("SELECT version_num FROM alembic_version")).scalar()
    return {"database": "ready", "version": version}


def plan_key(job_id: str) -> str:
    return f"jobs/{job_id}/plan.json"


def extracted_key(job_id: str) -> str:
    return f"jobs/{job_id}/extracted.json"


def put_json(blobs, key: str, value) -> None:
    blobs.put(key, json.dumps(value, ensure_ascii=False).encode("utf-8"))


def get_json(blobs, key: str):
    return json.loads(blobs.get(key).decode("utf-8"))


def prepare(event, context=None) -> dict:
    from src.services.platform import jobsteps

    pf = platform()
    store = pf.service.job_store
    job = store.update(event["job_id"], status=RUNNING, started_at=_now())
    tenant = job.get("tenant_id") or DEFAULT_TENANT
    with tenant_scope(tenant), job_context(store, job["job_id"]):
        put_json(pf.files.blobs, plan_key(job["job_id"]), jobsteps.prepare(pf, job))
    return {"job_id": job["job_id"], "tenant_id": tenant, "kind": job["kind"]}


def finish(event, context=None) -> dict:
    from src.services.platform import jobsteps

    pf = platform()
    store = pf.service.job_store
    job = store.read(event["job_id"])
    with tenant_scope(job.get("tenant_id") or DEFAULT_TENANT), job_context(store, job["job_id"]):
        blobs = pf.files.blobs
        result = jobsteps.finish(pf, job, get_json(blobs, plan_key(job["job_id"])),
                                 get_json(blobs, extracted_key(job["job_id"])))
    store.update(job["job_id"], status=DONE, finished_at=_now(), result=result)
    return {"job_id": job["job_id"], "status": DONE}


def fail(event, context=None) -> dict:
    """event: {"job_id", "error": {"Error": exception name, "Cause": the Lambda's error as JSON}} (the Catch)."""
    error = event.get("error") or {}
    message = FAILED_MESSAGE
    if error.get("Error") == "JobError":  # a message meant for the user (src.services.jobs.JobError)
        try:
            message = json.loads(error.get("Cause") or "{}").get("errorMessage") or message
        except (TypeError, ValueError):
            pass
    else:
        print(json.dumps({"job_id": event.get("job_id"), "error": error}, ensure_ascii=False))  # for the logs
    platform().service.job_store.update(event["job_id"], status=FAILED, finished_at=_now(), error=message)
    return {"job_id": event["job_id"], "status": FAILED}
