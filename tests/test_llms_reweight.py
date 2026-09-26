"""Tests pinning llms.txt's place in the scoring model.

llms.txt evidence is counted once, inside GEO's crawl_access factor. It
is not an AAX factor, and adding or removing the file must never move
any composite outside GEO.
"""

from meshweave.scoring.composite import (
    AAX_WEIGHTS,
    AEO_WEIGHTS,
    GEO_WEIGHTS,
    SCORING_VERSION,
)
from meshweave.scoring.engine import compute_aax_score, compute_scores

_WITH_LLMS = {
    "llms_txt": {"exists": True, "size": 200},
    "llms_full_txt": {"exists": True},
}
_WITHOUT_LLMS = {
    "llms_txt": {"exists": False},
    "llms_full_txt": {"exists": False},
}


def _payload(llms: dict) -> dict:
    return {
        "robots": {
            "exists": True,
            "bots": {
                "GPTBot": "allowed",
                "ClaudeBot": "allowed",
                "PerplexityBot": "allowed",
            },
            "sitemaps": ["https://example.com/sitemap.xml"],
        },
        "llms_txt": llms,
    }


def _aax_result(llms: dict) -> dict:
    return {
        "status": "completed",
        "homepage_comprehension": {
            "brand": "Acme",
            "product": "Widgets",
            "target_audience": "Builders",
            "key_features": ["fast", "solid", "cheap", "small"],
            "call_to_action": "Buy",
            "clarity": "clear",
            "information_density": "dense",
            "would_remember": True,
        },
        "meta_optimization": {
            "completeness": "complete",
            "clarity": "clear",
            "llm_optimization": "optimized",
            "would_click_through": True,
        },
        "content_delta": {
            "company": {"name": "Acme", "description": "Great"},
            "product": {
                "name": "Widgets",
                "description": "Best",
                "features": ["A", "B"],
            },
            "pricing": {"model": "subscription"},
            "target_audience": "All",
            "strengths": ["Fast"],
            "coherence": "consistent",
            "completeness": "comprehensive",
        },
        "contactability": {"score": 75.0},
        "email_validation": {
            "valid_contacts": [
                {"email": "sales@acme.com", "contact_type": "sales"},
            ],
            "confidence": "high",
            "best_contact": "sales@acme.com",
        },
        "llms_txt": llms,
        "tests_completed": 5,
        "tests_skipped": 0,
    }


class TestWeightPins:
    def test_weights_sum_to_one(self):
        assert abs(sum(AEO_WEIGHTS.values()) - 1.0) < 0.001
        assert abs(sum(GEO_WEIGHTS.values()) - 1.0) < 0.001
        assert abs(sum(AAX_WEIGHTS.values()) - 1.0) < 0.001

    def test_aax_weights(self):
        assert AAX_WEIGHTS == {
            "homepage_comprehension": 0.35,
            "content_delta": 0.25,
            "meta_optimization": 0.15,
            "contactability": 0.15,
            "email_validation": 0.10,
        }

    def test_llms_txt_is_not_an_aax_factor(self):
        assert "llms_txt" not in AAX_WEIGHTS

    def test_scoring_version_pin(self):
        assert SCORING_VERSION == "1.3"


class TestLlmsCountedOnce:
    def test_aax_ignores_llms_data(self):
        with_llms = compute_aax_score(_aax_result(_WITH_LLMS))
        without_llms = compute_aax_score(_aax_result(_WITHOUT_LLMS))
        assert "llms_txt" not in (with_llms.get("factors") or {})
        assert with_llms["composite"] == without_llms["composite"]

    def test_site_without_llms_loses_nothing_outside_crawl_access(self):
        with_llms = compute_scores(_payload(_WITH_LLMS))
        without_llms = compute_scores(_payload(_WITHOUT_LLMS))
        # Only the GEO crawl_access factor may differ.
        assert with_llms["aeo"] == without_llms["aeo"]
        for key in GEO_WEIGHTS:
            if key != "crawl_access":
                assert (
                    with_llms["geo"]["factors"][key]
                    == without_llms["geo"]["factors"][key]
                )

    def test_publishing_llms_txt_moves_geo_only(self):
        with_llms = compute_scores(_payload(_WITH_LLMS))
        without_llms = compute_scores(_payload(_WITHOUT_LLMS))
        assert with_llms["geo"]["composite"] > without_llms["geo"]["composite"]
        assert with_llms["aeo"]["composite"] == without_llms["aeo"]["composite"]
