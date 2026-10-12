"""The one place that guards the API and says who is acting.

Today: the shared API token (API_TOKEN, `Authorization: Bearer ...` or `X-API-Token`) and the staff member chosen
on the screen (`X-Actor`, URL-encoded) as the actor written to the `*_by` columns and the histories. A login
later replaces `current_actor` (the user of the session) and adds permission checks to `require_token`;
the routes do not change.
"""
from __future__ import annotations

import secrets
from urllib.parse import unquote

from fastapi import Request

from src.services.estimate import ServiceError


def token_guard(api_token: str):
    def require_token(request: Request) -> None:
        if not api_token:
            return
        header = request.headers.get("authorization", "")
        given = header[7:].strip() if header.lower().startswith("bearer ") else request.headers.get("x-api-token", "")
        if not secrets.compare_digest(given.encode(), api_token.encode()):
            raise ServiceError("APIトークンが必要です。", 401, "UNAUTHORIZED")

    return require_token


def current_actor(request: Request) -> str:
    return unquote(request.headers.get("x-actor", "")).strip()[:80]
