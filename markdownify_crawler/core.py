"""Core library for markdownify-crawler.

Provides:
- Rendering via Playwright (through fetcher.get_rendered_html)
- HTML preprocessing and markdown conversion
- Link classification (internal/external) with path filtering and domain-ignore support
- Email extraction (mailto + deobfuscated text)
- Optional BFS crawl of internal links up to a page limit
"""

import asyncio
import os
import re
import time
from collections import deque
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import unquote, urljoin, urlparse, urlsplit, urlunsplit

from bs4 import BeautifulSoup
from bs4.element import Comment, Tag
from html_to_markdown import convert_to_markdown

from .fetcher import get_rendered_html

# -------------------------
# URL and link utilities
# -------------------------


def _strip_www(domain: str) -> str:
    """Return the given domain lowercased with a leading 'www.' stripped if present.

    Parameters:
        domain (str): Domain name, possibly with 'www.' prefix.

    Returns:
        str: Normalized domain without 'www.' and in lowercase.
    """
    d = (domain or "").lower()
    return d[4:] if d.startswith("www.") else d


def _domain_of(url: str) -> str:
    """Extract the normalized domain (netloc) from a URL with 'www.' removed.

    Parameters:
        url (str): A URL string.

    Returns:
        str: Lowercased netloc without 'www.' or empty string on failure.
    """
    try:
        parsed = urlparse(url or "")
        return _strip_www(parsed.netloc or "")
    except Exception:
        return ""


def _remove_query_and_fragment(href: str) -> str:
    """Remove query and fragment from an href while keeping scheme, netloc and path.

    Parameters:
        href (str): Absolute or relative href.

    Returns:
        str: The href without query (?...) and fragment (#...).
    """
    try:
        parts = urlsplit(href or "")
        return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
    except Exception:
        return href


def _normalize_abs_url(href: str, base_url: str) -> str:
    """Resolve href against base_url and normalize the result for deduplication.

    - Resolve relative links to absolute using base_url.
    - Lowercase the scheme and host.
    - Remove query string and fragment.
    - Remove trailing slash from path unless it is root ("/").

    Parameters:
        href (str): The link to resolve (relative or absolute).
        base_url (str): Base URL against which to resolve relative links.

    Returns:
        str: A normalized absolute URL string.
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
    """Return True if two URLs share the same normalized base domain.

    Parameters:
        u1 (str): First URL.
        u2 (str): Second URL.

    Returns:
        bool: True if both normalize to the same domain (with 'www.' stripped).
    """
    return _domain_of(u1) == _domain_of(u2)


def _is_skippable(href: Any) -> bool:
    """Return True for hrefs that should be skipped during extraction.

    Skips:
      - None, empty strings, fragment-only anchors ('#...')
      - Non-navigational schemes: mailto:, javascript:, tel:, data:

    Parameters:
        href (Any): Raw href attribute value.

    Returns:
        bool: True if href should be ignored.
    """
    if href is None:
        return True
    h = str(href).strip()
    if not h or h.startswith("#"):
        return True
    lower = h.lower()
    return lower.startswith(("mailto:", "javascript:", "tel:", "data:"))


# -------------------------
# Ignore patterns for internal links
# -------------------------

_DEFAULT_IGNORE_PATTERNS = [
    r"^/(api|auth|account|login|signup)(/|$)",
    r"^/(static|assets|cdn)/",
    r"\.(mp3|mp4|pdf|zip|png|jpe?g|svg|webp|ico)(\?|$)",
]


def _compile_ignore_regexes() -> List[re.Pattern]:
    """Compile path ignore regexes from defaults and MARKDOWNIFY_IGNORE_PATHS.

    Environment:
        MARKDOWNIFY_IGNORE_PATHS (str): Comma-separated regexes to ignore.

    Returns:
        list[re.Pattern]: Compiled regex list for path filtering.
    """
    env_patterns = [
        p.strip()
        for p in (os.getenv("MARKDOWNIFY_IGNORE_PATHS") or "").split(",")
        if p.strip()
    ]
    patterns = _DEFAULT_IGNORE_PATTERNS + env_patterns
    return [re.compile(p, re.I) for p in patterns]


_IGNORE_REGEXES = _compile_ignore_regexes()

# -------------------------
# Domain ignore (do-not-follow) list
# -------------------------


def _normalize_domain(d: str) -> str:
    """Normalize a domain string.

    Parameters:
        d (str): Domain name or URL netloc.

    Returns:
        str: Domain lowercased without 'www.' prefix.
    """
    d = (d or "").strip().lower()
    if d.startswith("www."):
        d = d[4:]
    return d


def _compile_ignore_domains() -> Set[str]:
    """Compile a set of ignored domains from MARKDOWNIFY_IGNORE_DOMAINS.

    Environment:
        MARKDOWNIFY_IGNORE_DOMAINS (str): Comma-separated domain list.

    Returns:
        set[str]: A set of normalized ignored domains.
    """
    raw = os.getenv("MARKDOWNIFY_IGNORE_DOMAINS", "") or ""
    domains = set()
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        domains.add(_normalize_domain(part))
    return domains


_IGNORED_DOMAINS: Set[str] = _compile_ignore_domains()


def _is_ignored_domain(value: str) -> bool:
    """Return True if a URL or domain matches any ignored domain.

    - Matching is suffix-based for subdomains:
      - 'github.com' matches 'github.com' and 'docs.github.com'
      - 'play.google.com' matches only that subdomain (not other google.com subdomains)

    Parameters:
        value (str): A URL or domain string.

    Returns:
        bool: True if domain matches the ignore list.
    """
    dom = value or ""
    # If 'value' looks like a URL, extract netloc
    if "://" in dom or dom.startswith("http"):
        try:
            dom = urlsplit(dom).netloc or dom
        except Exception:
            pass
    dom = _normalize_domain(dom)
    if not dom or not _IGNORED_DOMAINS:
        return False
    for ign in _IGNORED_DOMAINS:
        if dom == ign or dom.endswith("." + ign):
            return True
    return False


def _should_ignore_path(path: str) -> bool:
    """Return True if a URL path should be ignored by compiled regexes.

    Parameters:
        path (str): URL path (e.g., '/api/auth').

    Returns:
        bool: True if path matches ignore regexes.
    """
    p = path or ""
    for rx in _IGNORE_REGEXES:
        if rx.search(p):
            return True
    return False


# -------------------------
# Link classification
# -------------------------


def _classify_links(soup: BeautifulSoup, base_url: str):
    """Extract, normalize, and classify links from a BeautifulSoup document.

    - Removes query and fragment.
    - Dedupes by normalized link string.
    - Classifies internal vs external relative to base_url's domain.
    - Skips root paths ('/' and absolute same-domain roots).
    - Applies ignore patterns (api/auth/static/media).
    - Optionally filters out domains listed in MARKDOWNIFY_IGNORE_DOMAINS from output.

    Parameters:
        soup (BeautifulSoup): Parsed DOM.
        base_url (str): Base URL used to determine internal domain.

    Returns:
        tuple[list[str], list[str], dict[str, Any]]: (internal_links, external_links, extraction_metrics)
    """
    start = time.perf_counter()
    base_domain = _domain_of(base_url)
    filter_ignored = (
        os.getenv("MARKDOWNIFY_FILTER_IGNORED_DOMAINS_IN_LINKS", "true").lower()
        != "false"
    )
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
                # Filter out ignored domains from links if configured
                if not (filter_ignored and _is_ignored_domain(link_domain)):
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


# -------------------------
# Email extraction
# -------------------------

_EMAIL_REGEX = re.compile(r"([a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[A-Za-z]{2,})")


def _deobfuscate_text(text: str) -> str:
    """Replace common textual obfuscations of email addresses.

    Examples replaced (case-insensitive, with token boundaries):
      - '[at]', '(at)', '{at}', or standalone ' at ' -> '@'
      - '[dot]', '(dot)', '{dot}', or standalone ' dot ' -> '.'

    Parameters:
        text (str): Visible text content from the page.

    Returns:
        str: Deobfuscated text.
    """
    s = text
    s = re.sub(r"(?i)\b\[\s*at\s*\]|\(\s*at\s*\)|\{\s*at\s*\}", "@", s)
    s = re.sub(r"(?i)\b\[\s*dot\s*\]|\(\s*dot\s*\)|\{\s*dot\s*\}", ".", s)
    s = re.sub(r"(?i)(?<=[\s,;:/\\\|<>\-\_])at(?=[\s,;:/\\\|<>\-\_])", "@", s)
    s = re.sub(r"(?i)(?<=[\s,;:/\\\|<>\-\_])dot(?=[\s,;:/\\\|<>\-\_])", ".", s)
    s = re.sub(r"(?i)\s+at\s+", " @ ", s)
    s = re.sub(r"(?i)\s+dot\s+", " . ", s)
    return s


def _extract_emails_from_mailto(soup: BeautifulSoup) -> Set[str]:
    """Extract emails from mailto: links within the provided BeautifulSoup document.

    Parameters:
        soup (BeautifulSoup): Parsed DOM.

    Returns:
        set[str]: Lowercased email addresses from mailto: links.
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
        addr = addr.split("?", 1)[0]
        addr = unquote(addr)
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
    """Extract emails from visible text content of an HTML string.

    Parameters:
        html (str): Full page HTML.
        deobfuscate (bool): Whether to run deobfuscation passes first.

    Returns:
        tuple[set[str], bool]: (emails, had_obfuscated_text)
    """
    soup = BeautifulSoup(html, "lxml")
    text = soup.get_text(" ")
    found_obfuscated = False

    emails: Set[str] = {m.group(1).lower() for m in _EMAIL_REGEX.finditer(text)}

    if deobfuscate:
        deob = _deobfuscate_text(text)
        if deob != text:
            found_obfuscated = True
        emails_obf: Set[str] = {m.group(1).lower() for m in _EMAIL_REGEX.finditer(deob)}
        emails |= emails_obf

    return emails, found_obfuscated


def extract_emails(
    html: str, deobfuscate: bool = True
) -> Tuple[Set[str], List[Dict[str, Any]]]:
    """Extract emails from an HTML string by combining mailto and text matches.

    Parameters:
        html (str): Full page HTML.
        deobfuscate (bool): If True, attempt to deobfuscate 'at'/'dot' etc.

    Returns:
        tuple[set[str], list[dict[str, Any]]]:
            - Unique lowercase emails
            - Sources list with items {"email": str, "found_as": "mailto"|"text"|"obfuscated"}
              (Note: caller attaches per-page URL later.)
    """
    sources: List[Dict[str, Any]] = []

    soup = BeautifulSoup(html, "lxml")
    mailto_emails = _extract_emails_from_mailto(soup)
    for e in mailto_emails:
        sources.append({"email": e, "found_as": "mailto"})

    text_emails, had_obf = _extract_emails_from_text(html, deobfuscate)
    for e in text_emails:
        sources.append({"email": e, "found_as": "obfuscated" if had_obf else "text"})

    all_unique = mailto_emails | text_emails
    return all_unique, sources


# -------------------------
# Page metadata and preprocessing
# -------------------------


def extract_page_meta(soup: BeautifulSoup) -> Dict[str, Any]:
    """Extract useful page metadata: title, meta description, Open Graph tags, and canonical link.

    Parameters:
        soup (BeautifulSoup): Parsed DOM.

    Returns:
        dict[str, Any]: Dict with keys {"title", "description", "og": {...}, "canonical"}.
    """

    def _safe_meta(s: BeautifulSoup, attr: str, value: str) -> str:
        """Return a meta tag's content if present, else empty string."""
        try:
            tag = s.find("meta", attrs={attr: value})
            if isinstance(tag, Tag) and tag.has_attr("content"):
                return str(tag.get("content", ""))
            return ""
        except Exception:
            return ""

    meta = {
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
                meta["canonical"] = str(href)
    except Exception:
        pass
    return meta


def preprocess_soup(soup: BeautifulSoup, base_url: str, final_url: str) -> BeautifulSoup:
    """Remove non-content sections, strip images, and normalize anchors to text for robust markdown.

    Parameters:
        soup (BeautifulSoup): Parsed DOM to be modified in-place.
        base_url (str): Original input URL.
        final_url (str): Final URL after redirects (for absolute URL resolution).

    Returns:
        BeautifulSoup: The modified soup.
    """
    # Remove non-content and utility sections
    for tag_name in ("nav", "footer", "header", "aside"):
        for node in soup.find_all(tag_name):
            node.decompose()

    # role-based removal
    for node in soup.find_all(
        attrs={"role": re.compile(r"^(navigation|banner|contentinfo)$", re.I)}
    ):
        node.decompose()

    # cookie/consent/GDPR
    for node in soup.find_all(
        True, {"class": re.compile(r"(cookie|consent|gdpr)", re.I)}
    ):
        node.decompose()
    for node in soup.find_all(True, id=re.compile(r"(cookie|consent|gdpr)", re.I)):
        node.decompose()

    # common nav/header/menu/footer/social classes
    for node in soup.find_all(
        True, {"class": re.compile(r"(nav|navbar|menu|header|footer|social)", re.I)}
    ):
        node.decompose()

    # strip HTML comments
    for c in soup.find_all(string=lambda t: isinstance(t, Comment)):
        c.extract()

    # For anchors containing image-like content: remove it and ensure link text
    for a in soup.find_all("a"):
        if not isinstance(a, Tag):
            continue

        imgs = [n for n in a.find_all("img") if isinstance(n, Tag)]
        pictures = [n for n in a.find_all("picture") if isinstance(n, Tag)]
        svgs = [n for n in a.find_all("svg") if isinstance(n, Tag)]
        image_like_nodes = imgs + pictures + svgs

        text = a.get_text(strip=True, separator=" ")
        collapsed = " ".join(text.split())

        raw_href = a.get("href")
        href = str(raw_href) if raw_href is not None else ""

        # Compute domain-based fallback label
        try:
            abs_url = (
                urljoin(str(final_url or base_url), str(href))
                if (final_url or base_url)
                else str(href)
            )
            parsed = urlparse(str(abs_url))
            domain = (parsed.netloc or "").lower()
            if domain.startswith("www."):
                domain = domain[4:]
            parsed_base = (
                urlparse(str(final_url or base_url)) if (final_url or base_url) else None
            )
            base_domain = ((parsed_base.netloc if parsed_base else "") or "").lower()
            if base_domain.startswith("www."):
                base_domain = base_domain[4:]
        except Exception:
            domain = ""
            base_domain = ""

        for node in image_like_nodes:
            node.decompose()

        a.clear()
        text_content = collapsed if collapsed else (domain or base_domain or "link")
        a.append(soup.new_string(str(text_content)))

    # Remove any remaining standalone image-like elements
    for tag_name in ("img", "picture", "svg"):
        for node in soup.find_all(tag_name):
            if isinstance(node, Tag):
                node.decompose()

    return soup


def to_markdown(soup: BeautifulSoup) -> str:
    """Convert a preprocessed BeautifulSoup document to markdown and normalize spacing.

    Parameters:
        soup (BeautifulSoup): Preprocessed DOM.

    Returns:
        str: Markdown text.
    """
    md = convert_to_markdown(source=str(soup), parser="lxml")

    # Strip injected HTML comment metadata block at the very top if present
    # Pattern: <!-- ... --> followed by newline at start
    if md.lstrip().startswith("<!--"):
        # remove first comment block
        end_idx = md.find("-->")
        if end_idx != -1:
            md = md[end_idx + 3 :].lstrip("\n\r ")

    # Normalize spacing to prevent adjacent links/text from sticking together
    md = re.sub(r"\)\[", ")\n[", md)
    md = re.sub(r"\)(?=\S)", ") ", md)
    md = re.sub(r"(?<=\S)\[", " [", md)
    return md


# -------------------------
# Rendering utilities
# -------------------------


async def render_page(
    url: str,
    *,
    cache_dir: Optional[str] = None,
    timeout: float = 30.0,
    progressive: bool = True,
):
    """Render a URL with Playwright and return (html, metrics).

    Parameters:
        url (str): Page URL to render (http/https).
        cache_dir (str | None): Cache directory override; uses env/default if None.
        timeout (float): Navigation timeout in seconds.
        progressive (bool): If True, progressively scroll to trigger lazy-loading.

    Returns:
        tuple[str, fetcher.RenderMetrics]: Rendered HTML and metrics object.
    """
    html, metrics = await get_rendered_html(
        url=url,
        progressive_scroll=bool(progressive),
        return_metrics=True,
        timeout=timeout,
        wait_until="domcontentloaded",
        cache_dir=cache_dir,
    )
    return html, metrics


def soup_from_html(html: str) -> BeautifulSoup:
    """Create a BeautifulSoup object from an HTML string using the lxml parser.

    Parameters:
        html (str): HTML source.

    Returns:
        BeautifulSoup: Parsed DOM.
    """
    return BeautifulSoup(html, "lxml")


# -------------------------
# Top-level crawl function
# -------------------------


async def crawl(
    url: str,
    *,
    crawl_internal: bool = False,
    crawl_max_pages: int = 25,
    same_domain_only: bool = True,
    include_emails: bool = True,
    deobfuscate_emails: bool = True,
    throttle_ms: int = 0,
    per_page_timeout: float = 15.0,
    cache_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """Render a page, convert to markdown, classify links, optionally crawl internal pages, and extract emails.

    Parameters:
        url (str): Starting URL.
        crawl_internal (bool): If True, BFS crawl internal links until crawl_max_pages is reached.
        crawl_max_pages (int): Hard cap on total pages visited, including the start page.
        same_domain_only (bool): If True, enforce visited pages remain on the starting domain after redirects.
        include_emails (bool): If True, extract emails on each visited page.
        deobfuscate_emails (bool): If True, attempt deobfuscation in textual matches.
        throttle_ms (int): Delay in milliseconds between page fetches.
        per_page_timeout (float): Timeout for each crawled page fetch, in seconds.
        cache_dir (str | None): Cache directory override; uses env/default if None.

    Returns:
        dict[str, Any]: Payload with keys:
            page (dict): Metadata (title, description, og, canonical)
            markdown (str): Page content in markdown
            links (dict): { "internal": list[str], "external": list[str] }
            metrics (dict): { "render": dict, "extraction": dict }
            emails (dict, optional): { "unique": list[str], "by_url": dict[str, list[str]], "sources": list[dict], "counts": dict }
            crawl (dict): { "enabled": bool, "start_url": str, "visited": list[str], "limits": { "max_pages": int }, "reason_stopped": str }
    """
    cache_dir_env = (
        cache_dir or os.getenv("MARKDOWNIFY_CACHE_DIR") or "/tmp/markdownify/cache"
    )

    # Render start page
    html, metrics = await render_page(
        url=url,
        cache_dir=cache_dir_env,
        timeout=30.0,
        progressive=True,
    )

    final_url = str(getattr(metrics, "final_url", ""))

    # Prepare soup -> metadata -> preprocessing
    soup = soup_from_html(html)
    page_meta = extract_page_meta(soup)
    soup = preprocess_soup(soup, base_url=url, final_url=final_url)

    # Markdown
    md = to_markdown(soup)

    # Extract and classify links
    internal_links, external_links, extraction_metrics = _classify_links(
        soup, base_url=final_url
    )

    # Render metrics payload
    render_metrics = {
        "final_url": final_url,
        "response_status": int(getattr(metrics, "response_status", 0)),
        "network_requests": int(getattr(metrics, "network_requests", 0)),
        "content_length": int(getattr(metrics, "content_length", 0)),
        "load_time_ms": round(float(getattr(metrics, "load_time", 0.0)) * 1000.0, 2),
        "cache_hit": bool(getattr(metrics, "cache_hit", False)),
        "errors": list(getattr(metrics, "errors", [])),
    }

    # Emails on start page
    all_emails: Set[str] = set()
    email_sources: List[Dict[str, Any]] = []
    emails_by_url: Dict[str, List[str]] = {}
    if include_emails:
        emails0, src0 = extract_emails(html, deobfuscate_emails)
        all_emails |= emails0
        if emails0:
            emails_by_url[final_url or url] = sorted(emails0)
        for s in src0:
            email_sources.append(
                {"email": s["email"], "found_as": s["found_as"], "url": final_url or url}
            )

    # Crawl (BFS) until crawl_max_pages, no depth limit
    visited_norm: Set[str] = set()
    visited_list: List[str] = []
    stop_reason = "queue_empty"

    norm_start = (
        _normalize_abs_url(final_url or url, final_url or url)
        if (final_url or url)
        else ""
    )
    if norm_start:
        visited_norm.add(norm_start)
        visited_list.append(final_url or url)

    if crawl_internal and crawl_max_pages > 1:
        q: deque[str] = deque()

        # Seed internal links
        for href in internal_links:
            absu = _normalize_abs_url(href, final_url or url)
            if not absu:
                continue
            if same_domain_only and not _same_domain(absu, final_url or url):
                continue
            if _should_ignore_path(urlsplit(absu).path or ""):
                continue
            if _is_ignored_domain(absu):
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
                if throttle_ms > 0:
                    await asyncio.sleep(throttle_ms / 1000.0)
                continue

            final_u = str(getattr(m2, "final_url", u)) or u

            # Skip off-domain redirects entirely if same_domain_only
            if same_domain_only and not _same_domain(final_u, final_url or url):
                if throttle_ms > 0:
                    await asyncio.sleep(throttle_ms / 1000.0)
                continue

            # Skip ignored domains (do-not-follow) regardless of same_domain_only
            if _is_ignored_domain(final_u):
                if throttle_ms > 0:
                    await asyncio.sleep(throttle_ms / 1000.0)
                continue

            visited_list.append(final_u)

            # Emails on this page
            if include_emails:
                emails_i, src_i = extract_emails(html2, deobfuscate_emails)
                all_emails |= emails_i
                if emails_i:
                    emails_by_url[final_u] = sorted(emails_i)
                for s in src_i:
                    email_sources.append(
                        {"email": s["email"], "found_as": s["found_as"], "url": final_u}
                    )

            # Expand further links
            soup2 = soup_from_html(html2)
            soup2 = preprocess_soup(soup2, base_url=final_u, final_url=final_u)
            new_internal, _, _ = _classify_links(soup2, base_url=final_u)
            for href2 in new_internal:
                abs2 = _normalize_abs_url(href2, final_u)
                if not abs2:
                    continue
                if same_domain_only and not _same_domain(abs2, final_url or url):
                    continue
                if _should_ignore_path(urlsplit(abs2).path or ""):
                    continue
                if _is_ignored_domain(abs2):
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

    # Deduplicate sources per (email,url) and aggregate found_as
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
        "start_url": final_url or url,
        "visited": visited_list,
        "limits": {"max_pages": int(crawl_max_pages)},
        "reason_stopped": stop_reason,
    }

    return payload
