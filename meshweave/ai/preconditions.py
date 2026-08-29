"""Precondition checks for AAX analysis tests.

Each test declares what data it needs. The orchestrator checks
prerequisites before running, skips ineligible tests, and returns
actionable UI messages for missing data.
"""

from __future__ import annotations


def check_homepage_comprehension(payload: dict) -> str | None:
    """Test 2: Requires homepage markdown >= 50 words."""
    homepage_md = _get_homepage_markdown(payload)
    if not homepage_md or len(homepage_md.split()) < 50:
        return (
            "Homepage content too thin or not crawled — ensure the homepage "
            "has meaningful text content (at least 50 words)"
        )
    return None


def check_meta_optimization(payload: dict) -> str | None:
    """Test 3: Requires at least title + description."""
    page = payload.get("page") or {}
    title = page.get("title") or ""
    desc = page.get("description") or ""
    if not title.strip() and not desc.strip():
        return (
            "Missing meta title and description — add basic meta tags to your homepage"
        )
    return None


def check_content_delta(payload: dict) -> str | None:
    """Test 5: Requires >= 3 crawled pages with markdown content."""
    md_dict = payload.get("markdowns") or {}
    pages_with_content = sum(
        1
        for _url, data in md_dict.items()
        if isinstance(data, dict)
        and (data.get("markdown") or "")
        and len(data.get("markdown", "").split()) >= 50
    )
    if pages_with_content < 3:
        return (
            f"Only {pages_with_content} page(s) with sufficient content found — "
            "run a site crawl with at least 3 pages to enable content delta analysis"
        )
    return None


def check_all(payload: dict) -> dict[str, str | None]:
    """Run all precondition checks. Returns dict of test_key → skip_reason."""
    return {
        "homepage_comprehension": check_homepage_comprehension(payload),
        "meta_optimization": check_meta_optimization(payload),
        "content_delta": check_content_delta(payload),
    }


def _get_homepage_markdown(payload: dict) -> str:
    """Get homepage markdown content.

    Handles both short keys ("", "/") and full URL keys
    ("https://example.com/", "https://example.com").
    """
    md_dict = payload.get("markdowns") or {}
    domain = payload.get("domain") or ""

    md = _markdown_from_short_key(md_dict)
    if md:
        return md

    md = _markdown_from_domain_key(md_dict, domain)
    if md:
        return md

    return _first_markdown(md_dict)


def _markdown_from_short_key(md_dict: dict) -> str:
    """Homepage markdown stored under a short key ("", "/", "homepage")."""
    for key in ("", "/", "homepage"):
        if key in md_dict:
            md = (md_dict[key].get("markdown") or "").strip()
            if md:
                return md
    return ""


def _markdown_from_domain_key(md_dict: dict, domain: str) -> str:
    """Homepage markdown stored under a full URL key matching the domain root."""
    if not domain:
        return ""
    for url, data in md_dict.items():
        # Match https://domain/ or https://domain or http://domain/
        url_stripped = url.rstrip("/")
        if url_stripped in _root_url_keys(domain):
            md = (data.get("markdown") or "").strip()
            if md:
                return md
    return ""


def _root_url_keys(domain: str) -> tuple[str, ...]:
    """URL keys (sans trailing slash) that denote the domain root."""
    return (
        f"https://{domain}",
        f"http://{domain}",
        f"https://www.{domain}",
        f"http://www.{domain}",
        domain,
    )


def _first_markdown(md_dict: dict) -> str:
    """Markdown of the first markdowns entry, or '' when there are none."""
    if not md_dict:
        return ""
    first = next(iter(md_dict.values()))
    return (first.get("markdown") or "").strip()
