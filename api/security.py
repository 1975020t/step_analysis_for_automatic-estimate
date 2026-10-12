"""The one place that guards the API and says who is acting, and for which company.

Two ways in, both `Authorization: Bearer ...`:
- signing in (Amazon Cognito, COGNITO_*, api/login.py): the ID token of the screens' sign-in gives the user and
  their company; the user is the actor written to the `*_by` columns and the histories.
- API tokens (scripts, other systems): `API_TOKEN` is the token of the `default` company, `TENANT_TOKENS` gives
  each further company its own token (`X-API-Token` works too); the actor is the `X-Actor` header (URL-encoded).
With neither configured the API is open and works on `default` (one company, as before).

`TenantMiddleware` decides the caller once per request and runs the request as their company (src/tenancy.py):
the routes and the services never name it. `token_guard` refuses a request without a valid caller.
"""
from __future__ import annotations

import secrets
from dataclasses import dataclass
from urllib.parse import unquote

from fastapi import Request
from starlette.concurrency import run_in_threadpool
from starlette.types import ASGIApp, Receive, Scope, Send

from api.login import CognitoVerifier, LoginError, User, looks_like_jwt
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


@dataclass(frozen=True)
class Caller:
    tenant: str
    user: User | None = None   # signed in; None for an API token or an open API


def token_guard(tokens: dict[str, str], login: bool = False):
    def require_caller(request: Request) -> None:
        if not tokens and not login:
            return
        caller: Caller | None = request.scope.get("state", {}).get("caller")
        if caller is None:
            raise ServiceError("ログインが必要です。" if login else "APIトークンが必要です。", 401, "UNAUTHORIZED")
        if caller.user is not None and not caller.tenant:
            raise ServiceError("このユーザーには会社が設定されていません。管理者に連絡してください。", 403, "NO_TENANT")

    return require_caller


class TenantMiddleware:
    """Runs each request as its caller's company (`default` when the API is open). A company seen for the first
    time gets its initial data. The caller is kept in `request.state.caller`; without a valid one the request runs
    as `default` but the guarded routes refuse it (401)."""

    def __init__(self, app: ASGIApp, tokens: dict[str, str], prepare, verifier: CognitoVerifier | None = None) -> None:
        self.app = app
        self.tokens = tokens
        self.prepare = prepare  # Platform.ensure_tenant
        self.verifier = verifier

    def caller(self, headers: dict[str, str]) -> Caller | None:
        token = given_token(headers)
        if not self.tokens and self.verifier is None:
            return Caller(DEFAULT_TENANT)
        tenant = tenant_of(self.tokens, token) if token else None
        if tenant:
            return Caller(tenant)
        if self.verifier is not None and looks_like_jwt(token):
            try:
                user = self.verifier.verify(token)
            except LoginError:
                return None
            return Caller(user.tenant or "", user)
        return None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        caller = await run_in_threadpool(self.caller, headers)  # the key fetch may wait on the network
        scope.setdefault("state", {})["caller"] = caller
        tenant = caller.tenant if caller and caller.tenant else DEFAULT_TENANT
        if caller and caller.tenant:
            await run_in_threadpool(self.prepare, tenant)
        with tenant_scope(tenant):
            await self.app(scope, receive, send)


def current_user(request: Request) -> User | None:
    caller: Caller | None = request.scope.get("state", {}).get("caller")
    return caller.user if caller else None


def current_actor(request: Request) -> str:
    """The signed-in user's name, else the staff member chosen on the screen (X-Actor)."""
    user = current_user(request)
    if user is not None:
        return user.name
    return unquote(request.headers.get("x-actor", "")).strip()[:80]
