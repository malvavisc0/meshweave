"""Shared composite math: lens weights, weighted composites, deltas.

Lives outside ``engine.py`` so ``recommendations.py`` can compute
model-derived expected deltas without importing the engine (which
imports the recommendation generator, creating a cycle).
"""

from __future__ import annotations

import logging

AEO_WEIGHTS: dict[str, float] = {
    "capture_rate": 0.30,
    "schema": 0.20,
    "content_structure": 0.20,
    "query_match": 0.15,
    "voice_rate": 0.10,
    "freshness": 0.05,
}

GEO_WEIGHTS: dict[str, float] = {
    "citation": 0.30,
    "topical_authority": 0.20,
    "eeat": 0.15,
    "crawl_access": 0.15,
    "content_depth": 0.10,
    "entity_consistency": 0.10,
}

# AAX factor weights (sum to 1.0). v1.1: llms.txt 15% → 5% — an
# optional, emerging file must not dominate the agent-experience lens
# (and its presence already earns points in GEO's crawl_access factor).
# The 10 points moved to homepage comprehension (30 → 35, the strongest
# agent-utility signal) and contactability (5 → 10, real actionability).
AAX_WEIGHTS: dict[str, float] = {
    "homepage_comprehension": 0.35,
    "meta_optimization": 0.20,
    "content_delta": 0.20,
    "llms_txt": 0.05,
    "email_validation": 0.10,
    "contactability": 0.10,
}

# Bumped when weights or factor formulas change so the diff page can
# flag cross-version comparisons. 1.1: AAX llms.txt reweight.
SCORING_VERSION = "1.1"

LENS_WEIGHTS: dict[str, dict[str, float]] = {
    "aeo": AEO_WEIGHTS,
    "geo": GEO_WEIGHTS,
    "aax": AAX_WEIGHTS,
}

logger = logging.getLogger(__name__)


def weighted_composite(
    factors: dict[str, dict],
    weights: dict[str, float],
) -> float | None:
    """Compute weighted composite, re-normalizing for available factors.

    Returns None if no factors have scores.
    """
    scored: dict[str, float] = {}
    for key, factor in factors.items():
        score = factor.get("score")
        if score is not None and key in weights:
            scored[key] = float(score)
        elif score is not None:
            logger.debug(
                "Factor %r has score %.1f but no weight — excluded",
                key,
                score,
            )

    if not scored:
        return None

    total_weight = sum(weights[k] for k in scored)
    if total_weight <= 0:
        return None

    composite = sum(scored[k] * weights[k] / total_weight for k in scored)

    # Calibration curve: compress the upper range so average sites don't
    # score artificially high. Power 1.15 maps 80→77, 70→66, 60→56,
    # 50→45, 40→35 while leaving 100 untouched. Compress more by raising
    # the exponent (e.g. 1.4 gives 80→73, 70→61, 60→49).
    calibrated = 100.0 * (float(composite) / 100.0) ** 1.15
    return float(round(min(100.0, calibrated), 1))


def expected_lens_delta(
    lens: str,
    factors: dict[str, dict],
    factor_key: str,
    target_score: float,
) -> float | None:
    """Model-derived composite delta when one factor improves to a target.

    Computes the lens composite twice — once with the factor's current
    score and once with it replaced by *target_score* — and returns the
    calibrated difference. Reuses ``weighted_composite`` so the
    prediction shares the exact renormalization and curve semantics of
    the real score: fix the factor, re-run, and the observed composite
    delta should match this prediction.

    Args:
        lens: Lens the factor belongs to ("aeo" | "geo" | "aax").
        factors: Current factor dicts for that lens (scores may be None).
        factor_key: The factor the recommendation improves.
        target_score: The factor score after the fix (0-100).

    Returns:
        The expected composite delta in points, or None when the lens or
        factor is unknown or the current composite is not computable.
    """
    weights = LENS_WEIGHTS.get(lens)
    if weights is None or factor_key not in weights:
        return None

    current_composite = weighted_composite(factors, weights)
    if current_composite is None:
        return None

    counterfactual = {
        k: ({"score": min(100.0, float(target_score))} if k == factor_key else v)
        for k, v in factors.items()
    }
    target_composite = weighted_composite(counterfactual, weights)
    if target_composite is None:
        return None

    return round(target_composite - current_composite, 1)
