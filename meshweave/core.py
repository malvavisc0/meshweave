"""Core crawl orchestration for meshweave."""

import logging
import os
import re
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlsplit

from .crawling import (
    BrowserSession,
    bfs_crawl,
    discover_sitemap_urls,
    get_rendered_html,
)
from .crawling.blocked import (
    CrawlBlockedError,
    blocked_error,
    find_blocked_pages,
    start_page_blocked_reason,
)
from .crawling.fetcher import render_metrics_to_dict
from .extraction import (
    analyze_faq_schema,
    audit_entity_consistency,
    audit_meta_uniqueness,
    audit_schema_coverage,
    check_llms_txt,
    classify_links,
    collect_emails,
    deduplicate_sources,
    extract_content_metrics,
    extract_headings,
    extract_page_meta,
    fetch_robots_info,
    preprocess_soup,
    soup_from_html,
    to_markdown,
)
from .urls import (
    domain_of,
    looks_like_domain,
    normalize_abs_url,
    normalize_domain,
    origin_prefix,
    should_follow,
)

logger = logging.getLogger(__name__)

# Crawler-directive files that must never be seeded as content pages from
# sitemap discovery: they carry no headings/schema and would skew every
# per-page average (structure, schema coverage, content depth).
_INFRA_FILE_RE = re.compile(
    r"(^|/)(robots\.txt|llms(?:-full)?\.txt)$",
    re.IGNORECASE,
)

__all__ = [
    "crawl",
]


def _resolve_cache_config(
    disable_cache: bool,
    cache_dir: str | None,
) -> str | None:
    """Resolve the cache directory from args, env, or default."""
    cache_env = cache_dir or os.getenv("MESHWEAVE_CACHE_DIR") or "/tmp/meshweave/cache"
    disable = disable_cache or os.getenv(
        "MESHWEAVE_DISABLE_CACHE", ""
    ).strip().lower() in ("1", "true", "yes", "on")
    return None if disable else cache_env


def _process_page(
    html: str,
    base_url: str,
    final_url: str,
) -> dict[str, Any]:
    """Parse HTML and extract all page-level data.

    Returns dict with keys: page_meta, markdown,
    internal_links, external_links, extraction_metrics.
    """
    soup_raw = soup_from_html(html)
    page_meta = extract_page_meta(soup_raw)
    headings = extract_headings(soup_raw)
    soup_pre = preprocess_soup(
        soup_raw,
        base_url=base_url,
        final_url=final_url,
    )
    md = to_markdown(soup_pre)
    content_metrics = extract_content_metrics(soup_raw, markdown=md)
    faq_analysis = analyze_faq_schema(page_meta.get("jsonld", []))
    internal, external, ext_metrics = classify_links(soup_raw, base_url=final_url)
    return {
        "page_meta": page_meta,
        "headings": headings,
        "content_metrics": content_metrics,
        "faq_analysis": faq_analysis,
        "markdown": md,
        "internal_links": internal,
        "external_links": external,
        "extraction_metrics": ext_metrics,
    }


def _build_payload(
    *,
    page_data: dict[str, Any],
    render_metrics: dict[str, Any],
    all_emails: set[str],
    emails_by_url: dict[str, list[str]],
    deduped_sources: list[dict[str, Any]],
    crawl_max_pages: int,
    origin: str,
    crawl_result: dict[str, Any],
    sitemap_meta: dict[str, Any],
    include_emails: bool,
    robots_info: dict[str, Any] | None = None,
    llms_info: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble the final JSON payload from all results."""
    # Merge start page markdown with crawled page markdowns
    markdowns: dict[str, dict[str, Any]] = dict(crawl_result.get("markdowns", {}))
    if origin and page_data.get("markdown"):
        start_page_emails = emails_by_url.get(origin, [])
        markdowns.setdefault(
            origin,
            {
                "markdown": page_data["markdown"],
                "page": page_data["page_meta"],
                "headings": page_data.get("headings", {}),
                "content_metrics": page_data.get("content_metrics", {}),
                "render_metrics": render_metrics,
                "links": {
                    "internal": page_data.get("internal_links", []),
                    "external": page_data.get("external_links", []),
                },
                "extraction_metrics": page_data.get("extraction_metrics", {}),
                "emails_unique": start_page_emails,
            },
        )

    # Partial block: drop sub-pages that came back as bot-protection
    # interstitials. Their challenge markup is not site content —
    # leaving it in would drag content-based scoring down as if the
    # site itself had thin pages. The URLs are recorded on the crawl
    # metadata so the exclusion stays debuggable.
    blocked_pages = find_blocked_pages(markdowns)
    for blocked_url in blocked_pages:
        markdowns.pop(blocked_url, None)

    payload: dict[str, Any] = {
        "page": page_data["page_meta"],
        "markdowns": markdowns,
        "links": {
            "internal": page_data["internal_links"],
            "external": page_data["external_links"],
        },
        "metrics": {
            "render": render_metrics,
            "extraction": page_data["extraction_metrics"],
        },
    }

    # Build pages[] array from markdowns dict
    # Origin page at index 0, rest sorted alphabetically by URL
    pages: list[dict[str, Any]] = []
    origin_url = origin
    origin_entry = None
    sorted_entries: list[tuple[str, dict[str, Any]]] = []

    for url, entry in markdowns.items():
        page_entry = {
            "url": url,
            "page": entry.get("page", {}),
            "markdown": entry.get("markdown", ""),
            "headings": entry.get("headings", {}),
            "content_metrics": entry.get("content_metrics", {}),
            "metrics": {
                "render": entry.get("render_metrics", {}),
                "extraction": entry.get("extraction_metrics", {}),
            },
            "emails": {"unique": entry.get("emails_unique", [])},
            "links": entry.get("links", {}),
        }
        if url == origin_url:
            origin_entry = page_entry
        else:
            sorted_entries.append((url, page_entry))

    # Sort remaining pages alphabetically by URL
    sorted_entries.sort(key=lambda x: x[0])

    # Origin first, then sorted pages
    if origin_entry:
        pages.append(origin_entry)
    pages.extend(entry for _, entry in sorted_entries)

    payload["pages"] = pages

    # Webapp convenience fields
    payload["scope"] = "site"
    payload["domain"] = domain_of(origin)
    payload["canonical_url"] = origin
    payload["summary"] = {
        "visited_count": len(crawl_result["visited"]),
        "reason_stopped": crawl_result["stop_reason"],
    }

    if include_emails:
        payload["emails"] = {
            "unique": sorted(all_emails),
            "by_url": emails_by_url,
            "sources": deduped_sources,
            "counts": {
                "total_unique": len(all_emails),
                "total_mentions": sum(len(v) for v in emails_by_url.values()),
            },
        }

    payload["crawl"] = {
        "enabled": True,
        "start_url": origin,
        "visited": crawl_result["visited"],
        "limits": {"max_pages": crawl_max_pages},
        "reason_stopped": crawl_result["stop_reason"],
        "sitemap": sitemap_meta,
        "blocked_pages": blocked_pages,
    }

    # AEO/GEO: FAQ analysis (cross-page, not per-page)
    # Always include scoring keys — never omit them.
    # None/null is a valid signal meaning "not applicable / not found".
    payload["faq_analysis"] = page_data.get("faq_analysis")

    # AEO/GEO: domain-level accessibility
    payload["robots"] = robots_info
    payload["llms_txt"] = llms_info

    # AEO/GEO: cross-page audits
    page_meta = page_data.get("page_meta")
    # The start page is already merged into *markdowns* above; only pass its
    # meta separately when it is absent from markdowns (e.g. no markdown was
    # extracted), so the audits do not double-count it.
    audit_start_meta = page_meta if origin not in markdowns else None
    payload["audit"] = {
        "meta": audit_meta_uniqueness(markdowns, audit_start_meta),
        "entity": audit_entity_consistency(markdowns, audit_start_meta),
        "schema_coverage": audit_schema_coverage(markdowns, audit_start_meta),
    }

    return payload


def _start_url(url: str) -> str:
    """Normalize a domain-only input to an https start URL."""
    is_domain = looks_like_domain(url)
    return f"https://{normalize_domain(url)}/" if is_domain else url


async def _fetch_robots_and_llms(
    base: str,
    session: BrowserSession,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Fetch robots.txt and llms.txt info, logging failures at debug level."""
    robots_info: dict[str, Any] = {}
    llms_info: dict[str, Any] = {}
    try:
        robots_info = await fetch_robots_info(base, session=session)
    except Exception:
        logger.debug(
            "robots.txt fetch failed for %s",
            base,
            exc_info=True,
        )
    try:
        llms_info = await check_llms_txt(base, session=session)
    except Exception:
        logger.debug(
            "llms.txt check failed for %s",
            base,
            exc_info=True,
        )
    return robots_info, llms_info


async def _discover_sitemap_seeds(
    origin: str,
    origin_pfx: str,
    robots_sitemaps: list,
    session: BrowserSession,
    max_urls: int,
) -> tuple[list[str], dict[str, Any]]:
    """Discover sitemap URLs and filter them into crawlable seeds.

    Returns (seeds, sitemap_meta) where the meta dict carries the
    discovery bookkeeping fields for the payload.
    """
    sitemap_meta: dict[str, Any] = {
        "used": False,
        "sources": [],
        "urls_seeded": 0,
        "discovered": 0,
    }
    seeds: list[str] = []
    try:
        discovered, sm_meta = await discover_sitemap_urls(
            domain_of(origin),
            session=session,
            max_urls=max_urls,
            robots_sitemaps=robots_sitemaps,
        )
        sitemap_meta["used"] = bool(discovered)
        sitemap_meta["discovered"] = len(discovered)
        sitemap_meta["sources"] = sm_meta.get("sources", [])
        seeds = _sitemap_seed_urls(discovered, origin, origin_pfx)
    except Exception:
        logger.debug(
            "Sitemap discovery failed for %s",
            origin,
            exc_info=True,
        )
    return seeds, sitemap_meta


def _sitemap_seed_urls(
    discovered: list,
    origin: str,
    origin_pfx: str,
) -> list[str]:
    """Normalize discovered sitemap URLs to in-scope content-page seeds."""
    seeds: list[str] = []
    for u in discovered:
        normu = normalize_abs_url(u, origin)
        if normu and should_follow(normu, origin_pfx):
            # Crawler directives are infrastructure, not content
            # pages: seeding them would drag every per-page average
            # (structure, schema coverage, depth) down.
            if _INFRA_FILE_RE.search(normu):
                continue
            seeds.append(normu)
    return seeds


async def crawl(
    url: str,
    *,
    crawl_max_pages: int = 25,
    max_depth: int = 1,
    include_emails: bool = True,
    deobfuscate_emails: bool = True,
    throttle_ms: int = 0,
    per_page_timeout: float = 15.0,
    disable_cache: bool = False,
    cache_dir: str | None = None,
    on_page_crawled: Callable[[str, dict[str, Any]], Awaitable[None]] | None = None,
    should_continue: Callable[[], Awaitable[bool]] | None = None,
    url_filter: Callable[[str], bool] | None = None,
) -> dict[str, Any]:
    """Render a URL, convert to markdown, classify links, BFS-crawl
    internal pages within the URL's path scope, extract emails.
    """
    local_cache = _resolve_cache_config(disable_cache, cache_dir)

    # 1) Determine start URL
    start_url = _start_url(url)

    # Origin prefix defines the crawl scope
    origin_pfx: str = ""  # set after rendering

    sitemap_meta: dict[str, Any] = {
        "used": False,
        "sources": [],
        "urls_seeded": 0,
        "discovered": 0,
    }

    async with BrowserSession() as session:
        html, metrics = await get_rendered_html(
            url=start_url,
            session=session,
            progressive_scroll=True,
            return_metrics=True,
            timeout=30.0,
            wait_until="networkidle",
            cache_dir=local_cache,
        )

        final_url = str(getattr(metrics, "final_url", ""))
        origin = final_url or start_url
        origin_pfx = origin_prefix(origin)

        # 3) Process page (parse, meta, clean, markdown, links)
        page_data = _process_page(html, url, final_url)

        # 3b) Abort early when the start page was a bot-protection
        # interstitial or a refusal status. Without this, the BFS below
        # would crawl every sitemap URL through the same block —
        # burning the whole crawl budget and time budget on challenged
        # pages before the caller could detect anything.
        blocked_reason = start_page_blocked_reason(
            status=int(getattr(metrics, "response_status", 0) or 0),
            title=str(page_data["page_meta"].get("title") or "").strip(),
            markdown=page_data.get("markdown") or "",
        )
        if blocked_reason:
            raise CrawlBlockedError(blocked_error(blocked_reason))

        # 4) robots.txt and llms.txt (always check)
        _parts = urlsplit(origin)
        base = f"{_parts.scheme}://{_parts.netloc}"
        robots_info, llms_info = await _fetch_robots_and_llms(base, session)

        # 5) Sitemap discovery (always attempt)
        sitemap_seeds, sitemap_meta = await _discover_sitemap_seeds(
            origin,
            origin_pfx,
            robots_info.get("sitemaps", []),
            session,
            max(1, crawl_max_pages * 5),
        )

        render_metrics = render_metrics_to_dict(metrics)

        # 5) Collect emails on start page
        all_emails: set[str] = set()
        emails_by_url: dict[str, list[str]] = {}
        email_sources: list[dict[str, Any]] = []

        collect_emails(
            html,
            origin,
            include_emails=include_emails,
            deobfuscate_emails=deobfuscate_emails,
            all_emails=all_emails,
            emails_by_url=emails_by_url,
            email_sources=email_sources,
        )

        # 6) BFS crawl (always crawl internal links)
        crawl_result: dict[str, Any] = {
            "visited": [origin] if origin else [],
            "stop_reason": "queue_empty",
            "seeded": 0,
            "all_emails": set(),
            "emails_by_url": {},
            "email_sources": [],
        }

        if crawl_max_pages > 1:
            crawl_result = await bfs_crawl(
                origin,
                page_data["internal_links"],
                session=session,
                crawl_max_pages=crawl_max_pages,
                max_depth=max_depth,
                include_emails=include_emails,
                deobfuscate_emails=deobfuscate_emails,
                throttle_ms=throttle_ms,
                per_page_timeout=per_page_timeout,
                cache_dir=local_cache,
                sitemap_seeds=sitemap_seeds,
                on_page_crawled=on_page_crawled,
                should_continue=should_continue,
                url_filter=url_filter,
            )

        # 7) Merge and deduplicate
        all_emails |= crawl_result["all_emails"]
        emails_by_url |= crawl_result["emails_by_url"]
        email_sources.extend(crawl_result["email_sources"])
        sitemap_meta["urls_seeded"] = crawl_result["seeded"]
        deduped = deduplicate_sources(email_sources)

        # 8) Build and return payload
        return _build_payload(
            page_data=page_data,
            render_metrics=render_metrics,
            all_emails=all_emails,
            emails_by_url=emails_by_url,
            deduped_sources=deduped,
            crawl_max_pages=crawl_max_pages,
            origin=origin,
            crawl_result=crawl_result,
            sitemap_meta=sitemap_meta,
            include_emails=include_emails,
            robots_info=robots_info,
            llms_info=llms_info,
        )
