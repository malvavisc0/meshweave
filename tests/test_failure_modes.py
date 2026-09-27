"""Failure-mode fixtures through both the structural scorers and the
grounded answerability test.

Five fixture sites (see ``failure_mode_fixtures``) cover the two central
failure modes and their healthy twin:

(a) meaningful content that cannot be reached or extracted,
(b) reachable but generic copy whose answers are unsupported,
(c) contradictory claims across pages,
(d) a missing audience statement,
(e) a clear, evidenced action path.

Each fixture is exercised through the real answerability orchestrator
(LLM call mocked) and the real structural scorers. Pins: all three score
groups are computable from the submitted site alone; a structurally
polished site still fails the fixed benchmark; a clear site gains score
only when its crawl supplies supporting evidence.
"""

from __future__ import annotations

import json

import pytest
from failure_mode_fixtures import (
    FIXTURES,
    SiteFixture,
    answer,
    clear_action_path,
    contradictory_claims,
    generic_copy,
    missing_audience,
    run_exercise,
    unreachable_content,
)

from meshweave.scoring.engine import compute_aax_score, compute_scores

_ALL_FINDINGS = {
    "Missing offer",
    "Missing audience",
    "Missing use case",
    "Unsubstantiated claim",
    "Absent constraint",
    "Next step not locatable",
}


def _scores(fixture: SiteFixture, result: dict) -> dict:
    payload = {**fixture.payload, "aax": {"answerability": result}}
    return compute_scores(payload)


def _answerability_findings(scores: dict) -> dict[str, dict]:
    return {
        r["title"]: r
        for r in scores["recommendations"]
        if r["factor"] == "answerability"
    }


class TestAllThreeScoreGroupsFromTheSiteAlone:
    """AEO, GEO, and AAX are computable from the submitted site alone."""

    @pytest.mark.parametrize("builder", FIXTURES, ids=lambda b: b().name)
    def test_computable_without_manual_external_data(self, builder):
        fixture = builder()
        result = run_exercise(fixture)
        assert result["status"] == "completed"
        assert result["question_count"] == 6

        scores = _scores(fixture, result)
        aax_section = compute_aax_score(fixture.aax_result)
        assert scores["aeo"]["composite"] is not None
        assert scores["geo"]["composite"] is not None
        assert aax_section is not None
        assert aax_section["composite"] is not None

        blob = json.dumps(
            {"aeo": scores["aeo"], "geo": scores["geo"], "aax": aax_section}
        )
        for banned in (
            "has_manual_input",
            "manual_input",
            "score_basis",
            "citation_sim",
            "citation_rate",
            "mention_rate",
            "capture_rate",
            "query_match",
            "voice_rate",
        ):
            assert banned not in blob


class TestUnreachableMeaningfulContent:
    """(a) The content exists in titles and meta but cannot be extracted."""

    def test_blocked_crawlers_and_empty_bodies_drag_the_structural_side(self):
        fixture = unreachable_content()
        scores = _scores(fixture, run_exercise(fixture))
        geo = scores["geo"]["factors"]
        # robots.txt blocks every AI bot and no llms.txt or sitemap exists.
        assert geo["crawl_access"]["score"] == 8.0
        # JS shells: no headings, no words — near-zero structure per page.
        assert geo["content_depth"]["score"] < 20
        assert scores["aeo"]["factors"]["content_structure"]["raw"]["site_average"] < 20
        assert scores["geo"]["composite"] < 30

    def test_unreachable_bodies_make_the_benchmark_unanswerable(self):
        fixture = unreachable_content()
        result = run_exercise(fixture)
        scores = _scores(fixture, result)
        factor = scores["aeo"]["factors"]["answerability"]
        # Only the title carries the offer, at partial credit.
        assert factor["score"] == pytest.approx(50 / 6)
        # Only the title-backed answer cites a crawled page (1 of 6,
        # rounded at the two-decimal product precision).
        assert result["evidence_coverage"] == 0.17
        findings = _answerability_findings(scores)
        assert set(findings) == _ALL_FINDINGS - {"Missing offer"}
        for finding in findings.values():
            assert finding["expected_points"] is not None
        assert (
            "not present in the crawled text" in findings["Missing audience"]["detail"]
        )


class TestGenericPolishedSiteFailsBenchmark:
    """(b) Structure and schema polish cannot fake answerability."""

    def test_structural_factors_high_answerability_zero(self):
        fixture = generic_copy()
        result = run_exercise(fixture)
        scores = _scores(fixture, result)
        aeo = scores["aeo"]["factors"]
        assert aeo["schema"]["score"] >= 70
        assert aeo["content_structure"]["score"] >= 70
        assert scores["geo"]["factors"]["crawl_access"]["score"] >= 55
        # The fixed benchmark fails: generic copy supports no answer.
        assert aeo["answerability"]["score"] == 0.0
        assert result["answer_support"] == 0.0
        assert aeo["answerability"]["score"] < 30

    def test_every_question_becomes_a_finding(self):
        fixture = generic_copy()
        scores = _scores(fixture, run_exercise(fixture))
        assert set(_answerability_findings(scores)) == _ALL_FINDINGS


class TestContradictoryClaims:
    """(c) Conflicting claims surface as findings with the evidence named."""

    def test_conflicts_become_findings_naming_the_missing_fact(self):
        fixture = contradictory_claims()
        result = run_exercise(fixture)
        scores = _scores(fixture, result)
        findings = _answerability_findings(scores)
        assert set(findings) == {"Unsubstantiated claim", "Absent constraint"}
        for finding in findings.values():
            assert "conflicting claims" in finding["detail"]
        assert "which claim is current" in findings["Unsubstantiated claim"]["detail"]
        assert "the current pricing model" in findings["Absent constraint"]["detail"]

    def test_contradictions_score_zero_and_inconsistency_is_visible(self):
        fixture = contradictory_claims()
        result = run_exercise(fixture)
        scores = _scores(fixture, result)
        # Four grounded supported answers, two contradictions: 400/6.
        assert scores["aeo"]["factors"]["answerability"]["score"] == pytest.approx(
            400 / 6
        )
        records = {q["question_id"]: q for q in result["questions"]}
        assert records["differentiation"]["verdict"] == "contradictory"
        assert records["scope"]["verdict"] == "contradictory"
        # The structural side sees the same inconsistency.
        assert scores["geo"]["factors"]["entity_consistency"]["score"] <= 35


class TestMissingAudience:
    """(d) A site that states everything but who it is for."""

    def test_missing_audience_is_the_single_named_gap(self):
        fixture = missing_audience()
        result = run_exercise(fixture)
        scores = _scores(fixture, result)
        findings = _answerability_findings(scores)
        assert set(findings) == {"Missing audience"}
        assert "who this is for" in findings["Missing audience"]["detail"]
        assert findings["Missing audience"]["expected_points"] is not None
        assert scores["aeo"]["factors"]["answerability"]["score"] == pytest.approx(
            500 / 6
        )


class TestClearActionPath:
    """(e) A clear, evidenced action path answers the whole benchmark."""

    def test_fully_supported_benchmark_generates_no_findings(self):
        fixture = clear_action_path()
        result = run_exercise(fixture)
        scores = _scores(fixture, result)
        assert result["answer_support"] == 1.0
        assert scores["aeo"]["factors"]["answerability"]["score"] == 100.0
        assert not _answerability_findings(scores)

    def test_clear_site_gains_score_only_with_grounded_evidence(self):
        """The gain requires supporting pages from the crawl itself."""
        fixture = clear_action_path()
        ungrounded = SiteFixture(
            name="ungrounded",
            domain=fixture.domain,
            payload=fixture.payload,
            answers={
                key: answer(
                    value.verdict,
                    value.answer,
                    pages=("https://outside.example/tip",),
                    missing=tuple(value.missing_facts),
                )
                for key, value in fixture.answers.items()
            },
            aax_result=fixture.aax_result,
        )
        grounded_scores = _scores(fixture, run_exercise(fixture))
        claimed_scores = _scores(ungrounded, run_exercise(ungrounded))
        without_scores = compute_scores(fixture.payload)

        grounded = grounded_scores["aeo"]["composite"]
        claimed = claimed_scores["aeo"]["composite"]
        without = without_scores["aeo"]["composite"]
        assert grounded_scores["aeo"]["factors"]["answerability"]["score"] == 100.0
        assert claimed_scores["aeo"]["factors"]["answerability"]["score"] == 0.0
        assert claimed < without < grounded
