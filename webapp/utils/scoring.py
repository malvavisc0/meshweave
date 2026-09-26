"""Shared AEO/GEO scoring helpers for templates and API responses."""

import os
from typing import Any

from meshweave.scoring.composite import LENS_WEIGHTS
from meshweave.scoring.interpretation import interpret_profile


def aax_pending(crawl: Any) -> bool:
    """True when AAX is enabled but has not finished for this crawl.

    Uses the durable aax_status column on the Crawl row. "Finished" includes
    terminal failure: a stored AAX status of "failed" or "disabled" must
    clear the pending state, or the result page shows "Running AI Analysis…"
    forever for a crawl whose analysis crashed (e.g. interrupted by a
    service restart).
    """
    if os.getenv("AAX_ENABLED", "false").lower() != "true":
        return False
    # A failed or cancelled crawl will never produce an AAX analysis;
    # without this guard the result page shows "Running AI Analysis…"
    # forever on a dead job.
    status = str(getattr(crawl, "status", "") or "").lower()
    if status not in ("pending", "running", "succeeded"):
        return False
    # Use the durable aax_status column — no need to query ScoreSnapshot
    aax_status = str(getattr(crawl, "aax_status", "") or "").lower()
    return aax_status in ("pending", "running")


# Mapping of factor keys to human-readable display names
FACTOR_DISPLAY_NAMES = {
    "answerability": "Answerability",
    "schema": "Schema Implementation",
    "content_structure": "Content Structure",
    "freshness": "Freshness",
    "topical_authority": "Topical Authority",
    "eeat": "E-E-A-T Signals",
    "crawl_access": "LLM Crawl Accessibility",
    "content_depth": "Content Depth",
    "entity_consistency": "Entity Consistency",
    # AAX factors
    "homepage_comprehension": "Homepage Comprehension",
    "meta_optimization": "Meta Optimization",
    "content_delta": "Content Delta",
    "email_validation": "Email Validation",
    "contactability": "Contactability",
}

PRIORITY_NUMERIC = {
    "high": 0,
    "medium": 1,
    "low": 2,
    "info": 3,
}


def score_implication(pillar: str, score: float | None) -> str:
    """Return a capability statement for a pillar score."""
    if score is None:
        return ""
    s = max(0, min(100, int(round(score))))

    implications = {
        "aeo": [
            (0, 25, "Not extractable."),
            (26, 45, "Limited extractability."),
            (46, 65, "Partially extractable."),
            (66, 85, "Reliably extractable."),
            (86, 100, "Fully extractable."),
        ],
        "geo": [
            (0, 25, "Unreachable."),
            (26, 45, "Fragmented machine context."),
            (46, 65, "Reachable but inconsistently connected."),
            (66, 85, "Consistently connected."),
            (86, 100, "Fully connected."),
        ],
        "aax": [
            (0, 24, "Not usable."),
            (25, 39, "Significant friction."),
            (40, 59, "Partial usability."),
            (60, 79, "Reliably usable."),
            (80, 100, "Fully agent-ready."),
        ],
    }

    bands = implications.get(pillar, [])
    for lo, hi, text in bands:
        if lo <= s <= hi:
            return text
    return ""


def rating_class(rating: str | None) -> str:
    mapping = {
        # AEO extractability bands
        "Not extractable": "rating-low",
        "Limited extractability": "rating-low",
        "Partially extractable": "rating-ok",
        "Reliably extractable": "rating-good",
        "Fully extractable": "rating-excellent",
        # GEO connectivity bands
        "Unreachable": "rating-low",
        "Fragmented": "rating-ok",
        "Reachable": "rating-ok",
        "Connected": "rating-good",
        "Fully connected": "rating-excellent",
        # AAX ratings (conservative: green only for "Fluent" 80+)
        "Opaque": "rating-low",
        "Unclear": "rating-low",
        "Readable": "rating-ok",
        "Clear": "rating-good",
        "Fluent": "rating-excellent",
    }
    return mapping.get(rating, "") if rating else ""


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
                    "weight": LENS_WEIGHTS.get(section_key, {}).get(key, 0),
                    "display_name": FACTOR_DISPLAY_NAMES.get(
                        key, key.replace("_", " ").title()
                    ),
                    "bar_color": bar_color(None),
                    "note": reason,
                    "skipped": True,
                }

        result[section_key] = {
            "composite": section.get("composite"),
            "rating": section.get("rating"),
            "factors": enriched_factors,
        }
    recs = list(score_data.get("recommendations", []))
    for rec in recs:
        rec["priority_numeric"] = PRIORITY_NUMERIC.get(rec.get("priority", "medium"), 1)
    result["recommendations"] = recs
    return result


def group_recommendations_by_pillar(
    recommendations: list[dict],
) -> dict[str, list[dict]]:
    """Group recommendations by their pillar (aeo, geo, aax).

    Returns a dict with keys 'aeo', 'geo', 'aax', each mapping
    to a list of recommendations sorted by priority.
    """
    groups: dict[str, list[dict]] = {"aeo": [], "geo": [], "aax": []}
    for rec in recommendations:
        pillar = rec.get("pillar", "aeo")
        groups.setdefault(pillar, []).append(rec)
    # Sort each group by priority
    for key in groups:
        groups[key].sort(
            key=lambda r: PRIORITY_NUMERIC.get(r.get("priority", "medium"), 1)
        )
    return groups


def _sorted_recommendations(ss: dict | None) -> list[dict]:
    """Pre-compute sorted recommendations for the template.

    Sorts by priority (high→low) then by weakest pillar first. Kept in this
    pure module (no webapp stack) so the export serializer can reuse it.
    """
    if not ss or not ss.get("recommendations"):
        return []
    pillar_scores = {
        "aeo": ss.get("aeo_score") or 100,
        "geo": ss.get("geo_score") or 100,
        "aax": ss.get("aax_score") or 100,
    }
    pillar_rank = {
        k: i
        for i, (k, _) in enumerate(sorted(pillar_scores.items(), key=lambda x: x[1]))
    }
    return sorted(
        ss["recommendations"],
        key=lambda r: (
            PRIORITY_NUMERIC.get(r.get("priority", "medium"), 1),
            pillar_rank.get(r.get("pillar", ""), 99),
        ),
    )


def build_score_snapshot_context(crawl) -> dict | None:
    """Build the score_snapshot template context dict from a Crawl row."""
    snapshot = getattr(crawl, "score_snapshot", None)
    if not snapshot:
        return None
    score_data = snapshot.score_json or {}
    score_data_enriched = build_score_data_for_template(score_data)

    # AAX section
    aax_section = score_data.get("aax", {})

    # AAX AI analysis raw data (for diagnostic section)
    ai_analysis = snapshot.ai_analysis_json or {}
    aax_analysis = ai_analysis.get("aax") or {}

    aax_composite = aax_section.get("composite")
    interp = interpret_profile(
        snapshot.aeo_score,
        snapshot.geo_score,
        aax_composite,
    )

    return {
        "crawl_id": crawl.id,
        "aeo_score": snapshot.aeo_score,
        "geo_score": snapshot.geo_score,
        "aeo_rating": snapshot.aeo_rating or "Unknown",
        "geo_rating": snapshot.geo_rating or "Unknown",
        "aeo_rating_class": rating_class(snapshot.aeo_rating),
        "geo_rating_class": rating_class(snapshot.geo_rating),
        "score_data": score_data_enriched,
        "recommendations": score_data_enriched.get("recommendations", []),
        # AAX fields
        "aax_score": aax_composite,
        "aax_rating": aax_section.get("rating", "Unknown"),
        "aax_rating_class": rating_class(aax_section.get("rating")),
        # AAX raw analysis data for diagnostic section
        "aax_analysis": aax_analysis,
        # Capability implication statements
        "aeo_implication": score_implication("aeo", snapshot.aeo_score),
        "geo_implication": score_implication("geo", snapshot.geo_score),
        "aax_implication": score_implication("aax", aax_composite),
        # Interpretation matrix result
        "interpretation": interp,
    }
