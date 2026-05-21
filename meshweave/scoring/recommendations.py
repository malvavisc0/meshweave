"""Recommendation generator from scoring raw data."""

from __future__ import annotations

from typing import Any


def generate_recommendations(
    aeo_factors: dict[str, dict],
    geo_factors: dict[str, dict],
    payload: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Generate actionable recommendations based on factor scores.

    Args:
        aeo_factors: AEO factor score dicts.
        geo_factors: GEO factor score dicts.
        payload: Optional crawl payload for audit.meta data
            (canonical_issues, duplicate_og_titles, etc.).

    Returns:
        List of recommendation dicts sorted by priority.
    """
    recs: list[dict[str, Any]] = []

    # --- AEO recommendations ---
    schema = aeo_factors.get("schema") or {}
    schema_raw = schema.get("raw") or {}
    coverage_pct = schema_raw.get("coverage_pct") or 0
    has_faq = schema_raw.get("has_faq_schema", False)

    if coverage_pct < 50:
        recs.append(
            {
                "factor": "schema",
                "priority": "high",
                "title": "Add structured data (JSON-LD) to more pages",
                "detail": (
                    f"Only {coverage_pct:.0f}% of pages have schema markup. "
                    "Add FAQPage, HowTo, or Article schema to key pages."
                ),
                "impact": "AEO +10-15 points estimated",
            }
        )

    if not has_faq:
        recs.append(
            {
                "factor": "schema",
                "priority": "high",
                "title": "Add FAQPage schema to key pages",
                "detail": (
                    "No FAQPage schema found. Add FAQ sections with 40-60 word "
                    "answers to product, pricing, and how-it-works pages."
                ),
                "impact": "AEO +10-15 points",
            }
        )

    content_struct = aeo_factors.get("content_structure") or {}
    content_raw = content_struct.get("raw") or {}
    site_avg = content_raw.get("site_average") or 0
    pages_evaluated = content_raw.get("pages_evaluated") or 0

    if site_avg < 40 and pages_evaluated > 0:
        recs.append(
            {
                "factor": "content_structure",
                "priority": "medium",
                "title": "Improve content structure across pages",
                "detail": (
                    f"Average content structure score is {site_avg:.0f}/100 "
                    f"across {pages_evaluated} pages. Add headings (H1-H6), "
                    "lists, tables, and ensure 300+ words per page."
                ),
                "impact": "AEO +5-10 points estimated",
            }
        )

    # Thin pages
    per_page = content_raw.get("per_page_scores") or {}
    thin_pages = [u for u, s in per_page.items() if s < 30]
    if thin_pages:
        examples = ", ".join(thin_pages[:3])
        recs.append(
            {
                "factor": "content_structure",
                "priority": "medium",
                "title": f"Enrich {len(thin_pages)} thin page(s)",
                "detail": (
                    f"Pages with low structure scores: {examples}. "
                    "Add headings, content, images with alt text."
                ),
                "impact": "AEO +5-8 points estimated",
            }
        )

    # --- GEO recommendations ---
    topical = geo_factors.get("topical_authority") or {}
    topical_raw = topical.get("raw") or {}
    same_as_count = topical_raw.get("same_as_count") or 0

    if same_as_count == 0:
        recs.append(
            {
                "factor": "entity_consistency",
                "priority": "high",
                "title": "Add sameAs links to your Organization schema",
                "detail": (
                    "No sameAs links found. Add links to your LinkedIn, GitHub, "
                    "Twitter, and other profiles in your Organization JSON-LD "
                    "to improve entity recognition."
                ),
                "impact": "GEO +5-10 points estimated",
            }
        )

    eeat = geo_factors.get("eeat") or {}
    eeat_raw = eeat.get("raw") or {}

    if not eeat_raw.get("has_org_schema"):
        recs.append(
            {
                "factor": "eeat",
                "priority": "high",
                "title": "Add Organization JSON-LD schema",
                "detail": (
                    "No Organization schema found. Add an Organization block "
                    "to your homepage with name, logo, url, and sameAs links."
                ),
                "impact": "GEO +10-15 points estimated",
            }
        )

    if not eeat_raw.get("has_author_info"):
        recs.append(
            {
                "factor": "eeat",
                "priority": "medium",
                "title": "Add author information to articles",
                "detail": (
                    "No author schema found in articles. Add author JSON-LD "
                    "to article pages with name, url, and sameAs."
                ),
                "impact": "GEO +5-10 points estimated",
            }
        )

    crawl_access = geo_factors.get("crawl_access") or {}
    crawl_note = crawl_access.get("note")
    crawl_score = crawl_access.get("score")

    if crawl_score is None and crawl_note:
        recs.append(
            {
                "factor": "crawl_access",
                "priority": "medium",
                "title": "Re-analyze as domain for accessibility score",
                "detail": crawl_note,
                "impact": "GEO +10-20 points",
            }
        )
    elif crawl_access.get("raw"):
        cr = crawl_access["raw"]
        if not cr.get("llms_txt_exists"):
            recs.append(
                {
                    "factor": "crawl_access",
                    "priority": "high",
                    "title": "Publish an llms.txt file",
                    "detail": (
                        "AI crawlers look for llms.txt to understand your site. "
                        "Create /.well-known/llms.txt with a brief site "
                        "description."
                    ),
                    "impact": "GEO +10-20 points estimated",
                }
            )
        if not cr.get("robots_exists"):
            recs.append(
                {
                    "factor": "crawl_access",
                    "priority": "medium",
                    "title": "Add a robots.txt file",
                    "detail": (
                        "No robots.txt found. Create one at /robots.txt to "
                        "control crawler access."
                    ),
                    "impact": "GEO +10 points estimated",
                }
            )

    content_depth = geo_factors.get("content_depth") or {}
    cd_raw = content_depth.get("raw") or {}
    avg_words = cd_raw.get("avg_words") or 0

    if avg_words < 300:
        recs.append(
            {
                "factor": "content_depth",
                "priority": "medium",
                "title": "Increase average content depth",
                "detail": (
                    f"Average word count is {avg_words:.0f} words per page. "
                    "Target 500+ words for key pages to improve "
                    "content depth signals."
                ),
                "impact": "GEO +5-10 points estimated",
            }
        )

    # --- Payload-based recommendations (audit.meta) ---
    if payload:
        audit = payload.get("audit") or {}
        meta = audit.get("meta") or {}

        # Canonical issues
        canonical_issues = meta.get("canonical_issues") or []
        if canonical_issues:
            recs.append(
                {
                    "factor": "schema",
                    "priority": "medium",
                    "title": f"Fix canonical URL mismatches on {len(canonical_issues)} page(s)",
                    "detail": (
                        "These pages have canonical URLs pointing to a different "
                        "page. This confuses search engines and hurts snippet "
                        "capture."
                    ),
                    "impact": "AEO +3-5 points estimated",
                }
            )

        # Duplicate OG titles
        dup_titles = meta.get("duplicate_og_titles") or {}
        if dup_titles:
            recs.append(
                {
                    "factor": "content_structure",
                    "priority": "medium",
                    "title": f"Differentiate OG titles — {len(dup_titles)} group(s) share the same title",
                    "detail": (
                        "Multiple pages share identical OG titles. This reduces "
                        "click-through rates and confuses social previews."
                    ),
                    "impact": "AEO +2-3 points estimated",
                }
            )

        # Site-wide image alt text
        md_dict = payload.get("markdowns") or {}
        if isinstance(md_dict, dict) and md_dict:
            total_imgs = 0
            imgs_with_alt = 0
            for _url, pg in md_dict.items():
                if isinstance(pg, dict):
                    cm = pg.get("content_metrics") or {}
                    total_imgs += cm.get("images_total") or 0
                    imgs_with_alt += cm.get("images_with_alt") or 0
            if total_imgs > 0 and (imgs_with_alt / total_imgs) < 0.5:
                missing = total_imgs - imgs_with_alt
                recs.append(
                    {
                        "factor": "content_structure",
                        "priority": "medium",
                        "title": f"Add alt text to {missing} image(s) across the site",
                        "detail": (
                            f"Only {imgs_with_alt}/{total_imgs} images have alt "
                            "text. Add descriptive alt text to improve "
                            "accessibility and image search visibility."
                        ),
                        "impact": "AEO +3-5 points estimated",
                    }
                )

    # Positive callouts (green)
    if coverage_pct >= 80:
        recs.append(
            {
                "factor": "schema",
                "priority": "low",
                "title": "Strong schema coverage — keep it up",
                "detail": f"{coverage_pct:.0f}% of pages have schema markup.",
                "impact": "",
            }
        )

    # Sort by priority
    priority_order = {"high": 0, "medium": 1, "low": 2}
    recs.sort(key=lambda r: priority_order.get(r["priority"], 1))

    return recs
