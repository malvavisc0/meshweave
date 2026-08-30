"""Count helpers derived from a crawl payload.

Extracted from ``webapp/routers/analysis.py`` so both the analysis router and
the diff module can compute the same page/email/link counts without a
router-to-router private import. Logic is unchanged from the original helpers.
"""

from __future__ import annotations


def external_count(payload: dict, external_links_count: int) -> int:
    """Apply the extraction reported external count (link count, not page count)."""
    try:
        if payload.get("metrics") and payload["metrics"].get("extraction"):
            ext = payload["metrics"]["extraction"]
            # Do not use internal_count for content_pages_count (it's link count, not page count)
            if ext.get("external_count") is not None:
                external_links_count = int(ext.get("external_count") or 0)
    except Exception:
        pass
    return external_links_count


def link_counts(payload: dict, internal: int, external: int) -> tuple[int, int]:
    """Count internal/external link lists, keeping the larger external count."""
    try:
        if payload.get("links"):
            if isinstance(payload["links"].get("internal"), list):
                internal = len(payload["links"]["internal"])
            if isinstance(payload["links"].get("external"), list):
                external = max(external, len(payload["links"]["external"]))
    except Exception:
        pass
    return internal, external


def emails_count(payload: dict, emails_count: int) -> int:
    """Set the email count from the payload counts when present."""
    try:
        if payload.get("emails") and payload["emails"].get("counts"):
            emails_count = int(payload["emails"]["counts"].get("total_unique") or 0)
    except Exception:
        pass
    return emails_count


def content_pages_count(payload: dict, content_pages_count: int) -> int:
    """Count content pages from pages list or summary visited_count."""
    try:
        if isinstance(payload.get("pages"), list):
            content_pages_count = len(payload["pages"])
        elif (
            isinstance(payload.get("summary"), dict)
            and payload["summary"].get("visited_count") is not None
        ):
            content_pages_count = int(payload["summary"]["visited_count"] or 0)
    except Exception:
        pass
    return content_pages_count


def json_ld_count(payload: dict | None) -> dict:
    """Derive the four structural counts from a payload (empty when missing).

    Used for JSON-LD serialization and by the diff module; the two callers
    need the same numbers.
    """
    if not payload:
        return {
            "content_pages_count": 0,
            "emails_count": 0,
            "internal_links_count": 0,
            "external_links_count": 0,
        }
    ext = external_count(payload, 0)
    internal_links_count, ext = link_counts(payload, 0, ext)
    return {
        "content_pages_count": content_pages_count(payload, 0),
        "emails_count": emails_count(payload, 0),
        "internal_links_count": internal_links_count,
        "external_links_count": ext,
    }
