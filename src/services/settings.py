"""Where things are kept and the API limits, from environment variables (defaults: the repository layout).

  ESTIMATE_DATA_DIR      masters (materials, processes, finishes, pricing policy, company)   default: data
  ESTIMATE_STORAGE_DIR   uploads, jobs, documents, 3D view files                              default: output
  PAST_QUOTES_PATH       quote history CSV (committed)                     default: data/past_quotes/history.csv
  QUOTE_LOG_PATH         issued-quote log                                  default: <storage>/quote_log.csv
  API_TOKEN              when set, every API call needs it (Authorization: Bearer ... or X-API-Token)
  MAX_UPLOAD_MB          largest accepted upload                                              default: 50
  JOB_WORKERS            background workers for analyses and drawing readings                 default: 2
  DATABASE_URL           the database (PostgreSQL: postgresql+psycopg://user:pass@host/db)
                                                                         default: SQLite <storage>/app.db
  DRAWING_READER         "recorded:<file.json>" replays recorded drawing readings (demo / screenshots,
                         no Claude API call)                              default: the Claude API reader
  WEB_DIST               the built screens (web/dist) served by the API at /   default: web/dist when present
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    data_dir: Path = Path("data")
    storage_dir: Path = Path("output")
    history_path: Path = Path("data/past_quotes/history.csv")
    quote_log_path: Path = Path("output/quote_log.csv")
    api_token: str = ""
    max_upload_bytes: int = 50 * 1024 * 1024
    job_workers: int = 2
    database_url: str = ""
    drawing_reader: str = ""
    web_dist: Path | None = None
    extra: dict = field(default_factory=dict)

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "Settings":
        env = dict(os.environ if env is None else env)
        storage = Path(env.get("ESTIMATE_STORAGE_DIR") or "output")
        return cls(
            data_dir=Path(env.get("ESTIMATE_DATA_DIR") or "data"),
            storage_dir=storage,
            history_path=Path(env.get("PAST_QUOTES_PATH") or "data/past_quotes/history.csv"),
            quote_log_path=Path(env.get("QUOTE_LOG_PATH") or storage / "quote_log.csv"),
            api_token=env.get("API_TOKEN", "").strip(),
            max_upload_bytes=int(float(env.get("MAX_UPLOAD_MB") or 50) * 1024 * 1024),
            job_workers=max(1, int(env.get("JOB_WORKERS") or 2)),
            database_url=env.get("DATABASE_URL", "").strip(),
            drawing_reader=env.get("DRAWING_READER", "").strip(),
            web_dist=Path(env["WEB_DIST"]) if env.get("WEB_DIST") else None,
        )

    @property
    def db_url(self) -> str:
        if self.database_url:
            return self.database_url
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{self.storage_dir.resolve() / 'app.db'}"

    @property
    def uploads_dir(self) -> Path:
        return self.storage_dir / "uploads"

    @property
    def jobs_dir(self) -> Path:
        return self.storage_dir / "jobs"

    @property
    def documents_dir(self) -> Path:
        return self.storage_dir / "documents"

    @property
    def viewer_dir(self) -> Path:
        return self.storage_dir / "viewer"
