import os
from contextlib import asynccontextmanager

from fastapi import FastAPI

from webapp.db import init_db
from webapp.infra import mount_static
from webapp.routers import api, pages


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan context (startup/shutdown).

    Initializes the database on startup. Designed for FastAPI's lifespan parameter.

    Args:
        app (FastAPI): The FastAPI application instance.

    Yields:
        None: Control back to FastAPI to run the application.
    """
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
