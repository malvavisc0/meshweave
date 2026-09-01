"""Tests pinning the v1.1 AAX llms.txt reweight.

llms.txt is an optional, emerging convention: a site without one must
not be punished as if it failed agent experience. At 5% weight, a
perfect site missing llms.txt still lands in the Fluent band's
neighborhood; at the old 15% it was capped near 83.
"""

from __future__ import annotations

from meshweave.scoring.composite import (
    AAX_WEIGHTS,
    SCORING_VERSION,
    weighted_composite,
)


def _all_perfect_except(llms_score: float) -> dict[str, dict]:
    return {
        "homepage_comprehension": {"score": 100.0},
        "meta_optimization": {"score": 100.0},
        "content_delta": {"score": 100.0},
        "llms_txt": {"score": llms_score},
        "email_validation": {"score": 100.0},
        "contactability": {"score": 100.0},
    }


class TestAaxLlmsReweight:
    def test_weights_sum_to_one(self):
        assert abs(sum(AAX_WEIGHTS.values()) - 1.0) < 1e-9

    def test_llms_txt_weight_is_light(self):
        assert AAX_WEIGHTS["llms_txt"] == 0.05

    def test_homepage_comprehension_leads(self):
        assert AAX_WEIGHTS["homepage_comprehension"] == 0.35
        assert AAX_WEIGHTS["homepage_comprehension"] == max(AAX_WEIGHTS.values())

    def test_perfect_site_without_llms_txt_scores_high(self):
        composite = weighted_composite(_all_perfect_except(0.0), AAX_WEIGHTS)
        # Old weighting capped this site at 83; the reweight lets a
        # site that fails only the optional-file check clear 90.
        assert composite is not None
        assert composite > 90.0

    def test_llms_fix_incentive_is_proportionate(self):
        """Publishing llms.txt must move AAX by ~3 points, not ~10."""
        base = weighted_composite(_all_perfect_except(0.0), AAX_WEIGHTS)
        fixed = weighted_composite(_all_perfect_except(60.0), AAX_WEIGHTS)
        assert base is not None and fixed is not None
        assert 2.0 <= round(fixed - base, 1) <= 4.0

    def test_scoring_version_bumped(self):
        assert SCORING_VERSION == "1.1"
