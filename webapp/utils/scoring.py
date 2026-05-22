"""Shared AEO/GEO scoring helpers for templates and API responses."""

from typing import Any

# Mapping of factor keys to human-readable display names
FACTOR_DISPLAY_NAMES = {
    "schema": "Schema Implementation",
    "content_structure": "Content Structure",
    "freshness": "Freshness",
    "capture_rate": "Snippet / AI Overview Capture Rate",
    "query_match": "Query Match Precision",
    "voice_rate": "Voice Selection Rate",
    "citation": "AI Citation Frequency & Quality",
    "topical_authority": "Topical Authority",
    "eeat": "E-E-A-T Signals",
    "crawl_access": "LLM Crawl Accessibility",
    "content_depth": "Content Depth",
    "entity_consistency": "Entity Consistency",
    # AAX factors
    "homepage_comprehension": "Homepage Comprehension",
    "meta_optimization": "Meta Optimization",
    "content_delta": "Content Delta",
    "llms_txt": "llms.txt",
    "email_validation": "Email Validation",
}

PRIORITY_NUMERIC = {
    "high": 0,
    "medium": 1,
    "low": 2,
    "info": 3,
}

MANUAL_INPUT_SPECS = {
    "capture_rate": {
        "label": "Snippet / AI Overview Capture Rate",
        "weight": 0.30,
        "guidance": (
            "Enter % of target keywords where you appear in featured snippets "
            "or AI Overviews (check Google Search Console → Performance → "
            "Search Appearance)."
        ),
        "placeholder": "0-100",
    },
    "query_match": {
        "label": "Query Match Precision",
        "weight": 0.15,
        "guidance": (
            "Estimate how closely your content matches natural language "
            "questions. Check 'People Also Ask' for your target keywords."
        ),
        "placeholder": "0-100",
    },
    "voice_rate": {
        "label": "Voice Selection Rate",
        "weight": 0.10,
        "guidance": (
            "Test target queries on Google Assistant, Siri, and Alexa. "
            "Enter % where your content is the answer."
        ),
        "placeholder": "0-100",
    },
    "citation": {
        "label": "AI Citation Frequency & Quality",
        "weight": 0.30,
        "guidance": (
            "Search your brand on ChatGPT, Claude, and Perplexity. Estimate "
            "how often you're cited. Tier 1 (named+link)=1.0x, "
            "Tier 2 (named)=0.7x, Tier 3 (paraphrased)=0.3x."
        ),
        "placeholder": "0-100",
    },
}


AAX_RATING_CLASSES = {
    "Opaque": "rating-low",
    "Unclear": "rating-low",
    "Readable": "rating-ok",
    "Clear": "rating-good",
    "Fluent": "rating-excellent",
}


def rating_class(rating: str | None) -> str:
    mapping = {
        "Poor": "rating-low",
        "Below Average": "rating-low",
        "Average": "rating-ok",
        "Strong": "rating-good",
        "Excellent": "rating-excellent",
        "Invisible": "rating-low",
        "Emerging": "rating-low",
        "Visible": "rating-ok",
        "Authoritative": "rating-good",
        "Dominant": "rating-excellent",
        # AAX ratings
        "Opaque": "rating-low",
        "Unclear": "rating-low",
        "Readable": "rating-ok",
        "Clear": "rating-good",
        "Fluent": "rating-excellent",
    }
    return mapping.get(rating, "") if rating else ""


def count_auto_factors(factors_section: dict) -> int:
    count = 0
    for factor in (factors_section.get("factors", {})).values():
        if factor.get("score") is not None:
            count += 1
    return count


def has_manual_missing(score_data: dict) -> bool:
    for section in [score_data.get("aeo", {}), score_data.get("geo", {})]:
        for factor in (section.get("factors", {})).values():
            if factor.get("score") is None and factor.get("auto_measurable") is False:
                return True
    return False


def bar_color(score: float | None) -> str:
    if score is None:
        return "var(--color-surface-2)"
    if score >= 70:
        return "var(--color-primary)"
    if score >= 40:
        return "var(--color-semantic-warning)"
    return "var(--color-semantic-error)"


def build_score_data_for_template(score_data: dict) -> dict:
    """Augment raw score_json with display names and bar colors for templates."""
    result: dict[str, Any] = {}
    for section_key in ["aeo", "geo", "aax"]:
        section = score_data.get(section_key, {})
        factors = section.get("factors", {})
        enriched_factors = {}
        for key, factor in factors.items():
            enriched = dict(factor)
            enriched["display_name"] = FACTOR_DISPLAY_NAMES.get(
                key, key.replace("_", " ").title()
            )
            enriched["bar_color"] = bar_color(factor.get("score"))
            enriched_factors[key] = enriched

        # Add skip reasons as notes on skipped factors
        skip_reasons = section.get("skip_reasons", {})
        for key, reason in skip_reasons.items():
            if key not in enriched_factors:
                enriched_factors[key] = {
                    "score": None,
                    "weight": 0,
                    "display_name": FACTOR_DISPLAY_NAMES.get(
                        key, key.replace("_", " ").title()
                    ),
                    "bar_color": bar_color(None),
                    "note": reason,
                    "skipped": True,
                }

        result[section_key] = {
            "composite": section.get("composite"),
            "auto_only_composite": section.get("auto_only_composite"),
            "rating": section.get("rating"),
            "auto_rating": section.get("auto_rating"),
            "factors": enriched_factors,
        }
    recs = list(score_data.get("recommendations", []))
    for rec in recs:
        rec["priority_numeric"] = PRIORITY_NUMERIC.get(rec.get("priority", "medium"), 1)
    result["recommendations"] = recs
    return result


def build_manual_input_fields(score_data: dict) -> list[dict]:
    fields = []
    for section_key in ["aeo", "geo"]:
        section = score_data.get(section_key, {}).get("factors", {})
        for key, factor in section.items():
            if factor.get("score") is None and factor.get("auto_measurable") is False:
                spec = MANUAL_INPUT_SPECS.get(key)
                if spec:
                    fields.append(
                        {
                            "key": key,
                            "label": spec["label"],
                            "weight": factor.get("weight", spec["weight"]),
                            "guidance": spec["guidance"],
                            "placeholder": spec["placeholder"],
                            "current_value": factor.get("user_value"),
                        }
                    )
    return fields


def build_score_snapshot_context(crawl) -> dict | None:
    """Build the score_snapshot template context dict from a Crawl row."""
    snapshot = getattr(crawl, "score_snapshot", None)
    if not snapshot:
        return None
    score_data = snapshot.score_json or {}
    score_data_enriched = build_score_data_for_template(score_data)

    # AAX section
    aax_section = score_data.get("aax", {})
    aax_tests_completed = aax_section.get("tests_completed", 0)
    aax_tests_skipped = aax_section.get("tests_skipped", 0)
    aax_tests_total = aax_tests_completed + aax_tests_skipped

    return {
        "crawl_id": crawl.id,
        "aeo_score": snapshot.aeo_score,
        "geo_score": snapshot.geo_score,
        "aeo_rating": snapshot.aeo_rating or "Unknown",
        "geo_rating": snapshot.geo_rating or "Unknown",
        "aeo_rating_class": rating_class(snapshot.aeo_rating),
        "geo_rating_class": rating_class(snapshot.geo_rating),
        "aeo_auto_count": count_auto_factors(score_data_enriched.get("aeo", {})),
        "geo_auto_count": count_auto_factors(score_data_enriched.get("geo", {})),
        "score_data": score_data_enriched,
        "recommendations": score_data_enriched.get("recommendations", []),
        "manual_input_fields": build_manual_input_fields(score_data),
        "has_manual_missing": has_manual_missing(score_data),
        # AAX fields
        "aax_score": aax_section.get("composite"),
        "aax_rating": aax_section.get("rating", "Unknown"),
        "aax_rating_class": rating_class(aax_section.get("rating")),
        "aax_tests_completed": aax_tests_completed,
        "aax_tests_total": aax_tests_total,
    }
