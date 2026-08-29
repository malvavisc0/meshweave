"""Sitemap discovery and XML parsing for meshweave.

Provides sitemap URL discovery via robots.txt and common endpoints,
and XML sitemap parsing for URL extraction.
"""

import logging
import xml.etree.ElementTree as ET
from collections import deque
from typing import Any
from urllib.parse import urljoin

from ..urls import normalize_domain
from .fetcher import BrowserSession, fetch_text

__all__ = [
    "discover_sitemap_urls",
]

logger = logging.getLogger(__name__)


def _lname(tag: str) -> str:
    """Local name of a (possibly namespaced) XML tag."""
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _parse_sitemap_xml(xml_text: str, base_url: str) -> tuple[list[str], list[str]]:
    """Parse a sitemap XML payload and return (urls, child_sitemaps).

    Args:
        xml_text: Raw XML text (already decompressed if needed).
        base_url: URL of the sitemap file for resolving
            relative loc entries.

    Returns:
        tuple[list[str], list[str]]: URLs and nested sitemap
            URLs (for sitemapindex).
    """
    urls: list[str] = []
    sitemaps: list[str] = []

    try:
        root = ET.fromstring(xml_text)
    except Exception:
        return urls, sitemaps

    # sitemapindex case: collect sitemap/loc
    if _lname(root.tag) == "sitemapindex":
        return urls, _collect_locs(root, base_url)

    # urlset (or generic): collect url/loc
    return _collect_locs(root, base_url), sitemaps


def _collect_locs(root: ET.Element, base_url: str) -> list[str]:
    """Absolute URLs from every ``loc`` element under *root*."""
    locs: list[str] = []
    for el in root.iter():
        if _lname(el.tag) == "loc":
            loc = (el.text or "").strip()
            if loc:
                locs.append(urljoin(base_url, loc))
    return locs


async def discover_sitemap_urls(
    domain: str,
    *,
    session: BrowserSession,
    max_urls: int = 1000,
    robots_sitemaps: list[str] | None = None,
) -> tuple[list[str], dict[str, Any]]:
    """Discover sitemap URLs for a given domain via robots.txt
    and common endpoints.

    Args:
        domain: Domain host (no scheme).
        max_urls: Upper bound on number of page URLs to collect.
        robots_sitemaps: Optional pre-fetched sitemap URLs from
            robots.txt.  When provided, the robots.txt fetch is
            skipped and these URLs are used directly.

    Returns:
        tuple[list[str], dict]: (urls, meta) where meta has a
            'sources' array describing attempts.
    """
    d = normalize_domain(domain or "")
    sources: list[dict[str, Any]] = []
    urls: list[str] = []
    seen_sitemaps: set[str] = set()

    candidates = await _sitemap_candidates(d, robots_sitemaps, session, sources)
    fetch_queue: deque[str] = deque(_dedupe(candidates))
    # Limit nested sitemap traversal
    child_sitemap_limit = 20

    while fetch_queue and len(urls) < max_urls:
        sm_url = fetch_queue.popleft()
        if sm_url in seen_sitemaps:
            continue
        seen_sitemaps.add(sm_url)

        page_urls, child_sitemaps, meta = await _fetch_sitemap(sm_url, session)
        _enqueue_child_sitemaps(
            child_sitemaps, fetch_queue, seen_sitemaps, child_sitemap_limit
        )
        _append_capped(urls, page_urls, max_urls)
        sources.append(meta)

    return urls, {"sources": sources}


async def _fetch_sitemap(
    sm_url: str,
    session: BrowserSession,
) -> tuple[list[str], list[str], dict[str, Any]]:
    """Fetch and parse one sitemap, returning (urls, children, source meta)."""
    text = await fetch_text(sm_url, session=session, timeout=12.0)
    if not text:
        return [], [], {"type": "sitemap", "url": sm_url, "ok": False}
    page_urls, child_sitemaps = _parse_sitemap_xml(text, base_url=sm_url)
    meta: dict[str, Any] = {
        "type": "sitemap",
        "url": sm_url,
        "ok": True,
        "urls": len(page_urls),
        "children": len(child_sitemaps),
    }
    return page_urls, child_sitemaps, meta


def _enqueue_child_sitemaps(
    child_sitemaps: list[str],
    fetch_queue: deque[str],
    seen_sitemaps: set[str],
    limit: int,
) -> None:
    """Queue unseen child sitemaps while staying within *limit*."""
    for cs in child_sitemaps:
        if len(seen_sitemaps) + len(fetch_queue) >= limit:
            break
        if cs not in seen_sitemaps:
            fetch_queue.append(cs)


def _append_capped(urls: list[str], page_urls: list[str], max_urls: int) -> None:
    """Append page URLs until *max_urls* is reached."""
    for u in page_urls:
        if len(urls) >= max_urls:
            break
        urls.append(u)


async def _sitemap_candidates(
    d: str,
    robots_sitemaps: list[str] | None,
    session: BrowserSession,
    sources: list[dict[str, Any]],
) -> list[str]:
    """Collect sitemap candidate URLs from robots.txt and common endpoints."""
    candidates: list[str] = []
    if robots_sitemaps is not None:
        # Use pre-fetched sitemap URLs (avoid redundant fetch). These are
        # authoritative, so they are tried before common-endpoint guesses.
        candidates.extend(robots_sitemaps)
        sources.append(
            {
                "type": "robots",
                "found": len(robots_sitemaps),
                "status": "ok",
            }
        )
    elif d:
        # robots.txt discovery
        for scheme in ("https", "http"):
            robots_url = f"{scheme}://{d}/robots.txt"
            text = await fetch_text(
                robots_url,
                session=session,
                timeout=8.0,
            )
            found = 0
            status = "miss"
            if text:
                try:
                    for line in text.splitlines():
                        stripped = line.strip()
                        if stripped.lower().startswith("sitemap:"):
                            loc = stripped.split(":", 1)[1].strip()
                            if loc:
                                candidates.append(loc)
                                found += 1
                    status = "ok"
                except Exception:
                    status = "error"
            sources.append(
                {
                    "type": "robots",
                    "url": robots_url,
                    "found": found,
                    "status": status,
                }
            )

    if d:
        # Common endpoints as fallback guesses
        candidates.extend(
            [
                f"https://{d}/sitemap.xml",
                f"https://{d}/sitemap_index.xml",
                f"http://{d}/sitemap.xml",
                f"http://{d}/sitemap_index.xml",
            ]
        )
    return candidates


def _dedupe(candidates: list[str]) -> list[str]:
    """De-duplicate candidates preserving order."""
    seen_c: set[str] = set()
    dedup_candidates: list[str] = []
    for c in candidates:
        c = str(c).strip()
        if not c or c in seen_c:
            continue
        seen_c.add(c)
        dedup_candidates.append(c)
    return dedup_candidates
