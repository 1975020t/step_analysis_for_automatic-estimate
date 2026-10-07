"""The platform: the database, the masters and the history kept in it, the file store and the background jobs,
shared by every operation of the screens (src/services/platform/*.py) and by the API (api/main.py).
"""
from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Iterator

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.db.models import Counter, PastQuoteRow
from src.db.seed import seed
from src.db.session import open_database
from src.master_loader import MasterLoader
from src.past_quotes import PastQuote
from src.services.estimate import EstimateService, ReaderFactory, ServiceError
from src.services.settings import Settings
from src.similar_quotes import SimilarQuoteSearch

PAST_FIELDS = [c for c in PastQuote.__dataclass_fields__]


def bump(session: Session, name: str) -> int:
    """Take the next value of a counter (row lock on PostgreSQL; SQLite serialises writers)."""
    row = session.get(Counter, name, with_for_update=True)
    if row is None:
        row = Counter(name=name, value=0)
        session.add(row)
        session.flush()
    row.value += 1
    session.flush()
    return row.value


def counter(session: Session, name: str) -> int:
    row = session.get(Counter, name)
    return row.value if row else 0


def past_quote(row: PastQuoteRow) -> PastQuote:
    return PastQuote(**{f: getattr(row, f) for f in PAST_FIELDS})


class DbHistory:
    """The quote history in the database, with the interface of src.past_quotes.HistoryStore."""

    def __init__(self, platform: "Platform") -> None:
        self.platform = platform

    def load(self) -> list[PastQuote]:
        with self.platform.session() as s:
            return [past_quote(r) for r in s.scalars(select(PastQuoteRow).order_by(PastQuoteRow.date, PastQuoteRow.quote_no))]

    def append(self, new: list[PastQuote], **links) -> tuple[int, int]:
        with self.platform.session() as s:
            known = {(r.quote_no, r.date, r.customer) for r in s.execute(
                select(PastQuoteRow.quote_no, PastQuoteRow.date, PastQuoteRow.customer))}
            added = 0
            for q in new:
                key = (q.quote_no, q.date, q.customer)
                if key in known:
                    continue
                known.add(key)
                s.add(PastQuoteRow(**{f: getattr(q, f) for f in PAST_FIELDS}, **links))
                added += 1
            if added:
                bump(s, "history_rev")
            s.commit()
        return added, len(new) - added

    def set_outcome(self, quote_no: str, outcome: str, customer: str | None = None) -> None:
        with self.platform.session() as s:
            query = select(PastQuoteRow).where(PastQuoteRow.quote_no == quote_no)
            if customer is not None:
                query = query.where(PastQuoteRow.customer == customer)
            row = s.scalars(query.order_by(PastQuoteRow.id)).first()
            if row is None:
                raise KeyError(quote_no)
            row.outcome = outcome
            bump(s, "history_rev")
            s.commit()


class RecordedReader:
    """Replays recorded drawing readings (DRAWING_READER=recorded:<file.json>): no Claude API call.
    The file holds one reading, or {"readings": {"<file stem>": reading, ...}, "default": reading}."""

    def __init__(self, path: str | Path) -> None:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        self.readings = data.get("readings") if "readings" in data else {}
        self.default = data.get("default") if "readings" in data else data

    def read(self, path) -> dict:
        stem = Path(str(path)).stem
        reading = next((r for k, r in self.readings.items() if stem == k or stem.endswith(k)), self.default)
        if reading is None:
            raise ValueError("記録された読み取り結果がありません")
        return {k: v for k, v in json.loads(json.dumps(reading)).items() if not k.startswith("_")}


class PlatformEstimateService(EstimateService):
    """EstimateService whose masters and history are the database's (the API endpoints of the first stage keep
    working, with the same data as the screens)."""

    def __init__(self, platform: "Platform", settings: Settings, reader_factory: ReaderFactory | None) -> None:
        self.platform = platform
        super().__init__(settings, reader_factory=reader_factory)

    @property
    def masters(self) -> MasterLoader:  # type: ignore[override]
        return self.platform.masters()

    @masters.setter
    def masters(self, _value) -> None:  # the CSV masters of the base class are not used
        pass

    def history_store(self) -> DbHistory:  # type: ignore[override]
        return DbHistory(self.platform)

    def search_index(self) -> SimilarQuoteSearch:  # type: ignore[override]
        return self.platform.history_index()


class Platform:
    def __init__(self, settings: Settings, reader_factory: ReaderFactory | None = None) -> None:
        if reader_factory is None and settings.drawing_reader.startswith("recorded:"):
            path = settings.drawing_reader.split(":", 1)[1]
            reader_factory = lambda: RecordedReader(path)  # noqa: E731
        self.settings = settings
        self.sessions = open_database(settings.db_url)
        with self.sessions() as s:
            seed(s, settings.data_dir, settings.history_path)
        self._lock = threading.Lock()
        self._masters: tuple[int, MasterLoader] | None = None
        self._index: tuple[tuple, SimilarQuoteSearch] | None = None
        self.service = PlatformEstimateService(self, settings, reader_factory)
        self.files = self.service.files
        self.queue = None  # set by the API (JobQueue)

    @contextmanager
    def session(self) -> Iterator[Session]:
        with self.sessions() as s:
            yield s

    # ------------------------------------------------------------ masters and history (cached until changed)
    def masters(self) -> MasterLoader:
        from src.services.platform.masters import load_masters

        with self.session() as s:
            rev = counter(s, "masters_rev")
            with self._lock:
                if self._masters and self._masters[0] == rev:
                    return self._masters[1]
            loaded = load_masters(s)
        with self._lock:
            self._masters = (rev, loaded)
        return loaded

    def history_index(self) -> SimilarQuoteSearch:
        with self.session() as s:
            key = (counter(s, "history_rev"), counter(s, "masters_rev"),
                   s.scalar(select(func.count()).select_from(PastQuoteRow)))
        with self._lock:
            if self._index and self._index[0] == key:
                return self._index[1]
        index = SimilarQuoteSearch(DbHistory(self).load(), self.masters())
        with self._lock:
            self._index = (key, index)
        return index

    def company(self):
        from src.services.platform.masters import load_company_db

        with self.session() as s:
            return load_company_db(s)

    # ------------------------------------------------------------ jobs
    def submit(self, kind: str, payload: dict) -> dict:
        if self.queue is None:
            raise ServiceError("処理の受付が起動していません。", 503, "NOT_READY")
        return self.queue.submit(kind, payload)

    def job(self, job_id: str) -> dict | None:
        if not job_id:
            return None
        try:
            return self.service.job_store.read(job_id)
        except KeyError:
            return None


def check_version(row, version: int | None, what: str) -> None:
    """Refuse a save based on an older version (someone else saved in between): HTTP 409, never a silent overwrite."""
    if version is not None and row.version != version:
        raise ServiceError(f"この{what}は、ほかの人が先に変更しました。最新の内容を表示します。", 409, "CONFLICT")


def iso(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)
