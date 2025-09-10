"""Minimal FastAPI app entrypoint for Uvicorn.

This module exposes `app` for Uvicorn using the app factory.
Keeps CLI compatibility with "webapp.main:app".
"""

from .app import create_app

app = create_app()
