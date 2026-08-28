"""Cross-page audit: OG/meta uniqueness, entity consistency, schema coverage."""

from __future__ import annotations

from collections import Counter
from typing import Any
from urllib.parse import urljoin, urlparse

__all__ = [
    "audit_meta_uniqueness",
    "audit_entity_consistency",
    "audit_schema_coverage",
]


def _canonical_key(url: str) -> tuple[str, str]:
    """Normalise a URL to (host, path) for canonical comparison.

    Scheme, query, and fragment are ignored; the host is lowercased and the
    path's trailing slash is stripped. This detects cross-domain and
    host-variant (e.g. ``www.``) mismatches, not just last-segment changes.
    """
    parsed = urlparse(url)
    host = (parsed.netloc or "").lower()
    path = parsed.path.rstrip("/") or "/"
    return host, path


def _gather_pages(
    markdowns: dict[str, dict[str, Any]],
    start_page_meta: dict[str, Any] | None,
) -> dict[str, dict[str, Any]]:
    """Pages to audit: the optional start page plus each markdowns entry."""
    all_pages: dict[str, dict[str, Any]] = {}
    if start_page_meta:
        all_pages["(start)"] = start_page_meta
    for url, data in markdowns.items():
        page = data.get("page", {})
        if page:
            all_pages[url] = page
    return all_pages


def audit_meta_uniqueness(
    markdowns: dict[str, dict[str, Any]],
    start_page_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Find duplicate OG titles/descriptions and canonical mismatches.

    *markdowns* is the ``markdowns`` dict from the crawl payload
    where each value has a ``page`` key with page metadata.
    """
    og_titles: dict[str, list[str]] = {}
    og_descs: dict[str, list[str]] = {}
    canonical_issues: list[dict[str, str]] = []

    all_pages = _gather_pages(markdowns, start_page_meta)

    for url, page in all_pages.items():
        og = page.get("og", {})
        og_t = og.get("title", "").strip()
        og_d = og.get("description", "").strip()
        if og_t:
            og_titles.setdefault(og_t, []).append(url)
        if og_d:
            og_descs.setdefault(og_d, []).append(url)

        # Canonical mismatch: the canonical URL points somewhere other than
        # the page itself. Resolve relative canonicals against the page URL,
        # then compare normalised (host, path) so cross-domain and host-variant
        # mismatches are caught — not only last-path-segment changes.
        canonical = page.get("canonical", "").strip()
        if canonical and url != "(start)":
            resolved = urljoin(url, canonical)
            if _canonical_key(resolved) != _canonical_key(url):
                canonical_issues.append(
                    {
                        "page": url,
                        "canonical": canonical,
                    }
                )

    # Only report groups with 2+ pages sharing the same value
    dup_titles = {v: urls for v, urls in og_titles.items() if len(urls) > 1}
    dup_descs = {v: urls for v, urls in og_descs.items() if len(urls) > 1}

    return {
        "duplicate_og_titles": dup_titles,
        "duplicate_og_descriptions": dup_descs,
        "canonical_issues": canonical_issues,
        "unique_og_titles": len(og_titles),
        "unique_og_descriptions": len(og_descs),
        "total_pages_checked": len(all_pages),
    }


def _extract_orgs_recursive(
    obj: Any,
    _visited: set[int] | None = None,
) -> list[dict[str, Any]]:
    """Recursively extract Organization objects from JSON-LD structures.

    Traverses nested dicts and lists, looking for objects with
    ``@type == "Organization"``.  Fields like ``provider``,
    ``publisher``, ``about``, and ``parentOrganization`` often
    contain nested Organization data that top-level scanning misses.
    """
    if _visited is None:
        _visited = set()

    results: list[dict[str, Any]] = []

    if isinstance(obj, dict):
        obj_id = id(obj)
        if obj_id in _visited:
            return results
        _visited.add(obj_id)

        if obj.get("@type") == "Organization":
            results.append(obj)

        # Recurse into all values
        for v in obj.values():
            results.extend(_extract_orgs_recursive(v, _visited))

    elif isinstance(obj, list):
        for item in obj:
            results.extend(_extract_orgs_recursive(item, _visited))

    return results


def _collect_org_fields(
    all_pages: dict[str, dict[str, Any]],
) -> tuple[list[str], list[str], list[str]]:
    """Collect Organization names, descriptions, and sameAs values."""
    names: list[str] = []
    descriptions: list[str] = []
    same_as_all: list[str] = []

    for page in all_pages.values():
        for item in page.get("jsonld", []):
            orgs = _extract_orgs_recursive(item)
            for org in orgs:
                name = org.get("name", "").strip()
                desc = org.get("description", "").strip()
                if name:
                    names.append(name)
                if desc:
                    descriptions.append(desc)
                for sa in org.get("sameAs", []):
                    if sa and sa not in same_as_all:
                        same_as_all.append(sa)

    return names, descriptions, same_as_all


def audit_entity_consistency(
    markdowns: dict[str, dict[str, Any]],
    start_page_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Check Organization schema consistency across all pages.

    Extracts name, description, and sameAs from Organization
    JSON-LD across all crawled pages and flags inconsistencies.
    Recursively traverses nested JSON-LD objects to find
    Organization schemas inside ``provider``, ``publisher``,
    ``about``, ``parentOrganization``, etc.
    """
    all_pages = _gather_pages(markdowns, start_page_meta)
    names, descriptions, same_as_all = _collect_org_fields(all_pages)

    name_counts = Counter(names)
    desc_counts = Counter(descriptions)

    return {
        "name": name_counts.most_common(1)[0][0] if names else None,
        "name_consistent": len(name_counts) <= 1,
        "name_variants": dict(name_counts) if len(name_counts) > 1 else {},
        "description_consistent": len(desc_counts) <= 1,
        "description_variants": list(desc_counts.keys()),
        "same_as": same_as_all,
        "pages_with_org_schema": len(names),
    }


def audit_schema_coverage(
    markdowns: dict[str, dict[str, Any]],
    start_page_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Summarise JSON-LD schema type coverage across all pages."""
    type_counts: Counter[str] = Counter()
    pages_with_schema = 0
    pages_without_schema = 0
    schema_types_per_page: dict[str, list[str]] = {}

    all_pages = _gather_pages(markdowns, start_page_meta)

    for url, page in all_pages.items():
        jsonld = page.get("jsonld", [])
        types = [
            item.get("@type", "Unknown") for item in jsonld if isinstance(item, dict)
        ]
        if types:
            pages_with_schema += 1
            schema_types_per_page[url] = types
            for t in types:
                type_counts[t] += 1
        else:
            pages_without_schema += 1

    total = pages_with_schema + pages_without_schema
    return {
        "pages_with_schema": pages_with_schema,
        "pages_without_schema": pages_without_schema,
        "coverage_pct": round(pages_with_schema / total * 100, 1) if total else 0,
        "type_counts": dict(type_counts.most_common()),
        "per_page": schema_types_per_page,
    }
