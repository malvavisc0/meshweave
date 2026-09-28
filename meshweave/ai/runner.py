"""Single AAX test runner using pydantic-ai.

Configures the LLM client and runs individual tests with structured output.
"""

import asyncio
import logging
import os
from typing import TypeVar

from pydantic import BaseModel
from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.output import NativeOutput
from pydantic_ai.providers.openai import OpenAIProvider

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

LLM_BASE_URL = os.getenv("LLM_BASE_URL", "")
LLM_MODEL = os.getenv("LLM_MODEL", "")
LLM_API_KEY = os.getenv("LLM_API_KEY", "")

# Per-test wall-clock budget: the AAX queue processes jobs sequentially, so
# one slow endpoint must not stall every pending analysis.
LLM_TEST_TIMEOUT_SECONDS = float(os.getenv("LLM_TEST_TIMEOUT_SECONDS", "120"))


def _get_model() -> OpenAIChatModel:
    """Configure the OpenAI-compatible model from env vars."""
    return OpenAIChatModel(
        LLM_MODEL,
        provider=OpenAIProvider(base_url=LLM_BASE_URL, api_key=LLM_API_KEY),
    )


async def run_structured_test[T: BaseModel](
    output_type: type[T],
    user_prompt: str,
    system_prompt: str,
    *,
    max_retries: int = 2,
) -> T:
    """Run a single AAX test with structured output.

    Args:
        result_type: Pydantic model class for the expected response.
        user_prompt: The user prompt with test-specific content.
        system_prompt: The system prompt.
        max_retries: Number of retries on validation failure.

    Returns:
        Parsed Pydantic model instance.

    Raises:
        TimeoutError: When the test exceeds the per-test wall-clock
            budget; the caller records a skip reason so the degradation
            is visible instead of stalling the queue.

    The AAX tests are graders: the same site must yield the same verdict
    every run. Sampling temperature is pinned to 0 so categorical
    verdicts (clarity, completeness, confidence) do not flip between
    grades on identical input.
    """
    model = _get_model()
    agent = Agent(
        model,
        output_type=NativeOutput(output_type),
        system_prompt=system_prompt,
        retries=max_retries,
    )
    result = await asyncio.wait_for(
        agent.run(user_prompt, model_settings={"temperature": 0}),
        timeout=LLM_TEST_TIMEOUT_SECONDS,
    )
    return result.output


def format_test_error(exc: BaseException) -> str:
    """Label a failed test as 'TypeName: message', never empty.

    Exceptions without a message (e.g. TimeoutError) would otherwise
    format to an empty string and hide the failure.
    """
    text = f"{type(exc).__name__}: {exc}".strip()
    return text.rstrip(": ").strip() or type(exc).__name__
