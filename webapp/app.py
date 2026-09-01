import asyncio
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse
from sqlalchemy import text
from starlette import status
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.gzip import GZipMiddleware

from webapp.db import get_session, init_db
from webapp.infra import mount_static, templates
from webapp.models import OAuthState
from webapp.routers import (
    all_public,
    analysis,
    api,
    auth,
    home,
    jobs,
    legal,
    products,
    profile,
    prospects,
    prospects_page,
    scores,
    scoring,
    submissions,
)
from webapp.utils.auth import AuthSessionMiddleware
from webapp.utils.config import _env_bool, get_telemetry_config
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
    except TimeoutError:
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
        now_iso = datetime.now(UTC).isoformat()
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
        now = datetime.now(UTC)
        try:
            with get_session() as s:
                s.query(OAuthState).filter(OAuthState.expires_at <= now).delete()
        except Exception:
            pass
        await _sleep_until(stop_event, 900.0)


def _init_sentry() -> None:
    """Initialize the Sentry SDK against a Bugsink instance if configured.

    SENTRY_DSN points at a Bugsink (Sentry-compatible) endpoint. Bugsink
    only supports error events, so transactions, client reports, and
    session tracking stay off.
    """
    dsn = os.getenv("SENTRY_DSN", "").strip()
    if not dsn:
        return
    import sentry_sdk

    sentry_sdk.init(
        dsn=dsn,
        environment=os.getenv("SENTRY_ENVIRONMENT", "staging").strip(),
        release=os.getenv("SENTRY_RELEASE", "").strip() or None,
        send_default_pii=False,
        max_request_body_size="never",
        traces_sample_rate=0,
        send_client_reports=False,
        auto_session_tracking=False,
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application lifespan context (startup/shutdown).

    Initializes error reporting, logging, and the database on startup.
    Designed for FastAPI's lifespan parameter.

    Args:
        app (FastAPI): The FastAPI application instance.

    Yields:
        None: Control back to FastAPI to run the application.
    """
    _init_sentry()
    init_logging()
    init_db()
    from meshweave.ai.observability import enable_langfuse

    enable_langfuse()

    # Enforce OAuth config in prod if policy requires (fail-fast)
    if os.getenv("WEBAPP_AUTH_ENABLED", "true").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    } and os.getenv("WEBAPP_READINESS_REQUIRE_OAUTH", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }:
        if not (os.getenv("OAUTH_CLIENT_ID") and os.getenv("OAUTH_CLIENT_SECRET")):
            raise RuntimeError(
                "OAuth config required but missing OAUTH_CLIENT_ID/SECRET (WEBAPP_READINESS_REQUIRE_OAUTH=true)"
            )

    # Start background cleanup tasks
    stop_event = asyncio.Event()
    app.state.stop_event = stop_event

    # AAX queue worker — processes pending AAX analyses from the DB
    from webapp.services.scoring import aax_worker

    app.state.cleanup_tasks = [
        asyncio.create_task(_cleanup_auth_sessions_loop(stop_event)),
        asyncio.create_task(_cleanup_oauth_states_loop(stop_event)),
        asyncio.create_task(aax_worker(stop_event)),
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
    app = FastAPI(title="MeshWeave", lifespan=lifespan)

    # Middleware: attach per-request ID
    app.add_middleware(RequestIDMiddleware)
    app.add_middleware(AuthSessionMiddleware)
    app.add_middleware(CSRFMiddleware)
    # Transfer compression for static and API responses
    app.add_middleware(GZipMiddleware, minimum_size=512)

    # Exception handlers (sanitized responses + audit logs)
    app.add_exception_handler(RequestValidationError, _handle_validation_error)
    app.add_exception_handler(Exception, _handle_unexpected_error)
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
    app.include_router(jobs.router)
    app.include_router(all_public.router)
    # Pages: Products management page
    app.include_router(products.router)
    # Prospects management page and API
    app.include_router(prospects.router)
    app.include_router(prospects_page.router)
    app.include_router(profile.router)
    # Scoring methodology page
    app.include_router(scoring.router)
    # Score API router
    app.include_router(scores.router)
    # API router remains last
    app.include_router(api.router)

    _setup_template_globals()
    _setup_csp_middleware(app, _telemetry_enabled(), _telemetry_url())

    return app


def _telemetry_enabled() -> bool:
    """Whether telemetry is enabled via config."""
    try:
        _enabled = get_telemetry_config()[2]
    except Exception:
        return False
    return _enabled


def _telemetry_url() -> str:
    """The telemetry script URL, or empty string."""
    try:
        return get_telemetry_config()[0] or ""
    except Exception:
        return ""


def _setup_template_globals() -> None:
    """Expose footer links and version as Jinja globals."""
    try:
        _telemetry_url, _telemetry_site, _telemetry_enabled = get_telemetry_config()
        templates.env.globals.update(
            {
                "APP_VERSION": os.getenv("APP_VERSION", "").strip(),
                "FOOTER_REPO_URL": os.getenv(
                    "FOOTER_REPO_URL", "https://github.com/malvavisc0/meshweave"
                ).strip(),
                "FOOTER_CONTACT_EMAIL": os.getenv(
                    "FOOTER_CONTACT_EMAIL", "hello@meshweaveai.com"
                ).strip(),
                "FOOTER_PRIVACY_URL": os.getenv(
                    "FOOTER_PRIVACY_URL", "/privacy"
                ).strip(),
                "FOOTER_TERMS_URL": os.getenv("FOOTER_TERMS_URL", "/terms").strip(),
                # Branding defaults for templates (used as fallbacks)
                "SITE_NAME_DEFAULT": os.getenv("SITE_NAME", "MeshWeave").strip(),
                # Convenience for footer ©
                "CURRENT_YEAR": datetime.now(UTC).year,
                # Telemetry (analytics) script — off unless configured
                "TELEMETRY_ENABLED": _telemetry_enabled,
                "TELEMETRY_SCRIPT_URL": _telemetry_url,
                "TELEMETRY_SITE_ID": _telemetry_site,
            }
        )
    except Exception:
        # If templates are not initialized for some reason, do not crash app startup
        pass


def _setup_csp_middleware(
    app: FastAPI, telemetry_enabled: bool, telemetry_url: str
) -> None:
    """Attach the baseline Content Security Policy middleware.

    Controlled by WEBAPP_ENABLE_CSP=true and optional WEBAPP_CSP override.
    Note: Current templates use inline scripts; default includes 'unsafe-inline' to avoid breakage.
    Tighten to nonced CSP in a subsequent phase.
    When telemetry is enabled, its script origin is added to the default
    policy's script-src/connect-src (not needed when WEBAPP_CSP overrides).
    """
    _telemetry_origin = ""
    if telemetry_enabled and telemetry_url:
        from urllib.parse import urlsplit

        _parts = urlsplit(telemetry_url)
        if _parts.scheme and _parts.netloc:
            _telemetry_origin = f"{_parts.scheme}://{_parts.netloc}"

    @app.middleware("http")
    async def _csp_middleware(request, call_next):
        resp = await call_next(request)
        try:
            if _env_bool("WEBAPP_ENABLE_CSP", False):
                _origin = f" {_telemetry_origin}" if _telemetry_origin else ""
                default_csp = (
                    "default-src 'self'; "
                    "img-src 'self' data: blob:; "
                    "style-src 'self' 'unsafe-inline'; "
                    "script-src 'self' 'unsafe-inline' 'wasm-unsafe-eval'"
                    f"{_origin}; "
                    f"connect-src 'self'{_origin}; "
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


async def _handle_validation_error(request: Request, exc: Exception) -> JSONResponse:
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
        logging.getLogger("audit").exception("unhandled_exception")
    except Exception:
        pass
    is_api = str(request.url.path).startswith("/api")
    if is_api:
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": "Internal Server Error"},
        )
    page_title, meta_description, abs_page_url = _error_context(
        request, "Server Error", "An unexpected error occurred."
    )
    resp = templates.TemplateResponse(
        request,
        "500.html",
        {
            "site_name": os.getenv("SITE_NAME", "MeshWeave"),
            "page_title": page_title,
            "meta_description": meta_description,
            "abs_page_url": abs_page_url,
            "og_image_url": os.getenv("OG_IMAGE_URL") or None,
        },
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
    )
    resp.headers["X-Robots-Tag"] = "noindex"
    return resp


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
        if str(request.url.path).startswith("/api"):
            return JSONResponse(
                status_code=status.HTTP_404_NOT_FOUND,
                content={"detail": "Not Found"},
            )
        return _http_error_response(
            request,
            "404.html",
            status.HTTP_404_NOT_FOUND,
            "Page Not Found",
            "The page you requested was not found.",
        )

    # Fallback behavior for other HTTP errors
    if isinstance(exc, StarletteHTTPException):
        is_api = str(request.url.path).startswith("/api")
        status_code = exc.status_code
        detail = exc.detail or _http_error_default_detail(status_code)
        if is_api:
            return JSONResponse(
                status_code=status_code,
                content={"detail": detail},
            )
        template = _http_error_template(status_code)
        if template is not None:
            return _http_error_response(
                request,
                template,
                status_code,
                detail,
                _http_error_description(status_code),
            )
        return PlainTextResponse(
            status_code=status_code,
            content=str(detail),
        )
    # Should not happen for non-HTTP exceptions here, but return generic error
    return PlainTextResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content="Internal Server Error",
    )


def _error_context(
    request: Request, page_title: str, meta_description: str
) -> tuple[str, str, str]:
    """Return (page_title, meta_description, abs_page_url) for an error template."""
    site_name = os.getenv("SITE_NAME", "MeshWeave")
    title = f"{page_title} — {site_name}"
    return title, meta_description, str(request.url)


def _http_error_default_detail(status_code: int) -> str:
    """Default detail text for common HTTP error status codes."""
    if status_code == status.HTTP_401_UNAUTHORIZED:
        return "Unauthorized"
    if status_code == status.HTTP_403_FORBIDDEN:
        return "Forbidden"
    return "Error"


def _http_error_template(status_code: int) -> str | None:
    """Name template for an HTTP status, or None for plain-text fallback."""
    if status_code == status.HTTP_404_NOT_FOUND:
        return "404.html"
    if status_code == status.HTTP_401_UNAUTHORIZED:
        return "401.html"
    if status_code == status.HTTP_403_FORBIDDEN:
        return "403.html"
    return None


def _http_error_description(status_code: int) -> str:
    """Meta description for a rendered HTTP error template."""
    if status_code == status.HTTP_401_UNAUTHORIZED:
        return "You need to sign in to access this page."
    if status_code == status.HTTP_403_FORBIDDEN:
        return "You don't have permission to access this page."
    return ""


def _http_error_response(
    request: Request,
    template: str,
    status_code: int,
    headline: str,
    meta_description: str,
):
    """Render an SEO HTML error page with noindex header."""
    page_title, meta_description, abs_page_url = _error_context(
        request, headline, meta_description
    )
    resp = templates.TemplateResponse(
        request,
        template,
        {
            "site_name": os.getenv("SITE_NAME", "MeshWeave"),
            "page_title": page_title,
            "meta_description": meta_description,
            "abs_page_url": abs_page_url,
            "og_image_url": os.getenv("OG_IMAGE_URL") or None,
        },
        status_code=status_code,
    )
    resp.headers["X-Robots-Tag"] = "noindex"
    return resp
