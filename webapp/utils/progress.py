"""Progress computation and stale-job finalization for the analysis page."""

import os
from datetime import UTC, datetime
from typing import Any

from webapp.db import get_session
from webapp.models import Crawl
from webapp.utils.config import _env_bool
from webapp.utils.metrics import stale_finalize_attempts, stale_finalize_finished


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
                existing_payload = row.payload_json or {}
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
                        "payload_json": payload,
                        "updated_at": now,
                    },
                    synchronize_session=False,
                )
            )
            return "ok" if updated == 1 else "race"
    except Exception:
        return "err"


def _count_visited_pages(row: Crawl) -> int:
    # Count visited pages from payload_json (CrawlLink table removed)
    visited_pages = 0
    try:
        _p = row.payload_json or {}
        visited_pages = len(_p.get("pages", [])) if isinstance(_p, dict) else 0
    except Exception:
        visited_pages = 0
    return visited_pages


def _build_limits(row: Crawl) -> dict[str, Any]:
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
    return limits


def _compute_elapsed(row: Crawl, now: datetime, limits: dict[str, Any]) -> int | None:
    # Elapsed: prefer started_at_ms from crawl_params; fallback to updated_at heuristic
    elapsed_ms = None
    try:
        now_ms = int(now.timestamp() * 1000)
        started_ms = None
        if bool(row.crawl_params):
            try:
                raw_started = (limits or {}).get("started_at_ms")
                if raw_started is not None:
                    started_ms = int(raw_started)
            except Exception:
                started_ms = None
        if started_ms is not None:
            elapsed_ms = max(0, now_ms - started_ms)
        elif (row.status or "").lower() == "running" and row.updated_at:
            elapsed_ms = int((now - row.updated_at).total_seconds() * 1000)
    except Exception:
        elapsed_ms = None
    return elapsed_ms


def _time_budget_ms(row: Crawl, limits: dict[str, Any]) -> int | None:
    # Time budget for site crawls, with env default fallback (enables staleness checks)
    if not row.crawl_params:
        return None
    try:
        v = limits.get("time_budget_ms") if isinstance(limits, dict) else None
        if v is not None:
            return int(v)
    except TypeError, ValueError:
        pass
    return _int_env("AUTH_SITE_TIME_BUDGET_MS_DEFAULT", 600000)


def _check_stale(
    row: Crawl, elapsed_ms: int | None, time_budget_ms_val: int | None
) -> tuple[bool, str]:
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
    return stale, scope


def _maybe_finalize_stale(
    row: Crawl, elapsed_ms: int | None, time_budget_ms_val: int | None
) -> Crawl:
    # Auto-finalize stale running jobs (if enabled)
    try:
        if (
            _env_bool("STALE_FINALIZE_ENABLED", True)
            and str(row.status or "").lower() == "running"
        ):
            stale, scope = _check_stale(row, elapsed_ms, time_budget_ms_val)
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
    return row


def _progress_counters(row: Crawl) -> tuple[int, int, int]:
    # Incremental counters from payload_json (CrawlLink/CrawlEmail removed)
    emails_so_far = 0
    links_internal_so_far = 0
    external_domains_so_far = 0
    try:
        _cp = row.payload_json or {}
        if isinstance(_cp, dict):
            emails_so_far = len((_cp.get("emails") or {}).get("unique", []))
            links_internal_so_far = len((_cp.get("links") or {}).get("internal", []))
            external_domains_so_far = len((_cp.get("links") or {}).get("external", []))
    except Exception:
        pass
    return emails_so_far, links_internal_so_far, external_domains_so_far


def _fmt_elapsed(ms: int | None) -> str:
    if not ms or ms < 0:
        return "0s"
    s = ms // 1000
    if s < 60:
        return f"{s}s"
    return f"{s // 60}m {s % 60}s"


def progress_view(row: Crawl, finalize: bool = True) -> tuple[Crawl, dict[str, Any]]:
    """Finalize a stale running row and build progress panel values for SSR.

    Pass finalize=False for non-owner renders so unauthenticated viewers
    cannot trigger the stale-finalize DB write.

    Returns the (possibly refreshed) row and a dict with visited, total,
    elapsed, pct, emails, links_int, and domains_ext.
    """
    now = datetime.now(UTC)
    limits = _build_limits(row)
    elapsed_ms = _compute_elapsed(row, now, limits)

    if finalize and str(row.status or "").lower() == "running":
        budget_ms = _time_budget_ms(row, limits)
        row = _maybe_finalize_stale(row, elapsed_ms, budget_ms)
        # Recompute after possible finalization/refresh
        limits = _build_limits(row)
        elapsed_ms = _compute_elapsed(row, now, limits)

    visited = _count_visited_pages(row)
    emails, links_int, domains_ext = _progress_counters(row)
    total = limits.get("max_pages") if isinstance(limits, dict) else None
    try:
        total = int(total) if total else None
    except TypeError, ValueError:
        total = None
    pct = min(100, round(visited / total * 100)) if total else 0

    return row, {
        "visited": visited,
        "total": total,
        "elapsed": _fmt_elapsed(elapsed_ms),
        "pct": pct,
        "emails": emails,
        "links_int": links_int,
        "domains_ext": domains_ext,
    }
