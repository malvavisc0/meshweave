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
from .fetcher import BrowserSession, get_rendered_html

__all__ = [
    "discover_sitemap_urls",
]

logger = logging.getLogger(__name__)

# HTML tags that CloakBrowser wraps around plain-text/XML content.
_STRIP_TAGS = (
    "<html>",
    "</html>",
    "<head>",
    "</head>",
    "<body>",
    "</body>",
    "<pre>",
    "</pre>",
)


async def _fetch_text(
    url: str,
    *,
    session: BrowserSession,
    timeout: float = 10.0,
) -> str | None:
    """Fetch a URL via CloakBrowser and return body text, or None.

    CloakBrowser renders through Chromium which handles bot
    detection, JS challenges, and TLS fingerprinting.
    Plain-text/XML responses are wrapped in minimal HTML tags
    which are stripped before returning.
    """
    try:
        html = await get_rendered_html(
            url=url,
            session=session,
            progressive_scroll=False,
            return_metrics=False,
            timeout=timeout,
            wait_until="domcontentloaded",
        )
        if isinstance(html, str):
            text = html
            for tag in _STRIP_TAGS:
                text = text.replace(tag, "")
            return text.strip()
    except Exception as exc:
        logger.debug("Failed to fetch %s: %s", url, exc)
    return None


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

    def _lname(tag: str) -> str:
        return tag.rsplit("}", 1)[-1] if "}" in tag else tag

    # sitemapindex case: collect sitemap/loc
    if _lname(root.tag) == "sitemapindex":
        for el in root.iter():
            if _lname(el.tag) == "loc":
                loc = (el.text or "").strip()
                if loc:
                    sitemaps.append(urljoin(base_url, loc))
        return urls, sitemaps

    # urlset (or generic): collect url/loc
    for el in root.iter():
        if _lname(el.tag) == "loc":
            loc = (el.text or "").strip()
            if loc:
                urls.append(urljoin(base_url, loc))
    return urls, sitemaps


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

    # Candidates: common endpoints
    candidates: list[str] = []
    if d:
        candidates.extend([
            f"https://{d}/sitemap.xml",
            f"https://{d}/sitemap_index.xml",
            f"http://{d}/sitemap.xml",
            f"http://{d}/sitemap_index.xml",
        ])

        if robots_sitemaps is not None:
            # Use pre-fetched sitemap URLs (avoid redundant fetch)
            candidates.extend(robots_sitemaps)
            sources.append({
                "type": "robots",
                "found": len(robots_sitemaps),
                "status": "ok",
            })
        else:
            # robots.txt discovery
            for scheme in ("https", "http"):
                robots_url = f"{scheme}://{d}/robots.txt"
                text = await _fetch_text(
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
                sources.append({
                    "type": "robots",
                    "url": robots_url,
                    "found": found,
                    "status": status,
                })

    # De-duplicate candidates preserving order
    seen_c: set[str] = set()
    dedup_candidates: list[str] = []
    for c in candidates:
        c = str(c).strip()
        if not c or c in seen_c:
            continue
        seen_c.add(c)
        dedup_candidates.append(c)

    fetch_queue: deque[str] = deque(dedup_candidates)
    # Limit nested sitemap traversal
    child_sitemap_limit = 20

    while fetch_queue and len(urls) < max_urls:
        sm_url = fetch_queue.popleft()
        if sm_url in seen_sitemaps:
            continue
        seen_sitemaps.add(sm_url)

        text = await _fetch_text(sm_url, session=session, timeout=12.0)
        meta: dict[str, Any] = {
            "type": "sitemap",
            "url": sm_url,
            "ok": bool(text),
        }
        if not text:
            sources.append(meta)
            continue

        page_urls, child_sitemaps = _parse_sitemap_xml(text, base_url=sm_url)

        # Enqueue child sitemaps within limit
        for cs in child_sitemaps:
            if len(seen_sitemaps) + len(fetch_queue) >= child_sitemap_limit:
                break
            if cs not in seen_sitemaps:
                fetch_queue.append(cs)

        # Collect URLs up to max
        for u in page_urls:
            if len(urls) >= max_urls:
                break
            urls.append(u)

        meta["ok"] = True
        meta["urls"] = len(page_urls)
        meta["children"] = len(child_sitemaps)
        sources.append(meta)

    return urls, {"sources": sources}
