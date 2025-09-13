from importlib.resources import files as resource_files
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

# Templates directory (kept minimal, no CSS for MVP)
try:
    # When installed as a package, resolve templates from package data
    templates_dir = resource_files("webapp") / "templates"
    templates = Jinja2Templates(directory=str(templates_dir))
except Exception:
    # Fallback for dev runs
    templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def mount_static(app: FastAPI) -> None:
    """Mount the /static files with a packaged fallback.

    Attempts to resolve the package's 'static' resources first; falls back to a
    local 'static' directory next to this file.

    Args:
        app (FastAPI): The FastAPI application instance.

    Returns:
        None
    """
    try:
        static_dir = resource_files("webapp") / "static"
        app.mount(
            "/static",
            StaticFiles(directory=str(static_dir), check_dir=False),
            name="static",
        )
    except Exception:
        app.mount(
            "/static",
            StaticFiles(directory=str(Path(__file__).parent / "static"), check_dir=False),
            name="static",
        )
