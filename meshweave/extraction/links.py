"""HTML link extraction and classification."""

import time
from typing import Any
from urllib.parse import SplitResult, urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup
from bs4.element import Tag

from ..urls import domain_of, is_ignored_domain, normalize_domain, should_ignore_path

__all__ = [
    "classify_links",
]


def _is_skippable(href: Any) -> bool:
    """True for hrefs that aren't navigational links."""
    if href is None:
        return True
    h = str(href).strip()
    if not h or h.startswith("#"):
        return True
    return h.lower().startswith(("mailto:", "javascript:", "tel:", "data:"))


def classify_links(
    soup: BeautifulSoup,
    base_url: str,
    ignored_domains: set[str] | None = None,
) -> tuple[list[str], list[str], dict[str, Any]]:
    """Extract, normalize, and classify links as internal/external.

    A link is *internal* if it shares the same domain as *base_url*.
    Everything else is *external*.

    Returns (internal_links, external_links, extraction_metrics).
    """
    start = time.perf_counter()
    base_domain = domain_of(base_url)
    seen: set[tuple[str, str]] = set()
    internal: list[str] = []
    external: list[str] = []
    total = 0

    for a in soup.find_all("a"):
        if not isinstance(a, Tag):
            continue
        href = a.get("href")
        if _is_skippable(href):
            continue
        total += 1

        _classify_link(
            href,
            base_url,
            base_domain,
            ignored_domains,
            seen,
            internal,
            external,
        )

    elapsed = (time.perf_counter() - start) * 1000.0
    metrics = {
        "total_candidates": total,
        "unique_total": len(internal) + len(external),
        "internal_count": len(internal),
        "external_count": len(external),
        "base_domain": base_domain,
        "parse_time_ms": round(elapsed, 2),
    }
    return internal, external, metrics


def _classify_link(
    href: Any,
    base_url: str,
    base_domain: str,
    ignored_domains: set[str] | None,
    seen: set[tuple[str, str]],
    internal: list[str],
    external: list[str],
) -> None:
    """Normalize a link and bucket it as internal or external (deduped)."""
    raw = str(href).strip()
    # Remove query/fragment
    parts = urlsplit(urljoin(base_url, raw))
    absu = _normalized_abs_url(parts)
    link_domain = normalize_domain(parts.netloc or "")

    if base_domain and link_domain == base_domain:
        _append_internal(parts, seen, internal)
    else:
        _append_external(absu, link_domain, ignored_domains, seen, external)


def _normalized_abs_url(parts: SplitResult) -> str:
    """Absolute URL with lowercase scheme/host, default path, no query/fragment."""
    return urlunsplit(
        (
            parts.scheme.lower(),
            (parts.netloc or "").lower(),
            parts.path or "/",
            "",
            "",
        )
    )


def _append_internal(
    parts: SplitResult,
    seen: set[tuple[str, str]],
    internal: list[str],
) -> None:
    """Record the internal path when it is crawl-worthy and unseen."""
    path = _normalized_path(parts.path or "/")
    if path == "/" or should_ignore_path(path):
        return
    key = ("int", path)
    if key not in seen:
        seen.add(key)
        internal.append(path)


def _normalized_path(path: str) -> str:
    """Leading-slashed path without a trailing slash (except the root)."""
    if not path.startswith("/"):
        path = "/" + path
    if len(path) > 1 and path.endswith("/"):
        path = path[:-1]
    return path


def _append_external(
    absu: str,
    link_domain: str,
    ignored_domains: set[str] | None,
    seen: set[tuple[str, str]],
    external: list[str],
) -> None:
    """Record the external URL when its domain is not ignored and unseen."""
    if is_ignored_domain(link_domain, ignored_domains):
        return
    key = ("ext", absu)
    if key not in seen:
        seen.add(key)
        external.append(absu)
