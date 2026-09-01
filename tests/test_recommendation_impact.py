"""Tests for model-derived recommendation impact and ordering."""

from __future__ import annotations

from meshweave.scoring.composite import (
    AEO_WEIGHTS,
    GEO_WEIGHTS,
    expected_lens_delta,
    weighted_composite,
)
from meshweave.scoring.engine import compute_scores
from meshweave.scoring.recommendations import generate_recommendations


def _geo_factors(**overrides: float | None) -> dict[str, dict]:
    """GEO auto factors at given scores (citation absent — manual)."""
    base = {
        "topical_authority": {"score": 0.0, "raw": {}},
        "eeat": {"score": 0.0, "raw": {}},
        "crawl_access": {"score": 0.0, "raw": {}},
        "content_depth": {"score": 0.0, "raw": {}},
        "entity_consistency": {"score": 0.0, "raw": {}},
    }
    for key, score in overrides.items():
        base[key] = {"score": score, "raw": {}}
    return base


def _eeat_raw(**flags: bool) -> dict:
    return {
        "has_org_schema": flags.get("org", False),
        "has_author_info": flags.get("author", False),
    }


class TestExpectedLensDelta:
    """The counterfactual math mirrors the real composite."""

    def test_delta_matches_recomputed_composite(self):
        factors = _geo_factors(eeat=10.0, crawl_access=30.0)
        delta = expected_lens_delta("geo", factors, "eeat", 25.0)
        before = weighted_composite(factors, GEO_WEIGHTS)
        after = weighted_composite({**factors, "eeat": {"score": 25.0}}, GEO_WEIGHTS)
        assert delta == round(after - before, 1)

    def test_none_factor_introduced_at_target(self):
        """A factor currently None joins the composite at its target."""
        factors = _geo_factors()
        factors["crawl_access"] = {"score": None}
        delta = expected_lens_delta("geo", factors, "crawl_access", 23.0)
        assert delta is not None and delta > 0

    def test_unknown_lens_returns_none(self):
        assert expected_lens_delta("nope", {}, "eeat", 50.0) is None

    def test_unknown_factor_returns_none(self):
        assert expected_lens_delta("geo", _geo_factors(), "nope", 50.0) is None

    def test_no_computable_factors_returns_none(self):
        assert expected_lens_delta("geo", {}, "eeat", 50.0) is None


class TestGEORecommendationOrdering:
    """Fix order follows the model, not the generator's insertion order."""

    def _geo_zero_payload(self) -> dict:
        """A site with no schema, no sameAs, no llms.txt, no robots."""
        return {
            "audit": {
                "schema_coverage": {"coverage_pct": 0, "type_counts": {}},
                "entity": {"same_as": [], "name_consistent": False},
            },
            "markdowns": {},
            "page": {},
            "robots": {"exists": True, "bots": {}, "sitemaps": []},
            "llms_txt": {
                "llms_txt": {"exists": False},
                "llms_full_txt": {"exists": False},
            },
        }

    def test_llms_txt_outranks_org_schema_outranks_sameas(self):
        payload = self._geo_zero_payload()
        scores = compute_scores(payload)
        geo_factors = scores["geo"]["factors"]
        # Force the rec-triggering raw shapes with zero scores.
        geo_factors["eeat"]["raw"] = _eeat_raw()
        geo_factors["topical_authority"]["raw"] = {"same_as_count": 0}
        geo_factors["crawl_access"]["raw"] = {
            "llms_txt_exists": False,
            "robots_exists": True,
            "bot_statuses": {},
            "sitemap_count": 0,
        }

        recs = generate_recommendations({}, geo_factors, payload=payload)
        titles = [r["title"] for r in recs if r["expected_points"] is not None]

        assert "Publish an llms.txt file" in titles
        assert "Add Organization JSON-LD schema" in titles
        assert "Add sameAs links to your Organization schema" in titles

        by_title = {r["title"]: r for r in recs}
        org_pts = by_title["Add Organization JSON-LD schema"]["expected_points"]
        sameas_pts = by_title["Add sameAs links to your Organization schema"][
            "expected_points"
        ]
        # 0.15 weight × (+15 llms) > 0.15 × (+15 org) share? No — llms
        # also lifts llms-full (target 15 vs org 15), but org and sameAs
        # share the eeat factor: the org rec must dominate sameAs.
        assert org_pts is not None and sameas_pts is not None
        assert org_pts > sameas_pts
        # And the ordering in the returned list follows the points.
        assert titles.index("Publish an llms.txt file") < titles.index(
            "Add sameAs links to your Organization schema"
        )

    def test_impact_string_renders_from_points(self):
        payload = self._geo_zero_payload()
        scores = compute_scores(payload)
        geo_factors = scores["geo"]["factors"]
        geo_factors["eeat"]["raw"] = _eeat_raw()
        geo_factors["topical_authority"]["raw"] = {"same_as_count": 0}
        geo_factors["crawl_access"]["raw"] = {
            "llms_txt_exists": False,
            "robots_exists": True,
            "bot_statuses": {},
            "sitemap_count": 0,
        }
        recs = generate_recommendations({}, geo_factors, payload=payload)
        llms_rec = next(r for r in recs if r["title"] == "Publish an llms.txt file")
        assert llms_rec["impact"].startswith("GEO +")
        assert "estimated" not in llms_rec["impact"]
        assert llms_rec["expected_points"] == float(
            llms_rec["impact"].split("+")[1].split()[0]
        )


class TestPointlessRecommendations:
    """Recs without a counterfactual sort after point-bearing recs."""

    def test_canonical_rec_has_no_expected_points(self):
        payload = {
            "audit": {
                "meta": {"canonical_issues": ["https://a", "https://b"]},
            },
        }
        recs = generate_recommendations({}, {}, payload=payload)
        canonical = next(
            r for r in recs if r["factor"] == "schema" and "canonical" in r["title"]
        )
        assert canonical["expected_points"] is None
        assert "estimated" in canonical["impact"]

    def test_pointless_recs_sort_after_point_bearing(self):
        payload = {
            "audit": {
                "meta": {"canonical_issues": ["https://a"]},
                "schema_coverage": {"coverage_pct": 0, "type_counts": {}},
                "entity": {"same_as": []},
            },
            "markdowns": {},
            "page": {},
        }
        scores = compute_scores(payload)
        aeo_factors = scores["aeo"]["factors"]
        geo_factors = scores["geo"]["factors"]
        geo_factors["eeat"]["raw"] = _eeat_raw()
        geo_factors["topical_authority"]["raw"] = {"same_as_count": 0}

        recs = generate_recommendations(aeo_factors, geo_factors, payload=payload)
        medium_band = [r for r in recs if r["priority"] == "medium"]
        with_pts = [r for r in medium_band if r["expected_points"] is not None]
        without_pts = [r for r in medium_band if r["expected_points"] is None]
        assert with_pts, "expected at least one point-bearing medium rec"
        assert without_pts, "expected the canonical rec in the medium band"
        last_with = medium_band.index(with_pts[-1])
        first_without = medium_band.index(without_pts[0])
        assert last_with < first_without

    def test_positive_callouts_keep_empty_impact(self):
        aeo_factors = {
            "schema": {
                "score": 85.0,
                "raw": {"coverage_pct": 85, "has_faq_schema": True},
            },
        }
        recs = generate_recommendations(aeo_factors, {})
        callout = next(r for r in recs if r["priority"] == "low")
        assert callout["expected_points"] is None
        assert callout["impact"] == ""


class TestAEOTargets:
    """AEO rec targets track the factor's own arithmetic."""

    def test_schema_coverage_targets_80(self):
        aeo_factors = {
            "schema": {
                "score": 20.0,
                "raw": {"coverage_pct": 20, "has_faq_schema": False},
            },
        }
        recs = generate_recommendations(aeo_factors, {})
        cov = next(r for r in recs if r["title"].startswith("Add structured data"))
        delta = cov["expected_points"]
        before = weighted_composite(aeo_factors, AEO_WEIGHTS)
        after = weighted_composite(
            {**aeo_factors, "schema": {"score": 80.0}}, AEO_WEIGHTS
        )
        assert delta == round(after - before, 1)

    def test_faq_schema_bonus_targets_current_plus_10(self):
        aeo_factors = {
            "schema": {
                "score": 40.0,
                "raw": {"coverage_pct": 40, "has_faq_schema": False},
            },
        }
        recs = generate_recommendations(aeo_factors, {})
        faq = next(r for r in recs if r["title"].startswith("Add FAQPage"))
        delta = faq["expected_points"]
        before = weighted_composite(aeo_factors, AEO_WEIGHTS)
        after = weighted_composite(
            {**aeo_factors, "schema": {"score": 50.0}}, AEO_WEIGHTS
        )
        assert delta == round(after - before, 1)


class TestEndToEndPrediction:
    """A predicted delta must match the observed one through compute_scores."""

    def test_content_depth_prediction_lands(self):
        payload = {
            "audit": {},
            "markdowns": {
                "https://a/": {"content_metrics": {"words": 100}},
                "https://b/": {"content_metrics": {"words": 120}},
            },
            "page": {},
        }
        scores = compute_scores(payload)
        geo_factors = scores["geo"]["factors"]
        recs = generate_recommendations({}, geo_factors, payload=payload)
        depth_rec = next(r for r in recs if r["factor"] == "content_depth")
        predicted = depth_rec["expected_points"]
        assert predicted is not None

        # Observed: same payload, but the depth factor jumps by the
        # rec's declared target delta (current + 20, the 500-word band).
        current = geo_factors["content_depth"]["score"]
        target = min(100.0, current + 20.0)
        observed = expected_lens_delta("geo", geo_factors, "content_depth", target)
        assert predicted == observed
