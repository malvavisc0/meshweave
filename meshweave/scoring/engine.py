"""Scoring engine orchestrator.

Calls AEO and GEO factor scorers, computes composite scores,
generates recommendations, and returns the full score_json.
"""

from __future__ import annotations

import logging
from typing import Any

from meshweave.scoring import aeo as aeo_mod
from meshweave.scoring import geo as geo_mod
from meshweave.scoring.composite import (
    AAX_WEIGHTS,
    AEO_WEIGHTS,
    GEO_WEIGHTS,
)
from meshweave.scoring.composite import (
    weighted_composite as _weighted_composite,
)
from meshweave.scoring.ratings import aax_rating, aeo_rating, geo_rating
from meshweave.scoring.recommendations import generate_recommendations

logger = logging.getLogger(__name__)

__all__ = [
    "AAX_WEIGHTS",
    "AEO_WEIGHTS",
    "GEO_WEIGHTS",
    "compute_aax_score",
    "compute_scores",
]


def compute_scores(
    payload: dict,
    aax_factors: dict[str, dict] | None = None,
) -> dict[str, Any]:
    """Compute AEO and GEO scores from a crawl payload.

    Args:
        payload: The crawl result payload (JSON-parsed).
        aax_factors: Optional AAX factor dicts for generating AAX
            recommendations.

    Returns:
        Full score_json dict.
    """
    # --- AEO factors ---
    # The answerability factor is part of the model but not yet computed;
    # the composite re-normalizes across the factors below until its
    # grounded test lands.
    aeo_factors: dict[str, dict] = {
        "schema": aeo_mod.score_schema(payload),
        "content_structure": aeo_mod.score_content_structure(payload),
        "freshness": aeo_mod.score_freshness(payload),
    }

    # --- GEO factors ---
    geo_factors: dict[str, dict] = {
        "topical_authority": geo_mod.score_topical_authority(payload),
        "eeat": geo_mod.score_eeat(payload),
        "crawl_access": geo_mod.score_crawl_access(payload),
        "content_depth": geo_mod.score_content_depth(payload),
        "entity_consistency": geo_mod.score_entity_consistency(payload),
    }

    # --- Composite scores ---
    aeo_composite = _weighted_composite(aeo_factors, AEO_WEIGHTS)
    geo_composite = _weighted_composite(geo_factors, GEO_WEIGHTS)

    # --- Recommendations ---
    recommendations = generate_recommendations(
        aeo_factors, geo_factors, payload=payload, aax_factors=aax_factors
    )

    # --- Build score_json ---
    return {
        "aeo": {
            "composite": aeo_composite,
            "rating": aeo_rating(aeo_composite),
            "factors": aeo_factors,
        },
        "geo": {
            "composite": geo_composite,
            "rating": geo_rating(geo_composite),
            "factors": geo_factors,
        },
        "recommendations": recommendations,
    }


def compute_aax_score(aax_result: dict[str, Any]) -> dict[str, Any] | None:
    """Compute AAX composite score from AAX analysis results.

    Takes the output of run_aax_analysis() and produces a score_json
    section for AAX.

    Returns None if AAX is disabled or has no completed tests.
    """
    if not _aax_completed(aax_result):
        return None

    # Score each test using categorical mappings
    factors: dict[str, dict] = {}
    _add_homepage_comprehension_factor(factors, aax_result)
    _add_meta_optimization_factor(factors, aax_result)
    _add_content_delta_factor(factors, aax_result)
    _add_email_validation_factor(factors, aax_result)
    _add_contactability_factor(factors, aax_result)

    if not factors:
        return None

    composite = _weighted_composite(factors, AAX_WEIGHTS)

    return {
        "composite": composite,
        "rating": aax_rating(composite),
        "factors": factors,
        "contactability": aax_result.get("contactability"),
        "skip_reasons": aax_result.get("skip_reasons", {}),
        "tests_completed": aax_result.get("tests_completed", 0),
        "tests_skipped": aax_result.get("tests_skipped", 0),
        "model_id": aax_result.get("model_id", ""),
    }


def _aax_completed(aax_result: dict[str, Any]) -> bool:
    """True when the AAX analysis ran to completion."""
    return bool(aax_result) and aax_result.get("status") == "completed"


def _add_homepage_comprehension_factor(
    factors: dict[str, dict],
    aax_result: dict[str, Any],
) -> None:
    """Score the homepage-comprehension test into *factors*."""
    hc = aax_result.get("homepage_comprehension")
    if not hc:
        return
    from meshweave.ai.runner import CLARITY_MAP, DENSITY_MAP

    clarity = CLARITY_MAP.get(hc.get("clarity", "unclear"), 20)
    density = DENSITY_MAP.get(hc.get("information_density", "sparse"), 30)
    remember = 100 if hc.get("would_remember") else 0
    fields_filled = sum(
        1
        for k in ("brand", "product", "target_audience", "call_to_action")
        if hc.get(k)
    )
    field_score = (fields_filled / 4) * 100
    features_score = min(len(hc.get("key_features", [])) * 15, 60)
    factors["homepage_comprehension"] = {
        "score": min(
            100.0,
            float(
                field_score * 0.4
                + clarity * 0.2
                + density * 0.2
                + features_score * 0.1
                + remember * 0.1
            ),
        ),
        "weight": AAX_WEIGHTS["homepage_comprehension"],
        "auto_measurable": True,
        "raw": hc,
    }


def _add_meta_optimization_factor(
    factors: dict[str, dict],
    aax_result: dict[str, Any],
) -> None:
    """Score the meta-optimization test into *factors*."""
    mo = aax_result.get("meta_optimization")
    if not mo:
        return
    from meshweave.ai.runner import CLARITY_MAP, COMPLETENESS_MAP, LLM_OPT_MAP

    completeness = COMPLETENESS_MAP.get(mo.get("completeness", "minimal"), 20)
    clarity = CLARITY_MAP.get(mo.get("clarity", "unclear"), 20)
    llm_opt = LLM_OPT_MAP.get(mo.get("llm_optimization", "poor"), 20)
    click = 100 if mo.get("would_click_through") else 0
    factors["meta_optimization"] = {
        "score": min(
            100.0,
            float(completeness * 0.35 + clarity * 0.25 + llm_opt * 0.25 + click * 0.15),
        ),
        "weight": AAX_WEIGHTS["meta_optimization"],
        "auto_measurable": True,
        "raw": mo,
    }


def _add_content_delta_factor(
    factors: dict[str, dict],
    aax_result: dict[str, Any],
) -> None:
    """Score the content-delta test into *factors*."""
    cd = aax_result.get("content_delta")
    if not cd:
        return
    from meshweave.ai.runner import COHERENCE_MAP, CONTENT_COMPLETENESS_MAP

    coherence = COHERENCE_MAP.get(cd.get("coherence", "somewhat_consistent"), 60)
    completeness = CONTENT_COMPLETENESS_MAP.get(
        cd.get("completeness", "incomplete"), 20
    )
    # Info richness: how many fields were extracted
    richness_score = _content_richness_score(cd)
    factors["content_delta"] = {
        "score": min(
            100.0,
            float(richness_score * 0.4 + coherence * 0.3 + completeness * 0.3),
        ),
        "weight": AAX_WEIGHTS["content_delta"],
        "auto_measurable": True,
        "raw": cd,
    }


def _content_richness_score(cd: dict[str, Any]) -> float:
    """Score for how many extractable content fields the pages filled."""
    product = cd.get("product") or {}
    pricing = cd.get("pricing") or {}
    richness = sum(
        [
            bool((cd.get("company") or {}).get("name")),
            bool(product.get("name")),
            bool(pricing.get("model")),
            bool(cd.get("target_audience")),
            bool(cd.get("strengths")),
        ]
    )
    return (richness / 5) * 100


def _add_email_validation_factor(
    factors: dict[str, dict],
    aax_result: dict[str, Any],
) -> None:
    """Score the email-validation test into *factors*."""
    ev = aax_result.get("email_validation")
    if not ev:
        return
    from meshweave.ai.runner import CONFIDENCE_MAP

    confidence = CONFIDENCE_MAP.get(ev.get("confidence", "low"), 30)
    contacts = ev.get("valid_contacts") or []
    presence = _email_presence_points(contacts)
    best_type = _email_type_points(contacts)
    has_best = 10 if ev.get("best_contact") else 0
    factors["email_validation"] = {
        "score": min(
            100.0,
            float(presence + best_type + confidence * 0.35 + has_best),
        ),
        "weight": AAX_WEIGHTS["email_validation"],
        "auto_measurable": True,
        "raw": ev,
    }


def _add_contactability_factor(
    factors: dict[str, dict],
    aax_result: dict[str, Any],
) -> None:
    """Score the contactability heuristic into *factors*."""
    ct = aax_result["contactability"]
    factors["contactability"] = {
        "score": float(ct["score"]),
        "weight": AAX_WEIGHTS["contactability"],
        "auto_measurable": True,
        "raw": ct,
    }


def _email_presence_points(contacts: list) -> float:
    """Presence points that saturate after the second valid contact."""
    if not contacts:
        return 0.0
    # Presence saturates quickly: one contact earns most of the
    # presence points, a second adds a little, more add nothing —
    # quantity must not outweigh quality.
    return min(30.0, 20.0 + 10.0 * min(len(contacts) - 1, 1))


# Best contact-type score awarded for a valid contact
_EMAIL_TYPE_SCORES: dict[str, int] = {
    "sales": 25,
    "support": 20,
    "general": 15,
    "legal": 5,
    "invalid": 0,
}


def _email_type_points(contacts: list) -> int:
    """Best contact-type score among the valid contacts."""
    return max(
        (_EMAIL_TYPE_SCORES.get(c.get("contact_type", "invalid"), 0) for c in contacts),
        default=0,
    )
