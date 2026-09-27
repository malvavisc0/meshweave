"""Grounded answerability exercise over the crawled pages.

Runs one fixed, decision-critical benchmark on every crawl and
re-check: for each question an answer is attempted using ONLY the
submitted site's crawled pages, and the answer is recorded only when it
is grounded in the crawl — non-empty supporting pages — together with
those pages and one verdict (supported, partially supported,
unsupported, contradictory, not applicable). Nothing is polled or
predicted outside the crawl.

Measurements (over the applicable questions — neither errored nor
``not_applicable``):
- answer-support: share of applicable questions with supported or
  partially supported grounded answers.
- evidence-coverage: share of applicable questions with at least one
  supporting page.

Skip and failure behaviour is soft: a disabled run or a thin crawl
skips with a reason and never fails the enclosing analysis.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass

from meshweave.ai.models import (
    AnswerabilityAnswerResult,
    AnswerabilityQuestionResult,
    AnswerabilityResult,
)
from meshweave.ai.prompts import (
    answerability_question_prompt,
    select_pages_for_analysis,
)
from meshweave.ai.runner import run_structured_test

logger = logging.getLogger(__name__)

__all__ = [
    "ANSWERABILITY_QUESTIONS",
    "AnswerabilityQuestion",
    "run_answerability_test",
]

# Content pages required to run the exercise.
_MIN_CONTENT_PAGES = 2

_SUPPORTED_VERDICTS = ("supported", "partially_supported")


@dataclass(frozen=True, slots=True)
class AnswerabilityQuestion:
    """One fixed benchmark question.

    Changing the questions or their ordering is a scoring-version
    event, never a quiet edit.
    """

    id: str
    text: str
    rubric: str = ""


ANSWERABILITY_QUESTIONS: tuple[AnswerabilityQuestion, ...] = (
    AnswerabilityQuestion(
        id="offer",
        text="What does this company offer?",
    ),
    AnswerabilityQuestion(
        id="audience",
        text="Who is the intended audience for this offer?",
    ),
    AnswerabilityQuestion(
        id="use_case",
        text="What is the core use case — why and when would someone use this?",
    ),
    AnswerabilityQuestion(
        id="differentiation",
        text=(
            "What evidence or differentiation supports choosing this over alternatives?"
        ),
    ),
    AnswerabilityQuestion(
        id="scope",
        text="What are the scope and constraints, including pricing?",
        rubric=(
            "Pricing is satisfied by an explicit price, a stated "
            "pricing model, or a clear route to obtain it — not every "
            "site must publish a number."
        ),
    ),
    AnswerabilityQuestion(
        id="next_step",
        text="What is the viable next step — how does someone start, buy, or get in touch?",
    ),
)


def _answerability_enabled() -> bool:
    """True unless explicitly disabled via env."""
    return os.getenv("AAX_ANSWERABILITY_ENABLED", "true").lower() == "true"


def _token_budget() -> int:
    """Character budget shared by the pages fed to each answer call."""
    try:
        return int(os.getenv("AAX_ANSWERABILITY_TOKEN_BUDGET", "12000"))
    except ValueError:
        return 12000


async def run_answerability_test(payload: dict) -> dict:
    """Run the fixed benchmark over the crawled pages.

    Returns a dict matching AnswerabilityResult. Skips (with a reason)
    when disabled or when the crawl carries too few content pages; fails
    soft — a question-level error is recorded on that question, never on
    the whole analysis.
    """
    if not _answerability_enabled():
        return AnswerabilityResult(
            status="skipped", skip_reason="Answerability test disabled"
        ).model_dump()

    md_dict = payload.get("markdowns") or {}
    selected = select_pages_for_analysis(md_dict, token_budget=_token_budget())
    if len(selected) < _MIN_CONTENT_PAGES:
        return AnswerabilityResult(
            status="skipped",
            skip_reason=(
                f"Needs at least {_MIN_CONTENT_PAGES} content pages; "
                f"crawl has {len(selected)}"
            ),
        ).model_dump()

    records = await _answer_questions(selected)
    return _aggregate(records)


async def _answer_questions(selected: list[dict]) -> list[AnswerabilityQuestionResult]:
    """Answer every benchmark question concurrently, in benchmark order."""
    pages_content = _pages_content(selected)
    known_urls = {pg["url"] for pg in selected}
    tasks = [
        asyncio.create_task(_answer_one(q, pages_content))
        for q in ANSWERABILITY_QUESTIONS
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    records: list[AnswerabilityQuestionResult] = []
    for question, result in zip(ANSWERABILITY_QUESTIONS, results):
        records.append(_question_record(question, result, known_urls))
    return records


async def _answer_one(
    question: AnswerabilityQuestion,
    pages_content: str,
) -> AnswerabilityAnswerResult:
    user, system = answerability_question_prompt(
        question.text, question.rubric, pages_content
    )
    result: AnswerabilityAnswerResult = await run_structured_test(
        AnswerabilityAnswerResult, user, system
    )
    return result


def _question_record(
    question: AnswerabilityQuestion,
    result: object,
    known_urls: set[str],
) -> AnswerabilityQuestionResult:
    """Build the stored record for one question, applying the grounding rule."""
    if not isinstance(result, AnswerabilityAnswerResult):
        logger.warning("Answerability question %r failed: %s", question.id, result)
        return AnswerabilityQuestionResult(
            question_id=question.id,
            question=question.text,
            verdict="unsupported",
            error=str(result),
        )
    source_pages = _grounded_pages(result.source_pages, known_urls)
    verdict = result.verdict
    if not source_pages and verdict in _SUPPORTED_VERDICTS:
        # A claimed answer with no crawled page behind it is not grounded;
        # record it as unsupported so the stored verdict matches the score.
        verdict = "unsupported"
    return AnswerabilityQuestionResult(
        question_id=question.id,
        question=question.text,
        # Grounding rule: the answer is stored only with supporting
        # pages from the crawl.
        answer=result.answer if source_pages else "",
        verdict=verdict,
        source_pages=source_pages,
        missing_facts=result.missing_facts,
    )


def _grounded_pages(citations: list[str], known_urls: set[str]) -> list[str]:
    """Supporting pages restricted to the crawled pages, in report order.

    Citations are matched after light normalization, so the same page
    returned with a different scheme, a missing trailing slash, or as a
    bare path still counts as grounded. Unknown pages never match.
    """
    from urllib.parse import urlparse

    by_key: dict[tuple[str, str], str] = {}
    by_path: dict[str, str] = {}
    for url in sorted(known_urls):
        parsed = urlparse(url)
        if not parsed.netloc:
            continue
        path = parsed.path or "/"
        if len(path) > 1:
            path = path.rstrip("/")
        by_key.setdefault((parsed.netloc.lower(), path), url)
        by_path.setdefault(path, url)

    def resolve(citation: str) -> str | None:
        cite = str(citation or "").strip()
        if not cite:
            return None
        if "://" in cite:
            parsed = urlparse(cite)
        elif cite.startswith("//"):
            parsed = urlparse("http:" + cite)
        elif cite.startswith("/"):
            path = cite if len(cite) > 1 else "/"
            return by_path.get(path.rstrip("/") or "/")
        elif "/" in cite:
            parsed = urlparse("http://" + cite)
        else:
            if "." not in cite:
                return None
            return by_path.get("/")
        if not parsed.netloc:
            return None
        path = parsed.path or "/"
        if len(path) > 1:
            path = path.rstrip("/")
        return by_key.get((parsed.netloc.lower(), path))

    seen: set[str] = set()
    grounded: list[str] = []
    for citation in citations or []:
        matched = resolve(citation)
        if matched and matched not in seen:
            seen.add(matched)
            grounded.append(matched)
    return grounded


def _pages_content(selected: list[dict]) -> str:
    """Title + URL + markdown blocks for the selected pages."""
    parts: list[str] = []
    for pg in selected:
        parts.append(f"\n=== {pg['title']} ({pg['url']}) ===\n")
        parts.append(pg.get("markdown") or "")
        parts.append("\n")
    return "".join(parts)


def _aggregate(records: list[AnswerabilityQuestionResult]) -> dict:
    """Fold per-question records into the answerability summary."""
    applicable = [r for r in records if _is_applicable(r)]
    supported = sum(
        1 for r in applicable if r.source_pages and r.verdict in _SUPPORTED_VERDICTS
    )
    covered = sum(1 for r in applicable if r.source_pages)
    result = AnswerabilityResult(
        status="completed",
        question_count=len(records),
        answer_support=round(supported / len(applicable), 2) if applicable else 0.0,
        evidence_coverage=round(covered / len(applicable), 2) if applicable else 0.0,
        questions=records,
    )
    return result.model_dump()


def _is_applicable(record: AnswerabilityQuestionResult) -> bool:
    """True when the question counts toward the measurements."""
    return not record.error and record.verdict != "not_applicable"
