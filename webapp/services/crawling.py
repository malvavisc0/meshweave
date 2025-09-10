import json
from datetime import datetime, timezone
from typing import Optional

from markdownify_crawler.core import crawl as crawler_run

from webapp.db import get_session
from webapp.models import Crawl


async def run_crawl_task(crawl_id: str, force_refresh: bool = False) -> None:
    """Background task to execute a crawl and persist results.

    Updates the Crawl row state to 'running', invokes the crawler, and persists
    the resulting payload or error.

    Args:
        crawl_id (str): Identifier of the Crawl row.
        force_refresh (bool, optional): Disable cache for the crawl run. Defaults to False.

    Returns:
        None
    """
    # Mark running and get URL in one session
    url: Optional[str] = None
    with get_session() as s:
        row = s.get(Crawl, crawl_id)
        if not row:
            return
        # If another worker already finished it, do nothing
        if row.status == "succeeded":
            return
        row.status = "running"
        row.updated_at = datetime.now(timezone.utc)
        url = row.url

    # Execute crawl
    try:
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
    except Exception as e:
        with get_session() as s:
            row = s.get(Crawl, crawl_id)
            if not row:
                return
            row.status = "failed"
            row.error = str(e)
            row.updated_at = datetime.now(timezone.utc)
