"""Background jobs (analyses, drawing readings) with state kept on disk.

A job is one JSON file (<storage>/jobs/<job_id>.json): kind, input, status (queued / running / done / failed),
times, result or error. The state survives a restart: on start, jobs left queued or running are queued again
(their input files are kept), finished ones stay readable.

`JobQueue` runs handlers in a thread pool of this process. To move to RQ / Celery later, keep JobStore (or put
it in a database) and replace JobQueue.submit with an enqueue call that runs `JobQueue.execute` in a worker.
"""
from __future__ import annotations

import json
import os
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from src.services.storage import new_id

QUEUED, RUNNING, DONE, FAILED = "queued", "running", "done", "failed"


_CURRENT = threading.local()


def report_progress(**progress) -> None:
    """Called from inside a job handler: store how far the job got (stage, values found so far) in the job, so
    that GET /api/jobs/{id} shows it while the job runs. Does nothing outside a job."""
    store, job_id = getattr(_CURRENT, "store", None), getattr(_CURRENT, "job_id", None)
    if store is not None and job_id:
        store.update(job_id, progress=progress)


class JobError(Exception):
    """A failure whose message is safe to show to the user (no paths, no secrets)."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class JobStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self._lock = threading.Lock()

    def _path(self, job_id: str) -> Path:
        if not job_id.startswith("j_") or not job_id[2:].isalnum():
            raise KeyError(job_id)
        return self.root / f"{job_id}.json"

    def create(self, kind: str, payload: dict) -> dict:
        job = {"job_id": new_id("j"), "kind": kind, "status": QUEUED, "input": payload, "created_at": _now(),
               "started_at": None, "finished_at": None, "result": None, "error": None}
        self.write(job)
        return job

    def read(self, job_id: str) -> dict:
        path = self._path(job_id)
        if not path.exists():
            raise KeyError(job_id)
        return json.loads(path.read_text(encoding="utf-8"))

    def write(self, job: dict) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self._path(job["job_id"])
        tmp = path.with_suffix(f".{threading.get_ident()}.tmp")
        with self._lock:
            tmp.write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, path)

    def update(self, job_id: str, **changes) -> dict:
        job = self.read(job_id)
        job.update(changes)
        self.write(job)
        return job

    def unfinished(self) -> list[dict]:
        if not self.root.exists():
            return []
        jobs = []
        for path in sorted(self.root.glob("j_*.json")):
            job = json.loads(path.read_text(encoding="utf-8"))
            if job["status"] in (QUEUED, RUNNING):
                jobs.append(job)
        return jobs


class JobQueue:
    """Runs job handlers in background threads. handlers: kind -> function(input dict) -> JSON-able result."""

    def __init__(self, store: JobStore, handlers: dict[str, Callable[[dict], dict]], workers: int = 2,
                 autostart: bool = True) -> None:
        self.store = store
        self.handlers = handlers
        self.autostart = autostart
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="job") if autostart else None

    def submit(self, kind: str, payload: dict, start: bool = True) -> dict:
        """Record a job and run it. start=False only records it (queued): call start() once whatever the job
        reads has been committed."""
        if kind not in self.handlers:
            raise KeyError(kind)
        job = self.store.create(kind, payload)
        if start:
            self.start(job["job_id"])
        return job

    def start(self, job_id: str) -> None:
        if self._pool:
            self._pool.submit(self.execute, job_id)

    def add_handlers(self, handlers: dict[str, Callable[[dict], dict]]) -> None:
        self.handlers.update(handlers)

    def resume(self) -> int:
        """Queue again the jobs a previous run left unfinished. Returns how many."""
        jobs = self.store.unfinished()
        for job in jobs:
            self.store.update(job["job_id"], status=QUEUED, started_at=None)
            if self._pool:
                self._pool.submit(self.execute, job["job_id"])
        return len(jobs)

    def execute(self, job_id: str) -> dict:
        job = self.store.update(job_id, status=RUNNING, started_at=_now())
        _CURRENT.store, _CURRENT.job_id = self.store, job_id
        try:
            result = self.handlers[job["kind"]](job["input"])
            return self.store.update(job_id, status=DONE, finished_at=_now(), result=result)
        except JobError as exc:
            return self.store.update(job_id, status=FAILED, finished_at=_now(), error=str(exc))
        except Exception:  # noqa: BLE001 - the details stay in the server log, not in the response
            traceback.print_exc()
            return self.store.update(job_id, status=FAILED, finished_at=_now(),
                                     error="処理中にエラーが発生しました。入力ファイルを確認してください。")
        finally:
            _CURRENT.store, _CURRENT.job_id = None, None

    def shutdown(self, wait: bool = True) -> None:
        if self._pool:
            self._pool.shutdown(wait=wait, cancel_futures=not wait)
