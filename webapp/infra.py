import json
import os
from importlib.resources import files as resource_files
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup

# Prefer local filesystem templates in dev when requested via env
# Set WEBAPP_PREFER_LOCAL_TEMPLATES=true to load from source tree without rebuilds.
_prefer_local_tpl = os.getenv(
    "WEBAPP_PREFER_LOCAL_TEMPLATES", "false"
).strip().lower() in {"1", "true", "yes", "on"}

if _prefer_local_tpl:
    _local_src = Path(os.getenv("WEBAPP_LOCAL_SRC_DIR", str(Path(__file__).parent)))
    templates = Jinja2Templates(directory=str(_local_src / "templates"))
else:
    try:
        # When installed as a package, resolve templates from package data
        templates_dir = resource_files("webapp") / "templates"
        templates = Jinja2Templates(directory=str(templates_dir))
    except Exception:
        # Fallback for dev runs
        templates = Jinja2Templates(
            directory=str(Path(__file__).parent / "templates"),
        )

# Trim Jinja control-structure whitespace to reduce output size
try:
    templates.env.trim_blocks = True
    templates.env.lstrip_blocks = True
except Exception:
    # Never fail startup due to env option wiring
    pass


# Register Jinja filters (available regardless of template source)
try:

    def _thousands(value):
        try:
            # Allow ints, floats, and numeric strings; default to int grouping
            iv = int(float(value))
            return f"{iv:,}"
        except Exception:
            try:
                return f"{int(value):,}"
            except Exception:
                try:
                    return f"{float(value):,}"
                except Exception:
                    return str(value)

    templates.env.filters["thousands"] = _thousands

    def _tojson(value):
        try:
            return Markup(json.dumps(value, ensure_ascii=False, separators=(",", ":")))
        except Exception:
            try:
                return Markup(json.dumps(str(value)))
            except Exception:
                return Markup("null")

    templates.env.filters["tojson"] = _tojson

    def _relative_time(dt):
        """Convert a datetime (or ISO string) to a relative time string like '12h ago'."""
        from datetime import UTC as _UTC
        from datetime import datetime as _dt

        try:
            now = _dt.now(_UTC)
            # Handle ISO string inputs (e.g. from .isoformat() in templates)
            if isinstance(dt, str):
                try:
                    dt = _dt.fromisoformat(dt)
                except ValueError, TypeError:
                    return ""
            base = dt or now
            if base.tzinfo is None:
                base = base.replace(tzinfo=_UTC)
            diff = now - base
            secs = int(max(0, diff.total_seconds()))
            if secs < 60:
                return f"{secs}s ago"
            mins = secs // 60
            if mins < 60:
                return f"{mins}m ago"
            hrs = mins // 60
            if hrs < 24:
                return f"{hrs}h ago"
            days = hrs // 24
            return f"{days}d ago"
        except Exception:
            return ""

    templates.env.filters["relative_time"] = _relative_time

    # Register scoring helpers as template globals and filters
    from webapp.utils.scoring import (  # noqa: E402
        group_recommendations_by_pillar,
        rating_class,
    )

    templates.env.globals["group_recommendations_by_pillar"] = (
        group_recommendations_by_pillar
    )
    templates.env.globals["rating_class"] = rating_class
    # Also register as a filter so templates can use {{ rating|rating_class }}
    templates.env.filters["rating_class"] = rating_class

    def _score_fill_class(score):
        """Return CSS BEM modifier class for score bar fill based on numeric score."""
        try:
            s = float(score)
        except TypeError, ValueError:
            return ""
        if s >= 80:
            return "score-bar-fill--good"
        if s >= 60:
            return "score-bar-fill--ok"
        return "score-bar-fill--low"

    templates.env.filters["score_fill_class"] = _score_fill_class
except Exception:
    # Never fail startup due to filter registration
    pass


def mount_static(app: FastAPI) -> None:
    """Mount the /static files with a packaged fallback (dev can prefer local).

    Env:
      - WEBAPP_PREFER_LOCAL_STATIC=true to serve from local source tree.
      - If unset, packaged resources are used when available, else local.
    """
    prefer_local_static = os.getenv(
        "WEBAPP_PREFER_LOCAL_STATIC",
        os.getenv("WEBAPP_PREFER_LOCAL_TEMPLATES", "false"),
    ).strip().lower() in {"1", "true", "yes", "on"}

    if prefer_local_static:
        _local_src = Path(os.getenv("WEBAPP_LOCAL_SRC_DIR", str(Path(__file__).parent)))
        app.mount(
            "/static",
            StaticFiles(directory=str(_local_src / "static"), check_dir=False),
            name="static",
        )
        return

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
            StaticFiles(
                directory=str(Path(__file__).parent / "static"), check_dir=False
            ),
            name="static",
        )
