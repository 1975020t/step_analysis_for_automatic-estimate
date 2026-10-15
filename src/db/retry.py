"""Running work again when the database refuses it for a conflicting write (Aurora DSQL).

Aurora DSQL never waits on a lock: two transactions that change the same row both run, and the one that commits
second fails with SQLSTATE 40001 (messages OC000 / OC001). The work is then simply done again from the start, on
the data as it is now. PostgreSQL (row locks) and SQLite (one writer) rarely or never raise it; the retry costs
nothing there.

`retry_on_conflict(fn)` runs a unit of work again (a job step, a counter, a log line). The API runs a whole
request again (`ConflictRetryRoute`), but only while nothing of it has been committed yet (`track_commits`).
"""
from __future__ import annotations

import random
import time
from contextvars import ContextVar
from typing import Callable, TypeVar

from sqlalchemy import event
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

T = TypeVar("T")
ATTEMPTS = 5
CHUNK = 1000  # rows per transaction when many are added (DSQL takes at most 3,000 changed rows in one)


class ConflictError(RuntimeError):
    """Still conflicting after the retries (the API answers 409: someone else saved at the same moment)."""


def is_conflict(exc: BaseException | None) -> bool:
    while exc is not None:
        if isinstance(exc, DBAPIError):
            orig = exc.orig
            code = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
            if code == "40001" or any(c in str(orig) for c in ("OC000", "OC001")):
                return True
        exc = exc.__cause__ or exc.__context__
    return False


def delay(attempt: int) -> float:
    """A random wait before the next try, longer each time (two that conflicted do not meet again)."""
    return random.uniform(0, 0.02 * 2 ** attempt)


def backoff(attempt: int) -> None:
    time.sleep(delay(attempt))


def retry_on_conflict(fn: Callable[[], T], attempts: int = ATTEMPTS) -> T:
    for attempt in range(attempts):
        try:
            return fn()
        except Exception as exc:
            if not is_conflict(exc):
                raise
            if attempt == attempts - 1:
                raise ConflictError("同時に保存された内容と重なりました。もう一度お試しください。") from exc
            backoff(attempt)
    raise AssertionError("unreachable")


# ---------------------------------------------------------------- commits of the current request
_commits: ContextVar[list | None] = ContextVar("commits", default=None)


def track_commits() -> list:
    """Start counting the commits of this request (the list is shared with the threads that serve it)."""
    seen: list = []
    _commits.set(seen)
    return seen


@event.listens_for(Session, "after_commit")
def _count_commit(_session) -> None:
    seen = _commits.get()
    if seen is not None:
        seen.append(1)
