"""The one place that guards the API and says who is acting, and for which company.

Today: API tokens (`Authorization: Bearer ...` or `X-API-Token`). `API_TOKEN` is the token of the `default` company,
`TENANT_TOKENS` gives each further company its own token; with no token configured the API is open and works on
`default` (one company, as before). The company of the token is set for the whole request (`TenantMiddleware`,
src/tenancy.py): the routes and the services never name it. The staff member chosen on the screen (`X-Actor`,
URL-encoded) is the actor written to the `*_by` columns and the histories.

A login later replaces the token lookup (the company and the user come from the signed-in session) and
`current_actor`; the routes do not change.
"""
from __future__ import annotations

import secrets
from urllib.parse import unquote

from fastapi import Request
from starlette.concurrency import run_in_threadpool
from starlette.types import ASGIApp, Receive, Scope, Send

from src.services.estimate import ServiceError
from src.tenancy import DEFAULT_TENANT, tenant_scope


def given_token(headers) -> str:
    header = headers.get("authorization", "")
    return header[7:].strip() if header.lower().startswith("bearer ") else headers.get("x-api-token", "")


def tenant_of(tokens: dict[str, str], token: str) -> str | None:
    """The company of a token (compared in constant time), None when it is not one of them."""
    found = None
    for known, tenant in tokens.items():
        if secrets.compare_digest(token.encode(), known.encode()):
            found = tenant
    return found


def token_guard(tokens: dict[str, str]):
    def require_token(request: Request) -> None:
        if tokens and tenant_of(tokens, given_token(request.headers)) is None:
            raise ServiceError("APIトークンが必要です。", 401, "UNAUTHORIZED")

    return require_token


class TenantMiddleware:
    """Runs each request as its company: the one of its token (`default` without tokens or without a valid one;
    the routes refuse an invalid token anyway). A company seen for the first time gets its initial data."""

    def __init__(self, app: ASGIApp, tokens: dict[str, str], prepare) -> None:
        self.app = app
        self.tokens = tokens
        self.prepare = prepare  # Platform.ensure_tenant

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        tenant = tenant_of(self.tokens, given_token(headers)) or DEFAULT_TENANT
        await run_in_threadpool(self.prepare, tenant)
        with tenant_scope(tenant):
            await self.app(scope, receive, send)


def current_actor(request: Request) -> str:
    return unquote(request.headers.get("x-actor", "")).strip()[:80]
