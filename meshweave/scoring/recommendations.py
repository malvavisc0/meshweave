"""Recommendation generator from scoring raw data."""

from __future__ import annotations

from typing import Any

from meshweave.scoring.composite import expected_lens_delta

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

    Each recommendation carries ``expected_points``: the model-derived
    composite delta the lens score moves by when the fix lands, computed
    with the same weights, renormalization, and calibration curve as the
    real score. Recommendations are sorted by that number so the fix
    order is the model's own ranking, not a typed-in estimate.

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
        List of recommendation dicts sorted by expected impact.
    """
    lens_factors = {
        "aeo": aeo_factors,
        "geo": geo_factors,
        "aax": aax_factors or {},
    }

    recs: list[dict[str, Any]] = []
    recs.extend(_aeo_recommendations(aeo_factors))
    recs.extend(_geo_recommendations(geo_factors))
    if payload:
        recs.extend(_payload_recommendations(payload, aeo_factors))
    if aax_factors:
        recs.extend(_aax_recommendations(aax_factors))
    recs.extend(_contactability_recommendations(contactability, payload))

    for rec in recs:
        rec["pillar"] = _FACTOR_TO_PILLAR.get(rec.get("factor", ""), "aeo")
        rec["guidance"] = _get_guidance(rec["title"], rec.get("factor", ""))
        _attach_expected_points(rec, lens_factors)

    _sort_recommendations(recs)

    return recs


def _attach_expected_points(
    rec: dict[str, Any],
    lens_factors: dict[str, dict[str, dict]],
) -> None:
    """Compute expected_points and render the impact string in place.

    Recs declare ``_target_score`` (the factor score after the fix) via
    the ``target`` helper. Recs without one — cosmetic issues outside
    the factor scales, positive callouts, LLM-verdict factors where no
    honest counterfactual exists — keep ``expected_points: None`` and
    keep their qualitative impact text.
    """
    target = rec.pop("_target_score", None)
    if target is None:
        rec["expected_points"] = None
        return
    lens = rec["pillar"]
    factors = lens_factors.get(lens) or {}
    delta = expected_lens_delta(lens, factors, rec["factor"], target)
    rec["expected_points"] = delta
    if delta is not None:
        rec["impact"] = f"{lens.upper()} +{delta:.1f} points"


def _sort_recommendations(recs: list[dict[str, Any]]) -> None:
    """Sort by expected impact first, priority band as tiebreak.

    Recs with an expected-points value outrank recs without one inside
    the same band; positive callouts (low band, no points) sink to the
    end regardless of insertion order.
    """
    priority_order = {"high": 0, "medium": 1, "low": 2}

    def sort_key(r: dict[str, Any]) -> tuple[int, int, float]:
        band = priority_order.get(r.get("priority", "medium"), 1)
        pts = r.get("expected_points")
        has_pts = 0 if pts is not None else 1
        return (band, has_pts, -(pts or 0.0))

    recs.sort(key=sort_key)


def target(current: float | None, gain: float) -> float:
    """Factor score after the fix: current plus the additive gain, capped.

    ``current`` is the factor's present score (None when the factor is
    not yet measured — e.g. the page-scope crawl_access placeholder);
    the fix introduces it at its additive value.
    """
    base = current if current is not None else 0.0
    return min(100.0, base + gain)


def _factor_score(factors: dict[str, dict], key: str) -> float | None:
    """Current numeric score for a factor, or None."""
    return (factors.get(key) or {}).get("score")


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
    current = _factor_score(aeo_factors, "schema")

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
                "impact": "AEO +5-10 points estimated",
                # Coverage percentage is the factor's base score; a solid
                # fix reaches 80% coverage.
                "_target_score": 80.0,
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
                "impact": "AEO +5-8 points estimated",
                # FAQPage bonus: +10 in the factor's additive scale.
                "_target_score": target(current, 10.0),
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
                # Band threshold: lift the site average out of "weak".
                "_target_score": 70.0,
            }
        )

    recs.extend(_thin_pages_recs(aeo_factors, content_raw))
    return recs


def _thin_pages_recs(
    aeo_factors: dict[str, dict], content_raw: dict
) -> list[dict[str, Any]]:
    """Recommendation for pages scoring below the weak-band boundary (40)."""
    # Thin pages — below the "broken"/"weak" band boundary (40)
    per_page = content_raw.get("per_page_scores") or {}
    thin_pages = [u for u, s in per_page.items() if s < 40]
    if not thin_pages:
        return []
    examples = ", ".join(thin_pages[:3])
    # Lifting each thin page to the strong-band boundary (70) raises the
    # site average proportionally to the share of thin pages.
    fixed_avg = _site_average_after_fix(per_page, thin_pages, 70.0)
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
            "_target_score": fixed_avg,
        }
    ]


def _site_average_after_fix(
    per_page: dict[str, float],
    fixed_urls: list[str],
    fixed_score: float,
) -> float:
    """Site-average structure score after lifting the fixed pages."""
    if not per_page:
        return 0.0
    total = sum(fixed_score if u in fixed_urls else s for u, s in per_page.items())
    return min(100.0, total / len(per_page))


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
    # sameAs presence earns +10 in the E-E-A-T additive scale (and feeds
    # topical_authority's sameAs term, conservatively ignored here).
    eeat_current = _factor_score(geo_factors, "eeat")
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
            "_target_score": target(eeat_current, 10.0),
        }
    ]


def _eeat_recs(geo_factors: dict[str, dict]) -> list[dict[str, Any]]:
    """Recommendations for missing Organization and author schema."""
    eeat_raw = _factor_raw(geo_factors, "eeat")
    eeat_current = _factor_score(geo_factors, "eeat")
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
                # Organization presence: +15 in the E-E-A-T additive scale.
                "_target_score": target(eeat_current, 15.0),
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
                # Author presence: +15 in the E-E-A-T additive scale.
                "_target_score": target(eeat_current, 15.0),
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
                # Introducing the factor at its robots+llms+sitemap
                # structural value when the fix is "run the domain crawl".
                "_target_score": target(crawl_score, 46.0),
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
            # 500+ average words reaches the 50-point band in
            # _avg_words_score; code/tables bonuses are not assumed.
            "_target_score": target(_factor_score(geo_factors, "content_depth"), 20.0),
        }
    ]


def _crawl_access_recs(crawl_access: dict) -> list[dict[str, Any]]:
    """Recommendations derived from the raw crawl-access data (llms.txt/robots)."""
    cr = crawl_access["raw"]
    current = crawl_access.get("score")
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
                # llms.txt +7 and llms-full.txt +8 in the additive scale.
                "_target_score": target(current, 15.0),
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
                # robots.txt existence: +8 in the additive scale.
                "_target_score": target(current, 8.0),
            }
        )
    return recs


def _payload_recommendations(
    payload: dict[str, Any],
    aeo_factors: dict[str, dict],
) -> list[dict[str, Any]]:
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
                # Metadata hygiene: no factor-scale counterfactual.
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
                # Metadata hygiene: no factor-scale counterfactual.
                "impact": "AEO +2-3 points estimated",
            }
        )

    recs.extend(_image_alt_recs(payload, aeo_factors))

    return recs


def _image_alt_recs(
    payload: dict[str, Any],
    aeo_factors: dict[str, dict],
) -> list[dict[str, Any]]:
    """Site-wide image alt-text recommendation."""
    total_imgs, imgs_with_alt = _site_image_counts(payload)
    if total_imgs <= 0 or (imgs_with_alt / total_imgs) >= 0.5:
        return []
    missing = total_imgs - imgs_with_alt
    # Alt coverage ≥80% earns the +10 alt points per affected page; the
    # site average rises by the pages' share of the +10.
    current = _factor_score(aeo_factors, "content_structure")
    fixed_avg = _alt_fix_target(aeo_factors, total_imgs, imgs_with_alt, current)
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
            "_target_score": fixed_avg,
        }
    ]


def _alt_fix_target(
    aeo_factors: dict[str, dict],
    total_imgs: int,
    imgs_with_alt: int,
    current: float | None,
) -> float | None:
    """Content-structure target once alt coverage reaches 80%."""
    if current is None:
        return None
    per_page = (
        _factor_raw(aeo_factors, "content_structure").get("per_page_scores") or {}
    )
    if not per_page:
        return None
    # Pages missing the 10-point alt bonus are those below 80% coverage;
    # approximate the share by the global alt gap.
    alt_gap = 1.0 - (imgs_with_alt / total_imgs) if total_imgs else 0.0
    gain = 10.0 * alt_gap
    return min(100.0, current + gain)


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
    # Filling all four identity fields lifts field_score to 100 (40% of
    # the factor); clarity/density gains are LLM verdicts, so predict
    # conservatively from the fields alone.
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
            "_target_score": target(hc.get("score"), 40.0),
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
    if weaknesses:
        examples = ", ".join(weaknesses[:3])
        recs.append(
            {
                "factor": "content_delta",
                "priority": "high",
                "title": f"Address {len(weaknesses)} content gap(s)",
                "detail": (
                    f"AI found gaps: {examples}. "
                    "Add missing product info, pricing, use cases, "
                    "or company details."
                ),
                "impact": "AAX +10-20 points estimated",
                # Richness carries 40% of the factor; coherence/completeness
                # are LLM verdicts, so predict from richness alone.
                "_target_score": target(cd.get("score"), 40.0),
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
                "_target_score": 70.0,
            }
        )
    return recs


def _meta_optimization_rec(aax_factors: dict[str, dict]) -> list[dict[str, Any]]:
    """Recommendation for weak/confusing metadata."""
    mo = aax_factors.get("meta_optimization")
    if not mo:
        return []
    mo_raw = mo.get("raw") or {}
    suggestions = _meta_suggestions(mo_raw)
    issues = _meta_issues(mo_raw)
    if not issues:
        return []
    if suggestions:
        detail = (
            "AI found specific metadata gaps: "
            + " ".join(suggestions[:3])
            + " Use clear, descriptive titles and meta tags for LLM "
            "understanding."
        )
    else:
        detail = (
            f"Improve: {', '.join(issues)}. Use clear, "
            "descriptive titles and meta tags for LLM "
            "understanding."
        )
    return [
        {
            "factor": "meta_optimization",
            "priority": "medium",
            "title": "Optimize metadata for AI crawlers",
            "detail": detail,
            "impact": "AAX +10-15 points estimated",
            # Complete+clear+optimized+click is the perfect-verdict
            # factor score; predict the verdict-scale midpoint of the
            # weak fields (50 each) rather than assume perfection.
            "_target_score": target(mo.get("score"), 25.0),
        }
    ]


def _meta_suggestions(mo_raw: dict) -> list[str]:
    """Trimmed, non-empty improvement suggestions from the meta raw data."""
    return [
        s.strip()
        for s in (mo_raw.get("improvement_suggestions") or [])
        if s and s.strip()
    ]


def _meta_issues(mo_raw: dict) -> list[str]:
    """Issue labels for the metadata weaknesses found.

    Prefers the LLM's concrete ``improvement_suggestions`` when populated;
    falls back to weak-structure labels derived from the verdict fields.
    """
    suggestions = _meta_suggestions(mo_raw)
    if suggestions:
        return suggestions[:3]

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
                # llms-full.txt moves the factor 60 → 100.
                "_target_score": target(llms.get("score"), 40.0),
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
                # A sales contact with high confidence scores
                # presence 30 + type 25 + best 10 + 90*0.35 ≈ 96.5;
                # predict a general contact at medium confidence.
                "_target_score": target(ev.get("score"), 50.0),
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
    # Each missing contact signal has a fixed additive value in the
    # contactability heuristic (email 20/5, mailto 10, contact page 10,
    # ContactPoint 15, social 10); predict the sum of the missing ones.
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
            "_target_score": _contactability_fix_target(contactability),
        }
    ]


# Additive point values of the contactability heuristic's signals.
_CONTACT_SIGNAL_POINTS: dict[str, float] = {
    "has_email": 20.0,
    "has_mailto": 10.0,
    "has_contact_page": 10.0,
    "has_social_links": 10.0,
    "has_contact_point_schema": 15.0,
}


def _contactability_fix_target(contactability: dict[str, Any]) -> float:
    """Contactability factor score once the missing signals are added."""
    current = contactability.get("score")
    base = float(current) if current is not None else 0.0
    gain = sum(
        pts
        for signal, pts in _CONTACT_SIGNAL_POINTS.items()
        if not contactability.get(signal)
    )
    return min(100.0, base + gain)


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
