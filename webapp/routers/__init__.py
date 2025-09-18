"""Route modules for the web application.

This package exposes router submodules for convenient import like:
    from webapp.routers import (home, analysis, api, auth, all_public, jobs, progress, prospects, submissions, legal)
"""

from . import all_public
from . import analysis
from . import api
from . import auth
from . import home
from . import jobs
from . import progress
from . import prospects
from . import submissions
from . import legal

__all__ = [
    "all_public",
    "analysis",
    "api",
    "auth",
    "home",
    "jobs",
    "progress",
    "prospects",
    "submissions",
    "legal",
]
