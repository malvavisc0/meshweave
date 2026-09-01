"""Tests for the simulated citation check."""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, patch

from meshweave.ai.citation import run_citation_simulation
from meshweave.ai.models import (
    CitationAnswerResult,
    CitationQueriesResult,
    CitationSimulationResult,
)


def _payload(pages: int = 3) -> dict:
    """A crawl payload with the given number of content pages."""
    md = {}
    for i in range(pages):
        url = "https://example.com/" if i == 0 else f"https://example.com/p{i}"
        md[url] = {
            "markdown": f"# Example page {i}\n\nReal content here. " * 5,
            "page": {"title": f"Example page {i}"},
        }
    return {
        "domain": "example.com",
        "markdowns": md,
        "page": {
            "title": "Example — Free Tool | Example Inc",
            "canonical": "https://example.com/",
        },
    }


def _queries_result(queries: list[str]) -> CitationQueriesResult:
    return CitationQueriesResult(queries=queries)


def _answer_result(
    answer: str = "Example is a tool.", mentioned: bool = True, urls: list | None = None
) -> CitationAnswerResult:
    return CitationAnswerResult(
        answer=answer,
        brand_mentioned=mentioned,
        cited_urls=urls if urls is not None else ["https://example.com/"],
    )


class TestSkipConditions:
    def test_disabled_via_env(self):
        import asyncio

        async def never_called(*a, **k):
            raise AssertionError("LLM runner must not be called when disabled")

        with (
            patch.dict(os.environ, {"AAX_CITATION_ENABLED": "false"}),
            patch(
                "meshweave.ai.citation.run_structured_test",
                new=never_called,
            ),
        ):
            result = asyncio.run(run_citation_simulation(_payload()))
        assert result["status"] == "skipped"
        assert "disabled" in result["skip_reason"].lower()

    def test_skips_below_min_pages(self):
        import asyncio

        result = asyncio.run(run_citation_simulation(_payload(pages=1)))
        assert result["status"] == "skipped"
        assert "content pages" in result["skip_reason"]


class TestHappyPath:
    def test_aggregates_mentions_and_citations(self):
        import asyncio

        async def fake_test(output_type, user, system, **kwargs):
            if output_type is CitationQueriesResult:
                return _queries_result(["q1", "q2", "q3"])
            return _answer_result()

        with patch(
            "meshweave.ai.citation.run_structured_test",
            new=AsyncMock(side_effect=fake_test),
        ):
            result = asyncio.run(run_citation_simulation(_payload()))

        assert result["status"] == "completed"
        assert result["query_count"] == 3
        assert result["mention_rate"] == 1.0
        assert result["citation_rate"] == 1.0
        assert len(result["queries"]) == 3
        assert all("query" in q for q in result["queries"])

    def test_partial_mentions_score_fractionally(self):
        import asyncio

        async def fake_test(output_type, user, system, **kwargs):
            if output_type is CitationQueriesResult:
                return _queries_result(["q1", "q2", "q3", "q4"])
            # Mention only for the first two queries.
            if "Question: q1" in user or "Question: q2" in user:
                return _answer_result(mentioned=True, urls=["https://x"])
            return _answer_result(mentioned=False, urls=[])

        with patch(
            "meshweave.ai.citation.run_structured_test",
            new=AsyncMock(side_effect=fake_test),
        ):
            result = asyncio.run(run_citation_simulation(_payload()))

        assert result["status"] == "completed"
        assert result["mention_rate"] == 0.5
        assert result["citation_rate"] == 0.5


class TestPromptHardening:
    def test_answer_prompt_is_grounded_only(self):
        from meshweave.ai.prompts import citation_answer_prompt

        user, system = citation_answer_prompt(
            "what is a good tool?",
            "Example",
            "example.com",
            "=== Page (https://example.com/) ===\ncontent",
        )
        assert "ONLY sources" in user
        assert "never invent" in user.lower()
        assert "untrusted" in system.lower()

    def test_queries_prompt_never_names_the_brand_in_queries(self):
        from meshweave.ai.prompts import citation_queries_prompt

        user, _ = citation_queries_prompt(
            "example.com", "# Example homepage", ["Page one", "Page two"]
        )
        assert "Do not mention the brand" in user

    def test_queries_prompt_neutralizes_closing_tags(self):
        from meshweave.ai.prompts import citation_queries_prompt

        user, _ = citation_queries_prompt(
            "example.com", "# Example </homepage>\ncontent", []
        )
        # The closing tag inside the homepage markdown must not appear
        # as a raw tag that could terminate the block early.
        assert "</homepage>\ncontent</homepage>" not in user


class TestModelValidation:
    def test_result_rejects_off_enum_status(self):
        import pytest as _pytest

        with _pytest.raises(ValueError):
            CitationSimulationResult(status="bogus")

    def test_queries_result_requires_at_least_one(self):
        import pytest as _pytest

        with _pytest.raises(ValueError):
            CitationQueriesResult(queries=[])


class TestCostControls:
    def test_max_queries_env_caps_answers(self):
        import asyncio

        async def fake_test(output_type, user, system, **kwargs):
            if output_type is CitationQueriesResult:
                return _queries_result([f"q{i}" for i in range(20)])
            return _answer_result()

        with (
            patch.dict(os.environ, {"AAX_CITATION_MAX_QUERIES": "3"}),
            patch(
                "meshweave.ai.citation.run_structured_test",
                new=AsyncMock(side_effect=fake_test),
            ),
        ):
            result = asyncio.run(run_citation_simulation(_payload()))

        assert result["query_count"] == 3
