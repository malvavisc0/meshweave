import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import AsyncIterator

from alembic import command as alembic_command
from alembic.config import Config as AlembicConfig
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
    ai,
    api,
    auth,
    home,
    jobs,
    legal,
    products,
    progress,
    prospects,
    prospects_page,
    submissions,
)
from webapp.utils.auth import AuthSessionMiddleware
from webapp.utils.config import _env_bool
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


def _auto_migrate_on_start() -> None:
    """Optionally run Alembic migrations on startup (all dialects).

    Controlled by WEBAPP_AUTO_MIGRATE=true|1|yes|on. Fails fast on errors.
    """
    from webapp.utils.config import _env_bool

    if not _env_bool("WEBAPP_AUTO_MIGRATE", False):
        return
    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    ini_path = os.path.join(base_dir, "alembic.ini")
    script_loc = os.path.join(base_dir, "alembic")

    cfg = AlembicConfig(ini_path if os.path.exists(ini_path) else None)
    # Ensure script_location is set, even if alembic.ini is missing or minimal
    if os.path.isdir(script_loc):
        cfg.set_main_option("script_location", script_loc)

    # If DATABASE_URL is provided, prefer it; otherwise fallback to SQLite path
    db_url = os.getenv("DATABASE_URL", "").strip()
    if not db_url:
        sqlite_path = os.getenv("SQLITE_PATH", "/db/app.db").strip()
        db_url = f"sqlite:///{sqlite_path}"
    cfg.set_main_option("sqlalchemy.url", db_url)

    try:
        alembic_command.upgrade(cfg, "head")
    except Exception as exc:
        # Fail fast with a clear error; deployment should provide logs
        raise RuntimeError(f"Automatic DB migration failed: {exc}") from exc


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application lifespan context (startup/shutdown).

    Initializes the database on startup. Designed for FastAPI's lifespan parameter.

    Args:
        app (FastAPI): The FastAPI application instance.

    Yields:
        None: Control back to FastAPI to run the application.
    """
    init_logging()
    _auto_migrate_on_start()
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
    app = FastAPI(title="Markdownify Web App", lifespan=lifespan)  # type: ignore[arg-type]

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
        # Non-API: render HTML template
        site_name = os.getenv("SITE_NAME", "Markdownify Web App")
        og_image_url = os.getenv("OG_IMAGE_URL") or None
        page_title = f"Server Error — {site_name}"
        meta_description = "An unexpected error occurred."
        abs_page_url = str(request.url)
        resp = templates.TemplateResponse(
            "500.html",
            {
                "request": request,
                "site_name": site_name,
                "page_title": page_title,
                "meta_description": meta_description,
                "abs_page_url": abs_page_url,
                "og_image_url": og_image_url,
            },
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
        resp.headers["X-Robots-Tag"] = "noindex"
        return resp

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
            status_code = exc.status_code
            detail = exc.detail or (
                "Unauthorized"
                if status_code == status.HTTP_401_UNAUTHORIZED
                else (
                    "Forbidden" if status_code == status.HTTP_403_FORBIDDEN else "Error"
                )
            )
            if is_api:
                return JSONResponse(
                    status_code=status_code,
                    content={"detail": detail},
                )
            # Non-API: render templates for 401/403; fallback to plain text for others
            if status_code == status.HTTP_401_UNAUTHORIZED:
                site_name = os.getenv("SITE_NAME", "Markdownify Web App")
                og_image_url = os.getenv("OG_IMAGE_URL") or None
                page_title = f"Sign in required — {site_name}"
                meta_description = "You need to sign in to access this page."
                abs_page_url = str(request.url)
                resp = templates.TemplateResponse(
                    "401.html",
                    {
                        "request": request,
                        "site_name": site_name,
                        "page_title": page_title,
                        "meta_description": meta_description,
                        "abs_page_url": abs_page_url,
                        "og_image_url": og_image_url,
                    },
                    status_code=status.HTTP_401_UNAUTHORIZED,
                )
                resp.headers["X-Robots-Tag"] = "noindex"
                return resp
            if status_code == status.HTTP_403_FORBIDDEN:
                site_name = os.getenv("SITE_NAME", "Markdownify Web App")
                og_image_url = os.getenv("OG_IMAGE_URL") or None
                page_title = f"Access denied — {site_name}"
                meta_description = "You don't have permission to access this page."
                abs_page_url = str(request.url)
                resp = templates.TemplateResponse(
                    "403.html",
                    {
                        "request": request,
                        "site_name": site_name,
                        "page_title": page_title,
                        "meta_description": meta_description,
                        "abs_page_url": abs_page_url,
                        "og_image_url": og_image_url,
                    },
                    status_code=status.HTTP_403_FORBIDDEN,
                )
                resp.headers["X-Robots-Tag"] = "noindex"
                return resp
            return PlainTextResponse(
                status_code=status_code,
                content=str(detail),
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
    # Legal pages
    app.include_router(legal.router)
    app.include_router(submissions.router)
    app.include_router(analysis.router)
    app.include_router(progress.router)
    app.include_router(jobs.router)
    app.include_router(all_public.router)
    # Prospects/Contacts/Products APIs
    app.include_router(prospects.router)
    # Pages: Products and Prospects management pages
    app.include_router(products.router)
    app.include_router(prospects_page.router)
    # AI chat router
    app.include_router(ai.router)
    # API router remains last
    app.include_router(api.router)

    # Expose footer links and version as Jinja globals
    try:
        templates.env.globals.update(
            {
                "APP_VERSION": os.getenv("APP_VERSION", "").strip(),
                "FOOTER_REPO_URL": os.getenv(
                    "FOOTER_REPO_URL", "https://github.com/your-org/markdownify-crawler"
                ).strip(),
                "FOOTER_CONTACT_EMAIL": os.getenv(
                    "FOOTER_CONTACT_EMAIL", "hello@acme.com"
                ).strip(),
                "FOOTER_PRIVACY_URL": os.getenv("FOOTER_PRIVACY_URL", "/privacy").strip(),
                "FOOTER_TERMS_URL": os.getenv("FOOTER_TERMS_URL", "/terms").strip(),
            }
        )
    except Exception:
        # If templates are not initialized for some reason, do not crash app startup
        pass

    # Baseline Content Security Policy (scaffold)
    # Controlled by WEBAPP_ENABLE_CSP=true and optional WEBAPP_CSP override.
    # Note: Current templates use inline scripts; default includes 'unsafe-inline' to avoid breakage.
    # Tighten to nonced CSP in a subsequent phase.
    @app.middleware("http")
    async def _csp_middleware(request, call_next):
        resp = await call_next(request)
        try:
            if _env_bool("WEBAPP_ENABLE_CSP", False):
                default_csp = (
                    "default-src 'self'; "
                    "img-src 'self' data: blob:; "
                    "style-src 'self' 'unsafe-inline'; "
                    "script-src 'self' 'unsafe-inline' 'wasm-unsafe-eval'; "
                    "connect-src 'self'; "
                    "font-src 'self' data:; "
                    "frame-ancestors 'none'; "
                    "base-uri 'self'; "
                    "form-action 'self'"
                )
                csp = os.getenv("WEBAPP_CSP", default_csp)
                # Do not override if already set upstream
                if "Content-Security-Policy" not in resp.headers:
                    resp.headers["Content-Security-Policy"] = csp
        except Exception:
            # Never fail requests due to CSP header wiring
            pass
        return resp

    return app
