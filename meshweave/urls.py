"""URL normalization, domain utilities, and filtering."""

import re
from urllib.parse import urljoin, urlparse, urlsplit, urlunsplit

__all__ = [
    "domain_of",
    "is_ignored_domain",
    "looks_like_domain",
    "normalize_abs_url",
    "normalize_domain",
    "same_domain",
    "should_follow",
    "should_ignore_path",
]

_IGNORE_PATH_RES = [
    re.compile(p, re.I)
    for p in [
        r"^/(api|auth|account|login|signup)(/|$)",
        r"^/(static|assets|cdn)/",
        r"\.(mp3|mp4|pdf|zip|png|jpe?g|svg|webp|ico)(\?|$)",
    ]
]


def normalize_domain(d: str) -> str:
    """Lowercase and strip leading www."""
    d = (d or "").strip().lower()
    return d[4:] if d.startswith("www.") else d


def domain_of(url: str) -> str:
    """Extract normalized domain from a URL."""
    try:
        return normalize_domain(urlparse(url or "").netloc)
    except Exception:
        return ""


def looks_like_domain(value: str) -> bool:
    """Heuristic to detect a bare domain (no scheme/path).

    Parameters:
        value (str): Input string to check.

    Returns:
        bool: True if the value looks like a bare FQDN.
    """
    v = (value or "").strip().lower()
    if not v:
        return False
    if "://" in v:
        return False
    if v.startswith("www."):
        v = v[4:]
    return bool(re.fullmatch(r"[a-z0-9.-]+\.[a-z]{2,24}", v))


def normalize_abs_url(href: str, base_url: str) -> str:
    """Resolve href against base, normalize for dedup."""
    try:
        absolute = urljoin(base_url or "", href or "")
        parts = urlsplit(absolute)
        scheme = parts.scheme.lower()
        netloc = (parts.netloc or "").lower()
        path = parts.path or "/"
        if len(path) > 1 and path.endswith("/"):
            path = path[:-1]
        return urlunsplit((scheme, netloc, path, "", ""))
    except Exception:
        return href or ""


def same_domain(u1: str, u2: str) -> bool:
    """True if two URLs share the same normalized domain."""
    return domain_of(u1) == domain_of(u2)


def is_ignored_domain(value: str, ignored: set[str] | None = None) -> bool:
    """Suffix-based domain ignore check."""
    if not ignored:
        return False
    dom = value or ""
    if "://" in dom:
        try:
            dom = urlsplit(dom).netloc or dom
        except Exception:
            pass
    dom = normalize_domain(dom)
    if not dom:
        return False
    for ign in ignored:
        if dom == ign or dom.endswith("." + ign):
            return True
    return False


def should_ignore_path(path: str) -> bool:
    """True if path matches built-in ignore patterns."""
    for rx in _IGNORE_PATH_RES:
        if rx.search(path or ""):
            return True
    return False


def should_follow(
    url: str,
    origin: str,
    same_domain_only: bool,
    ignored_domains: set[str] | None = None,
) -> bool:
    """Check if a URL should be followed during BFS."""
    if not url:
        return False
    if same_domain_only and not same_domain(url, origin):
        return False
    path = urlsplit(url).path or ""
    if should_ignore_path(path):
        return False
    if is_ignored_domain(url, ignored_domains):
        return False
    return True
