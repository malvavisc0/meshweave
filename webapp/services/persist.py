"""Persistence helpers for crawl data.

CrawlLink and CrawlEmail tables have been removed. All link/email data
is now stored in Crawl.payload_json. These functions are kept as no-op
stubs to avoid breaking existing callers during the transition.
"""

from collections.abc import Sequence
from typing import Any


def _abs_internal_url(base_domain: str, u: str) -> tuple[str, str]:
    """Compute absolute URL and domain for an internal link string."""
    return "", ""


def clear_crawl_data(crawl_id: str) -> None:
    """No-op: CrawlLink/CrawlEmail tables removed."""
    pass


def persist_page(
    *,
    crawl_id: str,
    page_url: str,
    base_domain: str,
    internal_links: Sequence[str] | None = None,
    external_links: Sequence[str] | None = None,
    email_sources: Sequence[dict[str, Any]] | None = None,
    emails_unique_fallback: Sequence[str] | None = None,
) -> None:
    """No-op: CrawlLink/CrawlEmail tables removed."""
    pass
