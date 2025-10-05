import os
from datetime import datetime, timezone

from fastapi import APIRouter, Request

from webapp.db import get_session
from webapp.models import Crawl, CrawlEmail, CrawlLink
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
    from collections import defaultdict

    try:
        with get_session() as s:
            row = s.get(Crawl, crawl_id)
            if not row:
                return "err"
            if str(getattr(row, "status", "")).lower() != "running":
                return "noop"

            # Load persisted links/emails
            link_rows = s.query(CrawlLink).filter(CrawlLink.crawl_id == crawl_id).all()
            email_rows = s.query(CrawlEmail).filter(CrawlEmail.crawl_id == crawl_id).all()

            # Dedup and aggregate
            internal = sorted(
                {
                    (lr.absolute_url or "").strip()
                    for lr in link_rows
                    if (lr.type or "") == "internal" and (lr.absolute_url or "").strip()
                }
            )
            external = sorted(
                {
                    (lr.absolute_url or "").strip()
                    for lr in link_rows
                    if (lr.type or "") == "external" and (lr.absolute_url or "").strip()
                }
            )
            visited_pages_count = len(
                {
                    (lr.page_url or "").strip()
                    for lr in link_rows
                    if (lr.page_url or "").strip()
                }
            )

            emails_unique_set = set()
            by_url = defaultdict(set)  # url -> set(emails)
            src_map = {}  # (email,url) -> set(found_as)
            for er in email_rows:
                em = (er.email or "").strip().lower()
                if not em:
                    continue
                pg = (er.page_url or "").strip() or row.canonical_url or row.url
                emails_unique_set.add(em)
                by_url[pg].add(em)
                fas = []
                try:
                    fas = [
                        x.strip().lower()
                        for x in (er.found_as or "").split(",")
                        if x.strip()
                    ]
                except Exception:
                    fas = []
                key = (em, pg)
                if key not in src_map:
                    src_map[key] = set()
                src_map[key].update(fas)

            emails_unique = sorted(emails_unique_set)
            emails_by_url = {u: sorted(list(v)) for u, v in by_url.items()}
            sources = [
                {"email": k[0], "url": k[1], "found_as": sorted(list(v))}
                for k, v in src_map.items()
            ]
            total_mentions = sum(len(v) for v in emails_by_url.values())

            # Limits (best-effort)
            try:
                import json as _json

                limits = _json.loads(row.limits_json or "{}")
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
            now = datetime.now(timezone.utc)
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

    # Elapsed: prefer started_at_ms from limits_json; fallback to updated_at heuristic
    elapsed_ms = None
    try:
        now_ms = int(now.timestamp() * 1000)
        started_ms = None
        if (row.scope or "page") == "site":
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

    # Fallback for site time budget if not yet persisted (enables staleness checks + UI budget)
    if (row.scope or "page") == "site" and time_budget_ms_val is None:
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
            and str((row.status or "")).lower() == "running"
        ):
            scope = row.scope or "page"
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

    # Incremental counters (best-effort; cheap counts)
    try:
        with get_session() as s:
            emails_so_far = (
                s.query(CrawlEmail.email)
                .filter(CrawlEmail.crawl_id == row.id)
                .distinct()
                .count()
            )
            links_internal_so_far = (
                s.query(CrawlLink.id)
                .filter(CrawlLink.crawl_id == row.id, CrawlLink.type == "internal")
                .count()
            )
            external_domains_so_far = (
                s.query(CrawlLink.domain)
                .filter(
                    CrawlLink.crawl_id == row.id,
                    CrawlLink.type == "external",
                    CrawlLink.domain.isnot(None),
                )
                .distinct()
                .count()
            )
    except Exception:
        emails_so_far = 0
        links_internal_so_far = 0
        external_domains_so_far = 0

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

    # Elapsed: prefer started_at_ms from limits_json; fallback to updated_at heuristic
    elapsed_ms = None
    try:
        now_ms = int(now.timestamp() * 1000)
        started_ms = None
        if (row.scope or "page") == "site":
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

    # Fallback for site time budget if not yet persisted (enables staleness checks + UI budget)
    if (row.scope or "page") == "site" and time_budget_ms_val is None:
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
            and str((row.status or "")).lower() == "running"
        ):
            scope = row.scope or "page"
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

    # Incremental counters (best-effort; cheap counts)
    try:
        with get_session() as s:
            emails_so_far = (
                s.query(CrawlEmail.email)
                .filter(CrawlEmail.crawl_id == row.id)
                .distinct()
                .count()
            )
            links_internal_so_far = (
                s.query(CrawlLink.id)
                .filter(CrawlLink.crawl_id == row.id, CrawlLink.type == "internal")
                .count()
            )
            external_domains_so_far = (
                s.query(CrawlLink.domain)
                .filter(
                    CrawlLink.crawl_id == row.id,
                    CrawlLink.type == "external",
                    CrawlLink.domain.isnot(None),
                )
                .distinct()
                .count()
            )
    except Exception:
        emails_so_far = 0
        links_internal_so_far = 0
        external_domains_so_far = 0

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
        "emails_so_far": emails_so_far,
        "links_internal_so_far": links_internal_so_far,
        "external_domains_so_far": external_domains_so_far,
    }
