"""The background jobs and the quote output log in the database (they were files in ESTIMATE_STORAGE_DIR: jobs/*.json
and quote_log.csv), so that several API processes or workers can share them.

`DbJobStore` has the interface of `src.services.jobs.JobStore`, `DbQuoteLog` that of `src.quote_log.QuoteLog`.
`import_files` takes in what an earlier version left in the folder (once; rows already in the database are kept).

With SQLite the jobs are kept in a file of their own (`jobs.db` next to `app.db`): a running job holds the write
lock of `app.db` until it commits, and its progress (written from the same thread) must not wait for it. A server
database (PostgreSQL) locks rows, so there the `jobs` table of the main database is used.
"""
from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from src.db.models import JobRow, QuoteLogRow, now
from src.db.retry import CHUNK, backoff, is_conflict, retry_on_conflict
from src.quote_log import FIELDS
from src.services.jobs import QUEUED, RUNNING, _now
from src.services.storage import new_id

_LOG_COLUMNS = [f for f in FIELDS if f != "quote_no"]


def job_sessions(db_url: str, sessions: sessionmaker[Session]) -> sessionmaker[Session]:
    """Where the jobs are kept: the main database, or `jobs.db` beside an SQLite `app.db` (see above)."""
    if not db_url.startswith("sqlite"):
        return sessions
    from src.db.session import make_engine

    main = create_engine(db_url).url.database
    path = Path(main).with_name("jobs.db") if main and main != ":memory:" else None
    engine = make_engine(f"sqlite:///{path}" if path else "sqlite://")
    JobRow.__table__.create(engine, checkfirst=True)
    return sessionmaker(engine, expire_on_commit=False)


class DbJobStore:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self.sessions = sessions

    @staticmethod
    def _check(job_id: str) -> str:
        if not isinstance(job_id, str) or not job_id.startswith("j_") or not job_id[2:].isalnum():
            raise KeyError(job_id)
        return job_id

    def create(self, kind: str, payload: dict) -> dict:
        job = {"job_id": new_id("j"), "kind": kind, "status": QUEUED, "input": payload,
               "created_at": _now(),
               "started_at": None, "finished_at": None, "result": None, "error": None}
        self.write(job)
        return job

    def read(self, job_id: str) -> dict:
        with self.sessions() as s:
            row = s.get(JobRow, self._check(job_id))
            if row is None:
                raise KeyError(job_id)
            return json.loads(json.dumps(row.data))

    def write(self, job: dict) -> None:
        job_id = self._check(job["job_id"])
        data = json.loads(json.dumps(job, ensure_ascii=False))

        def save() -> None:
            with self.sessions() as s:
                row = s.get(JobRow, job_id)
                if row is None:
                    s.add(JobRow(job_id=job_id, kind=job["kind"], status=job["status"], data=data))
                else:
                    row.status, row.data, row.updated_at = job["status"], data, now()
                s.commit()

        retry_on_conflict(save)

    def update(self, job_id: str, **changes) -> dict:
        job = self.read(job_id)
        job.update(changes)
        self.write(job)
        return job

    def unfinished(self) -> list[dict]:
        with self.sessions() as s:
            rows = s.scalars(select(JobRow).where(JobRow.status.in_([QUEUED, RUNNING])).order_by(JobRow.job_id)).all()
            return [json.loads(json.dumps(r.data)) for r in rows]


class DbQuoteLog:
    """Quote numbers `Q` + issue date + `-` + the day's serial. Two outputs at the same moment both try the next
    number; the primary key lets one of them in and the other takes the number after it."""

    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self.sessions = sessions

    def rows(self) -> list[dict[str, str]]:
        with self.sessions() as s:
            return [{"quote_no": r.quote_no, **{k: getattr(r, k) for k in _LOG_COLUMNS}}
                    for r in s.scalars(select(QuoteLogRow).order_by(QuoteLogRow.issued_at, QuoteLogRow.quote_no))]

    def next_number(self, issued_at: datetime, taken: list[str] | tuple[str, ...] = ()) -> str:
        prefix = f"Q{issued_at:%Y%m%d}-"
        with self.sessions() as s:
            numbers = list(s.scalars(select(QuoteLogRow.quote_no).where(QuoteLogRow.quote_no.like(prefix + "%"))))
        numbers += list(taken)
        serials = [int(n[len(prefix):]) for n in numbers if n.startswith(prefix) and n[len(prefix):].isdigit()]
        return f"{prefix}{max(serials, default=0) + 1:03d}"

    def issue(self, issued_at: datetime, row: dict[str, object], taken: list[str] | tuple[str, ...] = ()) -> str:
        values = {k: "" if row.get(k) is None else str(row.get(k, "")) for k in _LOG_COLUMNS}
        values["issued_at"] = issued_at.isoformat(timespec="seconds")
        for _ in range(50):
            number = self.next_number(issued_at, taken)
            with self.sessions() as s:
                s.add(QuoteLogRow(quote_no=number, **values))
                try:
                    s.commit()
                    return number
                except IntegrityError:  # taken by an output at the same moment: the next one
                    s.rollback()
                except DBAPIError as exc:  # the same on Aurora DSQL (a conflict at commit)
                    if not is_conflict(exc):
                        raise
                    s.rollback()
                    backoff(0)
        raise RuntimeError("見積番号を決められませんでした。もう一度出力してください。")


def import_files(jobs: sessionmaker[Session], log: sessionmaker[Session], jobs_dir: Path,
                 quote_log_path: Path) -> dict[str, int]:
    """Take in the job files (jobs/j_*.json) and the quote log CSV of an earlier version. The files stay."""
    counts = {"jobs": 0, "quote_log": 0}
    with jobs() as s:
        if jobs_dir.is_dir():
            known = set(s.scalars(select(JobRow.job_id)))
            for path in sorted(jobs_dir.glob("j_*.json")):
                try:
                    job = json.loads(path.read_text(encoding="utf-8"))
                    job_id = DbJobStore._check(job["job_id"])
                except (ValueError, KeyError, TypeError):
                    continue
                if job_id in known:
                    continue
                s.add(JobRow(job_id=job_id, kind=str(job.get("kind", "")), status=str(job.get("status", "")), data=job))
                known.add(job_id)
                counts["jobs"] += 1
                if counts["jobs"] % CHUNK == 0:
                    s.commit()
        s.commit()
    with log() as s:
        if quote_log_path.is_file():
            known = set(s.scalars(select(QuoteLogRow.quote_no)))
            with quote_log_path.open(encoding="utf-8-sig", newline="") as handle:
                for row in csv.DictReader(handle):
                    number = (row.get("quote_no") or "").strip()
                    if not number or number in known:
                        continue
                    s.add(QuoteLogRow(quote_no=number, **{k: row.get(k) or "" for k in _LOG_COLUMNS}))
                    known.add(number)
                    counts["quote_log"] += 1
                    if counts["quote_log"] % CHUNK == 0:
                        s.commit()
        s.commit()
    return counts
