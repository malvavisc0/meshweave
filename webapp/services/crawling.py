import json
import time
from datetime import datetime, timezone
from typing import Optional

from markdownify_crawler.core import crawl as crawler_run

from webapp.db import get_session
from webapp.models import Crawl
from webapp.utils.logging import log_audit
from webapp.utils.metrics import job_duration


async def run_crawl_task(
    crawl_id: str, force_refresh: bool = False, user_id: Optional[str] = None
) -> None:
    """Background task to execute a crawl and persist results.

    Updates the Crawl row state to 'running', invokes the crawler, and persists
    the resulting payload or error.

    Args:
        crawl_id (str): Identifier of the Crawl row.
        force_refresh (bool, optional): Disable cache for the crawl run. Defaults to False.
        user_id (Optional[str], optional): ID of the user who initiated the crawl, used for
            audit/metrics context. Defaults to None.

    Returns:
        None: Performs side effects (DB updates and metrics) and does not return a value.
    """
    # Attempt to transition to 'running' atomically to avoid races
    url: Optional[str] = None
    now = datetime.now(timezone.utc)
    with get_session() as s:
        row = s.get(Crawl, crawl_id)
        if not row:
            return
        url = row.url
        # If provided, attach ownership when missing (e.g., from retry)
        if user_id and not getattr(row, "user_id", None):
            row.user_id = user_id
        # Only one worker should transition into running; others exit early
        updated = (
            s.query(Crawl)
            .filter(
                Crawl.id == crawl_id, Crawl.status.in_(["pending", "failed", "succeeded"])
            )
            .update({"status": "running", "updated_at": now})
        )
        if updated == 0:
            # Another worker already running; exit
            return

    # Execute crawl
    started = time.monotonic()
    try:
        try:
            log_audit("crawl_started", crawl_id=crawl_id, user_id=user_id)
        except Exception:
            pass
        payload = await crawler_run(
            url=url,
            crawl_internal=False,
            same_domain_only=True,
            include_emails=True,
            deobfuscate_emails=True,
            disable_cache=force_refresh,
            cache_dir=None,  # env MARKDOWNIFY_CACHE_DIR applies in core
        )
        payload_json = json.dumps(payload)
        with get_session() as s:
            row = s.get(Crawl, crawl_id)
            if not row:
                return
            row.payload_json = payload_json
            row.status = "succeeded"
            row.error = None
            row.updated_at = datetime.now(timezone.utc)
        try:
            log_audit("crawl_succeeded", crawl_id=crawl_id, user_id=user_id)
        except Exception:
            pass
        try:
            job_duration.labels("page", "succeeded").observe(
                max(0.0, time.monotonic() - started)
            )
        except Exception:
            pass
    except Exception as e:
        with get_session() as s:
            row = s.get(Crawl, crawl_id)
            if not row:
                return
            row.status = "failed"
            row.error = str(e)
            row.updated_at = datetime.now(timezone.utc)
        try:
            log_audit("crawl_failed", crawl_id=crawl_id, user_id=user_id)
        except Exception:
            pass
        try:
            job_duration.labels("page", "failed").observe(
                max(0.0, time.monotonic() - started)
            )
        except Exception:
            pass
