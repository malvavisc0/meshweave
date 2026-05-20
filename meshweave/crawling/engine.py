"""BFS crawl engine."""

import asyncio
import logging
from collections import deque
from collections.abc import Awaitable, Callable
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


def _render_metrics_to_dict(metrics: Any) -> dict[str, Any]:
    """Extract render metrics dict from a metrics dataclass."""
    return {
        "final_url": str(getattr(metrics, "final_url", "")),
        "response_status": int(getattr(metrics, "response_status", 0)),
        "network_requests": int(getattr(metrics, "network_requests", 0)),
        "content_length": int(getattr(metrics, "content_length", 0)),
        "load_time_ms": round(
            float(getattr(metrics, "load_time", 0.0)) * 1000,
            2,
        ),
        "cache_hit": bool(getattr(metrics, "cache_hit", False)),
        "errors": list(getattr(metrics, "errors", [])),
    }


async def bfs_crawl(
    origin: str,
    internal_links: list[str],
    *,
    session: BrowserSession,
    crawl_max_pages: int = 25,
    max_depth: int = 0,
    same_domain_only: bool = True,
    include_emails: bool = True,
    deobfuscate_emails: bool = True,
    throttle_ms: int = 0,
    per_page_timeout: float = 15.0,
    cache_dir: str | None = None,
    sitemap_seeds: list[str] | None = None,
    on_page_crawled: Callable[[str, dict[str, Any]], Awaitable[None]] | None = None,
    should_continue: Callable[[], Awaitable[bool]] | None = None,
    url_filter: Callable[[str], bool] | None = None,
) -> dict[str, Any]:
    """Run BFS crawl starting from origin, returning crawl state.

    Parameters
    ----------
    origin:
        Canonical start URL.
    internal_links:
        Seed internal links discovered from the start page.
    session:
        Active browser session.
    crawl_max_pages:
        Maximum pages to visit (including the origin).
    max_depth:
        Maximum link depth from origin.  0 means unlimited.
    same_domain_only:
        Restrict BFS to the origin's registrable domain.
    include_emails:
        Extract email addresses from pages.
    deobfuscate_emails:
        Attempt to deobfuscate email addresses.
    throttle_ms:
        Milliseconds to sleep between page fetches.
    per_page_timeout:
        Timeout per page render in seconds.
    cache_dir:
        Directory for rendered HTML cache.  None disables caching.
    sitemap_seeds:
        Additional URLs discovered from sitemaps.
    on_page_crawled:
        Async callback ``(url, page_data)`` invoked after each page is
        processed.  Used by the webapp for heartbeats.
    should_continue:
        Async callback ``()`` checked before each BFS iteration.
        Return ``False`` to stop the crawl (cancellation).
    url_filter:
        Synchronous predicate ``(url) -> bool`` applied to candidate
        URLs before enqueuing.  Return ``False`` to skip.

    Returns
    -------
    dict
        Keys: ``visited``, ``markdowns``, ``stop_reason``, ``seeded``,
        ``all_emails``, ``emails_by_url``, ``email_sources``.
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

    q: deque[tuple[str, int]] = deque()

    def _enqueue(href: str, base: str, depth: int) -> bool:
        absu = normalize_abs_url(href, base)
        if (
            absu
            and should_follow(absu, origin, same_domain_only)
            and absu not in visited_norm
            and len(visited_norm) < crawl_max_pages
        ):
            if url_filter and not url_filter(absu):
                return False
            visited_norm.add(absu)
            q.append((absu, depth))
            return True
        return False

    # Seed from start page links (depth 1)
    for href in internal_links:
        _enqueue(href, origin, 1)

    # Seed from sitemap (depth 1)
    seeded = 0
    for su in sitemap_seeds or []:
        if _enqueue(su, origin, 1):
            seeded += 1

    # BFS loop
    while q and len(visited_list) < crawl_max_pages:
        # Cooperative cancellation check
        if should_continue and not (await should_continue()):
            stop_reason = "cancelled"
            break

        u, depth = q.popleft()

        # Depth limit
        if max_depth > 0 and depth > max_depth:
            stop_reason = "max_depth"
            continue

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

        # Per-page render metrics
        render_metrics = _render_metrics_to_dict(m2)

        # Generate markdown and extract metadata for crawled page
        soup_raw = None
        page_data: dict[str, Any] = {}
        try:
            soup_raw = soup_from_html(html2)
            page_meta = extract_page_meta(soup_raw)
            soup_pre = preprocess_soup(
                soup_raw,
                base_url=final_u,
                final_url=final_u,
            )
            md = to_markdown(soup_pre)
            page_data = {
                "markdown": md,
                "page": page_meta,
                "headings": extract_headings(soup_raw),
                "content_metrics": extract_content_metrics(soup_raw, markdown=md),
            }
            markdowns[final_u] = page_data
        except Exception:
            logger.debug("Extraction failed for %s", final_u, exc_info=True)

        # Per-page emails
        collect_emails(
            html2,
            final_u,
            include_emails=include_emails,
            deobfuscate_emails=deobfuscate_emails,
            all_emails=all_emails,
            emails_by_url=emails_by_url,
            email_sources=email_sources,
        )

        # Per-page links and extraction metrics
        link_soup = soup_raw if soup_raw is not None else soup_from_html(html2)
        new_int, new_ext, ext_metrics = classify_links(link_soup, base_url=final_u)
        page_data["links"] = {"internal": new_int, "external": new_ext}
        page_data["extraction_metrics"] = ext_metrics
        page_data["render_metrics"] = render_metrics
        page_data["emails_unique"] = emails_by_url.get(final_u, [])

        # Expand BFS frontier
        for href2 in new_int:
            _enqueue(href2, final_u, depth + 1)

        # Heartbeat callback
        if on_page_crawled:
            try:
                await on_page_crawled(final_u, page_data)
            except Exception:
                logger.debug("on_page_crawled callback failed", exc_info=True)

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
