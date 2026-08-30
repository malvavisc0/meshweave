"""Business-logic regression tests for the scoring engine.

Covers fixes found in the 2026-08-27 scoring audit:
- freshness start-page double-count
- contactability recommendation availability
- llms.txt recommendation dedupe
- recommendation thresholds aligned with bands
- email_validation quality-over-quantity balance
- sameAs shared bucketing
- schema FAQ bonus requires majority-in-range answers
"""

from meshweave.scoring.aeo import score_freshness, score_schema
from meshweave.scoring.geo import score_entity_consistency
from meshweave.scoring.recommendations import generate_recommendations


def _payload_with_dates():
    """Start page + markdowns where the origin page appears in both."""
    start_jsonld = [{"@type": "WebPage", "dateModified": "2026-08-27"}]
    return {
        "page": {
            "url": "http://x/",
            "jsonld": start_jsonld,
        },
        "markdowns": {
            # Same page as payload["page"] — must be counted once.
            "http://x/": {"page": {"jsonld": start_jsonld}},
            "http://x/old": {
                "page": {"jsonld": [{"@type": "WebPage", "dateModified": "2020-01-01"}]}
            },
        },
    }


def test_freshness_counts_unique_pages():
    raw = score_freshness(_payload_with_dates())["raw"]
    assert raw["pages_with_dates"] == 2  # not 3 (start page not doubled)


def test_freshness_uses_all_dates():
    # One fresh page (0 days) + one ~6.5-year-old page → avg ≈ 1187 days → 20
    raw = score_freshness(_payload_with_dates())
    assert raw["raw"]["pages_with_dates"] == 2
    assert raw["score"] == 20.0


def test_contactability_rec_from_explicit_param():
    """The contactability card must fire when passed explicitly —
    payload["scores"] is not set yet at first-generation time."""
    aeo_factors = {
        "schema": {"score": 100.0, "raw": {"coverage_pct": 100, "has_faq_schema": True}}
    }
    geo_factors = {"eeat": {"score": 55.0, "raw": {"has_org_schema": True}}}
    contactability = {
        "has_email": True,
        "has_mailto": True,
        "has_contact_page": False,
        "has_social_links": False,
        "has_contact_point_schema": False,
    }
    recs = generate_recommendations(
        aeo_factors,
        geo_factors,
        payload={"audit": {}},  # no "scores" key at all
        contactability=contactability,
    )
    titles = [r["title"] for r in recs]
    assert "Improve contactability for AI agents" in titles


def test_llms_txt_recommendation_not_duplicated():
    """Missing llms.txt must produce exactly one llms.txt card (GEO),
    not two (GEO + AAX)."""
    aeo_factors = {"schema": {"score": 100.0, "raw": {"coverage_pct": 100}}}
    geo_factors = {
        "crawl_access": {
            "score": 38.0,
            "raw": {
                "robots_exists": True,
                "llms_txt_exists": False,
                "llms_full_txt_exists": False,
            },
        }
    }
    aax_factors = {
        "llms_txt": {
            "score": 0.0,
            "raw": {
                "llms_txt": {"exists": False},
                "llms_full_txt": {"exists": False},
            },
        }
    }
    recs = generate_recommendations(aeo_factors, geo_factors, aax_factors=aax_factors)
    llms_titles = [r["title"] for r in recs if "llms" in r["title"].lower()]
    assert len(llms_titles) == 1


def test_structure_recommendation_fires_in_weak_band():
    """Site-average 45 (weak band) must produce the structure rec."""
    aeo_factors = {
        "content_structure": {
            "score": 45.0,
            "raw": {
                "site_average": 45.0,
                "pages_evaluated": 2,
                "per_page_scores": {"http://x/a": 45.0, "http://x/b": 45.0},
            },
        }
    }
    recs = generate_recommendations(aeo_factors, {})
    titles = [r["title"] for r in recs]
    assert "Improve content structure across pages" in titles


def test_thin_page_recommendation_threshold_40():
    """Pages below the weak/broken boundary (40) are flagged thin."""
    aeo_factors = {
        "content_structure": {
            "score": 60.0,
            "raw": {
                "site_average": 60.0,
                "pages_evaluated": 2,
                "per_page_scores": {"http://x/a": 75.0, "http://x/b": 39.5},
            },
        }
    }
    recs = generate_recommendations(aeo_factors, {})
    titles = [r["title"] for r in recs]
    assert any(t.startswith("Enrich 1 thin page") for t in titles)


def test_email_validation_quality_beats_quantity():
    """One perfect sales contact should outscore three generic ones."""
    from meshweave.scoring.engine import compute_aax_score

    base = {
        "status": "completed",
        "contactability": {"score": 50.0, "email_count": 1},
        "tests_completed": 1,
        "tests_skipped": 0,
    }
    perfect = dict(
        base,
        email_validation={
            "confidence": "high",
            "valid_contacts": [{"contact_type": "sales"}],
            "best_contact": "sales@x.com",
        },
    )
    generic = dict(
        base,
        email_validation={
            "confidence": "low",
            "valid_contacts": [
                {"contact_type": "general"},
                {"contact_type": "general"},
                {"contact_type": "general"},
            ],
            "best_contact": None,
        },
    )
    p = compute_aax_score(perfect)["factors"]["email_validation"]["score"]
    g = compute_aax_score(generic)["factors"]["email_validation"]["score"]
    assert p > g
    assert p >= 85  # a single high-confidence sales contact is near-max


def test_same_as_buckets_shared_scale():
    """1 sameAs → 40*0.4=16 of the 40-point entity slot; 6+ → full 40."""
    one = score_entity_consistency(
        {
            "audit": {
                "entity": {
                    "name_consistent": True,
                    "description_consistent": True,
                    "same_as": ["https://x"],
                }
            }
        }
    )
    six = score_entity_consistency(
        {
            "audit": {
                "entity": {
                    "name_consistent": True,
                    "description_consistent": True,
                    "same_as": [f"https://x/{i}" for i in range(6)],
                }
            }
        }
    )
    assert one["score"] == 51.0  # 20 + 15 + 16
    assert six["score"] == 75.0  # 20 + 15 + 40


def test_schema_faq_bonus_requires_majority_in_range():
    """A single in-range answer among four must not earn the +10 bonus.

    Base coverage is 60 (not 100) so the score is not saturated and the
    bonus difference is observable.
    """
    payload_minority = {
        "audit": {
            "schema_coverage": {
                "coverage_pct": 60.0,
                "type_counts": {"WebPage": 1},
            }
        },
        "faq_analysis": {
            "count": 4,
            "answers_in_optimal_range": 1,
        },
    }
    payload_majority = {
        "audit": {
            "schema_coverage": {
                "coverage_pct": 60.0,
                "type_counts": {"WebPage": 1},
            }
        },
        "faq_analysis": {
            "count": 4,
            "answers_in_optimal_range": 2,
        },
    }
    minority = score_schema(payload_minority)["score"]
    majority = score_schema(payload_majority)["score"]
    assert minority == 60.0  # no FAQ bonus at all
    assert majority - minority == 10.0
