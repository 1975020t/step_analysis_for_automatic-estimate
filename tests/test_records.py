"""The background jobs and the quote output log in the database (several API processes can share them)."""
from __future__ import annotations

import csv
import json
import threading
import time
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

import tests.test_api as api_tests
from api.main import create_app
from src.db.session import open_database
from src.services.jobs import DONE, QUEUED, RUNNING, JobQueue
from src.db.models import Staff
from src.services.platform.records import DbJobStore, DbQuoteLog, import_files, job_sessions

ISSUED = datetime(2026, 10, 12, 9, 0)


@pytest.fixture
def sessions(tmp_path):
    return open_database(f"sqlite:///{tmp_path / 'app.db'}")


def test_job_store_keeps_the_whole_job(sessions):
    store = DbJobStore(sessions)
    job = store.create("analysis", {"file_id": "f_0123456789abcdef", "thickness_mm": 1.6})
    assert store.read(job["job_id"]) == job and job["status"] == QUEUED
    store.update(job["job_id"], status=RUNNING, progress={"stage": "解析", "values": {"板厚": 1.6}})
    assert store.read(job["job_id"])["progress"] == {"stage": "解析", "values": {"板厚": 1.6}}
    assert [j["job_id"] for j in store.unfinished()] == [job["job_id"]]
    store.update(job["job_id"], status=DONE, result={"ok": True})
    assert store.unfinished() == [] and store.read(job["job_id"])["result"] == {"ok": True}
    for bad in ("j_missing", "x", "j_../a", None):
        with pytest.raises(KeyError):
            store.read(bad)


def test_the_queue_runs_on_the_database_store(sessions):
    store = DbJobStore(sessions)
    queue = JobQueue(store, {"double": lambda p: {"value": p["value"] * 2}}, workers=1)
    job = queue.submit("double", {"value": 21})
    queue.shutdown(wait=True)
    assert store.read(job["job_id"])["status"] == DONE and store.read(job["job_id"])["result"] == {"value": 42}


def test_progress_does_not_wait_for_the_running_job_with_sqlite(tmp_path, sessions):
    """A job holds the write lock of app.db until it commits; its progress goes to jobs.db and is not blocked."""
    store = DbJobStore(job_sessions(f"sqlite:///{tmp_path / 'app.db'}", sessions))
    job = store.create("estimate", {"quote_id": 1})
    with sessions() as s:
        s.add(Staff(name="試験"))
        s.flush()  # the write lock of app.db is held from here until the end of the session
        started = time.monotonic()
        store.update(job["job_id"], progress={"stage": "見積の計算"})
        assert time.monotonic() - started < 2
    assert (tmp_path / "jobs.db").exists() and store.read(job["job_id"])["progress"] == {"stage": "見積の計算"}
    assert job_sessions("postgresql+psycopg://u:p@db/estimate", sessions) is sessions


def test_quote_numbers_are_unique_when_issued_at_the_same_moment(sessions):
    log = DbQuoteLog(sessions)
    numbers: list[str] = []
    lock = threading.Lock()

    def issue():
        number = log.issue(ISSUED, {"customer": "株式会社テスト", "total": 1000})
        with lock:
            numbers.append(number)

    threads = [threading.Thread(target=issue) for _ in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(numbers) == [f"Q20261012-{i:03d}" for i in range(1, 13)]
    assert log.next_number(ISSUED, taken=["Q20261012-020"]) == "Q20261012-021"
    assert log.rows()[0]["customer"] == "株式会社テスト" and log.rows()[0]["issued_at"] == "2026-10-12T09:00:00"


def test_files_of_an_earlier_version_are_taken_in_once(tmp_path, sessions):
    jobs = tmp_path / "jobs"
    jobs.mkdir()
    old = {"job_id": "j_00112233aabbccdd", "kind": "drawing", "status": DONE, "input": {}, "result": {"reading": {}}}
    (jobs / "j_00112233aabbccdd.json").write_text(json.dumps(old), encoding="utf-8")
    (jobs / "j_broken.json").write_text("{", encoding="utf-8")
    log_path = tmp_path / "quote_log.csv"
    with log_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["quote_no", "issued_at", "customer", "subject", "drawing_no",
                                                    "quantity", "total", "is_estimate", "input_files"])
        writer.writeheader()
        writer.writerow({"quote_no": "Q20261012-007", "issued_at": "2026-10-12T08:00:00", "customer": "A"})
    assert import_files(sessions, sessions, jobs, log_path) == {"jobs": 1, "quote_log": 1}
    assert import_files(sessions, sessions, jobs, log_path) == {"jobs": 0, "quote_log": 0}
    assert DbJobStore(sessions).read("j_00112233aabbccdd") == old
    assert DbQuoteLog(sessions).next_number(ISSUED) == "Q20261012-008"  # the numbering goes on


def test_the_api_keeps_jobs_and_the_quote_log_in_the_database(tmp_path):
    settings = api_tests.settings_for(tmp_path)
    with TestClient(create_app(settings, reader_factory=api_tests.FakeReader)) as client:
        api_tests.test_step_analysis_quote_document_and_similar_quotes(client)
        service = client.app.state.service
        assert isinstance(service.job_store, DbJobStore) and len(service.quote_log().rows()) >= 1
    assert not settings.jobs_dir.exists() and not settings.quote_log_path.exists()
