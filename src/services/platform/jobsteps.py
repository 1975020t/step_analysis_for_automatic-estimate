"""The database steps of each kind of job, one call per step: what the AWS state machine runs (api/aws_lambda.py).

In one process a job runs its three steps in a thread (run_register_job, run_estimate_job, ...); on AWS each step is
a Lambda function and these are the first and the last:
  prepare(pf, job)                      -> the plan (JSON)
  extract  (src.services.extraction.extract_job, without the database)  -> the results (JSON)
  finish(pf, job, plan, extracted)      -> the job's result
The functions are those the threads call; only the progress shown while the files are read is coarser.
"""
from __future__ import annotations

from src.db.retry import retry_on_conflict
from src.services.jobs import report_progress
from src.services.platform.core import Platform
from src.services.platform.drawings import finish_register, prepare_register
from src.services.platform.quotes import (STAGES, finish_estimate, finish_reanalysis, prepare_estimate,
                                          prepare_reanalysis)


def _estimate_stage(values: dict):
    def stage(i: int, **found) -> None:
        values.update(found)
        report_progress(step=i + 1, steps=len(STAGES), stage=STAGES[i], stages=STAGES, values=dict(values))
    return stage


def prepare(pf: Platform, job: dict) -> dict:
    kind, payload = job["kind"], job["input"]
    if kind == "register":
        report_progress(stage="CADデータの解析")
        return retry_on_conflict(lambda: prepare_register(pf, payload))
    if kind == "estimate":
        _estimate_stage({})(0)
        return retry_on_conflict(lambda: prepare_estimate(pf, payload))
    if kind == "reanalysis":
        report_progress(step=1, steps=1, stage=STAGES[0], stages=STAGES[:1])
        return retry_on_conflict(lambda: prepare_reanalysis(pf, payload))
    if kind in ("analysis", "drawing"):
        return payload  # the first API's jobs: the request is the plan
    raise KeyError(kind)


def finish(pf: Platform, job: dict, plan: dict, extracted: dict) -> dict:
    kind, payload = job["kind"], job["input"]
    if kind == "register":
        return retry_on_conflict(lambda: finish_register(pf, payload, plan, extracted))
    if kind == "estimate":
        values: dict = {}
        return retry_on_conflict(lambda: finish_estimate(pf, payload, plan, extracted, _estimate_stage(values), values))
    if kind == "reanalysis":
        return retry_on_conflict(lambda: finish_reanalysis(pf, payload, plan, extracted))
    if kind == "analysis":
        return extracted["analysis"]
    if kind == "drawing":
        return pf.service.finish_drawing_job(extracted)
    raise KeyError(kind)
