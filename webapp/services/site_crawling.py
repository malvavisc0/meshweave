"""Site crawl background task — thin wrapper around meshweave.core.crawl()."""

import logging
import os
import time
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

from meshweave.core import crawl as core_crawl
from meshweave.crawling.blocked import blocked_error, blocked_render_reason
from meshweave.urls import normalize_domain, should_ignore_path
from webapp.db import get_session
from webapp.models import Crawl, ScoreSnapshot
from webapp.utils.logging import log_audit
from webapp.utils.metrics import job_duration

logger = logging.getLogger(__name__)


def _int_env(name: str, default: int) -> int:
    """Read an integer environment variable with a fallback."""
    try:
        return int(os.getenv(name, str(default)))
    except Exception:
        return default


def _limits_from_row(row: Crawl) -> dict[str, int]:
    """Resolve crawl limits from row.crawl_params and env defaults/caps."""
    is_authenticated = bool(getattr(row, "user_id", None))
    if is_authenticated:
        defaults = {
            "max_pages": _int_env("AUTH_SITE_MAX_PAGES_DEFAULT", 25),
            "max_depth": _int_env("AUTH_SITE_MAX_DEPTH_DEFAULT", 1),
            "time_budget_ms": _int_env("AUTH_SITE_TIME_BUDGET_MS_DEFAULT", 600_000),
        }
        caps = {
            "max_pages": _int_env("AUTH_SITE_MAX_PAGES_CAP", 100),
            "max_depth": _int_env("AUTH_SITE_MAX_DEPTH_CAP", 5),
            "time_budget_ms": max(
                60_000,
                _int_env("AUTH_SITE_TIME_BUDGET_MS_CAP", 3_600_000),
            ),
        }
    else:
        defaults = {
            "max_pages": _int_env("ANON_SITE_MAX_PAGES_DEFAULT", 10),
            "max_depth": _int_env("ANON_SITE_MAX_DEPTH_DEFAULT", 1),
            "time_budget_ms": _int_env("ANON_SITE_TIME_BUDGET_MS_DEFAULT", 600_000),
        }
        caps = {
            "max_pages": _int_env("ANON_SITE_MAX_PAGES_CAP", 15),
            "max_depth": _int_env("ANON_SITE_MAX_DEPTH_CAP", 5),
            "time_budget_ms": max(
                60_000,
                _int_env("ANON_SITE_TIME_BUDGET_MS_CAP", 3_600_000),
            ),
        }
    req = row.crawl_params or {}
    lim = {
        "max_pages": int(
            req.get("max_pages", defaults["max_pages"]) or defaults["max_pages"]
        ),
        "max_depth": int(
            req.get("max_depth", defaults["max_depth"]) or defaults["max_depth"]
        ),
        "time_budget_ms": int(
            req.get("time_budget_ms", defaults["time_budget_ms"])
            or defaults["time_budget_ms"]
        ),
    }
    lim["max_pages"] = max(1, min(lim["max_pages"], caps["max_pages"]))
    lim["max_depth"] = max(0, min(lim["max_depth"], caps["max_depth"]))
    lim["time_budget_ms"] = max(
        60_000,
        min(lim["time_budget_ms"], caps["time_budget_ms"]),
    )
    return lim


def _begin_crawl_transition(
    crawl_id: str, now: datetime
) -> tuple[str | None, Crawl | None]:
    """Transition a pending/failed/succeeded crawl to running."""
    with get_session() as s:
        row = s.get(Crawl, crawl_id)
        if not row:
            return None, None
        start_url = row.url
        updated = (
            s.query(Crawl)
            .filter(
                Crawl.id == crawl_id,
                Crawl.status.in_(("pending", "failed", "succeeded")),
            )
            .update(
                {
                    "status": "running",
                    "updated_at": now,
                }
            )
        )
        if updated == 0:
            return None, None
    return start_url, row


def _persist_initial_limits(crawl_id: str, limits: dict[str, Any]) -> None:
    """Persist resolved limits for progress tracking (best-effort)."""
    try:
        with get_session() as s:
            r = s.get(Crawl, crawl_id)
            if r:
                r.crawl_params = limits
                r.updated_at = datetime.now(UTC)
    except Exception:
        pass


def _resolve_allowed_domain(start_url: str | None) -> str | None:
    """Resolve the whitelisted domain for the url_filter."""
    if not start_url:
        return None
    try:
        parts = urlsplit(start_url)
        return normalize_domain(parts.netloc or "")
    except Exception:
        return None


def _build_url_filter(allowed_domain: str | None):
    """Build a url_filter rejecting URLs outside the allowed domain."""

    def _url_filter(u: str) -> bool:
        try:
            p = urlsplit(u)
            dom = normalize_domain(p.netloc or "")
            if allowed_domain and dom != allowed_domain:
                return False
            if should_ignore_path(p.path or ""):
                return False
        except Exception:
            return False
        return True

    return _url_filter


def _build_should_continue(
    crawl_id: str, time_budget_s: float, started_monotonic: float
):
    """Build the cancellation/time-budget callback."""

    async def _should_continue() -> bool:
        if (time.monotonic() - started_monotonic) > time_budget_s:
            return False
        try:
            with get_session() as s:
                r = s.get(Crawl, crawl_id)
            if not r:
                return False
            st = str(getattr(r, "status", "")).lower()
            return st == "running"
        except Exception:
            return False

    return _should_continue


def _build_on_page(crawl_id: str):
    """Build the heartbeat callback touching updated_at per page."""

    async def _on_page(url: str, data: dict[str, Any]) -> None:
        try:
            with get_session() as s:
                r = s.get(Crawl, crawl_id)
                if r:
                    r.updated_at = datetime.now(UTC)
        except Exception:
            pass

    return _on_page


async def _run_crawl(
    crawl_id: str,
    start_url: str | None,
    limits: dict[str, Any],
    max_depth: int,
    force_refresh: bool,
    on_page: Any,
    should_continue: Any,
    url_filter: Any,
) -> dict[str, Any] | None:
    """Run the unified crawl, returning None on failure."""
    try:
        log_audit("site_crawl_started", crawl_id=crawl_id)
    except Exception:
        pass

    try:
        return await core_crawl(
            url=start_url or "",
            crawl_max_pages=limits["max_pages"],
            max_depth=max_depth,
            include_emails=True,
            deobfuscate_emails=True,
            per_page_timeout=30.0,
            disable_cache=force_refresh,
            on_page_crawled=on_page,
            should_continue=should_continue,
            url_filter=url_filter,
        )
    except Exception as e:
        _finish_task(crawl_id, "failed", error=str(e))
        return None


def _dispatch_finish(
    crawl_id: str,
    payload: dict[str, Any],
    started_monotonic: float,
    time_budget_s: float,
    started_overall: float,
) -> None:
    """Pick succeeded/cancelled/failed based on outcome and budget."""
    stop_reason = payload.get("summary", {}).get("reason_stopped", "queue_empty")
    timed_out = (time.monotonic() - started_monotonic) > time_budget_s
    elapsed = max(0.0, time.monotonic() - started_overall)

    if timed_out:
        _finish_task(
            crawl_id,
            "failed",
            error="time_budget_exceeded",
            payload=payload,
            audit_event="site_crawl_failed_time_budget",
            metric_status="failed",
            elapsed=elapsed,
        )
    elif stop_reason == "cancelled":
        _finish_task(
            crawl_id,
            "cancelled",
            error="cancelled_by_user",
            payload=payload,
            audit_event="site_crawl_cancelled",
            metric_status="cancelled",
            elapsed=elapsed,
        )
    else:
        blocked = blocked_render_reason(payload)
        if blocked:
            # The start page was a bot-protection interstitial or a
            # refusal status: the site was never read, so scoring the
            # payload would judge the blocker instead of the site.
            _finish_task(
                crawl_id,
                "failed",
                error=blocked_error(blocked),
                payload=payload,
                audit_event="site_crawl_blocked",
                metric_status="failed",
                elapsed=elapsed,
            )
        else:
            _finish_task(
                crawl_id,
                "succeeded",
                payload=payload,
                audit_event="site_crawl_succeeded",
                metric_status="succeeded",
                elapsed=elapsed,
            )


async def run_site_crawl_task(crawl_id: str, force_refresh: bool = False) -> None:
    """Background task: site crawl via meshweave.core.crawl().

    Transitions the Crawl row through pending → running →
    succeeded/failed/cancelled, delegates actual crawling to the
    unified core, and stores the resulting payload_json.
    """
    now = datetime.now(UTC)
    started_overall = time.monotonic()

    # ── 1. Transition to running ─────────────────────────────────
    start_url, row = _begin_crawl_transition(crawl_id, now)
    if not row:
        return

    # ── 2. Resolve limits and persist for progress tracking ──────
    limits = (
        _limits_from_row(row)
        if start_url
        else {
            "max_pages": 1,
            "max_depth": 0,
            "time_budget_ms": 600_000,
        }
    )
    limits["started_at_ms"] = int(time.time() * 1000)
    _persist_initial_limits(crawl_id, limits)

    time_budget_s = max(1.0, float(limits["time_budget_ms"]) / 1000.0)
    max_depth = limits["max_depth"]

    # ── 3. Domain whitelist for url_filter ───────────────────────
    allowed_domain = _resolve_allowed_domain(start_url)
    url_filter = _build_url_filter(allowed_domain)

    # ── 4. Callbacks for heartbeats and cancellation ─────────────
    started_monotonic = time.monotonic()
    should_continue = _build_should_continue(crawl_id, time_budget_s, started_monotonic)
    on_page = _build_on_page(crawl_id)

    # ── 5. Run the unified crawl ────────────────────────────────
    payload = await _run_crawl(
        crawl_id,
        start_url,
        limits,
        max_depth,
        force_refresh,
        on_page,
        should_continue,
        url_filter,
    )
    if payload is None:
        return

    # ── 6. Determine final status ───────────────────────────────
    _dispatch_finish(
        crawl_id,
        payload,
        started_monotonic,
        time_budget_s,
        started_overall,
    )


def _score_crawl(payload: dict[str, Any], crawl_id: str) -> None:
    """Compute AEO/GEO scores and include them in payload_json."""
    try:
        from webapp.services.scoring import score_crawl

        score_json = score_crawl(crawl_id, payload=payload)
        payload["scores"] = score_json
    except Exception:
        logger.exception("score_crawl failed for crawl %s", crawl_id)


def _enqueue_aax(crawl_id: str, payload: dict[str, Any]) -> None:
    """Schedule AAX analysis on the running event loop."""
    import asyncio

    from webapp.services.scoring import run_aax_for_crawl

    loop = asyncio.get_running_loop()
    loop.create_task(run_aax_for_crawl(crawl_id, payload=payload))


def _persist_task_result(
    crawl_id: str,
    status: str,
    error: str | None,
    payload: dict[str, Any] | None,
) -> bool:
    """Write the final status and payload to the Crawl row (best-effort).

    Args:
        crawl_id: The Crawl row ID.
        status: Final status to write.
        error: Error message to write, if any.
        payload: Final payload to persist, if any.

    Returns:
        True when post-write steps should continue; False when the
        crawl row is missing.
    """
    try:
        with get_session() as s:
            r = s.get(Crawl, crawl_id)
            if not r:
                return False
            r.status = status
            r.error = error
            r.updated_at = datetime.now(UTC)
            if payload is not None:
                r.payload_json = payload
            if status != "succeeded":
                # Crawl rows are re-used across retries, so a failed or
                # cancelled run may still carry scores from a previous
                # successful run. Clear them: list cards and the API
                # must not show the old report's headlines for a crawl
                # whose latest run did not complete.
                r.aeo_score = None
                r.geo_score = None
                r.aeo_rating = None
                r.geo_rating = None
                snap = (
                    s.query(ScoreSnapshot)
                    .filter(ScoreSnapshot.crawl_id == crawl_id)
                    .one_or_none()
                )
                if snap:
                    s.delete(snap)
    except Exception:
        pass
    return True


def _emit_task_observability(
    crawl_id: str,
    audit_event: str,
    metric_status: str,
    elapsed: float,
) -> None:
    """Emit the audit log event and job duration metric (best-effort).

    Args:
        crawl_id: The Crawl row ID.
        audit_event: Audit event name; empty to skip.
        metric_status: Metric status label; empty to skip.
        elapsed: Elapsed seconds to observe.
    """
    if audit_event:
        try:
            log_audit(audit_event, crawl_id=crawl_id)
        except Exception:
            pass
    if metric_status:
        try:
            job_duration.labels("site", metric_status).observe(elapsed)
        except Exception:
            pass


def _finish_task(
    crawl_id: str,
    status: str,
    *,
    error: str | None = None,
    payload: dict[str, Any] | None = None,
    audit_event: str = "",
    metric_status: str = "",
    elapsed: float = 0.0,
) -> None:
    """Write final status/payload to DB and emit observability."""
    # Compute AEO/GEO scores before writing so they're included in payload_json
    if status == "succeeded" and payload is not None:
        _score_crawl(payload, crawl_id)

    if not _persist_task_result(crawl_id, status, error, payload):
        return

    # Run AAX analysis (async, best-effort) — will update payload_json when done
    if status == "succeeded" and payload is not None:
        _enqueue_aax(crawl_id, payload)

    _emit_task_observability(crawl_id, audit_event, metric_status, elapsed)
