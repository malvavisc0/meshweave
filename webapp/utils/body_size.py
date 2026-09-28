"""Request-body size cap enforced before parsing.

A declared ``Content-Length`` alone is not enough: a chunked upload has
none, so the cap must count actual bytes off the request stream. Every
write endpoint — JSON API and browser forms — rejects with 413 past the
configured limit instead of buffering an unbounded body in memory.
"""

from __future__ import annotations

import os

from fastapi import HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

DEFAULT_BODY_MAX_BYTES = 64 * 1024
_WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def body_max_bytes() -> int:
    """Configured request-body cap in bytes."""
    try:
        return int(os.getenv("WEBAPP_BODY_MAX_BYTES", str(DEFAULT_BODY_MAX_BYTES)))
    except ValueError:
        return DEFAULT_BODY_MAX_BYTES


async def enforce_body_size(request: Request) -> None:
    """Reject an oversized body before parsing (HTTP 413).

    Counts actual bytes off the request stream — a chunked upload carries
    no ``Content-Length``, so the header check alone is bypassable. The
    read bytes are cached on the request (``_body``), which
    ``Request.body()``/``json()``/``form()`` and Starlette's
    ``BaseHTTPMiddleware`` both replay, so downstream parsers still see
    the full (small) body.
    """
    limit = body_max_bytes()
    declared = request.headers.get("content-length")
    if declared:
        try:
            if int(declared) > limit:
                raise HTTPException(status_code=413, detail="Request body too large")
        except ValueError:
            raise HTTPException(
                status_code=400, detail="Invalid Content-Length"
            ) from None
    total = 0
    chunks: list[bytes] = []
    async for chunk in request.stream():
        total += len(chunk)
        if total > limit:
            raise HTTPException(status_code=413, detail="Request body too large")
        chunks.append(chunk)
    request._body = b"".join(chunks)  # noqa: SLF001


class BodySizeMiddleware(BaseHTTPMiddleware):
    """Cap every write request's body before it reaches the route.

    Form endpoints parse multipart in the route signature, so a handler-
    level check runs too late; the middleware covers API and forms alike.
    Caching the consumed body on ``_body`` is the supported replay
    mechanism: ``_CachedRequest.wrapped_receive`` returns it verbatim to
    the downstream app, and an explicit ``Content-Length`` is rejected
    before any buffering.
    """

    async def dispatch(self, request, call_next):
        if request.method not in _WRITE_METHODS:
            return await call_next(request)
        limit = body_max_bytes()
        declared = request.headers.get("content-length")
        if declared:
            try:
                if int(declared) > limit:
                    return JSONResponse(
                        status_code=413, content={"detail": "Request body too large"}
                    )
            except ValueError:
                return JSONResponse(
                    status_code=400, content={"detail": "Invalid Content-Length"}
                )
        total = 0
        chunks: list[bytes] = []
        async for chunk in request.stream():
            total += len(chunk)
            if total > limit:
                return JSONResponse(
                    status_code=413, content={"detail": "Request body too large"}
                )
            chunks.append(chunk)
        request._body = b"".join(chunks)  # noqa: SLF001
        return await call_next(request)
