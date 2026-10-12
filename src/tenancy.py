"""Which company (tenant) the current work is for.

Several companies share one database and one file store (pool model). Every row of a company's data carries its
`tenant_id` (src/db/models.py `TenantScoped`): reads see only the current company's rows and new rows get its id
(src/db/tenant_filter.py); files sit under `tenants/<id>/` (src/services/storage.py `TenantBlobStore`).

The company is set once per request from the sign-in (api/security.py) and once per background job from the job
(src/services/jobs.py); everything else reads `current_tenant()`. Without a sign-in (one company, as before) it is
`default`, whose files keep their places of before.
"""
from __future__ import annotations

import re
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator

DEFAULT_TENANT = "default"
_TENANT_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
_current: ContextVar[str] = ContextVar("tenant", default=DEFAULT_TENANT)


def check_tenant(tenant_id: str) -> str:
    """A company id: lower-case letters, digits, `-` and `_`, at most 40 (it becomes part of file keys)."""
    if not isinstance(tenant_id, str) or not _TENANT_ID.match(tenant_id):
        raise ValueError(f"会社IDが正しくありません: {tenant_id!r}")
    return tenant_id


def current_tenant() -> str:
    return _current.get()


def set_tenant(tenant_id: str) -> None:
    """Set the company for the rest of this request (the request runs in a context of its own)."""
    _current.set(check_tenant(tenant_id))


@contextmanager
def tenant_scope(tenant_id: str) -> Iterator[str]:
    token = _current.set(check_tenant(tenant_id))
    try:
        yield tenant_id
    finally:
        _current.reset(token)
