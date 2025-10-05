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
        raw = str(href).strip()
        normalized = _remove_query_and_fragment(raw)
        # Always resolve to absolute for robust classification and external list
        absu = _normalize_abs_url(normalized, base_url)
        parts = urlsplit(absu)
        link_domain = _strip_www(parts.netloc or "")

        # Same-domain -> internal (store as normalized path)
        # Internal link = same normalized domain as base_url
        if base_domain and (link_domain == base_domain):
            path = parts.path or "/"
            if not path.startswith("/"):
                path = "/" + path
            # Skip root paths to reduce noise per docstring
            if path == "/":
                continue
            if _should_ignore_path(path or ""):
                continue
            key = ("int", path)
            if key in seen:
                continue
            seen.add(key)
            internal.append(path)
        else:
            # External: optionally filter ignored domains and store absolute URL
            if filter_ignored and _is_ignored_domain(link_domain):
                continue
            key = ("ext", absu)
            if key in seen:
                continue
            seen.add(key)
            external.append(absu)

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

# Common top-level domains for validation
_COMMON_TLDS = {
    "com", "org", "net", "edu", "gov", "mil", "info", "biz", "co", "uk", "de", "fr", "it", "es", "ca", "au", "jp", "cn", "in", "br", "mx", "nl", "se", "no", "fi", "dk", "pl", "ru", "be", "at", "ch", "pt", "cz", "gr", "tr", "hu", "sk", "si", "hr", "ba", "me", "rs", "mk", "al", "bg", "ro", "md", "ua", "by", "kz", "uz", "tj", "tm", "kg", "az", "am", "ge", "ee", "lv", "lt", "mt", "cy", "lu", "is", "ie", "gi", "pt", "ad", "li", "mc", "sm", "va", "ai", "io", "sh", "ac", "to", "tv", "cc", "st", "ms", "gs", "tc", "vg", "je", "gg", "im", "fo", "gl", "sj", "ax", "pm", "re", "wf", "tf", "yt", "mq", "gp", "gf", "pf", "nc", "vu", "sb", "fm", "ki", "nr", "pw", "ws", "as", "ck", "nu", "tk", "nf", "hm", "bv", "cx", "aq"
}


def _is_valid_email(email: str) -> bool:
    """Validate if an email address appears legitimate and not a false positive.

    Performs stricter checks than the basic regex to filter out common false positives
    from deobfuscation, such as emails starting with numbers or containing invalid patterns.

    Parameters:
        email (str): Email address to validate.

    Returns:
        bool: True if the email passes validation checks.
    """
    if not email or '@' not in email:
        return False

    local, domain = email.split('@', 1)
    if not local or not domain:
        return False

    # Local part checks
    if (local.startswith('.') or local.endswith('.') or
        '..' in local or len(local) < 2):
        return False

    # Domain checks
    if ('.' not in domain or domain.startswith('.') or domain.endswith('.') or
        '..' in domain):
        return False

    # Stricter regex check
    strict_regex = re.compile(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$')
    if not strict_regex.fullmatch(email):
        return False

    # Additional heuristics for false positives
    # Exclude emails where local part starts with digit and is very short (likely false positive)
    if local[0].isdigit() and len(local) < 5:
        return False

    # Exclude emails with unusual domain extensions or patterns
    domain_parts = domain.split('.')
    if len(domain_parts) < 2 or any(len(part) < 2 for part in domain_parts[-2:]):
        return False

    # Check if TLD is common
    tld = domain_parts[-1].lower()
    if tld not in _COMMON_TLDS:
        return False

    return True


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
    # Replace bracketed/parenthesized tokens like [at], (at), {at} and [dot], etc.
    s = re.sub(r"(?i)\[\s*at\s*\]|\(\s*at\s*\)|\{\s*at\s*\}", "@", s)
    s = re.sub(r"(?i)\[\s*dot\s*\]|\(\s*dot\s*\)|\{\s*dot\s*\}", ".", s)
    # Replace plain 'at'/'dot' when surrounded by common separators or whitespace
    s = re.sub(r"(?i)(?<=[\s,;:/\\\|<>\-\_])at(?=[\s,;:/\\\|<>\-\_])", "@", s)
    s = re.sub(r"(?i)(?<=[\s,;:/\\\|<>\-\_])dot(?=[\s,;:/\\\|<>\-\_])", ".", s)
    # Normalize spaced tokens like " at " and " dot "
    s = re.sub(r"(?i)\s+at\s+", " @ ", s)
    s = re.sub(r"(?i)\s+dot\s+", " . ", s)
    # Collapse whitespace around email separators to form valid addresses
    s = re.sub(r"\s*@\s*", "@", s)
    s = re.sub(r"\s*\.\s*", ".", s)
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
    # Remove non-visible/scripted content before text extraction to reduce false positives
    for t in ("script", "style", "noscript"):
        for n in soup.find_all(t):
            try:
                n.decompose()
            except Exception:
                pass
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
    # Filter out invalid emails to reduce false positives
    valid_emails = {email for email in all_unique if _is_valid_email(email)}
    return valid_emails, sources


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

    # additional common noise: ads, popups, modals, banners
    for node in soup.find_all(
        True, {"class": re.compile(r"(ad|ads|popup|modal|banner|overlay|tooltip)", re.I)}
    ):
        node.decompose()
    for node in soup.find_all(True, id=re.compile(r"(ad|ads|popup|modal|banner|overlay)", re.I)):
        node.decompose()

    # role-based additional removals
    for node in soup.find_all(
        attrs={"role": re.compile(r"^(complementary|banner|contentinfo|dialog|alert)$", re.I)}
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
    disable_cache: bool = False,
    cache_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """Render a page (or a bare domain), convert to markdown, classify links, optionally crawl internal pages, extract emails,
    and when given a bare domain attempt sitemap discovery to seed the crawl.

    Parameters:
        url (str): Starting URL or bare domain (e.g., "example.com"). If a bare domain is provided:
            - The crawler will start at https://{domain}/ (falling back to http:// on initial fetch failure).
            - It will attempt to discover sitemap URLs via robots.txt and common endpoints (sitemap.xml, sitemap_index.xml).
            - When crawl_internal is True, discovered sitemap URLs are used to seed the BFS queue alongside internal links.
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
            crawl (dict): {
              "enabled": bool,
              "start_url": str,
              "visited": list[str],
              "limits": {"max_pages": int},
              "reason_stopped": str,
              "sitemap": {"used": bool, "sources": list, "urls_seeded": int, "discovered": int}
            }
    """
    cache_dir_env = (
        cache_dir or os.getenv("MARKDOWNIFY_CACHE_DIR") or "/tmp/markdownify/cache"
    )
    # Per-run cache control: allow env MARKDOWNIFY_DISABLE_CACHE to force bypass
    disable_cache_env = os.getenv("MARKDOWNIFY_DISABLE_CACHE", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    # When disable_cache=True or env disables cache, bypass HTML cache entirely
    local_cache_dir = None if (disable_cache or disable_cache_env) else cache_dir_env

    # Determine start URL and optional sitemap metadata if a bare domain is provided
    input_value = url
    sitemap_meta = {"used": False, "sources": [], "urls_seeded": 0, "discovered": 0}
    is_domain_input = _looks_like_domain(input_value)
    start_url = input_value
    if is_domain_input:
        dom = _normalize_domain(input_value)
        start_url = f"https://{dom}/"

    # Render start page with fallback to http for bare domains if https fails
    try:
        html, metrics = await render_page(
            url=start_url,
            cache_dir=local_cache_dir,
            timeout=30.0,
            progressive=True,
        )
    except Exception:
        if is_domain_input and start_url.startswith("https://"):
            try:
                html, metrics = await render_page(
                    url=start_url.replace("https://", "http://", 1),
                    cache_dir=local_cache_dir,
                    timeout=30.0,
                    progressive=True,
                )
            except Exception:
                # Re-raise if both https and http fail
                raise
        else:
            raise

    final_url = str(getattr(metrics, "final_url", ""))

    # Prepare soup -> metadata -> preprocessing
    # Use raw DOM for metadata and link discovery
    soup_raw = soup_from_html(html)
    page_meta = extract_page_meta(soup_raw)
    # Use preprocessed DOM only for markdown conversion
    soup_pre = preprocess_soup(soup_from_html(html), base_url=url, final_url=final_url)

    # Markdown
    md = to_markdown(soup_pre)

    # Extract and classify links from RAW DOM to keep nav/social anchors
    internal_links, external_links, extraction_metrics = _classify_links(
        soup_raw, base_url=final_url
    )

    # Optionally discover sitemap URLs for bare domain input to seed BFS
    sitemap_seeds: List[str] = []
    if is_domain_input:
        try:
            base_dom = _domain_of(final_url or start_url)
            discovered, sm_meta = await _discover_sitemap_urls(
                base_dom, max_urls=max(1, crawl_max_pages * 5)
            )
            sitemap_meta["used"] = bool(discovered)
            sitemap_meta["discovered"] = len(discovered)
            sitemap_meta["sources"] = sm_meta.get("sources", [])

            for u in discovered:
                # Filter to domain and ignore paths/domains according to existing rules
                if same_domain_only and not _same_domain(u, final_url or start_url):
                    continue
                if _is_ignored_domain(u):
                    continue
                if _should_ignore_path(urlsplit(u).path or ""):
                    continue
                normu = _normalize_abs_url(u, final_url or start_url)
                if normu:
                    sitemap_seeds.append(normu)
        except Exception:
            # ignore discovery failures; proceed without sitemap seeding
            pass

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

        # Seed internal links discovered from the start page
        for href in internal_links:
            absu = _normalize_abs_url(href, final_url or start_url)
            if not absu:
                continue
            if same_domain_only and not _same_domain(absu, final_url or start_url):
                continue
            if _should_ignore_path(urlsplit(absu).path or ""):
                continue
            if _is_ignored_domain(absu):
                continue
            if absu not in visited_norm and (len(visited_list) + len(q)) < crawl_max_pages:
                visited_norm.add(absu)
                q.append(absu)

        # Additionally seed sitemap URLs when available
        if sitemap_seeds:
            for su in sitemap_seeds:
                if not su:
                    continue
                if same_domain_only and not _same_domain(su, final_url or start_url):
                    continue
                if _should_ignore_path(urlsplit(su).path or ""):
                    continue
                if _is_ignored_domain(su):
                    continue
                if su not in visited_norm and (len(visited_list) + len(q)) < crawl_max_pages:
                    visited_norm.add(su)
                    q.append(su)
                    sitemap_meta["urls_seeded"] += 1

        while q and len(visited_list) < crawl_max_pages:
            u = q.popleft()
            try:
                html2, m2 = await get_rendered_html(
                    url=u,
                    progressive_scroll=False,
                    return_metrics=True,
                    timeout=max(1.0, float(per_page_timeout)),
                    wait_until="domcontentloaded",
                    cache_dir=local_cache_dir,
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
            # Use raw DOM for classification; preprocessed DOM only for markdown upstream (if needed)
            soup2_raw = soup_from_html(html2)
            new_internal, _, _ = _classify_links(soup2_raw, base_url=final_u)
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
        "start_url": final_url or start_url,
        "visited": visited_list,
        "limits": {"max_pages": int(crawl_max_pages)},
        "reason_stopped": stop_reason,
        "sitemap": sitemap_meta,
    }

    return payload


# -------------------------
# Sitemap/domain helpers
# -------------------------

def _looks_like_domain(value: str) -> bool:
    """Heuristic to detect a bare domain (no scheme/path)."""
    v = (value or "").strip().lower()
    if not v:
        return False
    if "://" in v:
        return False
    if v.startswith("www."):
        v = v[4:]
    # Simple FQDN check: labels with letters/digits/hyphen and a TLD of 2-24 letters
    return bool(re.fullmatch(r"[a-z0-9.-]+\.[a-z]{2,24}", v))


async def _fetch_bytes_async(url: str, timeout: float = 10.0) -> Optional[bytes]:
    """Fetch raw bytes via urllib in a thread to avoid extra deps.

    Returns:
        bytes on success; None on failure.
    """
    import urllib.request
    import urllib.error
    import gzip
    import io

    def _fetch() -> Optional[bytes]:
        try:
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": "markdownify-crawler/1.0",
                    "Accept": "*/*",
                    "Accept-Encoding": "gzip, deflate",
                },
                method="GET",
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = resp.read()
                enc = (resp.headers.get("Content-Encoding", "") or "").lower()
                if enc == "gzip" or url.lower().endswith(".gz"):
                    # Try to decompress
                    try:
                        return gzip.decompress(data)
                    except Exception:
                        try:
                            with gzip.GzipFile(fileobj=io.BytesIO(data)) as gz:
                                return gz.read()
                        except Exception:
                            return data
                return data
        except Exception:
            return None

    return await asyncio.to_thread(_fetch)


def _parse_sitemap_xml(xml_bytes: bytes, base_url: str) -> Tuple[List[str], List[str]]:
    """Parse a sitemap XML payload and return (urls, child_sitemaps).

    Args:
        xml_bytes: Raw XML bytes (already decompressed if needed).
        base_url: URL of the sitemap file for resolving relative loc entries.

    Returns:
        tuple[list[str], list[str]]: URLs and nested sitemap URLs (for sitemapindex).
    """
    import xml.etree.ElementTree as ET

    urls: List[str] = []
    sitemaps: List[str] = []

    try:
        root = ET.fromstring(xml_bytes)
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


async def _discover_sitemap_urls(domain: str, *, max_urls: int = 1000) -> Tuple[List[str], Dict[str, Any]]:
    """Discover sitemap URLs for a given domain via robots.txt and common endpoints.

    Args:
        domain: Domain host (no scheme).
        max_urls: Upper bound on number of page URLs to collect.

    Returns:
        tuple[list[str], dict]: (urls, meta) where meta has a 'sources' array describing attempts.
    """
    d = _normalize_domain(domain or "")
    sources: List[Dict[str, Any]] = []
    urls: List[str] = []
    seen_sitemaps: Set[str] = set()

    # Candidates: common endpoints
    candidates: List[str] = []
    if d:
        candidates.extend(
            [
                f"https://{d}/sitemap.xml",
                f"https://{d}/sitemap_index.xml",
                f"http://{d}/sitemap.xml",
                f"http://{d}/sitemap_index.xml",
            ]
        )

        # robots.txt discovery
        for scheme in ("https", "http"):
            robots_url = f"{scheme}://{d}/robots.txt"
            b = await _fetch_bytes_async(robots_url, timeout=8.0)
            found = 0
            status = "miss"
            if b:
                try:
                    text = b.decode("utf-8", errors="ignore")
                    for line in text.splitlines():
                        if "sitemap:" in line.lower():
                            try:
                                loc = line.split(":", 1)[1].strip()
                            except Exception:
                                loc = ""
                            if loc:
                                candidates.append(loc)
                                found += 1
                    status = "ok"
                except Exception:
                    status = "error"
            sources.append({"type": "robots", "url": robots_url, "found": found, "status": status})

    # De-duplicate candidates preserving order
    seen_c: Set[str] = set()
    dedup_candidates: List[str] = []
    for c in candidates:
        c = str(c).strip()
        if not c or c in seen_c:
            continue
        seen_c.add(c)
        dedup_candidates.append(c)

    fetch_queue: List[str] = list(dedup_candidates)
    # Limit nested sitemap traversal to stay efficient
    child_sitemap_limit = 20

    while fetch_queue and len(urls) < max_urls:
        sm_url = fetch_queue.pop(0)
        if sm_url in seen_sitemaps:
            continue
        seen_sitemaps.add(sm_url)

        b = await _fetch_bytes_async(sm_url, timeout=12.0)
        meta: Dict[str, Any] = {"type": "sitemap", "url": sm_url, "ok": bool(b)}
        if not b:
            sources.append(meta)
            continue

        page_urls, child_sitemaps = _parse_sitemap_xml(b, base_url=sm_url)

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
