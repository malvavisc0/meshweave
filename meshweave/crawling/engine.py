"""BFS crawl engine."""

import asyncio
import logging
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
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
from ..urls import normalize_abs_url, origin_prefix, should_follow
from .fetcher import (
    BrowserSession,
    get_rendered_html,
    render_metrics_to_dict,
)

logger = logging.getLogger(__name__)

__all__ = [
    "bfs_crawl",
]


@dataclass
class _CrawlState:
    """Mutable state accumulated across BFS iterations."""

    origin: str
    origin_pfx: str
    session: BrowserSession
    include_emails: bool
    deobfuscate_emails: bool
    throttle_ms: int
    per_page_timeout: float
    crawl_max_pages: int
    max_depth: int
    cache_dir: str | None
    url_filter: Callable[[str], bool] | None
    on_page_crawled: Callable[[str, dict[str, Any]], Awaitable[None]] | None

    visited_norm: set[str] = field(default_factory=set)
    visited_list: list[str] = field(default_factory=list)
    markdowns: dict[str, dict[str, Any]] = field(default_factory=dict)
    all_emails: set[str] = field(default_factory=set)
    email_sources: list[dict[str, Any]] = field(default_factory=list)
    emails_by_url: dict[str, list[str]] = field(default_factory=dict)
    seeded: int = 0
    frontier_truncated_by_depth: bool = False

    def enqueue(
        self, q: deque[tuple[str, int]], href: str, base: str, depth: int
    ) -> bool:
        absu = normalize_abs_url(href, base)
        if (
            absu
            and should_follow(absu, self.origin_pfx)
            and absu not in self.visited_norm
            # Bound queued work to the page budget using *actual* visits plus
            # already-queued URLs. visited_norm is reserved for dedup only, so
            # failed renders / redirect aliases don't shrink the budget.
            and len(self.visited_list) + len(q) < self.crawl_max_pages
        ):
            if self.url_filter and not self.url_filter(absu):
                return False
            self.visited_norm.add(absu)
            q.append((absu, depth))
            return True
        return False

    async def wait_throttle(self) -> None:
        if self.throttle_ms > 0:
            await asyncio.sleep(self.throttle_ms / 1000)


async def bfs_crawl(
    origin: str,
    internal_links: list[str],
    *,
    session: BrowserSession,
    crawl_max_pages: int = 25,
    max_depth: int = 1,
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
        Maximum link depth from origin.  0 means unlimited.  Default is 1 (only pages linked from origin).
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
    state = _CrawlState(
        origin=origin,
        origin_pfx=origin_prefix(origin),
        session=session,
        include_emails=include_emails,
        deobfuscate_emails=deobfuscate_emails,
        throttle_ms=throttle_ms,
        per_page_timeout=per_page_timeout,
        crawl_max_pages=crawl_max_pages,
        max_depth=max_depth,
        cache_dir=cache_dir,
        url_filter=url_filter,
        on_page_crawled=on_page_crawled,
    )

    norm_start = normalize_abs_url(origin, origin)
    if norm_start:
        state.visited_norm.add(norm_start)
        state.visited_list.append(origin)

    q: deque[tuple[str, int]] = deque()
    _seed_queue(state, q, internal_links, sitemap_seeds, origin)

    # BFS loop
    stop_reason = "queue_empty"
    while q and len(state.visited_list) < crawl_max_pages:
        # Cooperative cancellation check
        if should_continue and not (await should_continue()):
            stop_reason = "cancelled"
            break

        u, depth = q.popleft()

        # Depth limit (defensive: frontier expansion below is already
        # depth-bounded, so queued items should not exceed max_depth). Do not
        # set stop_reason here — depth pruning is not a loop-termination reason.
        if max_depth > 0 and depth > max_depth:
            continue

        if not await _crawl_page(state, q, u, depth):
            continue

    stop_reason = _finalize_stop_reason(stop_reason, q, state, crawl_max_pages)

    return {
        "visited": state.visited_list,
        "markdowns": state.markdowns,
        "stop_reason": stop_reason,
        "seeded": state.seeded,
        "all_emails": state.all_emails,
        "emails_by_url": state.emails_by_url,
        "email_sources": state.email_sources,
    }


def _seed_queue(
    state: _CrawlState,
    q: deque[tuple[str, int]],
    internal_links: list[str],
    sitemap_seeds: list[str] | None,
    origin: str,
) -> None:
    # Seed from start page links (shortest URLs first, then alpha).
    # Links discovered on the origin page are first-hop, i.e. depth 1.
    for href in sorted(internal_links, key=lambda u: (len(u), u)):
        state.enqueue(q, href, origin, 1)

    # Seed from sitemap (shortest URLs first, then alpha). Sitemap URLs are
    # treated as first-hop seeds (depth 1) so depth stays a pure BFS hop count.
    for su in sorted(sitemap_seeds or [], key=lambda u: (len(u), u)):
        if state.enqueue(q, su, origin, 1):
            state.seeded += 1


def _finalize_stop_reason(
    stop_reason: str,
    q: deque[tuple[str, int]],
    state: _CrawlState,
    crawl_max_pages: int,
) -> str:
    # "cancelled" is set on break and must not be overridden.
    if stop_reason != "cancelled":
        if q and len(state.visited_list) >= crawl_max_pages:
            stop_reason = "max_pages"
        elif not q and state.frontier_truncated_by_depth:
            stop_reason = "max_depth"
        # otherwise the queue drained naturally: keep the default "queue_empty"
    return stop_reason


async def _crawl_page(
    state: _CrawlState,
    q: deque[tuple[str, int]],
    u: str,
    depth: int,
) -> bool:
    """Fetch, parse, score, and enqueue one URL. Returns False to skip."""
    try:
        html2, m2 = await get_rendered_html(
            url=u,
            session=state.session,
            progressive_scroll=False,
            return_metrics=True,
            timeout=max(1.0, state.per_page_timeout),
            wait_until="networkidle",
            cache_dir=state.cache_dir,
        )
    except Exception:
        logger.debug("Failed to fetch %s", u, exc_info=True)
        await state.wait_throttle()
        return False

    final_u = str(getattr(m2, "final_url", u)) or u

    # Track the final (possibly redirected) URL to avoid revisits
    norm_final = normalize_abs_url(final_u, final_u)
    if norm_final and norm_final != normalize_abs_url(u, u):
        if norm_final in state.visited_norm:
            # Already visited via a different URL
            await state.wait_throttle()
            return False
        state.visited_norm.add(norm_final)

    if not should_follow(final_u, state.origin_pfx):
        await state.wait_throttle()
        return False

    state.visited_list.append(final_u)

    return await _process_page(state, q, html2, m2, u, final_u, depth)


async def _process_page(
    state: _CrawlState,
    q: deque[tuple[str, int]],
    html2: str,
    m2: Any,
    u: str,
    final_u: str,
    depth: int,
) -> bool:
    """Extract markdown/emails/links from a fetched page and expand the frontier."""
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
        state.markdowns[final_u] = page_data
    except Exception:
        logger.debug("Extraction failed for %s", final_u, exc_info=True)

    # Per-page emails
    collect_emails(
        html2,
        final_u,
        include_emails=state.include_emails,
        deobfuscate_emails=state.deobfuscate_emails,
        all_emails=state.all_emails,
        emails_by_url=state.emails_by_url,
        email_sources=state.email_sources,
    )

    # Per-page links and extraction metrics
    link_soup = soup_raw if soup_raw is not None else soup_from_html(html2)
    new_int, new_ext, ext_metrics = classify_links(link_soup, base_url=final_u)
    page_data["links"] = {"internal": new_int, "external": new_ext}
    page_data["extraction_metrics"] = ext_metrics
    page_data["render_metrics"] = render_metrics_to_dict(m2)
    page_data["emails_unique"] = state.emails_by_url.get(final_u, [])

    # Expand BFS frontier (only if within depth limit)
    if state.max_depth == 0 or depth < state.max_depth:
        for href2 in new_int:
            state.enqueue(q, href2, final_u, depth + 1)
    elif new_int:
        # Internal links exist but the depth limit stops us from following
        # them — the crawl is bounded by depth rather than running dry.
        state.frontier_truncated_by_depth = True

    # Heartbeat callback
    if state.on_page_crawled:
        try:
            await state.on_page_crawled(final_u, page_data)
        except Exception:
            logger.debug("on_page_crawled callback failed", exc_info=True)

    await state.wait_throttle()
    return True
