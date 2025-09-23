"""Route modules for the web application.

This package exposes router submodules for convenient import like:
    from webapp.routers import (home, analysis, api, auth, all_public, jobs, progress, prospects, submissions, legal)
"""

from . import (
    all_public,
    analysis,
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

__all__ = [
    "all_public",
    "analysis",
    "api",
    "auth",
    "home",
    "jobs",
    "progress",
    "prospects",
    "prospects_page",
    "submissions",
    "legal",
    "products",
]
