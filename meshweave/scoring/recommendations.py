"""Recommendation generator from scoring raw data."""

from __future__ import annotations

from typing import Any

# Map factor keys to their pillar (aeo, geo, aax)
_FACTOR_TO_PILLAR: dict[str, str] = {
    # AEO factors
    "schema": "aeo",
    "content_structure": "aeo",
    "freshness": "aeo",
    "capture_rate": "aeo",
    "query_match": "aeo",
    "voice_rate": "aeo",
    # GEO factors
    "topical_authority": "geo",
    "eeat": "geo",
    "crawl_access": "geo",
    "content_depth": "geo",
    "entity_consistency": "geo",
    "citation": "geo",
    # AAX factors
    "homepage_comprehension": "aax",
    "meta_optimization": "aax",
    "content_delta": "aax",
    "llms_txt": "aax",
    "email_validation": "aax",
    "contactability": "aax",
}


def generate_recommendations(
    aeo_factors: dict[str, dict],
    geo_factors: dict[str, dict],
    payload: dict[str, Any] | None = None,
    aax_factors: dict[str, dict] | None = None,
) -> list[dict[str, Any]]:
    """Generate actionable recommendations based on factor scores.

    Args:
        aeo_factors: AEO factor score dicts.
        geo_factors: GEO factor score dicts.
        payload: Optional crawl payload for audit.meta data
            (canonical_issues, duplicate_og_titles, etc.).
        aax_factors: Optional AAX factor score dicts.

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

    # --- AAX recommendations ---
    if aax_factors:
        # Homepage Comprehension
        hc = aax_factors.get("homepage_comprehension")
        if hc:
            hc_raw = hc.get("raw") or {}
            missing_fields = []
            if not hc_raw.get("brand"):
                missing_fields.append("brand")
            if not hc_raw.get("product"):
                missing_fields.append("product")
            if not hc_raw.get("target_audience"):
                missing_fields.append("audience")
            if not hc_raw.get("call_to_action"):
                missing_fields.append("CTA")

            if missing_fields and hc.get("score", 0) < 70:
                recs.append(
                    {
                        "factor": "homepage_comprehension",
                        "priority": "high",
                        "title": "Improve homepage clarity for AI agents",
                        "detail": (
                            f"AI agents struggle to understand your site. "
                            f"Missing: {', '.join(missing_fields)}. "
                            "Make brand, product, audience, and CTA clear."
                        ),
                        "impact": "AAX +15-25 points estimated",
                    }
                )

        # Content Delta (AI content gaps)
        cd = aax_factors.get("content_delta")
        if cd:
            cd_raw = cd.get("raw") or {}
            weaknesses = cd_raw.get("weaknesses") or []
            strengths_count = len(cd_raw.get("strengths") or [])
            if weaknesses and strengths_count < 3:
                examples = ", ".join(weaknesses[:3])
                recs.append(
                    {
                        "factor": "content_delta",
                        "priority": "medium",
                        "title": f"Address {len(weaknesses)} content gap(s)",
                        "detail": (
                            f"AI found gaps: {examples}. "
                            "Add missing product info, pricing, use cases, "
                            "or company details."
                        ),
                        "impact": "AAX +10-20 points estimated",
                    }
                )
            # Low cohort score
            if cd.get("score", 0) < 50:
                recs.append(
                    {
                        "factor": "content_delta",
                        "priority": "high",
                        "title": "Enhance content richness for AI processing",
                        "detail": (
                            "AI finds content incomplete. Add product "
                            "descriptions, pricing, target audience, "
                            "features, and use cases."
                        ),
                        "impact": "AAX +20-30 points estimated",
                    }
                )

        # Meta Optimization
        mo = aax_factors.get("meta_optimization")
        if mo:
            mo_raw = mo.get("raw") or {}
            completeness = mo_raw.get("completeness", "minimal")
            clarity = mo_raw.get("clarity", "unclear")
            llm_opt = mo_raw.get("llm_optimization", "poor")
            would_click = mo_raw.get("would_click_through", False)

            if not would_click or completeness == "minimal" or clarity == "unclear":
                issues = []
                if not would_click:
                    issues.append("value proposition")
                if completeness == "minimal":
                    issues.append("metadata")
                if clarity == "unclear":
                    issues.append("messaging")
                if llm_opt == "poor":
                    issues.append("LLM-optimized descriptions")

                if issues:
                    recs.append(
                        {
                            "factor": "meta_optimization",
                            "priority": "medium",
                            "title": "Optimize metadata for AI crawlers",
                            "detail": (
                                f"Improve: {', '.join(issues)}. Use clear, "
                                "descriptive titles and meta tags for LLM "
                                "understanding."
                            ),
                            "impact": "AAX +10-15 points estimated",
                        }
                    )

        # llms.txt
        llms = aax_factors.get("llms_txt")
        if llms:
            llms_raw = llms.get("raw") or {}
            llms_txt_data = llms_raw.get("llms_txt") or {}
            llms_full_data = llms_raw.get("llms_full_txt") or {}

            if not llms_txt_data.get("exists"):
                recs.append(
                    {
                        "factor": "llms_txt",
                        "priority": "high",
                        "title": "Publish llms.txt for AI crawler discovery",
                        "detail": (
                            "Create /.well-known/llms.txt with site "
                            "name, description, and AI crawler guidelines. "
                            "Helps AI systems discover your site."
                        ),
                        "impact": "AAX +10-15 points estimated",
                    }
                )
            elif not llms_full_data.get("exists"):
                recs.append(
                    {
                        "factor": "llms_txt",
                        "priority": "medium",
                        "title": "Publish llms-full.txt for full AI access",
                        "detail": (
                            "Create /.well-known/llms-full.txt for full "
                            "AI crawler access."
                        ),
                        "impact": "AAX +5-10 points estimated",
                    }
                )

        # Email Validation
        ev = aax_factors.get("email_validation")
        if ev:
            ev_raw = ev.get("raw") or {}
            contacts = ev_raw.get("valid_contacts") or []
            confidence = ev_raw.get("confidence", "low")
            best_contact_exists = ev_raw.get("best_contact", False)

            if confidence == "low" or (not contacts and not best_contact_exists):
                recs.append(
                    {
                        "factor": "email_validation",
                        "priority": "high",
                        "title": "Add valid contact emails for AI verification",
                        "detail": (
                            "AI needs clear contact emails to verify your "
                            "business. Add mailto: links with valid "
                            "addresses on contact page."
                        ),
                        "impact": "AAX +10-20 points est",
                    }
                )

    # --- Contactability recommendations (from payload) ---
    if payload:
        scores = payload.get("scores") or {}
        aax_scores = scores.get("aax") or {}
        contactability = aax_scores.get("contactability")
        if contactability:
            missing: list[str] = []
            if not contactability.get("has_email"):
                missing.append("email address")
            if not contactability.get("has_mailto"):
                missing.append("mailto: link")
            if not contactability.get("has_contact_page"):
                missing.append("contact page")
            if not contactability.get("has_social_links"):
                missing.append("social links")
            if not contactability.get("has_contact_point_schema"):
                missing.append("ContactPoint schema")

            if missing:
                recs.append(
                    {
                        "factor": "contactability",
                        "priority": "high",
                        "title": "Improve contactability for AI agents",
                        "detail": (
                            f"Missing: {', '.join(missing)}. "
                            "AI agents need clear contact signals to "
                            "verify and recommend your business."
                        ),
                        "impact": "AAX +10-20 points estimated",
                    }
                )

    # Add pillar and guidance to each recommendation
    for rec in recs:
        rec["pillar"] = _FACTOR_TO_PILLAR.get(rec.get("factor", ""), "aeo")
        rec["guidance"] = _get_guidance(rec["title"], rec.get("factor", ""))

    # Sort by priority
    priority_order = {"high": 0, "medium": 1, "low": 2}
    recs.sort(key=lambda r: priority_order.get(r["priority"], 1))

    return recs


def _get_guidance(title: str, factor: str) -> str:
    """Return guidance string for a recommendation, or empty string."""
    # Exact match first
    if title in _GUIDANCE:
        return _GUIDANCE[title]
    # Prefix match for dynamic titles
    for prefix, guidance in _GUIDANCE_PREFIX.items():
        if title.startswith(prefix):
            return guidance
    return ""


# Guidance strings keyed on exact recommendation title.
# Low-priority positive callouts get no guidance.
_GUIDANCE: dict[str, str] = {
    "Add structured data (JSON-LD) to more pages": (
        "Add FAQPage, HowTo, or Article JSON-LD to key pages."
    ),
    "Add FAQPage schema to key pages": (
        "Add FAQ sections with short, direct answers to product and pricing pages."
    ),
    "Improve content structure across pages": (
        "Add headings, lists, and tables. Shoot for 300+ words per page."
    ),
    "Add sameAs links to your Organization schema": (
        "Add links to your LinkedIn, GitHub, and social profiles in Organization JSON-LD."
    ),
    "Add Organization JSON-LD schema": (
        "Drop an Organization block on your homepage with name, logo, URL, and sameAs."
    ),
    "Add author information to articles": (
        "Add author JSON-LD with name, URL, and sameAs to article pages."
    ),
    "Re-analyze as domain for accessibility score": (
        "Run the analysis at the domain root for a complete accessibility score."
    ),
    "Publish an llms.txt file": (
        "Create /.well-known/llms.txt with a short site description for AI crawlers."
    ),
    "Add a robots.txt file": (
        "Create /robots.txt to control which crawlers can access your site."
    ),
    "Increase average content depth": ("Aim for 500+ words on your key pages."),
    "Optimize metadata for AI crawlers": (
        "Improve your value proposition, metadata, and descriptions for LLM understanding."
    ),
    "Publish llms.txt for AI crawler discovery": (
        "Create /.well-known/llms.txt with your site name, description, and crawler rules."
    ),
    "Publish llms-full.txt for full AI access": (
        "Create /.well-known/llms-full.txt for full AI crawler access."
    ),
    "Add valid contact emails for AI verification": (
        "Add mailto: links with real addresses to your contact page."
    ),
    "Improve contactability for AI agents": (
        "Add the missing contact signals so AI agents can verify and recommend you."
    ),
    "Improve homepage clarity for AI agents": (
        "Make your brand, product, audience, and call-to-action clear on the homepage."
    ),
    "Enhance content richness for AI processing": (
        "Add product descriptions, pricing, audience info, features, and use cases."
    ),
}

# Prefix-based guidance for dynamic titles (contain counts, etc.).
_GUIDANCE_PREFIX: dict[str, str] = {
    "Enrich": "Add headings, content, and alt text to the listed pages.",
    "Fix canonical URL mismatches": (
        "Update the canonical tag on the listed page to match its actual URL."
    ),
    "Differentiate OG titles": (
        "Give each page a unique OG title that describes what's actually on it."
    ),
    "Add alt text to": "Add descriptive alt text to the listed images.",
    "Address": "Fill in the missing product info, pricing, use cases, or company details.",
}
