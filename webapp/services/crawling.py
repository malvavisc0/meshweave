import json
import time
from datetime import UTC, datetime

from meshweave.core import crawl as crawler_run
from webapp.db import get_session
from webapp.models import Crawl
from webapp.services.persist import clear_crawl_data, persist_page
from webapp.utils.logging import log_audit
from webapp.utils.metrics import job_duration


async def run_crawl_task(
    crawl_id: str, force_refresh: bool = False, user_id: str | None = None
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
    url: str | None = None
    now = datetime.now(UTC)
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
                Crawl.id == crawl_id,
                Crawl.status.in_(["pending", "failed", "succeeded"]),
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
            cache_dir=None,  # env MESHWEAVE_CACHE_DIR applies in core
        )

        # Best-effort cancellation: if user cancelled while rendering, skip persistence
        try:
            with get_session() as s:
                st_row = s.get(Crawl, crawl_id)
            if st_row and str(getattr(st_row, "status", "")).lower() == "cancelled":
                # Do not persist payload; honor cancellation
                try:
                    log_audit(
                        "page_crawl_cancelled", crawl_id=crawl_id, user_id=user_id
                    )
                except Exception:
                    pass
                try:
                    job_duration.labels("page", "cancelled").observe(
                        max(0.0, time.monotonic() - started)
                    )
                except Exception:
                    pass
                # Touch updated_at for visibility
                with get_session() as s:
                    row2 = s.get(Crawl, crawl_id)
                    if row2:
                        row2.updated_at = datetime.now(UTC)
                return
        except Exception:
            # On any error, proceed with normal persist flow
            pass

        # Persist links/emails into relational tables (idempotent)
        try:
            # Clear previous persisted rows for this crawl
            clear_crawl_data(crawl_id)

            final_url = ((payload.get("metrics") or {}).get("render") or {}).get(
                "final_url"
            ) or (url or "")
            extraction = (payload.get("metrics") or {}).get("extraction") or {}
            base_domain = (extraction.get("base_domain") or "").strip()

            links = payload.get("links") or {}
            internal_links = links.get("internal") or []
            external_links = links.get("external") or []

            emails = payload.get("emails") or {}
            email_sources = emails.get("sources") or None
            emails_unique = emails.get("unique") or None

            persist_page(
                crawl_id=crawl_id,
                page_url=str(final_url),
                base_domain=str(base_domain),
                internal_links=internal_links,
                external_links=external_links,
                email_sources=email_sources,
                emails_unique_fallback=emails_unique,
            )
        except Exception:
            # Do not fail the crawl if persistence has issues; it can be retried later
            pass

        payload_json = json.dumps(payload)
        with get_session() as s:
            row = s.get(Crawl, crawl_id)
            if not row:
                return
            row.payload_json = payload_json
            row.status = "succeeded"
            row.error = None
            row.updated_at = datetime.now(UTC)
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
            row.updated_at = datetime.now(UTC)
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
