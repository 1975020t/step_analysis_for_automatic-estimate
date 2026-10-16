"""Where things are kept and the API limits, from environment variables (defaults: the repository layout).

  ESTIMATE_DATA_DIR      masters (materials, processes, finishes, pricing policy, company)   default: data
  ESTIMATE_STORAGE_DIR   uploads, documents, 3D view files, SQLite                           default: output
  ESTIMATE_STORAGE_URL   s3://bucket/prefix keeps the uploads, documents and 3D view files in S3 instead of
                         ESTIMATE_STORAGE_DIR (SQLite stays in the folder)   default: none
  PAST_QUOTES_PATH       quote history CSV (committed)                     default: data/past_quotes/history.csv
  QUOTE_LOG_PATH         issued-quote log of an earlier version (taken into the database on start)
                                                                         default: <storage>/quote_log.csv
  API_TOKEN              when set, every API call needs it (Authorization: Bearer ... or X-API-Token)
  TENANT_TOKENS          companies and their API tokens, "<company id>=<token>,..." (several companies in one
                         system; a call with a company's token works on that company's data only)   default: none
  COGNITO_USER_POOL_ID   sign-in with Amazon Cognito (e.g. ap-northeast-1_AbCdEf123): the screens show a login page and
                         the company comes from the user's `custom:tenant_id` (api/login.py)   default: none
  COGNITO_CLIENT_ID      the app client of the screens (public, authorization code with PKCE)
  COGNITO_DOMAIN         the sign-in pages, https://<prefix>.auth.<region>.amazoncognito.com (or a custom domain)
  COGNITO_TENANT_CLAIM   the claim holding the company id                   default: custom:tenant_id
  COGNITO_ISSUER         only for a stand-in user pool (tests): the issuer instead of the one of COGNITO_USER_POOL_ID
  MAX_UPLOAD_MB          largest accepted upload                                              default: 50
  DIRECT_DOWNLOAD_MB     with S3, larger files are fetched straight from the bucket (a short-lived link) instead
                         of through the API (a response of AWS Lambda holds about 6MB)        default: 4
  JOB_WORKERS            background workers for analyses and drawing readings                 default: 2
  DATABASE_URL           the database (PostgreSQL: postgresql+psycopg://user:pass@host/db)
                                                                         default: SQLite <storage>/app.db
  DRAWING_READER         "recorded:<file.json>" replays recorded drawing readings (demo / screenshots,
                         no Claude API call)                              default: the Claude API reader
  PDF_SHAPE_ESTIMATOR    "recorded:<file.json>" replays recorded shape estimates of drawing PDFs (demo / screenshots),
                         "off" never estimates shapes from drawing PDFs        default: the Claude API estimator
  WEB_DIST               the built screens (web/dist) served by the API at /   default: web/dist when present
  CORS_ORIGINS           screens on another domain that may call the API, "https://app.example.com,..." (not needed
                         when the screens and the API are behind one domain)   default: none
  JOBS_STATE_MACHINE_ARN on AWS: the jobs run as executions of this Step Functions state machine (prepare -> extract
                         -> finish, deploy/aws/) instead of threads of the API process   default: none (threads)
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
    direct_download_bytes: int = 4 * 1024 * 1024
    job_workers: int = 2
    database_url: str = ""
    drawing_reader: str = ""
    pdf_shape_estimator: str = ""
    web_dist: Path | None = None
    storage_url: str = ""
    tenant_tokens: str = ""
    cognito_user_pool_id: str = ""
    cognito_client_id: str = ""
    cognito_domain: str = ""
    cognito_tenant_claim: str = "custom:tenant_id"
    cognito_issuer: str = ""          # derived from the user pool; set only for a stand-in (tests)
    cors_origins: tuple[str, ...] = ()
    jobs_state_machine: str = ""
    extra: dict = field(default_factory=dict)

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "Settings":
        env = dict(os.environ if env is None else env)
        storage = Path(env.get("ESTIMATE_STORAGE_DIR") or "output")
        return cls(
            data_dir=Path(env.get("ESTIMATE_DATA_DIR") or "data"),
            storage_dir=storage,
            storage_url=env.get("ESTIMATE_STORAGE_URL", "").strip(),
            history_path=Path(env.get("PAST_QUOTES_PATH") or "data/past_quotes/history.csv"),
            quote_log_path=Path(env.get("QUOTE_LOG_PATH") or storage / "quote_log.csv"),
            api_token=env.get("API_TOKEN", "").strip(),
            max_upload_bytes=int(float(env.get("MAX_UPLOAD_MB") or 50) * 1024 * 1024),
            direct_download_bytes=int(float(env.get("DIRECT_DOWNLOAD_MB") or 4) * 1024 * 1024),
            job_workers=max(1, int(env.get("JOB_WORKERS") or 2)),
            database_url=env.get("DATABASE_URL", "").strip(),
            drawing_reader=env.get("DRAWING_READER", "").strip(),
            pdf_shape_estimator=env.get("PDF_SHAPE_ESTIMATOR", "").strip(),
            web_dist=Path(env["WEB_DIST"]) if env.get("WEB_DIST") else None,
            tenant_tokens=env.get("TENANT_TOKENS", "").strip(),
            cognito_user_pool_id=env.get("COGNITO_USER_POOL_ID", "").strip(),
            cognito_client_id=env.get("COGNITO_CLIENT_ID", "").strip(),
            cognito_domain=env.get("COGNITO_DOMAIN", "").strip().rstrip("/"),
            cognito_tenant_claim=env.get("COGNITO_TENANT_CLAIM", "").strip() or "custom:tenant_id",
            cognito_issuer=env.get("COGNITO_ISSUER", "").strip().rstrip("/"),
            cors_origins=tuple(o.strip().rstrip("/") for o in env.get("CORS_ORIGINS", "").split(",") if o.strip()),
            jobs_state_machine=env.get("JOBS_STATE_MACHINE_ARN", "").strip(),
        )

    @property
    def login(self) -> bool:
        """Sign-in with Cognito (the screens show a login page)."""
        return bool(self.cognito_user_pool_id and self.cognito_client_id)

    @property
    def login_issuer(self) -> str:
        if self.cognito_issuer:
            return self.cognito_issuer
        region = self.cognito_user_pool_id.split("_", 1)[0]
        return f"https://cognito-idp.{region}.amazonaws.com/{self.cognito_user_pool_id}"

    def tokens(self) -> dict[str, str]:
        """API token -> company id (API_TOKEN is the token of the `default` company)."""
        from src.tenancy import DEFAULT_TENANT, check_tenant

        tokens = {self.api_token: DEFAULT_TENANT} if self.api_token else {}
        for item in filter(None, (p.strip() for p in self.tenant_tokens.split(","))):
            tenant, sep, token = item.partition("=")
            if not sep or not token.strip():
                raise ValueError("TENANT_TOKENS は <会社ID>=<トークン> をカンマで区切って指定してください")
            tokens[token.strip()] = check_tenant(tenant.strip())
        return tokens

    @property
    def db_url(self) -> str:
        if self.database_url:
            return self.database_url
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{self.storage_dir.resolve() / 'app.db'}"

    def blob_store(self):
        """Where the uploads, documents and 3D view files are kept (a folder or S3)."""
        from src.services.storage import TenantBlobStore, blob_store

        return TenantBlobStore(blob_store(self.storage_url, self.storage_dir))

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
