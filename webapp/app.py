import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse
from starlette import status

from webapp.db import init_db
from webapp.infra import mount_static
from webapp.routers import api, pages
from webapp.utils.csrf import CSRFMiddleware
from webapp.utils.logging import RequestIDMiddleware, init_logging, log_audit


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
    yield


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
    app.add_middleware(CSRFMiddleware)

    # Exception handlers (sanitized responses + audit logs)
    async def _handle_validation_error(request: Request, exc: Exception):
        try:
            log_audit("request_validation_error", request=request, level=logging.WARNING)
        except Exception:
            pass
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={"detail": "Invalid request"},
        )

    async def _handle_unexpected_error(request: Request, exc: Exception):
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

    # Mount static files
    mount_static(app)

    # Expose optional base URL override for URL helpers
    env_base = os.getenv("SITE_BASE_URL")
    if env_base:
        app.state.SITE_BASE_URL_OVERRIDE = env_base.rstrip("/")

    # Include route modules
    app.include_router(pages.router)
    app.include_router(api.router)

    return app
