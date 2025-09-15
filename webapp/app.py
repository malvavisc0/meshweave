import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse
from sqlalchemy import text
from starlette import status
from starlette.exceptions import HTTPException as StarletteHTTPException

from webapp.db import get_session, init_db
from webapp.infra import mount_static, templates
from webapp.routers import (
    all_public,
    analysis,
    api,
    auth,
    home,
    jobs,
    progress,
    submissions,
)
from webapp.utils.auth import AuthSessionMiddleware
from webapp.utils.csrf import CSRFMiddleware
from webapp.utils.logging import RequestIDMiddleware, init_logging, log_audit
from webapp.utils.metrics import active_sessions


async def _sleep_until(event: asyncio.Event, seconds: float):
    """Sleep until event is set or timeout elapses, whichever comes first.

    Args:
        event (asyncio.Event): Event to wait for.
        seconds (float): Maximum seconds to wait.

    Returns:
        None
    """
    try:
        await asyncio.wait_for(event.wait(), timeout=seconds)
    except asyncio.TimeoutError:
        pass


async def _cleanup_auth_sessions_loop(stop_event: asyncio.Event):
    """Periodic cleanup of expired auth sessions and refresh of the active_sessions gauge.

    Args:
        stop_event (asyncio.Event): Event used to signal shutdown.

    Returns:
        None
    """
    while True:
        if stop_event.is_set():
            break
        now_iso = datetime.now(timezone.utc).isoformat()
        try:
            with get_session() as s:
                s.execute(
                    text("DELETE FROM auth_sessions WHERE expires_at <= :now"),
                    {"now": now_iso},
                )
                cnt = (
                    s.execute(
                        text(
                            "SELECT COUNT(1) FROM auth_sessions WHERE expires_at > :now"
                        ),
                        {"now": now_iso},
                    ).scalar()
                    or 0
                )
            try:
                active_sessions.set(int(cnt))
            except Exception:
                pass
        except Exception:
            # Swallow and retry later
            pass
        await _sleep_until(stop_event, 900.0)  # 15 minutes


async def _cleanup_oauth_states_loop(stop_event: asyncio.Event):
    """Periodic cleanup of expired OAuth state entries.

    Args:
        stop_event (asyncio.Event): Event used to signal shutdown.

    Returns:
        None
    """
    while True:
        if stop_event.is_set():
            break
        now_iso = datetime.now(timezone.utc).isoformat()
        try:
            with get_session() as s:
                s.execute(
                    text("DELETE FROM oauth_states WHERE expires_at <= :now"),
                    {"now": now_iso},
                )
        except Exception:
            pass
        await _sleep_until(stop_event, 900.0)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan context (startup/shutdown).

    Initializes the database on startup. Designed for FastAPI's lifespan parameter.

    Args:
        app (FastAPI): The FastAPI application instance.

    Yields:
        None: Control back to FastAPI to run the application.
    """
    init_logging()
    init_db()

    # Enforce OAuth config in prod if policy requires (fail-fast)
    if os.getenv("WEBAPP_AUTH_ENABLED", "true").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    } and os.getenv("WEBAPP_READYZ_REQUIRE_AUTH", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }:
        if not (os.getenv("OAUTH_CLIENT_ID") and os.getenv("OAUTH_CLIENT_SECRET")):
            raise RuntimeError(
                "OAuth config required but missing OAUTH_CLIENT_ID/SECRET (WEBAPP_READYZ_REQUIRE_AUTH=true)"
            )

    # Start background cleanup tasks
    stop_event = asyncio.Event()
    app.state.stop_event = stop_event
    app.state.cleanup_tasks = [
        asyncio.create_task(_cleanup_auth_sessions_loop(stop_event)),
        asyncio.create_task(_cleanup_oauth_states_loop(stop_event)),
    ]

    try:
        yield
    finally:
        # Stop background tasks gracefully
        stop_event.set()
        for t in getattr(app.state, "cleanup_tasks", []):
            t.cancel()
        try:
            await asyncio.gather(
                *getattr(app.state, "cleanup_tasks", []), return_exceptions=True
            )
        except Exception:
            pass


def create_app() -> FastAPI:
    """Create and configure the FastAPI application.

    Sets title, lifespan, mounts static files, includes routers, and
    propagates SITE_BASE_URL from environment into app.state for URL utils.

    Returns:
        FastAPI: Configured FastAPI application instance.
    """
    app = FastAPI(title="Markdownify Web App", lifespan=lifespan)

    # Middleware: attach per-request ID
    app.add_middleware(RequestIDMiddleware)
    app.add_middleware(AuthSessionMiddleware)
    app.add_middleware(CSRFMiddleware)

    # Exception handlers (sanitized responses + audit logs)
    async def _handle_validation_error(request: Request, exc: Exception):
        """Return sanitized 422 response for validation errors.

        Args:
            request (Request): Incoming request.
            exc (Exception): Validation exception.

        Returns:
            JSONResponse: 422 with generic detail.
        """
        try:
            log_audit("request_validation_error", request=request, level=logging.WARNING)
        except Exception:
            pass
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={"detail": "Invalid request"},
        )

    async def _handle_unexpected_error(request: Request, exc: Exception):
        """Return sanitized 500 response for unexpected exceptions.

        Args:
            request (Request): Incoming request.
            exc (Exception): Unhandled exception.

        Returns:
            JSONResponse or PlainTextResponse: JSON for /api/* paths; plain text otherwise.
        """
        try:
            log_audit("unhandled_exception", request=request, level=logging.ERROR)
        except Exception:
            pass
        is_api = str(request.url.path).startswith("/api")
        if is_api:
            return JSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                content={"detail": "Internal Server Error"},
            )
        return PlainTextResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content="Internal Server Error",
        )

    app.add_exception_handler(RequestValidationError, _handle_validation_error)
    app.add_exception_handler(Exception, _handle_unexpected_error)

    # Custom HTTP exception handler (404 -> HTML page or JSON; others -> simple JSON/text)
    async def _handle_http_exception(request: Request, exc: Exception):
        """Handle HTTP errors with HTML/JSON responses and custom 404 page.

        Args:
            request (Request): Incoming request.
            exc (Exception): The caught Starlette HTTP exception.

        Returns:
            Response: JSON or PlainTextResponse for API/non-API, or templated 404 page.
        """
        if (
            isinstance(exc, StarletteHTTPException)
            and exc.status_code == status.HTTP_404_NOT_FOUND
        ):
            is_api = str(request.url.path).startswith("/api")
            if is_api:
                return JSONResponse(
                    status_code=status.HTTP_404_NOT_FOUND,
                    content={"detail": "Not Found"},
                )
            site_name = os.getenv("SITE_NAME", "Markdownify Web App")
            og_image_url = os.getenv("OG_IMAGE_URL") or None
            page_title = f"Page Not Found — {site_name}"
            meta_description = "The page you requested was not found."
            abs_page_url = str(request.url)
            resp = templates.TemplateResponse(
                "404.html",
                {
                    "request": request,
                    "site_name": site_name,
                    "page_title": page_title,
                    "meta_description": meta_description,
                    "abs_page_url": abs_page_url,
                    "og_image_url": og_image_url,
                },
                status_code=status.HTTP_404_NOT_FOUND,
            )
            resp.headers["X-Robots-Tag"] = "noindex"
            return resp

        # Fallback behavior for other HTTP errors
        if isinstance(exc, StarletteHTTPException):
            is_api = str(request.url.path).startswith("/api")
            if is_api:
                return JSONResponse(
                    status_code=exc.status_code,
                    content={"detail": exc.detail or "Error"},
                )
            return PlainTextResponse(
                status_code=exc.status_code,
                content=str(exc.detail or "Error"),
            )
        # Should not happen for non-HTTP exceptions here, but return generic error
        return PlainTextResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content="Internal Server Error",
        )

    app.add_exception_handler(StarletteHTTPException, _handle_http_exception)

    # Mount static files
    mount_static(app)

    # Expose optional base URL override for URL helpers
    env_base = os.getenv("SITE_BASE_URL")
    if env_base:
        app.state.SITE_BASE_URL_OVERRIDE = env_base.rstrip("/")

    # Include route modules
    app.include_router(auth.router)
    # Split routers (replaces pages.router)
    app.include_router(home.router)
    app.include_router(submissions.router)
    app.include_router(analysis.router)
    app.include_router(progress.router)
    app.include_router(jobs.router)
    app.include_router(all_public.router)
    # API router remains last
    app.include_router(api.router)

    return app
