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

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from src.db.models import Counter, PastQuoteRow, Tenant
from src.db.retry import CHUNK
from src.db.seed import seed
from src.db.session import open_database
from src.db.tenant_filter import key
from src.master_loader import MasterLoader
from src.past_quotes import PastQuote
from src.services.estimate import EstimateService, ReaderFactory, ServiceError
from src.services.platform.records import DbJobStore, DbQuoteLog, import_files, job_sessions
from src.services.settings import Settings
from src.similar_quotes import SimilarQuoteSearch
from src.tenancy import DEFAULT_TENANT, current_tenant, tenant_scope

PAST_FIELDS = [c for c in PastQuote.__dataclass_fields__]


def bump(session: Session, name: str, start: int = 0) -> int:
    """Take the next value of a counter, safely when several people save at the same moment.

    The database adds 1 itself (``value = value + 1``): reading the value and writing value+1 from Python let two
    saves take the same number (SQLite does not lock what it reads), and the second save then failed on the
    unique case / document number. A missing counter is created with ``start`` (insert-if-absent, so two first
    uses do not collide either). The increment locks the row (PostgreSQL) or the database (SQLite) until commit."""
    dialect = session.get_bind().dialect.name
    if dialect in ("sqlite", "postgresql"):
        insert = __import__(f"sqlalchemy.dialects.{dialect}", fromlist=["insert"]).insert
        session.execute(insert(Counter).values(tenant_id=current_tenant(), name=name, value=start)
                        .on_conflict_do_nothing(index_elements=["tenant_id", "name"]))
    elif session.get(Counter, key(Counter, name)) is None:
        session.add(Counter(name=name, value=start))
        session.flush()
    session.execute(update(Counter).where(Counter.name == name).values(value=Counter.value + 1)
                    .execution_options(synchronize_session=False))
    value = session.execute(select(Counter.value).where(Counter.name == name)).scalar_one()
    stale = session.identity_map.get(session.identity_key(Counter, (current_tenant(), name)))
    if stale is not None:
        session.expire(stale)
    return value


def counter(session: Session, name: str) -> int:
    row = session.get(Counter, key(Counter, name))
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
                if added % CHUNK == 0:  # a large import: several transactions (Aurora DSQL)
                    s.commit()
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

    def __init__(self, platform: "Platform", settings: Settings, reader_factory: ReaderFactory | None,
                 shape_estimator_factory=None) -> None:
        self.platform = platform
        super().__init__(settings, reader_factory=reader_factory, shape_estimator_factory=shape_estimator_factory)
        self.job_store = DbJobStore(platform.job_sessions)

    def quote_log(self) -> DbQuoteLog:  # type: ignore[override]
        return DbQuoteLog(self.platform.sessions)

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
    def __init__(self, settings: Settings, reader_factory: ReaderFactory | None = None,
                 shape_estimator_factory=None) -> None:
        if reader_factory is None and settings.drawing_reader.startswith("recorded:"):
            path = settings.drawing_reader.split(":", 1)[1]
            reader_factory = lambda: RecordedReader(path)  # noqa: E731
        self.settings = settings
        self.sessions = open_database(settings.db_url)
        with tenant_scope(DEFAULT_TENANT), self.sessions() as s:
            seed(s, settings.data_dir, settings.history_path)
            if s.get(Tenant, DEFAULT_TENANT) is None:
                s.add(Tenant(tenant_id=DEFAULT_TENANT))
                s.commit()
        self.job_sessions = job_sessions(settings.db_url, self.sessions)
        import_files(self.job_sessions, self.sessions, settings.jobs_dir, settings.quote_log_path)  # an earlier version's
        self._lock = threading.Lock()
        self._tenants = {DEFAULT_TENANT}
        self._masters: dict[str, tuple[int, MasterLoader]] = {}       # per company
        self._index: dict[str, tuple[tuple, SimilarQuoteSearch]] = {}
        self.service = PlatformEstimateService(self, settings, reader_factory, shape_estimator_factory)
        self.files = self.service.files
        self.queue = None  # set by the API (JobQueue)

    @contextmanager
    def session(self) -> Iterator[Session]:
        with self.sessions() as s:
            yield s

    # ------------------------------------------------------------ companies
    def ensure_tenant(self, tenant: str) -> None:
        """A company seen for the first time gets its starting data: the masters of data/*.csv, the statuses,
        attributes and templates, empty company details (no sample history or partners)."""
        if tenant in self._tenants:
            return
        with self._lock:
            with tenant_scope(tenant), self.session() as s:
                if s.get(Tenant, tenant) is None:
                    seed(s, self.settings.data_dir, self.settings.history_path, sample=False)
                    s.add(Tenant(tenant_id=tenant))
                    s.commit()
            self._tenants.add(tenant)

    # ------------------------------------------------------------ masters and history (cached until changed)
    def masters(self) -> MasterLoader:
        from src.services.platform.masters import load_masters

        tenant = current_tenant()
        with self.session() as s:
            rev = counter(s, "masters_rev")
            with self._lock:
                cached = self._masters.get(tenant)
                if cached and cached[0] == rev:
                    return cached[1]
            loaded = load_masters(s)
        with self._lock:
            self._masters[tenant] = (rev, loaded)
        return loaded

    def history_index(self) -> SimilarQuoteSearch:
        with self.session() as s:
            key = (counter(s, "history_rev"), counter(s, "masters_rev"),
                   s.scalar(select(func.count()).select_from(PastQuoteRow)))
        tenant = current_tenant()
        with self._lock:
            cached = self._index.get(tenant)
            if cached and cached[0] == key:
                return cached[1]
        index = SimilarQuoteSearch(DbHistory(self).load(), self.masters())
        with self._lock:
            self._index[tenant] = (key, index)
        return index

    def company(self):
        from src.services.platform.masters import load_company_db

        with self.session() as s:
            return load_company_db(s)

    # ------------------------------------------------------------ jobs
    def submit(self, kind: str, payload: dict, start: bool = True) -> dict:
        if self.queue is None:
            raise ServiceError("処理の受付が起動していません。", 503, "NOT_READY")
        return self.queue.submit(kind, payload, start)

    def start(self, job_id: str) -> None:
        self.queue.start(job_id)

    def job(self, job_id: str) -> dict | None:
        if not job_id:
            return None
        try:
            return self.service.job_of_tenant(job_id)
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
