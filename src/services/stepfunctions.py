"""The jobs on AWS: each job is an execution of a Step Functions state machine instead of a thread of the API.

The state machine (deploy/aws/jobs_state_machine.asl.json) runs three Lambda functions, the same three steps a
thread runs in one process (src/services/extraction.py):
  prepare  api/aws_lambda.py:prepare     (the API's package, with the database)  -> the plan, kept in the store
  extract  src/services/aws_extract.py   (the container with CadQuery, no database) -> the results, in the store
  finish   api/aws_lambda.py:finish      (with the database) -> the job is done
and on any error api/aws_lambda.py:fail marks the job failed. Between the steps only {"job_id", "tenant_id", "kind"}
travels (a state holds 256KB at most); the plan and the results are JSON files in the file store
(jobs/<job_id>/plan.json, extracted.json).
"""
from __future__ import annotations

import json
from typing import Callable

from src.services.jobs import FAILED, _now


class StepFunctionsQueue:
    """The interface of JobQueue: submit records a job (queued) and start runs it as an execution."""

    def __init__(self, store, kinds, state_machine_arn: str, client=None) -> None:
        self.store = store
        self.kinds = set(kinds)
        self.state_machine_arn = state_machine_arn
        self._client = client

    @property
    def client(self):
        if self._client is None:
            import boto3

            self._client = boto3.client("stepfunctions")
        return self._client

    def submit(self, kind: str, payload: dict, start: bool = True) -> dict:
        if kind not in self.kinds:
            raise KeyError(kind)
        job = self.store.create(kind, payload)
        if start:
            self.start(job["job_id"])
        return job

    def start(self, job_id: str) -> None:
        try:  # the execution's name is the job id: starting the same job twice starts it once
            self.client.start_execution(stateMachineArn=self.state_machine_arn, name=job_id,
                                        input=json.dumps({"job_id": job_id}))
        except Exception:  # noqa: BLE001 - the job shows as failed instead of staying queued forever
            import traceback

            traceback.print_exc()
            self.store.update(job_id, status=FAILED, finished_at=_now(),
                              error="処理を始められませんでした。しばらくしてからもう一度お試しください。")

    def add_handlers(self, handlers: dict[str, Callable[[dict], dict]]) -> None:
        self.kinds.update(handlers)

    def resume(self) -> int:
        return 0  # the executions run on their own; nothing is left in this process

    def shutdown(self, wait: bool = True) -> None:
        pass
