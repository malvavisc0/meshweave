"""Durable crawl queue for API/bulk-submitted jobs.

Form submissions ride ``BackgroundTasks`` and can be lost on restart; bulk
API batches (up to 25 crawls per call) must survive a restart, so they are
created with ``queue_status = 'pending'`` (by the submit endpoint, in the
same transaction as the crawl row) and processed by a polling worker — the
same claim-by-UPDATE + stale-reclaim pattern as the AAX worker
(``webapp.services.scoring``).

Queue states live on the crawl row: ``pending`` → ``running`` → terminal
(``done``/``failed``). The crawl's own ``status`` column continues to track
the crawl lifecycle itself (pending/running/succeeded/failed); the queue
columns only track who is responsible for executing it.

Recovery guarantees: a worker crash mid-crawl leaves the crawl ``running``
and the queue job ``running``; stale reclaim resets the queue job to
``pending`` and the claim UPDATE resets a stranded ``running`` crawl back to
``pending`` in the same atomic statement, so the job re-runs instead of
stranding. A crawl that already reached a terminal outcome before the
worker died is never re-crawled — its queue job is just closed out.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import UTC, datetime, timedelta

from sqlalchemy import case

from webapp.db import get_session
from webapp.models import Crawl

logger = logging.getLogger(__name__)

QUEUE_STALE_MINUTES = 30  # Reclaim "running" queue jobs older than this
QUEUE_WORKER_POLL_INTERVAL = 5.0  # Seconds between queue polls
QUEUE_BATCH_LIMIT = 5  # Jobs claimed per poll
QUEUE_JOB_TIMEOUT_SECONDS = 15 * 60  # Hard cap per queued crawl execution


def _max_concurrent_crawls() -> int:
    """Global concurrency cap across queue jobs.

    N identities can otherwise saturate the single LightPanda and the
    DB pool. 0 disables the cap.
    """
    try:
        return int(os.getenv("WEBAPP_MAX_CONCURRENT_CRAWLS", "3"))
    except ValueError:
        return 3


def _count_running_queue_jobs() -> int:
    """Crawl rows currently claimed by a queue worker."""
    with get_session() as s:
        return s.query(Crawl.id).filter(Crawl.queue_status == "running").count()


def _claim_pending_job(crawl_id: str) -> bool:
    """Atomically claim a pending queue job (pending → running).

    A crawl stranded in its own ``running`` state by a dead worker is reset
    to ``pending`` in the same statement so the re-claimed job can re-run.
    """
    now = datetime.now(UTC)
    with get_session() as s:
        updated = (
            s.query(Crawl)
            .filter(Crawl.id == crawl_id, Crawl.queue_status == "pending")
            .update(
                {
                    "queue_status": "running",
                    "queue_started_at": now,
                    "status": case(
                        (Crawl.status == "running", "pending"),
                        else_=Crawl.status,
                    ),
                },
                synchronize_session=False,
            )
        )
        return updated == 1


def _mark_terminal(crawl_id: str, status: str) -> None:
    """Mark a queue job terminally done (done/failed)."""
    with get_session() as s:
        row = s.get(Crawl, crawl_id)
        if row:
            row.queue_status = status
            row.queue_started_at = None


def reset_stale_jobs() -> int:
    """Reclaim queue jobs stuck in "running" past the stale window."""
    stale_cutoff = datetime.now(UTC) - timedelta(minutes=QUEUE_STALE_MINUTES)
    with get_session() as s:
        updated = (
            s.query(Crawl)
            .filter(
                Crawl.queue_status == "running",
                Crawl.queue_started_at < stale_cutoff,
            )
            .update(
                {"queue_status": "pending", "queue_started_at": None},
                synchronize_session=False,
            )
        )
        if updated:
            logger.info("Reclaimed %s stale crawl queue jobs", updated)
        return updated


def adopt_stranded_form_jobs() -> int:
    """Move stranded form-path jobs onto the durable queue.

    Form submissions ride ``BackgroundTasks``; a restart between row
    creation and task execution leaves the row ``pending`` with no
    queue_status forever. This sweep adopts rows that have waited past
    one poll interval onto the durable queue, whose worker re-runs them
    like any other job. Retired rows are excluded: a retire clears
    queue_status on a row that was pending, and re-crawling a retired
    revision would undo the retirement.
    """
    cutoff = datetime.now(UTC) - timedelta(minutes=QUEUE_STALE_MINUTES)
    with get_session() as s:
        updated = (
            s.query(Crawl)
            .filter(
                Crawl.queue_status.is_(None),
                Crawl.status == "pending",
                Crawl.created_at < cutoff,
                Crawl.is_latest == True,  # noqa: E712
            )
            .update(
                {"queue_status": "pending", "queue_started_at": None},
                synchronize_session=False,
            )
        )
        if updated:
            logger.info("Adopted %s stranded form jobs onto the crawl queue", updated)
        return updated


def _force_fail_running(crawl_id: str) -> None:
    """Fail a crawl still stuck in ``running`` (e.g. after a job timeout)."""
    with get_session() as s:
        s.query(Crawl).filter(Crawl.id == crawl_id, Crawl.status == "running").update(
            {"status": "failed", "error": "queue job timed out"},
            synchronize_session=False,
        )


def _fetch_pending_ids(limit: int = QUEUE_BATCH_LIMIT) -> list[str]:
    """IDs of pending queue jobs, oldest first."""
    with get_session() as s:
        rows = (
            s.query(Crawl.id)
            .filter(Crawl.queue_status == "pending")
            .order_by(Crawl.created_at.asc())
            .limit(limit)
            .all()
        )
        return [r[0] for r in rows]


def _job_scope_owner_status(crawl_id: str) -> tuple[str, str | None, str] | None:
    """(scope, user_id, crawl status) for a queued crawl; None when gone."""
    with get_session() as s:
        row = s.get(Crawl, crawl_id)
        if not row:
            return None
        scope = "site" if row.crawl_params is not None else "page"
        return scope, row.user_id, str(row.status or "")


async def _process_job(crawl_id: str) -> None:
    """Claim and run one queued crawl, marking terminal state at the end."""
    if not _claim_pending_job(crawl_id):
        return
    logger.debug("Claimed crawl queue job %s", crawl_id)
    try:
        info = _job_scope_owner_status(crawl_id)
        if info is None:
            _mark_terminal(crawl_id, "failed")
            return
        scope, user_id, crawl_status = info
        # The worker may have died after the crawl finished but before the
        # queue job was closed out — never re-crawl a terminal crawl.
        if crawl_status in ("succeeded", "failed", "cancelled"):
            _mark_terminal(
                crawl_id, "done" if crawl_status == "succeeded" else "failed"
            )
            return
        if scope == "site":
            from webapp.services.site_crawling import run_site_crawl_task

            job = run_site_crawl_task(crawl_id, False)
        else:
            from webapp.services.crawling import run_crawl_task

            job = run_crawl_task(crawl_id, False, user_id=user_id)
        try:
            async with asyncio.timeout(QUEUE_JOB_TIMEOUT_SECONDS):
                await job
        except TimeoutError:
            logger.warning("Crawl queue job %s timed out; failing it", crawl_id)
            _force_fail_running(crawl_id)
            _mark_terminal(crawl_id, "failed")
            return
        # Terminal queue state follows the crawl's own outcome
        with get_session() as s:
            row = s.get(Crawl, crawl_id)
            outcome = getattr(row, "status", "") if row else ""
        _mark_terminal(crawl_id, "done" if outcome == "succeeded" else "failed")
    except Exception:
        logger.exception("Crawl queue worker failed for %s", crawl_id)
        _mark_terminal(crawl_id, "failed")


async def _run_queue_batch(stop_event: asyncio.Event, cap: int) -> None:
    """Claim and execute one batch of pending jobs under the concurrency cap."""
    running = _count_running_queue_jobs() if cap > 0 else 0
    if cap > 0 and running >= cap:
        logger.debug("Crawl concurrency cap reached (%s running)", running)
        return
    limit = max(1, cap - running) if cap > 0 else QUEUE_BATCH_LIMIT
    batch = _fetch_pending_ids(limit=limit)
    if batch:
        await asyncio.gather(
            *(_process_job(cid) for cid in batch if not stop_event.is_set())
        )


async def crawl_queue_worker(stop_event: asyncio.Event) -> None:
    """Poll the durable crawl queue and execute pending jobs.

    Runs as a long-lived asyncio task inside the FastAPI lifespan, next to
    the AAX worker. Each poll claims a batch and runs its jobs
    concurrently so one slow crawl does not stall the rest of the queue;
    stale reclaim runs every poll so a long-lived process also recovers
    jobs orphaned by a crashed co-worker. The batch size is bounded by
    the global crawl-concurrency cap.
    """
    logger.info("Crawl queue worker started")
    while not stop_event.is_set():
        try:
            reset_stale_jobs()
            adopt_stranded_form_jobs()
            await _run_queue_batch(stop_event, _max_concurrent_crawls())
        except Exception:
            logger.exception("Crawl queue worker poll failed")
        try:
            await asyncio.wait_for(
                stop_event.wait(), timeout=QUEUE_WORKER_POLL_INTERVAL
            )
        except TimeoutError:
            pass
    logger.info("Crawl queue worker stopped")
