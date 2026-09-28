"""Per-test LLM wall-clock timeout degrades visibly, never silently.

A structured-test call that outruns LLM_TEST_TIMEOUT_SECONDS (env:
LLM_TEST_TIMEOUT_SECONDS) must surface a named TimeoutError in the skip
reason and engage the composite-suppression path.
"""

from __future__ import annotations

import asyncio

import meshweave.ai.analyses as analyses
import meshweave.ai.runner as runner
from meshweave.ai.models import MetaOptimizationResult
from meshweave.scoring.engine import compute_aax_score


class _SlowAgent:
    """Agent whose run call never answers within the test budget."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    async def run(self, *args: object, **kwargs: object) -> None:
        await asyncio.sleep(60)


def test_timeout_is_named_and_degrades_the_composite(monkeypatch):
    monkeypatch.setattr(runner, "LLM_TEST_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(runner, "_get_model", lambda: None)
    monkeypatch.setattr(runner, "Agent", _SlowAgent)

    async def scenario():
        tasks = {
            "meta_optimization": asyncio.create_task(
                runner.run_structured_test(MetaOptimizationResult, "user", "system")
            )
        }
        return await analyses._gather_results(tasks, {})

    results, skip_reasons = asyncio.run(scenario())
    assert results == {}
    reason = skip_reasons["meta_optimization"]
    assert reason.startswith("Test failed")
    assert "TimeoutError" in reason

    section = compute_aax_score(
        {
            "status": "completed",
            "skip_reasons": skip_reasons,
            "contactability": {"score": 50.0},
        }
    )
    assert section["degraded"] is True
    assert section["composite"] is None


def test_email_validation_timeout_is_named(monkeypatch):
    monkeypatch.setattr(runner, "LLM_TEST_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(runner, "_get_model", lambda: None)
    monkeypatch.setattr(runner, "Agent", _SlowAgent)

    skip_reasons: dict[str, str] = {}
    result = asyncio.run(
        analyses._run_email_validation_task(
            {
                "emails": {
                    "unique": ["sales@example.com"],
                    "by_url": {"https://example.com/contact": ["sales@example.com"]},
                    "sources": [],
                }
            },
            "example.com",
            skip_reasons,
        )
    )
    assert result is None
    assert skip_reasons["email_validation"].startswith("Test failed")
    assert "TimeoutError" in skip_reasons["email_validation"]
