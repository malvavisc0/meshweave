"""BFS crawl engine."""

import asyncio
import logging
from collections import deque
from typing import Any

from ..extraction import (
    classify_links,
    collect_emails,
    extract_content_metrics,
    extract_headings,
    extract_page_meta,
    preprocess_soup,
    soup_from_html,
    to_markdown,
)
from ..urls import normalize_abs_url, should_follow
from .fetcher import BrowserSession, get_rendered_html

logger = logging.getLogger(__name__)

__all__ = [
    "bfs_crawl",
]


async def bfs_crawl(
    origin: str,
    internal_links: list[str],
    *,
    session: BrowserSession,
    crawl_max_pages: int = 25,
    same_domain_only: bool = True,
    include_emails: bool = True,
    deobfuscate_emails: bool = True,
    throttle_ms: int = 0,
    per_page_timeout: float = 15.0,
    cache_dir: str | None = None,
    sitemap_seeds: list[str] | None = None,
) -> dict[str, Any]:
    """Run BFS crawl starting from origin, returning crawl state.

    Returns a dict with keys: visited, stop_reason, seeded,
    all_emails, emails_by_url, email_sources.
    """
    visited_norm: set[str] = set()
    visited_list: list[str] = []
    markdowns: dict[str, dict[str, Any]] = {}
    stop_reason = "queue_empty"

    norm_start = normalize_abs_url(origin, origin)
    if norm_start:
        visited_norm.add(norm_start)
        visited_list.append(origin)

    all_emails: set[str] = set()
    email_sources: list[dict[str, Any]] = []
    emails_by_url: dict[str, list[str]] = {}

    q: deque[str] = deque()

    def _enqueue(href: str, base: str):
        absu = normalize_abs_url(href, base)
        if (
            absu
            and should_follow(absu, origin, same_domain_only)
            and absu not in visited_norm
            and len(visited_norm) < crawl_max_pages
        ):
            visited_norm.add(absu)
            q.append(absu)
            return True
        return False

    # Seed from start page links
    for href in internal_links:
        _enqueue(href, origin)

    # Seed from sitemap
    seeded = 0
    for su in sitemap_seeds or []:
        if _enqueue(su, origin):
            seeded += 1

    # BFS loop
    while q and len(visited_list) < crawl_max_pages:
        u = q.popleft()
        try:
            html2, m2 = await get_rendered_html(
                url=u,
                session=session,
                progressive_scroll=False,
                return_metrics=True,
                timeout=max(1.0, per_page_timeout),
                wait_until="domcontentloaded",
                cache_dir=cache_dir,
            )
        except Exception:
            logger.debug("Failed to fetch %s", u, exc_info=True)
            if throttle_ms > 0:
                await asyncio.sleep(throttle_ms / 1000)
            continue

        final_u = str(getattr(m2, "final_url", u)) or u

        # Track the final (possibly redirected) URL to avoid revisits
        norm_final = normalize_abs_url(final_u, final_u)
        if norm_final and norm_final != normalize_abs_url(u, u):
            if norm_final in visited_norm:
                # Already visited via a different URL
                if throttle_ms > 0:
                    await asyncio.sleep(throttle_ms / 1000)
                continue
            visited_norm.add(norm_final)

        if not should_follow(final_u, origin, same_domain_only):
            if throttle_ms > 0:
                await asyncio.sleep(throttle_ms / 1000)
            continue

        visited_list.append(final_u)

        # Generate markdown and extract metadata for crawled page
        soup_raw = None
        try:
            soup_raw = soup_from_html(html2)
            page_meta = extract_page_meta(soup_raw)
            soup_pre = preprocess_soup(
                soup_raw,
                base_url=final_u,
                final_url=final_u,
            )
            md = to_markdown(soup_pre)
            markdowns[final_u] = {
                "markdown": md,
                "page": page_meta,
                "headings": extract_headings(soup_raw),
                "content_metrics": extract_content_metrics(soup_raw, markdown=md),
            }
        except Exception:
            logger.debug("Extraction failed for %s", final_u, exc_info=True)

        collect_emails(
            html2,
            final_u,
            include_emails=include_emails,
            deobfuscate_emails=deobfuscate_emails,
            all_emails=all_emails,
            emails_by_url=emails_by_url,
            email_sources=email_sources,
        )

        # Expand BFS frontier (reuse soup_raw if available)
        link_soup = soup_raw if soup_raw is not None else soup_from_html(html2)
        new_int, _, _ = classify_links(link_soup, base_url=final_u)
        for href2 in new_int:
            _enqueue(href2, final_u)

        if throttle_ms > 0:
            await asyncio.sleep(throttle_ms / 1000)

    if q and len(visited_list) >= crawl_max_pages:
        stop_reason = "max_pages"

    return {
        "visited": visited_list,
        "markdowns": markdowns,
        "stop_reason": stop_reason,
        "seeded": seeded,
        "all_emails": all_emails,
        "emails_by_url": emails_by_url,
        "email_sources": email_sources,
    }
