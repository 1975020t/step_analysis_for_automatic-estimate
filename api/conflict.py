"""Run a request again when the database refused one of its writes for a conflict (Aurora DSQL, SQLSTATE 40001).

The request is run again from the start (its body is kept to send it again) only while nothing of it has been
committed yet and no response has started: a request that already saved something is not repeated (it would save
it twice), it answers 409 like a save that met a newer version. See src/db/retry.py.
"""
from __future__ import annotations

import asyncio

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from src.db.retry import ATTEMPTS, delay, is_conflict, track_commits

KEEP_BODY_BYTES = 12 * 1024 * 1024  # a larger body is not kept: such a request is not repeated

CONFLICT = {"error": {"code": "CONFLICT", "message": "同時に保存された内容と重なりました。画面を開き直してから、もう一度お試しください。"}}


class ConflictRetryMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        kept, whole = await _keep_body(receive)
        for attempt in range(ATTEMPTS):
            commits = track_commits()
            started = False

            async def sending(message: Message) -> None:
                nonlocal started
                started = True
                await send(message)

            try:
                await self.app(scope, _replay(kept, receive), sending)
                return
            except Exception as exc:
                if not is_conflict(exc) or started:
                    raise
                if commits or not whole or attempt == ATTEMPTS - 1:
                    await JSONResponse(CONFLICT, status_code=409)(scope, receive, send)
                    return
                await asyncio.sleep(delay(attempt))


async def _keep_body(receive: Receive) -> tuple[list[Message], bool]:
    """The body messages, up to KEEP_BODY_BYTES (whole=False: the rest is still to be received)."""
    kept: list[Message] = []
    size = 0
    while True:
        message = await receive()
        kept.append(message)
        if message["type"] != "http.request":
            return kept, False
        size += len(message.get("body", b""))
        if not message.get("more_body"):
            return kept, True
        if size > KEEP_BODY_BYTES:
            return kept, False


def _replay(kept: list[Message], receive: Receive) -> Receive:
    """The kept messages, then whatever comes (the rest of a large body, or the disconnect)."""
    queue = list(kept)

    async def replay() -> Message:
        return queue.pop(0) if queue else await receive()

    return replay
