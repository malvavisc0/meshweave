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
    contactability: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Generate actionable recommendations based on factor scores.

    Args:
        aeo_factors: AEO factor score dicts.
        geo_factors: GEO factor score dicts.
        payload: Optional crawl payload for audit.meta data
            (canonical_issues, duplicate_og_titles, etc.).
        aax_factors: Optional AAX factor score dicts.
        contactability: Optional contactability signal dict
            (``aax.contactability`` from the AAX analysis). Passed
            explicitly because ``payload["scores"]`` is not yet set
            when recommendations are first generated.

    Returns:
        List of recommendation dicts sorted by priority.
    """
    recs: list[dict[str, Any]] = []
    recs.extend(_aeo_recommendations(aeo_factors))
    recs.extend(_geo_recommendations(geo_factors))
    if payload:
        recs.extend(_payload_recommendations(payload))
    if aax_factors:
        recs.extend(_aax_recommendations(aax_factors))
    recs.extend(_contactability_recommendations(contactability, payload))

    # Add pillar and guidance to each recommendation
    for rec in recs:
        rec["pillar"] = _FACTOR_TO_PILLAR.get(rec.get("factor", ""), "aeo")
        rec["guidance"] = _get_guidance(rec["title"], rec.get("factor", ""))

    # Sort by priority (stable — preserves insertion order within a band)
    priority_order = {"high": 0, "medium": 1, "low": 2}
    recs.sort(key=lambda r: priority_order.get(r["priority"], 1))

    return recs


def _factor_raw(factors: dict[str, dict], key: str) -> dict:
    """Raw data dict for a factor, defaulted to empty."""
    return (factors.get(key) or {}).get("raw") or {}


def _aeo_recommendations(aeo_factors: dict[str, dict]) -> list[dict[str, Any]]:
    """Recommendations for AEO factors (schema, content structure)."""
    recs: list[dict[str, Any]] = []
    recs.extend(_schema_coverage_recs(aeo_factors))
    recs.extend(_content_structure_recs(aeo_factors))
    return recs


def _schema_coverage_recs(aeo_factors: dict[str, dict]) -> list[dict[str, Any]]:
    """Schema-coverage and FAQ-schema recommendations."""
    recs: list[dict[str, Any]] = []
    schema_raw = _factor_raw(aeo_factors, "schema")
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

    return recs


def _content_structure_recs(aeo_factors: dict[str, dict]) -> list[dict[str, Any]]:
    """Site-average structure and thin-page recommendations."""
    recs: list[dict[str, Any]] = []
    content_raw = _factor_raw(aeo_factors, "content_structure")
    site_avg = content_raw.get("site_average") or 0
    pages_evaluated = content_raw.get("pages_evaluated") or 0

    # Thresholds track the interpretation bands: a site-average below 55
    # sits in the "weak" band (40-59) or lower and needs the general
    # structure recommendation.
    if site_avg < 55 and pages_evaluated > 0:
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

    recs.extend(_thin_pages_recs(content_raw))
    return recs


def _thin_pages_recs(content_raw: dict) -> list[dict[str, Any]]:
    """Recommendation for pages scoring below the weak-band boundary (40)."""
    # Thin pages — below the "broken"/"weak" band boundary (40)
    per_page = content_raw.get("per_page_scores") or {}
    thin_pages = [u for u, s in per_page.items() if s < 40]
    if not thin_pages:
        return []
    examples = ", ".join(thin_pages[:3])
    return [
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
    ]


def _geo_recommendations(geo_factors: dict[str, dict]) -> list[dict[str, Any]]:
    """Recommendations for GEO factors (entity consistency, EEAT, crawl)."""
    recs: list[dict[str, Any]] = []
    recs.extend(_same_as_rec(geo_factors))
    recs.extend(_eeat_recs(geo_factors))
    recs.extend(_crawl_access_recommendations(geo_factors))
    recs.extend(_content_depth_rec(geo_factors))
    return recs


def _same_as_rec(geo_factors: dict[str, dict]) -> list[dict[str, Any]]:
    """Recommendation for missing sameAs links."""
    topical_raw = _factor_raw(geo_factors, "topical_authority")
    same_as_count = topical_raw.get("same_as_count") or 0

    if same_as_count:
        return []
    return [
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
    ]


def _eeat_recs(geo_factors: dict[str, dict]) -> list[dict[str, Any]]:
    """Recommendations for missing Organization and author schema."""
    eeat_raw = _factor_raw(geo_factors, "eeat")
    recs: list[dict[str, Any]] = []

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
    return recs


def _crawl_access_recommendations(geo_factors: dict[str, dict]) -> list[dict[str, Any]]:
    """Crawl-accessibility recommendations (note-based or raw-based)."""
    crawl_access = geo_factors.get("crawl_access") or {}
    crawl_note = crawl_access.get("note")
    crawl_score = crawl_access.get("score")

    if crawl_score is None and crawl_note:
        return [
            {
                "factor": "crawl_access",
                "priority": "medium",
                "title": "Re-analyze as domain for accessibility score",
                "detail": crawl_note,
                "impact": "GEO +10-20 points",
            }
        ]
    if crawl_access.get("raw"):
        return _crawl_access_recs(crawl_access)
    return []


def _content_depth_rec(geo_factors: dict[str, dict]) -> list[dict[str, Any]]:
    """Recommendation for shallow average content depth."""
    cd_raw = _factor_raw(geo_factors, "content_depth")
    avg_words = cd_raw.get("avg_words") or 0

    if avg_words >= 300:
        return []
    return [
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
    ]


def _crawl_access_recs(crawl_access: dict) -> list[dict[str, Any]]:
    """Recommendations derived from the raw crawl-access data (llms.txt/robots)."""
    cr = crawl_access["raw"]
    recs: list[dict[str, Any]] = []
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
    return recs


def _payload_recommendations(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Recommendations driven by the crawl payload audit.meta data."""
    recs: list[dict[str, Any]] = []
    meta = (payload.get("audit") or {}).get("meta") or {}

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

    recs.extend(_image_alt_recs(payload))

    return recs


def _image_alt_recs(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Site-wide image alt-text recommendation."""
    total_imgs, imgs_with_alt = _site_image_counts(payload)
    if total_imgs <= 0 or (imgs_with_alt / total_imgs) >= 0.5:
        return []
    missing = total_imgs - imgs_with_alt
    return [
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
    ]


def _site_image_counts(payload: dict[str, Any]) -> tuple[int, int]:
    """Total images and images with alt text across all markdown pages."""
    md_dict = payload.get("markdowns") or {}
    total_imgs = 0
    imgs_with_alt = 0
    if isinstance(md_dict, dict) and md_dict:
        for _url, pg in md_dict.items():
            if isinstance(pg, dict):
                cm = pg.get("content_metrics") or {}
                total_imgs += cm.get("images_total") or 0
                imgs_with_alt += cm.get("images_with_alt") or 0
    return total_imgs, imgs_with_alt


def _aax_recommendations(aax_factors: dict[str, dict]) -> list[dict[str, Any]]:
    """Recommendations for AAX factors."""
    recs: list[dict[str, Any]] = []
    recs.extend(_homepage_comprehension_rec(aax_factors))
    recs.extend(_content_delta_rec(aax_factors))
    recs.extend(_meta_optimization_rec(aax_factors))
    recs.extend(_llms_txt_rec(aax_factors))
    recs.extend(_email_validation_rec(aax_factors))
    return recs


def _homepage_comprehension_rec(aax_factors: dict[str, dict]) -> list[dict[str, Any]]:
    """Recommendation for unclear homepage comprehension."""
    hc = aax_factors.get("homepage_comprehension")
    if not hc:
        return []
    hc_raw = hc.get("raw") or {}
    missing_fields: list[str] = []
    if not hc_raw.get("brand"):
        missing_fields.append("brand")
    if not hc_raw.get("product"):
        missing_fields.append("product")
    if not hc_raw.get("target_audience"):
        missing_fields.append("audience")
    if not hc_raw.get("call_to_action"):
        missing_fields.append("CTA")

    if not missing_fields or hc.get("score", 0) >= 70:
        return []
    return [
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
    ]


def _content_delta_rec(aax_factors: dict[str, dict]) -> list[dict[str, Any]]:
    """Recommendations for content gaps detected by AI processing."""
    cd = aax_factors.get("content_delta")
    if not cd:
        return []
    recs: list[dict[str, Any]] = []
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
    return recs


def _meta_optimization_rec(aax_factors: dict[str, dict]) -> list[dict[str, Any]]:
    """Recommendation for weak/confusing metadata."""
    mo = aax_factors.get("meta_optimization")
    if not mo:
        return []
    issues = _meta_issues(mo.get("raw") or {})
    if not issues:
        return []
    return [
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
    ]


def _meta_issues(mo_raw: dict) -> list[str]:
    """Issue labels for the metadata weaknesses found."""
    completeness = mo_raw.get("completeness", "minimal")
    clarity = mo_raw.get("clarity", "unclear")
    llm_opt = mo_raw.get("llm_optimization", "poor")
    would_click = mo_raw.get("would_click_through", False)

    if would_click and completeness != "minimal" and clarity != "unclear":
        return []
    issues: list[str] = []
    if not would_click:
        issues.append("value proposition")
    if completeness == "minimal":
        issues.append("metadata")
    if clarity == "unclear":
        issues.append("messaging")
    if llm_opt == "poor":
        issues.append("LLM-optimized descriptions")
    return issues


def _llms_txt_rec(aax_factors: dict[str, dict]) -> list[dict[str, Any]]:
    """Recommendation for the llms-full.txt variant (full-file AAX access)."""
    llms = aax_factors.get("llms_txt")
    if not llms:
        return []
    llms_raw = llms.get("raw") or {}
    llms_txt_data = llms_raw.get("llms_txt") or {}
    llms_full_data = llms_raw.get("llms_full_txt") or {}

    if llms_txt_data.get("exists") and not llms_full_data.get("exists"):
        return [
            {
                "factor": "llms_txt",
                "priority": "medium",
                "title": "Publish llms-full.txt for full AI access",
                "detail": (
                    "Create /.well-known/llms-full.txt for full AI crawler access."
                ),
                "impact": "AAX +5-10 points estimated",
            }
        ]
    return []


def _email_validation_rec(aax_factors: dict[str, dict]) -> list[dict[str, Any]]:
    """Recommendation for lacking valid contact email signals."""
    ev = aax_factors.get("email_validation")
    if not ev:
        return []
    ev_raw = ev.get("raw") or {}
    contacts = ev_raw.get("valid_contacts") or []
    confidence = ev_raw.get("confidence", "low")
    best_contact_exists = ev_raw.get("best_contact", False)

    if confidence == "low" or (not contacts and not best_contact_exists):
        return [
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
        ]
    return []


def _contactability_recommendations(
    contactability: dict[str, Any] | None,
    payload: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Recommendation for missing contact signals.

    Prefers the explicitly passed contactability signal; falls back to the
    payload only when the caller knows ``scores.aax`` is already present
    (e.g. manual-input re-scores loading a persisted payload).
    """
    contactability = _resolve_contactability(contactability, payload)
    if not contactability:
        return []
    missing_signals = _missing_contact_signals(contactability)
    if not missing_signals:
        return []
    return [
        {
            "factor": "contactability",
            "priority": "high",
            "title": "Improve contactability for AI agents",
            "detail": (
                f"Missing: {', '.join(missing_signals)}. "
                "AI agents need clear contact signals to "
                "verify and recommend your business."
            ),
            "impact": "AAX +10-20 points estimated",
        }
    ]


def _resolve_contactability(
    contactability: dict[str, Any] | None,
    payload: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Explicit contactability signal, or the payload's persisted one."""
    if contactability:
        return contactability
    if not payload:
        return None
    scores = payload.get("scores") or {}
    aax_scores = scores.get("aax") or {}
    return aax_scores.get("contactability")


# Contact signal field → label used in the recommendation detail
_CONTACT_SIGNAL_LABELS: dict[str, str] = {
    "has_email": "email address",
    "has_mailto": "mailto: link",
    "has_contact_page": "contact page",
    "has_social_links": "social links",
    "has_contact_point_schema": "ContactPoint schema",
}


def _missing_contact_signals(contactability: dict[str, Any]) -> list[str]:
    """Labels of the contact signals the site is missing."""
    missing: list[str] = []
    for signal, label in _CONTACT_SIGNAL_LABELS.items():
        if not contactability.get(signal):
            missing.append(label)
    return missing


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
