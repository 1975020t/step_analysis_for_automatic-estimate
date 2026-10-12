"""File storage behind a small interface: a local folder or an object store (S3).

Two layers:
- `BlobStore` keeps bytes under a key (`uploads/f_.../name.step`, `documents/Q-.../quote.pdf`, `viewer/...`).
  `LocalBlobStore` is a folder (the layout of `ESTIMATE_STORAGE_DIR` is unchanged), `S3BlobStore` a bucket and
  prefix (`ESTIMATE_STORAGE_URL=s3://bucket/prefix`). `local_file` hands a path on this machine to the tools that
  read files themselves (CadQuery, pdfium): the file itself for a folder, a temporary copy for S3.
- `FileStore` keeps the uploaded files (a file id, its name, kind and size) in a `BlobStore`.
- `TenantBlobStore` puts each company's keys under `tenants/<company id>/` (the `default` company keeps the keys of
  before), so that one bucket holds every company's files apart.

Keys are generated here (never taken from a user) so a key cannot point outside the store.
"""
from __future__ import annotations

import json
import re
import secrets
import shutil
import tempfile
import unicodedata
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import Iterator, Protocol

from src.tenancy import DEFAULT_TENANT, current_tenant

KINDS = {".step": "step", ".stp": "step", ".dxf": "dxf", ".pdf": "pdf", ".csv": "csv"}
_KEY_PART = re.compile(r"^[\w.\-()（）]+$")


def safe_name(filename: str) -> str:
    name = unicodedata.normalize("NFKC", Path(filename or "upload").name)
    name = re.sub(r"[^\w.\-()（）]+", "_", name).strip("._") or "upload"
    return name[:120]


def new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(8)}"


def check_key(key: str) -> str:
    """A relative key of safe parts ("a/b/c.pdf"): no "..", no absolute path, no empty part."""
    parts = (key or "").split("/")
    if any(p in ("", ".", "..") or not _KEY_PART.match(p) for p in parts):
        raise KeyError(key)
    return key


class BlobStore(Protocol):
    def put(self, key: str, data: bytes) -> None: ...
    def get(self, key: str) -> bytes: ...          # KeyError when missing
    def exists(self, key: str) -> bool: ...
    def local_file(self, key: str): ...            # context manager -> Path (KeyError when missing)


class LocalBlobStore:
    """Keys are paths under a folder."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def _path(self, key: str) -> Path:
        return self.root / check_key(key)

    def put(self, key: str, data: bytes) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def get(self, key: str) -> bytes:
        path = self._path(key)
        if not path.is_file():
            raise KeyError(key)
        return path.read_bytes()

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    @contextmanager
    def local_file(self, key: str) -> Iterator[Path]:
        path = self._path(key)
        if not path.is_file():
            raise KeyError(key)
        yield path


class S3BlobStore:
    """Keys are objects under `<prefix>/` in a bucket. boto3 is imported only when this store is used."""

    def __init__(self, bucket: str, prefix: str = "", client=None) -> None:
        self.bucket = bucket
        self.prefix = prefix.strip("/")
        if client is None:
            import boto3

            client = boto3.client("s3")
        self.client = client

    def _key(self, key: str) -> str:
        key = check_key(key)
        return f"{self.prefix}/{key}" if self.prefix else key

    def put(self, key: str, data: bytes) -> None:
        self.client.put_object(Bucket=self.bucket, Key=self._key(key), Body=data)

    def get(self, key: str) -> bytes:
        try:
            return self.client.get_object(Bucket=self.bucket, Key=self._key(key))["Body"].read()
        except self.client.exceptions.NoSuchKey:
            raise KeyError(key) from None

    def exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=self._key(key))
            return True
        except self.client.exceptions.ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                return False
            raise

    @contextmanager
    def local_file(self, key: str) -> Iterator[Path]:
        data = self.get(key)
        folder = Path(tempfile.mkdtemp(prefix="blob_"))
        try:
            path = folder / PurePosixPath(check_key(key)).name  # keep the name: readers look at the suffix
            path.write_bytes(data)
            yield path
        finally:
            shutil.rmtree(folder, ignore_errors=True)


class TenantBlobStore:
    """The keys of the current company (src/tenancy.py): `tenants/<id>/<key>`, or `<key>` for `default`."""

    def __init__(self, blobs: BlobStore) -> None:
        self.blobs = blobs

    @staticmethod
    def _key(key: str) -> str:
        tenant = current_tenant()
        return check_key(key) if tenant == DEFAULT_TENANT else f"tenants/{tenant}/{check_key(key)}"

    def put(self, key: str, data: bytes) -> None:
        self.blobs.put(self._key(key), data)

    def get(self, key: str) -> bytes:
        return self.blobs.get(self._key(key))

    def exists(self, key: str) -> bool:
        return self.blobs.exists(self._key(key))

    def local_file(self, key: str):
        return self.blobs.local_file(self._key(key))


def blob_store(url: str, root: str | Path) -> BlobStore:
    """`s3://bucket/prefix` -> S3BlobStore, empty -> LocalBlobStore(root)."""
    url = (url or "").strip()
    if not url:
        return LocalBlobStore(root)
    if url.startswith("s3://"):
        bucket, _, prefix = url[5:].partition("/")
        if not bucket:
            raise ValueError("ESTIMATE_STORAGE_URL は s3://<バケット名>/<接頭辞> の形で指定してください")
        return S3BlobStore(bucket, prefix)
    raise ValueError(f"ESTIMATE_STORAGE_URL に対応していない形式です: {url}")


class FileStore:
    """Uploaded files: uploads/<file_id>/<file name> plus uploads/<file_id>/meta.json (name, kind, size)."""

    ID = re.compile(r"^f_[0-9a-f]{16}$")

    def __init__(self, blobs: BlobStore, folder: str = "uploads") -> None:
        self.blobs = blobs
        self.folder = folder

    def _key(self, file_id: str, name: str) -> str:
        return f"{self.folder}/{file_id}/{name}"

    def save(self, data: bytes, filename: str) -> dict:
        file_id = new_id("f")
        name = safe_name(filename)
        meta = {"file_id": file_id, "filename": name, "kind": KINDS.get(Path(name).suffix.lower(), "other"),
                "size": len(data)}
        self.blobs.put(self._key(file_id, name), data)
        self.blobs.put(self._key(file_id, "meta.json"), json.dumps(meta, ensure_ascii=False).encode("utf-8"))
        return meta

    def meta(self, file_id: str) -> dict:
        if not self.ID.match(file_id or ""):
            raise KeyError(file_id)
        return json.loads(self.blobs.get(self._key(file_id, "meta.json")).decode("utf-8"))

    def get(self, file_id: str) -> tuple[bytes, dict]:
        meta = self.meta(file_id)
        return self.blobs.get(self._key(file_id, meta["filename"])), meta

    @contextmanager
    def local_path(self, file_id: str) -> Iterator[Path]:
        """The file as a path on this machine, for the tools that open files themselves (CadQuery, pdfium)."""
        meta = self.meta(file_id)
        with self.blobs.local_file(self._key(file_id, meta["filename"])) as path:
            yield path

