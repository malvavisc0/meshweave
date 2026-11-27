"""Result-page metadata builders shared by the analysis renders.

Extracted from :mod:`webapp.routers.analysis` so the over-cap router shrinks
instead of growing. Pure builders over row/payload attributes: SEO meta,
JSON-LD, factor extremes, and the email source map. Logic is unchanged.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from webapp.models import Crawl
from webapp.utils.payload_counts import json_ld_count as _json_ld_count
from webapp.utils.reasons import friendly_reason


def factor_extremes(factors: dict) -> tuple:
    """Return (weakest_item, strongest_item, missing_keys).

    Each item is (factor_key, factor_dict) or None. missing_keys lists
    every factor key with no score (empty when all factors are scored),
    so the missing-data caveat names all of them.
    """
    scored = [(k, v) for k, v in factors.items() if v.get("score") is not None]
    missing = [k for k, v in factors.items() if v.get("score") is None]
    if scored:
        return (
            min(scored, key=lambda x: x[1]["score"]),
            max(scored, key=lambda x: x[1]["score"]),
            missing,
        )
    return None, None, missing


def build_factor_extremes(ss: dict | None) -> dict:
    """Build factor_extremes dict for all pillars from score_snapshot."""
    if not ss or not ss.get("score_data"):
        return {
            "aeo": (None, None, []),
            "geo": (None, None, []),
            "aax": (None, None, []),
        }
    data = ss["score_data"]
    return {
        pillar: factor_extremes(data.get(pillar, {}).get("factors", {}))
        for pillar in ("aeo", "geo", "aax")
    }


def seo_title_desc(payload: dict | None) -> tuple[str, str]:
    """Extract (title_from_payload, desc_from_payload) from the page payload."""
    title_from_payload = ""
    desc_from_payload = ""
    try:
        if payload:
            pg = payload.get("page") or {}
            title_from_payload = (pg.get("title") or "").strip()
            desc_from_payload = (pg.get("description") or "").strip()
    except Exception:
        pass
    return title_from_payload, desc_from_payload


def payload_scope(payload: dict | None, row: Crawl) -> str:
    """Compute the effective scope value used for the SEO page title."""
    scope_val = ""
    try:
        if isinstance(payload, dict):
            scope_val = str(payload.get("scope") or "").strip().lower()
    except Exception:
        scope_val = ""
    if not scope_val:
        scope_val = str(getattr(row, "scope", "") or "").strip().lower()
    return scope_val


def page_title(
    scope_val: str,
    domain_val: str,
    path_val: str,
    title_from_payload: str,
    site_name: str,
) -> str:
    """Build the SEO-friendly page title for the given scope."""
    if scope_val == "site":
        return f"{domain_val} Site Analysis — Pages, Links, Emails | {site_name}"
    path_or_title = title_from_payload or path_val
    return f"Page Analysis — {path_or_title} — {domain_val} | {site_name}"


def meta_description(desc_from_payload: str) -> str:
    """Build the truncated meta description for the page.

    Derived from the page description only. It never falls back to payload
    markdown: the meta description is embedded in the page head for every
    viewer, and markdown is not part of the public preview allowlist.
    """
    meta_description = (desc_from_payload or "").strip()
    if len(meta_description) > 300:
        meta_description = meta_description[:297] + "…"
    return meta_description


def structured_data(
    identifier: str,
    domain: str,
    abs_page_url: str,
    updated_at: datetime | None,
    status: str | None,
    counts: dict,
) -> str:
    """Build the JSON-LD CreativeWork payload (with fallback)."""
    try:
        return json.dumps(
            {
                "@context": "https://schema.org",
                "@type": "CreativeWork",
                "name": "MeshWeave Analysis",
                "identifier": identifier,
                "about": (domain or "").strip(),
                "url": abs_page_url,
                "dateModified": (updated_at or datetime.now(UTC)).strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                ),
                "creativeWorkStatus": (status or "").title(),
                "measurementTechnique": [
                    "web-crawl",
                    "markdown-extraction",
                    "link-analysis",
                    "email-detection",
                ],
                "isAccessibleForFree": True,
                "keywords": [
                    "markdown",
                    "link map",
                    "email intelligence",
                    "ai summary",
                ],
                "additionalProperty": [
                    {
                        "@type": "PropertyValue",
                        "name": "content_pages_count",
                        "value": str(counts["content_pages_count"]),
                    },
                    {
                        "@type": "PropertyValue",
                        "name": "emails_count",
                        "value": str(counts["emails_count"]),
                    },
                    {
                        "@type": "PropertyValue",
                        "name": "internal_links_count",
                        "value": str(counts["internal_links_count"]),
                    },
                    {
                        "@type": "PropertyValue",
                        "name": "external_links_count",
                        "value": str(counts["external_links_count"]),
                    },
                ],
            }
        )
    except Exception:
        return json.dumps(
            {
                "@context": "https://schema.org",
                "@type": "CreativeWork",
                "name": "MeshWeave Analysis",
                "identifier": identifier,
                "about": (domain or "").strip(),
                "url": abs_page_url,
            }
        )


def reason_stopped_label(payload: dict | None) -> str:
    """Build the friendly reason-stopped label for the template."""
    return (
        friendly_reason(payload.get("summary", {}).get("reason_stopped", ""))
        if payload and payload.get("summary")
        else ""
    )


def email_source_map(payload: dict | None) -> dict[str, str]:
    """Map each detected email to the page URL it was found on.

    Derived from ``payload.emails.by_url`` (``{url: [emails]}``). Used to
    server-render the owner email table's "Found On" column without any
    client-side lookups.
    """
    mapping: dict[str, str] = {}
    if not payload:
        return mapping
    by_url = (payload.get("emails") or {}).get("by_url") or {}
    for url, addrs in by_url.items():
        addresses = addrs if isinstance(addrs, list) else [addrs]
        for addr in addresses:
            mapping[str(addr)] = (url or "").strip()
    return mapping


def analysis_json_ld(
    row: Crawl, payload: dict | None, in_progress: bool, abs_page_url: str, identifier
) -> tuple[str | None, str]:
    """Return (json_ld, reason_stopped_label) for an analysis render."""
    if in_progress:
        return None, ""
    # JSON-LD: CreativeWork (LLM-first)
    counts = _json_ld_count(payload)
    json_ld = structured_data(
        identifier, row.domain, abs_page_url, row.updated_at, row.status, counts
    )
    return json_ld, reason_stopped_label(payload)
