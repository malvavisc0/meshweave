"""Retention for the unbounded growth stores/.

Two stores grow without bound in production:

- ``submissions`` rows carry raw client IPs and cookies per anonymous
  visit; the privacy policy says they are not kept forever.
- the fetcher's on-disk page cache under ``MESHWEAVE_CACHE_DIR`` is
  never evicted — a disk-fill outage on the cache volume.

Both are pruned on the same periodic maintenance loop, bounded by
``WEBAPP_SUBMISSION_RETENTION_DAYS`` and
``WEBAPP_CACHE_RETENTION_DAYS``. Zero disables a pruner.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from webapp.db import get_session
from webapp.models import Submission

logger = logging.getLogger(__name__)


def _int_env(name: str, default: int) -> int:
    """Read an integer env var with a fallback."""
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def prune_submissions() -> int:
    """Delete submission metadata rows older than the retention window."""
    days = _int_env("WEBAPP_SUBMISSION_RETENTION_DAYS", 30)
    if days <= 0:
        return 0
    cutoff = datetime.now(UTC) - timedelta(days=days)
    try:
        with get_session() as s:
            deleted = (
                s.query(Submission)
                .filter(Submission.created_at < cutoff)
                .delete(synchronize_session=False)
            )
        if deleted:
            logger.info("Pruned %s submission rows older than %s days", deleted, days)
        return deleted
    except Exception:
        logger.exception("Submission pruning failed")
        return 0


def prune_page_cache() -> int:
    """Evict page-cache files older than the retention window."""
    days = _int_env("WEBAPP_CACHE_RETENTION_DAYS", 14)
    if days <= 0:
        return 0
    cache_dir = os.getenv("MESHWEAVE_CACHE_DIR", "").strip()
    if not cache_dir:
        return 0
    cutoff = time.time() - days * 86400
    removed = 0
    try:
        for path in Path(cache_dir).glob("*.html"):
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
                    removed += 1
            except OSError:
                continue
    except Exception:
        logger.exception("Page cache pruning failed")
    if removed:
        logger.info("Pruned %s page cache files older than %s days", removed, days)
    return removed


def prune_stores_once() -> None:
    """One maintenance pass over every pruned store."""
    prune_submissions()
    prune_page_cache()
