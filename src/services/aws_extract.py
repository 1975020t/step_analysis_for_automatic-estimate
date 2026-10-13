"""AWS Lambda entry point of the extraction step (the container image with CadQuery: Dockerfile.lambda).

  src.services.aws_extract.handler    step 2 of a job (Step Functions), between api/aws_lambda.py prepare and finish

It reads the plan the prepare step left in the file store (jobs/<job_id>/plan.json), analyses the shape and reads
the drawing, and leaves the results beside it (extracted.json). No database: this function needs only the file store
(ESTIMATE_STORAGE_URL, the S3 bucket) and, for the drawing reading, the Claude API key (ANALYSIS_ANTHROPIC_API_KEY).
This module must not import src.db or src.services.platform (tests/test_aws.py checks it).
"""
from __future__ import annotations

import json

from src.tenancy import tenant_scope

_service = None


def service():
    global _service
    if _service is None:
        from src.services.estimate import EstimateService
        from src.services.settings import Settings

        _service = EstimateService(Settings.from_env())
    return _service


def handler(event, context=None) -> dict:
    from src.services.extraction import extract_job

    job_id = event["job_id"]
    svc = service()
    with tenant_scope(event["tenant_id"]):
        plan = json.loads(svc.blobs.get(f"jobs/{job_id}/plan.json").decode("utf-8"))
        extracted = extract_job(svc, event["kind"], plan)
        svc.blobs.put(f"jobs/{job_id}/extracted.json", json.dumps(extracted, ensure_ascii=False).encode("utf-8"))
    return {"job_id": job_id, "tenant_id": event["tenant_id"], "kind": event["kind"]}
