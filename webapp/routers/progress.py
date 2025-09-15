import os
from datetime import datetime, timezone

from fastapi import APIRouter, Request

from webapp.db import get_session
from webapp.models import Crawl, CrawlLink
from webapp.utils.auth import require_ownership

router = APIRouter()


@router.get("/api/progress/{crawl_id}")
async def api_progress(request: Request, crawl_id: str):
    """Return lightweight progress info for a private crawl (owner only).

    Args:
        request (Request): Incoming request (used for ownership check).
        crawl_id (str): UUID of the crawl.

    Returns:
        dict: {
          "id": str,
          "status": str,
          "scope": "page"|"site",
          "visited_pages": int,
          "limits": {...} | {},
          "elapsed_ms": int | None,
          "est_remaining_ms": int | None,
          "time_budget_ms": int | None,
          "time_budget_remaining_ms": int | None,
          "last_updated": ISO timestamp
        }
    """
    row = await require_ownership(request, crawl_id)
    now = datetime.now(timezone.utc)

    # Count distinct page_url's we have already persisted (works for both page/site)
    with get_session() as s:
        visited_pages = (
            s.query(CrawlLink.page_url)
            .filter(CrawlLink.crawl_id == crawl_id)
            .distinct()
            .count()
        )

    # Limits (for site crawls)
    limits = {}
    if (row.scope or "page") == "site":
        try:
            import json

            limits = json.loads(row.limits_json or "{}")
        except Exception:
            limits = {}
        # Fallback if effective limits not yet persisted
        if not isinstance(limits, dict):
            limits = {}
        if ("max_pages" not in limits) or (not limits.get("max_pages")):
            try:
                limits["max_pages"] = int(os.getenv("AUTH_SITE_MAX_PAGES_DEFAULT", "200"))
            except Exception:
                limits["max_pages"] = 200

    # Best-effort elapsed: time since updated_at while running (approximation)
    elapsed_ms = None
    try:
        if (row.status or "").lower() == "running" and row.updated_at:
            elapsed_ms = int((now - row.updated_at).total_seconds() * 1000)
    except Exception:
        elapsed_ms = None

    # Estimate remaining time for site crawls
    est_remaining_ms = None
    time_budget_ms_val = None
    time_budget_remaining_ms = None
    try:
        if (row.scope or "page") == "site":
            # ensure integer max_pages
            total = None
            v_total = limits.get("max_pages") if isinstance(limits, dict) else None
            try:
                total = int(v_total) if v_total is not None else None
            except Exception:
                total = None
            done = int(visited_pages or 0)
            if elapsed_ms is not None and total and total > 0 and done > 0:
                avg = float(elapsed_ms) / float(done)
                rem_pages = max(0, total - done)
                est_remaining_ms = int(avg * rem_pages)
            # time budget info if available
            v_budget = limits.get("time_budget_ms") if isinstance(limits, dict) else None
            try:
                time_budget_ms_val = int(v_budget) if v_budget is not None else None
            except Exception:
                time_budget_ms_val = None
            if time_budget_ms_val is not None and elapsed_ms is not None:
                try:
                    time_budget_remaining_ms = max(
                        0, int(time_budget_ms_val) - int(elapsed_ms)
                    )
                except Exception:
                    time_budget_remaining_ms = None
    except Exception:
        est_remaining_ms = None
        time_budget_remaining_ms = None

    return {
        "id": row.id,
        "status": row.status,
        "scope": row.scope or "page",
        "visited_pages": visited_pages,
        "limits": limits,
        "elapsed_ms": elapsed_ms,
        "est_remaining_ms": est_remaining_ms,
        "time_budget_ms": time_budget_ms_val,
        "time_budget_remaining_ms": time_budget_remaining_ms,
        "last_updated": (row.updated_at or now).isoformat(),
    }


@router.get("/api/progress/public/{key}")
async def api_progress_public(key: str):
    """Return read-only progress info for a public crawl by short key.

    No authentication required; only available for visibility='public' rows.
    """
    with get_session() as s:
        row = (
            s.query(Crawl)
            .filter(Crawl.key == key, Crawl.visibility == "public")
            .one_or_none()
        )
        if not row:
            # Hide existence when key invalid or not public
            from fastapi import HTTPException

            raise HTTPException(status_code=404, detail="Not found")

    now = datetime.now(timezone.utc)

    # Count distinct page_url's already persisted
    with get_session() as s:
        visited_pages = (
            s.query(CrawlLink.page_url)
            .filter(CrawlLink.crawl_id == row.id)
            .distinct()
            .count()
        )

    # Limits (for site crawls)
    limits = {}
    if (row.scope or "page") == "site":
        try:
            import json

            limits = json.loads(row.limits_json or "{}")
        except Exception:
            limits = {}
        if not isinstance(limits, dict):
            limits = {}
        if ("max_pages" not in limits) or (not limits.get("max_pages")):
            try:
                limits["max_pages"] = int(os.getenv("AUTH_SITE_MAX_PAGES_DEFAULT", "200"))
            except Exception:
                limits["max_pages"] = 200

    # Elapsed (approx)
    elapsed_ms = None
    try:
        if (row.status or "").lower() == "running" and row.updated_at:
            elapsed_ms = int((now - row.updated_at).total_seconds() * 1000)
    except Exception:
        elapsed_ms = None

    # Estimate remaining time for site crawls
    est_remaining_ms = None
    time_budget_ms_val = None
    time_budget_remaining_ms = None
    try:
        if (row.scope or "page") == "site":
            total = None
            v_total = limits.get("max_pages") if isinstance(limits, dict) else None
            try:
                total = int(v_total) if v_total is not None else None
            except Exception:
                total = None
            done = int(visited_pages or 0)
            if elapsed_ms is not None and total and total > 0 and done > 0:
                avg = float(elapsed_ms) / float(done)
                rem_pages = max(0, total - done)
                est_remaining_ms = int(avg * rem_pages)
            v_budget = limits.get("time_budget_ms") if isinstance(limits, dict) else None
            try:
                time_budget_ms_val = int(v_budget) if v_budget is not None else None
            except Exception:
                time_budget_ms_val = None
            if time_budget_ms_val is not None and elapsed_ms is not None:
                try:
                    time_budget_remaining_ms = max(
                        0, int(time_budget_ms_val) - int(elapsed_ms)
                    )
                except Exception:
                    time_budget_remaining_ms = None
    except Exception:
        est_remaining_ms = None
        time_budget_remaining_ms = None

    return {
        "status": row.status,
        "scope": row.scope or "page",
        "visited_pages": visited_pages,
        "limits": limits,
        "elapsed_ms": elapsed_ms,
        "est_remaining_ms": est_remaining_ms,
        "time_budget_ms": time_budget_ms_val,
        "time_budget_remaining_ms": time_budget_remaining_ms,
        "last_updated": (row.updated_at or now).isoformat(),
    }
