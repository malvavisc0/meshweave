import logging
import time
from datetime import UTC, datetime
from typing import Any

from meshweave.core import crawl as crawler_run
from webapp.db import get_session
from webapp.models import Crawl
from webapp.utils.logging import log_audit
from webapp.utils.metrics import job_duration

logger = logging.getLogger(__name__)


def _safe_log_audit(kind: str, crawl_id: str, user_id: str | None) -> None:
    try:
        log_audit(kind, crawl_id=crawl_id, user_id=user_id)
    except Exception:
        pass


def _safe_job_duration(status: str, started: float) -> None:
    try:
        job_duration.labels("page", status).observe(
            max(0.0, time.monotonic() - started)
        )
    except Exception:
        pass


def _begin_crawl(crawl_id: str, user_id: str | None, now: datetime) -> str | None:
    # Attempt to transition to 'running' atomically to avoid races
    with get_session() as s:
        row = s.get(Crawl, crawl_id)
        if not row:
            return None
        url = row.url
        # If provided, attach ownership when missing (e.g., from retry)
        if user_id and not getattr(row, "user_id", None):
            row.user_id = user_id
        # Only one worker should transition into running; others exit early
        updated = (
            s.query(Crawl)
            .filter(
                Crawl.id == crawl_id,
                Crawl.status.in_(["pending", "failed", "succeeded"]),
            )
            .update({"status": "running", "updated_at": now})
        )
        if updated == 0:
            # Another worker already running; exit
            return None
    return url


def _should_abort_cancelled(crawl_id: str, user_id: str | None, started: float) -> bool:
    # Best-effort cancellation: if user cancelled while rendering, skip persistence
    try:
        with get_session() as s:
            st_row = s.get(Crawl, crawl_id)
        if st_row and str(getattr(st_row, "status", "")).lower() == "cancelled":
            # Do not persist payload; honor cancellation
            _safe_log_audit("page_crawl_cancelled", crawl_id, user_id)
            _safe_job_duration("cancelled", started)
            # Touch updated_at for visibility
            with get_session() as s:
                row2 = s.get(Crawl, crawl_id)
                if row2:
                    row2.updated_at = datetime.now(UTC)
            return True
    except Exception:
        # On any error, proceed with normal persist flow
        pass
    return False


def _attach_scores(crawl_id: str, payload: dict[str, Any]) -> None:
    # Compute AEO/GEO scores before writing so they're
    # included in payload_json
    try:
        from webapp.services.scoring import score_crawl

        score_json = score_crawl(crawl_id, payload=payload)
        payload["scores"] = score_json
    except Exception:
        logger.exception("score_crawl failed for crawl %s", crawl_id)


def _persist_succeeded(crawl_id: str, payload: dict[str, Any]) -> bool:
    with get_session() as s:
        row = s.get(Crawl, crawl_id)
        if not row:
            return False
        row.payload_json = payload
        row.status = "succeeded"
        row.error = None
        row.updated_at = datetime.now(UTC)
    return True


def _persist_failed(crawl_id: str, error: str) -> bool:
    with get_session() as s:
        row = s.get(Crawl, crawl_id)
        if not row:
            return False
        row.status = "failed"
        row.error = error
        row.updated_at = datetime.now(UTC)
    return True


async def _run_aax(crawl_id: str, payload: dict[str, Any]) -> None:
    # Run AAX analysis — will update payload_json when done
    try:
        from webapp.services.scoring import run_aax_for_crawl

        await run_aax_for_crawl(crawl_id, payload=payload)
    except Exception:
        pass


async def run_crawl_task(
    crawl_id: str, force_refresh: bool = False, user_id: str | None = None
) -> None:
    """Background task to execute a crawl and persist results.

    Updates the Crawl row state to 'running', invokes the crawler, and persists
    the resulting payload or error.

    Args:
        crawl_id: Identifier of the Crawl row.
        force_refresh: Disable cache for the crawl run.
        user_id: ID of the user who initiated the crawl.

    Returns:
        None: Side effects only (DB updates and metrics).
    """
    # Attempt to transition to 'running' atomically to avoid races
    now = datetime.now(UTC)
    url = _begin_crawl(crawl_id, user_id, now)
    if url is None:
        return

    # Execute crawl
    started = time.monotonic()
    try:
        _safe_log_audit("crawl_started", crawl_id, user_id)
        payload = await crawler_run(
            url=url,
            include_emails=True,
            deobfuscate_emails=True,
            disable_cache=force_refresh,
            cache_dir=None,  # env MESHWEAVE_CACHE_DIR applies in core
        )

        if _should_abort_cancelled(crawl_id, user_id, started):
            return

        _attach_scores(crawl_id, payload)

        if not _persist_succeeded(crawl_id, payload):
            return

        await _run_aax(crawl_id, payload)

        _safe_log_audit("crawl_succeeded", crawl_id, user_id)
        _safe_job_duration("succeeded", started)
    except Exception as e:
        if not _persist_failed(crawl_id, str(e)):
            return
        _safe_log_audit("crawl_failed", crawl_id, user_id)
        _safe_job_duration("failed", started)
