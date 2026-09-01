"""Simulated citation check over the crawled pages.

Measures whether a model reading ONLY this site's crawled pages would
mention and cite the brand when answering realistic buyer queries. This
is a grounded simulation — not a live answer-engine measurement — and is
labeled as simulated everywhere it surfaces.

Two stages:
1. Generate buyer queries for the site's category from the crawl content
   (one structured call).
2. Answer each query (bounded) with context restricted to the crawled
   pages, then check whether the brand was mentioned and which URLs the
   model cited.
"""

from __future__ import annotations

import asyncio
import logging
import os
from urllib.parse import urlsplit

from meshweave.ai.models import (
    CitationAnswerResult,
    CitationQueriesResult,
    CitationSimulationResult,
)
from meshweave.ai.prompts import (
    citation_answer_prompt,
    citation_queries_prompt,
    select_pages_for_analysis,
)
from meshweave.ai.runner import run_structured_test

logger = logging.getLogger(__name__)

__all__ = ["run_citation_simulation"]

# Content pages required to run the simulation.
_MIN_CONTENT_PAGES = 2


def _citation_enabled() -> bool:
    """True unless explicitly disabled via env."""
    return os.getenv("AAX_CITATION_ENABLED", "true").lower() == "true"


def _max_answered_queries() -> int:
    """Cap on answered queries per simulation (cost control)."""
    try:
        return max(1, int(os.getenv("AAX_CITATION_MAX_QUERIES", "6")))
    except ValueError:
        return 6


def _token_budget() -> int:
    """Character budget shared by the pages fed to each answer call."""
    try:
        return int(os.getenv("AAX_CITATION_TOKEN_BUDGET", "12000"))
    except ValueError:
        return 12000


async def run_citation_simulation(payload: dict) -> dict:
    """Simulated citation check over the crawled pages.

    Returns a dict matching CitationSimulationResult: status, rates,
    and per-query details. Skips (with a reason) when disabled or when
    the crawl carries too few content pages; fails soft — a query-level
    error is recorded on that query, not on the whole simulation.
    """
    if not _citation_enabled():
        return CitationSimulationResult(
            status="skipped", skip_reason="Citation simulation disabled"
        ).model_dump()

    md_dict = payload.get("markdowns") or {}
    domain = _simulation_domain(payload)
    if not domain:
        return CitationSimulationResult(
            status="skipped", skip_reason="No domain to check mentions for"
        ).model_dump()

    selected = select_pages_for_analysis(md_dict, token_budget=_token_budget())
    if len(selected) < _MIN_CONTENT_PAGES:
        return CitationSimulationResult(
            status="skipped",
            skip_reason=(
                f"Needs at least {_MIN_CONTENT_PAGES} content pages; "
                f"crawl has {len(selected)}"
            ),
        ).model_dump()

    brand = _brand_name(payload, domain)
    queries = await _generate_queries(domain, selected)
    if not queries:
        return CitationSimulationResult(
            status="failed", skip_reason="Query generation returned no queries"
        ).model_dump()

    answered = await _answer_queries(queries, brand, domain, selected)
    return _aggregate(answered)


def _simulation_domain(payload: dict) -> str:
    """The site's canonical host, falling back to the crawl domain."""
    page = payload.get("page") or {}
    canonical = page.get("canonical") or (page.get("og") or {}).get("url") or ""
    if canonical:
        host = urlsplit(canonical).hostname
        if host:
            return host
    return payload.get("domain") or ""


def _brand_name(payload: dict, domain: str) -> str:
    """Best brand name from the crawl: homepage title, else the domain."""
    page = payload.get("page") or {}
    title = (page.get("title") or "").strip()
    if title:
        # Titles like "Acme — Free Tool | Acme Tools" — take the first
        # segment before separator characters.
        for sep in ("|", "—", "–", " - "):
            if sep in title:
                title = title.split(sep)[0].strip()
                break
        if title:
            return title
    return domain


async def _generate_queries(domain: str, selected: list[dict]) -> list[str]:
    """Buyer queries for the category, generated from the crawl content."""
    homepage_md = next((pg["markdown"] for pg in selected if pg.get("markdown")), "")
    titles = [pg.get("title") or "" for pg in selected]
    user, system = citation_queries_prompt(domain, homepage_md, titles)
    try:
        result: CitationQueriesResult = await run_structured_test(
            CitationQueriesResult, user, system
        )
    except Exception as e:
        logger.warning("Citation query generation failed: %s", e)
        return []
    return result.queries[: _max_answered_queries()]


async def _answer_queries(
    queries: list[str],
    brand: str,
    domain: str,
    selected: list[dict],
) -> list[dict]:
    """Answer each query concurrently, grounded in the selected pages."""
    pages_content = _pages_content(selected)
    tasks = [
        asyncio.create_task(_answer_one(q, brand, domain, pages_content))
        for q in queries
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    answered: list[dict] = []
    for query, result in zip(queries, results):
        if isinstance(result, BaseException):
            logger.warning("Citation answer failed for %r: %s", query, result)
            answered.append(
                {
                    "query": query,
                    "answer": "",
                    "brand_mentioned": False,
                    "cited_urls": [],
                    "error": str(result),
                }
            )
        elif isinstance(result, CitationAnswerResult):
            answered.append(
                {
                    "query": query,
                    "answer": result.answer,
                    "brand_mentioned": result.brand_mentioned,
                    "cited_urls": result.cited_urls,
                }
            )
    return answered


async def _answer_one(
    query: str,
    brand: str,
    domain: str,
    pages_content: str,
) -> CitationAnswerResult:
    user, system = citation_answer_prompt(query, brand, domain, pages_content)
    result: CitationAnswerResult = await run_structured_test(
        CitationAnswerResult, user, system
    )
    return result


def _pages_content(selected: list[dict]) -> str:
    """Title + URL + markdown blocks for the selected pages."""
    parts: list[str] = []
    for pg in selected:
        parts.append(f"\n=== {pg['title']} ({pg['url']}) ===\n")
        parts.append(pg.get("markdown") or "")
        parts.append("\n")
    return "".join(parts)


def _aggregate(answered: list[dict]) -> dict:
    """Fold per-query results into the simulation summary."""
    total = len(answered)
    mentions = sum(1 for a in answered if a.get("brand_mentioned"))
    citations = sum(1 for a in answered if a.get("cited_urls"))
    result = CitationSimulationResult(
        status="completed",
        query_count=total,
        mention_rate=round(mentions / total, 2) if total else 0.0,
        citation_rate=round(citations / total, 2) if total else 0.0,
        queries=answered,
    )
    return result.model_dump()
