"""File storage behind a small interface: local folders now, an object store (S3 compatible etc.) later.

Keys are generated here (never taken from a user) so a key cannot point outside the store.
"""
from __future__ import annotations

import json
import re
import secrets
import unicodedata
from pathlib import Path
from typing import Protocol

KINDS = {".step": "step", ".stp": "step", ".dxf": "dxf", ".pdf": "pdf", ".csv": "csv"}


class FileStore(Protocol):
    def save(self, data: bytes, filename: str) -> dict: ...
    def get(self, file_id: str) -> tuple[bytes, dict]: ...
    def path(self, file_id: str) -> Path: ...


def safe_name(filename: str) -> str:
    name = unicodedata.normalize("NFKC", Path(filename or "upload").name)
    name = re.sub(r"[^\w.\-()（）]+", "_", name).strip("._") or "upload"
    return name[:120]


def new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(8)}"


class LocalFileStore:
    """<root>/<file_id>/<file name> plus meta.json (name, kind, size)."""

    ID = re.compile(r"^f_[0-9a-f]{16}$")

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def save(self, data: bytes, filename: str) -> dict:
        file_id = new_id("f")
        name = safe_name(filename)
        folder = self.root / file_id
        folder.mkdir(parents=True, exist_ok=True)
        (folder / name).write_bytes(data)
        meta = {"file_id": file_id, "filename": name, "kind": KINDS.get(Path(name).suffix.lower(), "other"),
                "size": len(data)}
        (folder / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        return meta

    def meta(self, file_id: str) -> dict:
        if not self.ID.match(file_id or ""):
            raise KeyError(file_id)
        path = self.root / file_id / "meta.json"
        if not path.exists():
            raise KeyError(file_id)
        return json.loads(path.read_text(encoding="utf-8"))

    def path(self, file_id: str) -> Path:
        return self.root / file_id / self.meta(file_id)["filename"]

    def get(self, file_id: str) -> tuple[bytes, dict]:
        meta = self.meta(file_id)
        return (self.root / file_id / meta["filename"]).read_bytes(), meta
