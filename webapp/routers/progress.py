import os
from datetime import UTC, datetime

from fastapi import APIRouter, Request

from webapp.db import get_session
from webapp.models import Crawl
from webapp.utils.auth import require_ownership
from webapp.utils.metrics import stale_finalize_attempts, stale_finalize_finished

router = APIRouter()

# --- Stale finalization helpers ---


def _env_bool(name: str, default: bool = False) -> bool:
    v = str(os.getenv(name, "1" if default else "0")).strip().lower()
    return v in ("1", "true", "yes", "on")


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except Exception:
        return default


def finalize_stale_job(crawl_id: str) -> str:
    """
    Finalize a 'running' crawl by synthesizing a minimal payload from persisted rows.

    Returns: "ok" (finalized), "race" (row no longer running), "noop" (not running), "err" (failed).
    """
    import json

    try:
        with get_session() as s:
            row = s.get(Crawl, crawl_id)
            if not row:
                return "err"
            if str(getattr(row, "status", "")).lower() != "running":
                return "noop"

            # Extract links/emails from existing payload_json or build minimal
            existing_payload = {}
            try:
                existing_payload = json.loads(row.payload_json or "{}")
            except Exception:
                existing_payload = {}
            if not isinstance(existing_payload, dict):
                existing_payload = {}

            internal = sorted(existing_payload.get("links", {}).get("internal", []))
            external = sorted(existing_payload.get("links", {}).get("external", []))
            visited_pages_count = len(existing_payload.get("pages", []))

            emails_unique = sorted(existing_payload.get("emails", {}).get("unique", []))
            emails_by_url = existing_payload.get("emails", {}).get("by_url", {})
            sources = existing_payload.get("emails", {}).get("sources", [])
            total_mentions = (
                existing_payload.get("emails", {})
                .get("counts", {})
                .get("total_mentions", 0)
            )

            # Limits (best-effort)
            try:
                limits = row.crawl_params or {}
            except Exception:
                limits = {}

            payload = {
                "scope": str(getattr(row, "scope", "") or "page"),
                "start_url": row.url,
                "limits": limits or {},
                "domain": row.domain,
                "canonical_url": row.canonical_url,
                "links": {
                    "internal": internal,
                    "external": external,
                },
                "metrics": {
                    "extraction": {
                        "base_domain": row.domain,
                        "internal_count": len(internal),
                        "external_count": len(external),
                    }
                },
                "emails": {
                    "unique": emails_unique,
                    "by_url": emails_by_url,
                    "sources": sources,
                    "counts": {
                        "total_unique": len(emails_unique),
                        "total_mentions": int(total_mentions),
                    },
                },
                "pages": [],
                "summary": {
                    "visited_count": int(visited_pages_count),
                    "reason_stopped": "stale_finalize",
                },
            }

            # Attempt optimistic finalize (avoid racing a live worker)
            now = datetime.now(UTC)
            updated = (
                s.query(Crawl)
                .filter(Crawl.id == crawl_id, Crawl.status == "running")
                .update(
                    {
                        "status": "succeeded",
                        "error": "finalized_stale",
                        "payload_json": json.dumps(payload),
                        "updated_at": now,
                    },
                    synchronize_session=False,
                )
            )
            return "ok" if updated == 1 else "race"
    except Exception:
        return "err"


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
    now = datetime.now(UTC)

    # Count visited pages from payload_json (CrawlLink table removed)
    visited_pages = 0
    try:
        import json as _pj

        _p = _pj.loads(row.payload_json or "{}")
        visited_pages = len(_p.get("pages", [])) if isinstance(_p, dict) else 0
    except Exception:
        visited_pages = 0

    # Limits (for site crawls)
    limits = {}
    if bool(row.crawl_params):
        try:
            limits = row.crawl_params or {}
        except Exception:
            limits = {}
        # Fallback if effective limits not yet persisted
        if not isinstance(limits, dict):
            limits = {}
        if ("max_pages" not in limits) or (not limits.get("max_pages")):
            try:
                limits["max_pages"] = int(
                    os.getenv("AUTH_SITE_MAX_PAGES_DEFAULT", "200")
                )
            except Exception:
                limits["max_pages"] = 200

    # Elapsed: prefer started_at_ms from crawl_params; fallback to updated_at heuristic
    elapsed_ms = None
    try:
        now_ms = int(now.timestamp() * 1000)
        started_ms = None
        if bool(row.crawl_params):
            try:
                started_ms = int((limits or {}).get("started_at_ms"))  # type: ignore[arg-type]
            except Exception:
                started_ms = None
        if started_ms is not None:
            elapsed_ms = max(0, now_ms - started_ms)
        elif (row.status or "").lower() == "running" and row.updated_at:
            elapsed_ms = int((now - row.updated_at).total_seconds() * 1000)
    except Exception:
        elapsed_ms = None

    # Estimate remaining time for site crawls
    est_remaining_ms = None
    time_budget_ms_val = None
    time_budget_remaining_ms = None
    try:
        if bool(row.crawl_params):
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
            v_budget = (
                limits.get("time_budget_ms") if isinstance(limits, dict) else None
            )
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

    # Fallback for site time budget if not yet persisted (enables staleness checks + UI budget)
    if bool(row.crawl_params) and time_budget_ms_val is None:
        try:
            time_budget_ms_val = int(
                os.getenv("AUTH_SITE_TIME_BUDGET_MS_DEFAULT", "600000")
            )
        except Exception:
            time_budget_ms_val = 600000
        if elapsed_ms is not None and time_budget_remaining_ms is None:
            try:
                time_budget_remaining_ms = max(
                    0, int(time_budget_ms_val) - int(elapsed_ms)
                )
            except Exception:
                time_budget_remaining_ms = None

    # Auto-finalize stale running jobs (if enabled)
    try:
        if (
            _env_bool("STALE_FINALIZE_ENABLED", True)
            and str(row.status or "").lower() == "running"
        ):
            scope = "site" if row.crawl_params else "page"
            stale = False
            if scope == "site":
                grace_ms = _int_env("STALE_FINALIZE_GRACE_MS", 120000)
                if (elapsed_ms is not None) and (time_budget_ms_val is not None):
                    stale = int(elapsed_ms) > int(time_budget_ms_val) + int(grace_ms)
            else:
                page_max_ms = _int_env("PAGE_STALE_FINALIZE_MAX_MS", 600000)
                if elapsed_ms is not None:
                    stale = int(elapsed_ms) > int(page_max_ms)
            if stale:
                try:
                    stale_finalize_attempts.labels(scope=scope).inc()
                except Exception:
                    pass
                outcome = finalize_stale_job(row.id)
                try:
                    stale_finalize_finished.labels(
                        scope=scope, outcome=str(outcome)
                    ).inc()
                except Exception:
                    pass
                # Refresh row (best-effort)
                with get_session() as s:
                    r2 = s.get(Crawl, row.id)
                    if r2:
                        row = r2
    except Exception:
        pass

    # Incremental counters from payload_json (CrawlLink/CrawlEmail removed)
    emails_so_far = 0
    links_internal_so_far = 0
    external_domains_so_far = 0
    try:
        import json as _pj

        _cp = _pj.loads(row.payload_json or "{}")
        if isinstance(_cp, dict):
            emails_so_far = len((_cp.get("emails") or {}).get("unique", []))
            links_internal_so_far = len((_cp.get("links") or {}).get("internal", []))
            external_domains_so_far = len((_cp.get("links") or {}).get("external", []))
    except Exception:
        pass

    return {
        "id": row.id,
        "status": row.status,
        "scope": "site" if row.crawl_params else "page",
        "visited_pages": visited_pages,
        "limits": limits,
        "elapsed_ms": elapsed_ms,
        "est_remaining_ms": est_remaining_ms,
        "time_budget_ms": time_budget_ms_val,
        "time_budget_remaining_ms": time_budget_remaining_ms,
        "last_updated": (row.updated_at or now).isoformat(),
        # incremental counters for UI consistency
        "emails_so_far": emails_so_far,
        "links_internal_so_far": links_internal_so_far,
        "external_domains_so_far": external_domains_so_far,
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

    now = datetime.now(UTC)

    # Count visited pages from payload_json (CrawlLink table removed)
    visited_pages = 0
    try:
        import json as _pj

        _p = _pj.loads(row.payload_json or "{}")
        visited_pages = len(_p.get("pages", [])) if isinstance(_p, dict) else 0
    except Exception:
        visited_pages = 0

    # Limits (for site crawls)
    limits = {}
    if bool(row.crawl_params):
        try:
            limits = row.crawl_params or {}
        except Exception:
            limits = {}
        if not isinstance(limits, dict):
            limits = {}
        if ("max_pages" not in limits) or (not limits.get("max_pages")):
            try:
                limits["max_pages"] = int(
                    os.getenv("AUTH_SITE_MAX_PAGES_DEFAULT", "200")
                )
            except Exception:
                limits["max_pages"] = 200

    # Elapsed: prefer started_at_ms from crawl_params; fallback to updated_at heuristic
    elapsed_ms = None
    try:
        now_ms = int(now.timestamp() * 1000)
        started_ms = None
        if bool(row.crawl_params):
            try:
                started_ms = int((limits or {}).get("started_at_ms"))  # type: ignore[arg-type]
            except Exception:
                started_ms = None
        if started_ms is not None:
            elapsed_ms = max(0, now_ms - started_ms)
        elif (row.status or "").lower() == "running" and row.updated_at:
            elapsed_ms = int((now - row.updated_at).total_seconds() * 1000)
    except Exception:
        elapsed_ms = None

    # Estimate remaining time for site crawls
    est_remaining_ms = None
    time_budget_ms_val = None
    time_budget_remaining_ms = None
    try:
        if bool(row.crawl_params):
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
            v_budget = (
                limits.get("time_budget_ms") if isinstance(limits, dict) else None
            )
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

    # Fallback for site time budget if not yet persisted (enables staleness checks + UI budget)
    if bool(row.crawl_params) and time_budget_ms_val is None:
        try:
            time_budget_ms_val = int(
                os.getenv("AUTH_SITE_TIME_BUDGET_MS_DEFAULT", "600000")
            )
        except Exception:
            time_budget_ms_val = 600000
        if elapsed_ms is not None and time_budget_remaining_ms is None:
            try:
                time_budget_remaining_ms = max(
                    0, int(time_budget_ms_val) - int(elapsed_ms)
                )
            except Exception:
                time_budget_remaining_ms = None

    # Auto-finalize stale running jobs (if enabled)
    try:
        if (
            _env_bool("STALE_FINALIZE_ENABLED", True)
            and str(row.status or "").lower() == "running"
        ):
            scope = "site" if row.crawl_params else "page"
            stale = False
            if scope == "site":
                grace_ms = _int_env("STALE_FINALIZE_GRACE_MS", 120000)
                if (elapsed_ms is not None) and (time_budget_ms_val is not None):
                    stale = int(elapsed_ms) > int(time_budget_ms_val) + int(grace_ms)
            else:
                page_max_ms = _int_env("PAGE_STALE_FINALIZE_MAX_MS", 600000)
                if elapsed_ms is not None:
                    stale = int(elapsed_ms) > int(page_max_ms)
            if stale:
                try:
                    stale_finalize_attempts.labels(scope=scope).inc()
                except Exception:
                    pass
                outcome = finalize_stale_job(row.id)
                try:
                    stale_finalize_finished.labels(
                        scope=scope, outcome=str(outcome)
                    ).inc()
                except Exception:
                    pass
                with get_session() as s:
                    r2 = s.get(Crawl, row.id)
                    if r2:
                        row = r2
    except Exception:
        pass

    # Incremental counters from payload_json (CrawlLink/CrawlEmail removed)
    emails_so_far = 0
    links_internal_so_far = 0
    external_domains_so_far = 0
    try:
        import json as _pj

        _cp = _pj.loads(row.payload_json or "{}")
        if isinstance(_cp, dict):
            emails_so_far = len((_cp.get("emails") or {}).get("unique", []))
            links_internal_so_far = len((_cp.get("links") or {}).get("internal", []))
            external_domains_so_far = len((_cp.get("links") or {}).get("external", []))
    except Exception:
        pass

    return {
        "status": row.status,
        "scope": "site" if row.crawl_params else "page",
        "visited_pages": visited_pages,
        "limits": limits,
        "elapsed_ms": elapsed_ms,
        "est_remaining_ms": est_remaining_ms,
        "time_budget_ms": time_budget_ms_val,
        "time_budget_remaining_ms": time_budget_remaining_ms,
        "last_updated": (row.updated_at or now).isoformat(),
        "emails_so_far": emails_so_far,
        "links_internal_so_far": links_internal_so_far,
        "external_domains_so_far": external_domains_so_far,
    }
