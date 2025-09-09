import asyncio
import os
import re
import time
from collections import deque
from typing import Any, Dict, List, Set, Tuple
from urllib.parse import unquote, urljoin, urlparse, urlsplit, urlunsplit

from bs4 import BeautifulSoup
from bs4.element import Tag, Comment
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from html_to_markdown import convert_to_markdown
from pydantic import HttpUrl

from fetcher import get_rendered_html

app = FastAPI()


def _strip_www(domain: str) -> str:
    """
    Return the domain lowercased without a leading 'www.' if present.

    Examples:
      - 'WWW.Example.com' -> 'example.com'
      - 'www.sub.example.com' -> 'sub.example.com'
      - 'example.com' -> 'example.com'
    """
    d = (domain or "").lower()
    return d[4:] if d.startswith("www.") else d


def _domain_of(url: str) -> str:
    """
    Extract the normalized domain (netloc) from a URL with 'www.' stripped.

    Args:
        url: Any string URL (may be empty or malformed).

    Returns:
        The domain portion lowercased with leading 'www.' removed, or empty string when not found.
    """
    try:
        parsed = urlparse(url or "")
        return _strip_www(parsed.netloc or "")
    except Exception:
        return ""


def _remove_query_and_fragment(href: str) -> str:
    """
    Remove query (?...) and fragment (#...) components from an href while keeping scheme, netloc and path.

    This function operates on both absolute and relative hrefs.
    For protocol-relative or relative paths, it preserves path and strips the rest.

    Args:
        href: The raw href value from an anchor.

    Returns:
        A string with query and fragment removed.
    """
    try:
        parts = urlsplit(href or "")
        return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
    except Exception:
        return href


def _normalize_abs_url(href: str, base_url: str) -> str:
    """
    Resolve href against a base URL and normalize it for deduplication during crawl.

    Normalization rules:
      - Resolve relative links to absolute using base_url.
      - Lowercase the scheme and host (netloc).
      - Remove query string and fragment.
      - Remove trailing slash from path unless it is the root path ("/").

    Args:
        href: The link to resolve (relative or absolute).
        base_url: The base URL for resolution.

    Returns:
        A normalized absolute URL string suitable for set-based deduplication.
    """
    try:
        absolute = urljoin(base_url or "", href or "")
        parts = urlsplit(absolute)
        scheme = parts.scheme.lower()
        netloc = (parts.netloc or "").lower()
        # normalize path: remove trailing slash if not root
        path = parts.path or "/"
        if len(path) > 1 and path.endswith("/"):
            path = path[:-1]
        return urlunsplit((scheme, netloc, path, "", ""))
    except Exception:
        return href or ""


def _same_domain(u1: str, u2: str) -> bool:
    """
    Check whether two URLs share the same normalized base domain (with 'www.' stripped).

    Args:
        u1: First URL
        u2: Second URL

    Returns:
        True if domains are equal after normalization, else False.
    """
    return _domain_of(u1) == _domain_of(u2)


def _is_skippable(href: Any) -> bool:
    """
    Determine if a link href should be skipped from consideration.

    Skips:
      - None, empty strings, or fragment-only anchors ('#...')
      - Non-navigational schemes: mailto:, javascript:, tel:, data:

    Args:
        href: Raw href value.

    Returns:
        True if the href should be ignored, else False.
    """
    if href is None:
        return True
    h = str(href).strip()
    if not h or h.startswith("#"):
        return True
    lower = h.lower()
    return lower.startswith(("mailto:", "javascript:", "tel:", "data:"))


# Ignore-path utilities for cleaner internal link lists and crawl queue
_DEFAULT_IGNORE_PATTERNS = [
    r"^/(api|auth|account|login|signup)(/|$)",
    r"^/(static|assets|cdn)/",
    r"\.(mp3|mp4|pdf|zip|png|jpe?g|svg|webp|ico)(\?|$)",
]
_ENV_IGNORE_PATTERNS = [
    p.strip()
    for p in (os.getenv("MARKDOWNIFY_IGNORE_PATHS") or "").split(",")
    if p.strip()
]
_IGNORE_REGEXES = [re.compile(p, re.I) for p in (_DEFAULT_IGNORE_PATTERNS + _ENV_IGNORE_PATTERNS)]


def _should_ignore_path(path: str) -> bool:
    """
    Return True if the given path should be ignored based on default/env patterns.
    """
    p = path or ""
    for rx in _IGNORE_REGEXES:
        if rx.search(p):
            return True
    return False


def _classify_links(soup: BeautifulSoup, base_url: str):
    """
    Extract, normalize, and classify links from a BeautifulSoup document.

    - Removes query and fragment from links.
    - Deduplicates by normalized link string.
    - Classifies links as internal vs external relative to base_url's domain.
    - Skips root paths ('/' and absolute same-domain roots) to avoid crawl loops.

    Args:
        soup: BeautifulSoup parsed DOM of the current page (post-processed to strip images).
        base_url: Final URL (after redirects) of the page the soup originates from.

    Returns:
        (internal, external, metrics) where:
          - internal: List[str] of normalized links within same domain or relative
          - external: List[str] of normalized links outside the domain
          - metrics: Dict with extraction stats and timings
    """
    start = time.perf_counter()
    base_domain = _domain_of(base_url)
    seen = set()
    internal = []
    external = []
    total_candidates = 0

    for a in soup.find_all("a"):
        if not isinstance(a, Tag):
            continue
        href = a.get("href")
        if _is_skippable(href):
            continue

        total_candidates += 1
        normalized = _remove_query_and_fragment(str(href).strip())

        # Skip root path to avoid crawl loops
        if normalized == "/":
            continue

        if normalized in seen:
            continue
        seen.add(normalized)

        parts = urlsplit(normalized)
        link_domain = _strip_www(parts.netloc or "")

        # Skip absolute site root (same-domain) to avoid loops
        if (
            base_domain
            and link_domain == base_domain
            and (parts.path == "" or parts.path == "/")
        ):
            continue

        # Absolute (http/https) or protocol-relative (//host)
        if parts.scheme in ("http", "https") or link_domain:
            if base_domain and link_domain == base_domain:
                if not _should_ignore_path(parts.path or ""):
                    internal.append(normalized)
            else:
                external.append(normalized)
        else:
            # Relative URL -> internal
            if not _should_ignore_path(parts.path or ""):
                internal.append(normalized)

    elapsed_ms = (time.perf_counter() - start) * 1000.0
    extraction_metrics = {
        "total_candidates": total_candidates,
        "unique_total": len(internal) + len(external),
        "internal_count": len(internal),
        "external_count": len(external),
        "base_domain": base_domain,
        "parse_time_ms": round(elapsed_ms, 2),
    }
    return internal, external, extraction_metrics


# Email extraction utilities
_EMAIL_REGEX = re.compile(r"([a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[A-Za-z]{2,})")


def _deobfuscate_text(text: str) -> str:
    """
    Best-effort deobfuscation of common textual email disguises.

    Replacements (case-insensitive, with token boundaries or brackets):
      - [at], (at), {at}, or standalone ' at ' -> '@'
      - [dot], (dot), {dot}, or standalone ' dot ' -> '.'

    Args:
        text: Raw visible text content from a page.

    Returns:
        The deobfuscated text string.
    """
    s = text

    # Normalize html entities variations of [at]/(at)/{at}
    # Use word boundaries and allow surrounding punctuation/spaces
    s = re.sub(r"(?i)\b\[\s*at\s*\]|\(\s*at\s*\)|\{\s*at\s*\}", "@", s)
    s = re.sub(r"(?i)\b\[\s*dot\s*\]|\(\s*dot\s*\)|\{\s*dot\s*\}", ".", s)

    # Replace standalone words
    s = re.sub(r"(?i)(?<=[\s,;:/\\\|<>\-\_])at(?=[\s,;:/\\\|<>\-\_])", "@", s)
    s = re.sub(r"(?i)(?<=[\s,;:/\\\|<>\-\_])dot(?=[\s,;:/\\\|<>\-\_])", ".", s)

    # Also handle variants like ' at ' and ' dot ' conservatively
    s = re.sub(r"(?i)\s+at\s+", " @ ", s)
    s = re.sub(r"(?i)\s+dot\s+", " . ", s)

    return s


def _extract_emails_from_mailto(soup: BeautifulSoup) -> Set[str]:
    """
    Extract emails from mailto: links within the provided BeautifulSoup document.

    - Unquotes URL-encoded characters.
    - Supports multiple recipients separated by ',' or ';'.
    - Strips any query parameters after '?'.

    Args:
        soup: BeautifulSoup DOM of a page.

    Returns:
        A set of lowercase email addresses found in mailto: links.
    """
    emails: Set[str] = set()
    for a in soup.find_all("a"):
        if not isinstance(a, Tag):
            continue
        href = a.get("href")
        if not href or not isinstance(href, str):
            continue
        lower = href.lower()
        if not lower.startswith("mailto:"):
            continue
        addr = href[len("mailto:") :]
        # Remove query params
        addr = addr.split("?", 1)[0]
        addr = unquote(addr)
        # Allow multiple recipients separated by , or ;
        parts = re.split(r"[;,]", addr)
        for p in parts:
            p = p.strip()
            if not p:
                continue
            m = _EMAIL_REGEX.fullmatch(p)
            if m:
                emails.add(m.group(1).lower())
    return emails


def _extract_emails_from_text(html: str, deobfuscate: bool) -> Tuple[Set[str], bool]:
    """
    Extract emails from visible text content of an HTML string.

    - Uses a robust email regex on the visible text derived via BeautifulSoup.get_text().
    - Optionally attempts deobfuscation and runs the regex again.
    - Deduplicates and lowercases all results.

    Args:
        html: Full HTML string of a page.
        deobfuscate: Whether to run deobfuscation passes first.

    Returns:
        (emails, had_obfuscated) where:
          - emails: Set of lowercase emails found in text
          - had_obfuscated: True if the deobfuscation altered the text (heuristic)
    """
    soup = BeautifulSoup(html, "lxml")
    text = soup.get_text(" ")
    found_obfuscated = False

    # First pass: raw text
    emails: Set[str] = {m.group(1).lower() for m in _EMAIL_REGEX.finditer(text)}

    if deobfuscate:
        deob = _deobfuscate_text(text)
        if deob != text:
            found_obfuscated = True
        emails_obf: Set[str] = {m.group(1).lower() for m in _EMAIL_REGEX.finditer(deob)}
        emails |= emails_obf

    return emails, found_obfuscated


def _extract_emails(
    html: str, deobfuscate: bool
) -> Tuple[Set[str], List[Dict[str, str]]]:
    """
    Aggregate email extraction results from both mailto: links and visible text.

    Args:
        html: Full HTML string to scan.
        deobfuscate: Whether to attempt obfuscation reversal for textual matches.

    Returns:
        (unique_emails, sources) where:
          - unique_emails: Set[str] of deduplicated lowercase emails across strategies
          - sources: List of dicts { "email": str, "found_as": "mailto"|"text"|"obfuscated" }
            Note: 'obfuscated' indicates the deobfuscation pass likely contributed.
    """
    sources: List[Dict[str, str]] = []
    soup = BeautifulSoup(html, "lxml")

    mailto_emails = _extract_emails_from_mailto(soup)
    for e in mailto_emails:
        sources.append({"email": e, "found_as": "mailto"})

    text_emails, had_obf = _extract_emails_from_text(html, deobfuscate)
    # For text emails, differentiate if deobfuscation likely contributed
    for e in text_emails:
        # If it also appears in mailto, we already have a mailto source; still add text source for completeness
        sources.append({"email": e, "found_as": "obfuscated" if had_obf else "text"})

    all_unique = mailto_emails | text_emails
    return all_unique, sources


@app.get("/crawl")
async def markdownify_html(
    url: HttpUrl,
    crawl_internal: bool = False,
    crawl_max_pages: int = 25,
    same_domain_only: bool = True,
    include_emails: bool = True,
    deobfuscate_emails: bool = True,
    throttle_ms: int = 0,
    per_page_timeout: float = 15.0,
):
    """
    Render a page, convert to markdown, classify links, optionally crawl internal pages, and extract emails.

    Query Parameters:
        url: Target page URL (http/https).
        crawl_internal: If True, perform breadth-first crawl of internal links.
        Crawls breadth-first until reaching crawl_max_pages.
        crawl_max_pages: Hard cap on total pages processed including the start page.
        same_domain_only: Restrict crawl to the same base domain as the starting page.
        include_emails: If True, extract emails from each visited page.
        deobfuscate_emails: If True, run deobfuscation heuristics for textual emails.
        throttle_ms: Milliseconds to sleep between crawled page fetches.
        per_page_timeout: Timeout in seconds for each crawled page fetch.

    Environment:
        MARKDOWNIFY_CACHE_DIR: If set, enables HTML caching for get_rendered_html.

    Returns:
        JSON payload containing:
          - markdown: Markdown conversion of the start page (images removed)
          - links: { internal: [...], external: [...] } from the start page
          - metrics: { render, extraction } stats for the start page
          - emails (optional): { unique, by_url, sources, counts }
          - crawl: { enabled, start_url, visited, limits, reason_stopped }
    """
    # Fetch starting page (cache directory from env, if provided)
    cache_dir_env = os.getenv("MARKDOWNIFY_CACHE_DIR") or "/tmp/markdownify/cache"
    html, metrics = await get_rendered_html(
        url=str(url),
        progressive_scroll=True,
        return_metrics=True,
        timeout=30,  # Reduced from 300s to 30s
        wait_until="domcontentloaded",  # Less strict than "networkidle"
        cache_dir=cache_dir_env,
    )

    # Preprocess HTML to remove images and preserve links that wrap images
    soup = BeautifulSoup(html, "lxml")

    # Extract page metadata (title, description, Open Graph, canonical)
    def _safe_meta(s: BeautifulSoup, attr: str, value: str) -> str:
        try:
            tag = s.find("meta", attrs={attr: value})
            if isinstance(tag, Tag) and tag.has_attr("content"):
                return str(tag.get("content", ""))
            return ""
        except Exception:
            return ""

    page_meta = {
        "title": (soup.title.get_text(strip=True) if soup.title else ""),
        "description": _safe_meta(soup, "name", "description"),
        "og": {
            "title": _safe_meta(soup, "property", "og:title"),
            "description": _safe_meta(soup, "property", "og:description"),
            "image": _safe_meta(soup, "property", "og:image"),
            "url": _safe_meta(soup, "property", "og:url"),
        },
        "canonical": "",
    }
    try:
        link_tag = soup.find("link", attrs={"rel": "canonical"})
        if isinstance(link_tag, Tag):
            href = link_tag.get("href")
            if href:
                page_meta["canonical"] = str(href)
    except Exception:
        pass

    # Remove non-content and utility sections
    for tag_name in ("nav", "footer", "header", "aside"):
        for node in soup.find_all(tag_name):
            node.decompose()
    # role-based removal
    for node in soup.find_all(attrs={"role": re.compile(r"^(navigation|banner|contentinfo)$", re.I)}):
        node.decompose()
    # cookie/consent/GDPR
    for node in soup.find_all(True, {"class": re.compile(r"(cookie|consent|gdpr)", re.I)}):
        node.decompose()
    for node in soup.find_all(True, id=re.compile(r"(cookie|consent|gdpr)", re.I)):
        node.decompose()
    # common nav/header/menu/footer/social classes
    for node in soup.find_all(True, {"class": re.compile(r"(nav|navbar|menu|header|footer|social)", re.I)}):
        node.decompose()
    # strip HTML comments
    for c in soup.find_all(string=lambda t: isinstance(t, Comment)):
        c.extract()

    # 1) For anchors containing image-like content: remove it and ensure link text
    for a in soup.find_all("a"):
        if not isinstance(a, Tag):
            continue

        # Collect image-like nodes: <img>, <picture>, <svg>
        imgs = [n for n in a.find_all("img") if isinstance(n, Tag)]
        pictures = [n for n in a.find_all("picture") if isinstance(n, Tag)]
        svgs = [n for n in a.find_all("svg") if isinstance(n, Tag)]
        image_like_nodes = imgs + pictures + svgs

        # Existing anchor text collapsed to a single line (prevents markdown syntax like ### inside links)
        text = a.get_text(strip=True, separator=" ")
        collapsed = " ".join(text.split())

        # Compute domain-based label when anchor has no visible text
        raw_href = a.get("href")
        href = str(raw_href) if raw_href is not None else ""
        # Safely get base_url from metrics (when return_metrics=True)
        base_url = ""
        try:
            base_url = str(getattr(metrics, "final_url", "")) or ""
        except Exception:
            base_url = ""
        try:
            abs_url = urljoin(str(base_url), str(href)) if base_url else str(href)
            parsed = urlparse(str(abs_url))
            domain = (parsed.netloc or "").lower()
            if domain.startswith("www."):
                domain = domain[4:]
            parsed_base = urlparse(str(base_url)) if base_url else None
            base_domain = ((parsed_base.netloc if parsed_base else "") or "").lower()
            if base_domain.startswith("www."):
                base_domain = base_domain[4:]
        except Exception:
            domain = ""
            base_domain = ""

        # Remove image-like nodes from the anchor (no-op if none)
        for node in image_like_nodes:
            node.decompose()

        # Replace anchor contents with simple text to avoid broken markdown
        a.clear()
        text_content = collapsed if collapsed else (domain or base_domain or "link")
        a.append(soup.new_string(str(text_content)))

    # 2) Remove any remaining standalone image-like elements
    for tag_name in ("img", "picture", "svg"):
        for node in soup.find_all(tag_name):
            if isinstance(node, Tag):
                node.decompose()

    # Convert to markdown
    md = convert_to_markdown(source=str(soup), parser="lxml")

    # Normalize spacing to prevent adjacent links/text from sticking together
    md = re.sub(r"\)\[", ")\n[", md)
    md = re.sub(r"\)(?=\S)", ") ", md)
    md = re.sub(r"(?<=\S)\[", " [", md)

    # Extract and classify links from the processed soup (hrefs preserved)
    start_final_url = str(getattr(metrics, "final_url", ""))
    internal_links, external_links, extraction_metrics = _classify_links(
        soup, base_url=start_final_url
    )

    # Prepare render metrics payload
    render_metrics = {
        "final_url": start_final_url,
        "response_status": int(getattr(metrics, "response_status", 0)),
        "network_requests": int(getattr(metrics, "network_requests", 0)),
        "content_length": int(getattr(metrics, "content_length", 0)),
        "load_time_ms": round(float(getattr(metrics, "load_time", 0.0)) * 1000.0, 2),
        "cache_hit": bool(getattr(metrics, "cache_hit", False)),
        "errors": list(getattr(metrics, "errors", [])),
    }

    # Email extraction (start page)
    all_emails: Set[str] = set()
    email_sources: List[Dict[str, str]] = []
    emails_by_url: Dict[str, List[str]] = {}
    if include_emails:
        emails0, src0 = _extract_emails(html, deobfuscate_emails)
        all_emails |= emails0
        if emails0:
            emails_by_url[start_final_url] = sorted(emails0)
        for s in src0:
            email_sources.append(
                {"email": s["email"], "found_as": s["found_as"], "url": start_final_url}
            )

    # Crawl internal links (BFS)
    visited_norm: Set[str] = set()
    visited_list: List[str] = []
    stop_reason = "queue_empty"

    # Mark start page as visited (normalized)
    norm_start = (
        _normalize_abs_url(start_final_url, start_final_url) if start_final_url else ""
    )
    if norm_start:
        visited_norm.add(norm_start)
        visited_list.append(start_final_url)

    if crawl_internal and crawl_max_pages > 1:
        q: deque[str] = deque()

        # Seed queue with internal links from start page
        for href in internal_links:
            absu = _normalize_abs_url(href, start_final_url)
            if not absu:
                continue
            if same_domain_only and not _same_domain(absu, start_final_url):
                continue
            # Skip ignored paths (e.g., /api, static assets)
            if _should_ignore_path(urlsplit(absu).path or ""):
                continue
            if absu not in visited_norm:
                visited_norm.add(absu)
                q.append(absu)

        while q and len(visited_list) < crawl_max_pages:
            u = q.popleft()
            try:
                html2, m2 = await get_rendered_html(
                    url=u,
                    progressive_scroll=False,
                    return_metrics=True,
                    timeout=max(1.0, float(per_page_timeout)),
                    wait_until="domcontentloaded",
                    cache_dir=cache_dir_env,
                )
            except Exception:
                # Skip errors and continue
                if throttle_ms > 0:
                    await asyncio.sleep(throttle_ms / 1000.0)
                continue

            final_u = str(getattr(m2, "final_url", u)) or u

            # If redirected off-domain and same_domain_only is set, skip recording/expanding
            if same_domain_only and not _same_domain(final_u, start_final_url):
                if throttle_ms > 0:
                    await asyncio.sleep(throttle_ms / 1000.0)
                continue

            visited_list.append(final_u)

            # Emails on this page
            if include_emails:
                emails_i, src_i = _extract_emails(html2, deobfuscate_emails)
                all_emails |= emails_i
                if emails_i:
                    emails_by_url[final_u] = sorted(emails_i)
                for s in src_i:
                    email_sources.append(
                        {"email": s["email"], "found_as": s["found_as"], "url": final_u}
                    )

            # Expand links if depth allows
            if True:
                soup2 = BeautifulSoup(html2, "lxml")
                new_internal, _, _ = _classify_links(soup2, base_url=final_u)
                for href2 in new_internal:
                    abs2 = _normalize_abs_url(href2, final_u)
                    if not abs2:
                        continue
                    if same_domain_only and not _same_domain(abs2, start_final_url):
                        continue
                    # Skip ignored paths (e.g., /api, static assets)
                    if _should_ignore_path(urlsplit(abs2).path or ""):
                        continue
                    if (
                        abs2 not in visited_norm
                        and len(visited_list) + len(q) < crawl_max_pages
                    ):
                        visited_norm.add(abs2)
                        q.append(abs2)

            if throttle_ms > 0:
                await asyncio.sleep(throttle_ms / 1000.0)

        if q and len(visited_list) >= crawl_max_pages:
            stop_reason = "max_pages"
        elif not q:
            stop_reason = "queue_empty"

    # Compose response payload
    # Deduplicate sources per (email, url) and aggregate found_as
    dedup_map: Dict[Tuple[str, str], Set[str]] = {}
    for s in email_sources:
        key = (s.get("email", "").lower(), s.get("url", ""))
        mode = s.get("found_as", "text")
        if key not in dedup_map:
            dedup_map[key] = set()
        dedup_map[key].add(mode)
    deduped_sources = [
        {"email": k[0], "url": k[1], "found_as": sorted(list(v))}
        for k, v in dedup_map.items()
    ]

    payload: Dict[str, Any] = {
        "page": page_meta,
        "markdown": md,
        "links": {
            "internal": internal_links,
            "external": external_links,
        },
        "metrics": {
            "render": render_metrics,
            "extraction": extraction_metrics,
        },
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
        "enabled": bool(crawl_internal),
        "start_url": start_final_url,
        "visited": visited_list,
        "limits": {"max_pages": int(crawl_max_pages)},
        "reason_stopped": stop_reason,
    }

    return JSONResponse(content=payload)
