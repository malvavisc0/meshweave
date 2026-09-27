"""Five fixture sites for the two central failure modes and their healthy twin.

Each fixture pairs a realistic crawl payload with the answers the grounded
answerability test derives from that site's actual text, plus the AAX
analysis dict for the same site. ``run_exercise`` drives the real
answerability orchestrator with those canned model answers.

(a) unreachable_content    — meaningful content that cannot be reached or
                             extracted (blocked crawlers, JS-shell pages)
(b) generic_copy           — reachable but generic copy: structurally
                             polished, answers unsupported
(c) contradictory_claims   — claims conflict across pages
(d) missing_audience       — no audience statement anywhere
(e) clear_action_path      — clear, evidenced action path
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, patch

from meshweave.ai.answerability import run_answerability_test
from meshweave.ai.models import AnswerabilityAnswerResult

HOME = "https://{d}/"
PRICING = "https://{d}/pricing"

# Question-text fragments of the fixed benchmark, in benchmark order.
Q_OFFER = "What does this company offer?"
Q_AUDIENCE = "Who is the intended audience"
Q_USE_CASE = "What is the core use case"
Q_DIFFERENTIATION = "What evidence or differentiation"
Q_SCOPE = "What are the scope and constraints"
Q_NEXT_STEP = "What is the viable next step"


@dataclass(frozen=True)
class SiteFixture:
    name: str
    domain: str
    payload: dict[str, Any]
    answers: dict[str, AnswerabilityAnswerResult]
    aax_result: dict[str, Any] = field(default_factory=dict)


def answer(
    verdict: str,
    text: str = "",
    pages: tuple[str, ...] = (),
    missing: tuple[str, ...] = (),
) -> AnswerabilityAnswerResult:
    return AnswerabilityAnswerResult(
        answer=text,
        verdict=verdict,
        source_pages=list(pages),
        missing_facts=list(missing),
    )


def page_entry(
    url: str,
    title: str,
    markdown: str,
    *,
    words: int,
    headings: dict[str, int] | None = None,
    lists: int = 0,
    tables: int = 0,
    paragraphs: int = 0,
    images: int = 0,
    images_with_alt: int = 0,
    jsonld: list | None = None,
) -> tuple[str, dict[str, Any]]:
    return url, {
        "markdown": markdown,
        "page": {"title": title, "url": url, "jsonld": list(jsonld or [])},
        "headings": headings or {"h1_count": 0, "total": 0, "depth": 0},
        "content_metrics": {
            "words": words,
            "lists": lists,
            "tables": tables,
            "paragraphs": paragraphs,
            "images_total": images,
            "images_with_alt": images_with_alt,
        },
    }


def weak_aax() -> dict[str, Any]:
    """An AAX analysis for a site an agent cannot act on."""
    return {
        "status": "completed",
        "tests_completed": 5,
        "homepage_comprehension": {
            "clarity": "unclear",
            "information_density": "sparse",
            "would_remember": False,
        },
        "meta_optimization": {
            "would_click_through": False,
            "completeness": "minimal",
            "clarity": "unclear",
            "llm_optimization": "poor",
        },
        "content_delta": {
            "weaknesses": ["pricing", "audience"],
            "coherence": "somewhat_consistent",
            "completeness": "incomplete",
        },
        "email_validation": {"confidence": "low", "valid_contacts": []},
        "contactability": {
            "score": 15.0,
            "has_email": False,
            "has_mailto": False,
            "has_contact_page": False,
            "has_social_links": False,
            "has_contact_point_schema": False,
        },
    }


def clear_aax() -> dict[str, Any]:
    """An AAX analysis for a site with a clear, evidenced action path."""
    return {
        "status": "completed",
        "tests_completed": 5,
        "homepage_comprehension": {
            "brand": "Northwind Telemetry",
            "product": "fleet sensor analytics",
            "target_audience": "logistics operators",
            "call_to_action": "Start a trial",
            "clarity": "clear",
            "information_density": "adequate",
            "would_remember": True,
        },
        "meta_optimization": {
            "would_click_through": True,
            "completeness": "complete",
            "clarity": "clear",
            "llm_optimization": "optimized",
        },
        "content_delta": {
            "company": {"name": "Northwind Telemetry"},
            "product": {"name": "Fleet Analytics"},
            "pricing": {"model": "per-vehicle monthly subscription"},
            "coherence": "consistent",
            "completeness": "comprehensive",
        },
        "email_validation": {
            "valid_contacts": [
                {"email": "sales@north.example", "contact_type": "sales"}
            ],
            "best_contact": "sales@north.example",
            "confidence": "high",
        },
        "contactability": {
            "score": 85.0,
            "has_email": True,
            "has_mailto": True,
            "has_contact_page": True,
            "has_social_links": True,
            "has_contact_point_schema": True,
        },
    }


def _shell_payload(domain: str) -> dict[str, Any]:
    """Meaningful titles and meta, but bodies are unreadable JS shells."""
    home = HOME.format(d=domain)
    about = f"https://{domain}/about"
    pages = dict(
        [
            page_entry(
                home,
                "Northwind Telemetry — fleet sensor analytics",
                "Loading…\nPlease enable JavaScript.",
                words=8,
                paragraphs=1,
            ),
            page_entry(
                about,
                "About Northwind Telemetry",
                "Loading…\nPlease enable JavaScript.",
                words=8,
                paragraphs=1,
            ),
        ]
    )
    return {
        "domain": domain,
        "page": {
            "title": "Northwind Telemetry — fleet sensor analytics",
            "canonical": home,
            "url": home,
            "description": "Fleet sensor analytics for logistics operators.",
            "og": {"description": "Fleet sensor analytics for logistics operators."},
        },
        "markdowns": pages,
        "audit": {
            "schema_coverage": {"coverage_pct": 0, "type_counts": {}},
            "entity": {
                "name_consistent": False,
                "description_consistent": False,
                "same_as": [],
                "pages_with_org_schema": 0,
            },
        },
        "robots": {
            "exists": True,
            "bots": {
                "GPTBot": "blocked",
                "ClaudeBot": "blocked",
                "PerplexityBot": "blocked",
            },
            "sitemaps": [],
        },
        "llms_txt": {},
    }


def _polished_payload(domain: str) -> dict[str, Any]:
    """Structurally polished pages with full machine-readable context."""
    home = HOME.format(d=domain)
    pricing = PRICING.format(d=domain)
    body = (
        "# Innovative solutions\n\n"
        "We deliver innovative solutions for modern businesses. "
        "Our platform empowers teams to unlock value.\n\n"
        "## Why choose us\n\n" + "Filler sentence about synergy and value. " * 120
    )
    pages = dict(
        [
            page_entry(
                home,
                "Acme — innovative solutions",
                body,
                words=700,
                headings={"h1_count": 1, "total": 4, "depth": 3},
                lists=2,
                tables=1,
                paragraphs=6,
                images=5,
                images_with_alt=5,
            ),
            page_entry(
                pricing,
                "Acme pricing",
                body,
                words=700,
                headings={"h1_count": 1, "total": 4, "depth": 3},
                lists=2,
                tables=1,
                paragraphs=6,
                images=5,
                images_with_alt=5,
            ),
        ]
    )
    return {
        "domain": domain,
        "page": {
            "title": "Acme — innovative solutions",
            "canonical": home,
            "url": home,
            "description": "Innovative solutions for modern businesses.",
            "og": {"description": "Innovative solutions for modern businesses."},
            "jsonld": [{"@type": "Organization", "name": "Acme"}],
        },
        "markdowns": pages,
        "faq_analysis": {"count": 2, "answers_in_optimal_range": 2},
        "audit": {
            "schema_coverage": {
                "coverage_pct": 90,
                "type_counts": {
                    "Organization": 1,
                    "WebSite": 1,
                    "FAQPage": 2,
                },
            },
            "entity": {
                "name_consistent": True,
                "description_consistent": True,
                "same_as": ["https://linkedin.com/acme", "https://github.com/acme"],
                "pages_with_org_schema": 2,
            },
        },
        "robots": {
            "exists": True,
            "bots": {
                "GPTBot": "allowed",
                "ClaudeBot": "allowed",
                "PerplexityBot": "allowed",
            },
            "sitemaps": [f"https://{domain}/sitemap.xml"],
        },
        "llms_txt": {
            "llms_txt": {"exists": True},
            "llms_full_txt": {"exists": True},
        },
    }


def unreachable_content() -> SiteFixture:
    """(a) Meaningful content that cannot be reached or extracted."""
    domain = "hollow.example"
    home = HOME.format(d=domain)
    missing = ("the page body is not present in the crawled text",)
    return SiteFixture(
        name="unreachable_content",
        domain=domain,
        payload=_shell_payload(domain),
        answers={
            Q_OFFER: answer(
                "partially_supported",
                "Fleet sensor analytics (from the title only).",
                pages=(home,),
                missing=("what the product actually includes",),
            ),
            Q_AUDIENCE: answer("unsupported", missing=missing),
            Q_USE_CASE: answer("unsupported", missing=missing),
            Q_DIFFERENTIATION: answer("unsupported", missing=missing),
            Q_SCOPE: answer("unsupported", missing=missing),
            Q_NEXT_STEP: answer("unsupported", missing=missing),
        },
        aax_result=weak_aax(),
    )


def generic_copy() -> SiteFixture:
    """(b) Reachable but generic copy: polished structure, no answers."""
    domain = "generic.example"
    return SiteFixture(
        name="generic_copy",
        domain=domain,
        payload=_polished_payload(domain),
        answers={
            Q_OFFER: answer("unsupported", missing=("what is actually offered",)),
            Q_AUDIENCE: answer("unsupported", missing=("who it is for",)),
            Q_USE_CASE: answer("unsupported", missing=("a concrete use case",)),
            Q_DIFFERENTIATION: answer(
                "unsupported", missing=("evidence for the claims made",)
            ),
            Q_SCOPE: answer("unsupported", missing=("pricing or scope constraints",)),
            Q_NEXT_STEP: answer(
                "unsupported", missing=("a concrete statement on the page",)
            ),
        },
        aax_result=weak_aax(),
    )


def contradictory_claims() -> SiteFixture:
    """(c) Claims conflict across pages."""
    domain = "contradict.example"
    home = HOME.format(d=domain)
    pricing = PRICING.format(d=domain)
    both = (home, pricing)
    return SiteFixture(
        name="contradictory_claims",
        domain=domain,
        payload={
            **_polished_payload(domain),
            "faq_analysis": {},
            "audit": {
                "schema_coverage": {
                    "coverage_pct": 60,
                    "type_counts": {"Organization": 1, "WebSite": 1},
                },
                "entity": {
                    "name_consistent": False,
                    "description_consistent": False,
                    "name_variants": ["Acme Fleet", "Acme Fleet Analytics"],
                    "description_variants": [
                        "Fastest fleet telemetry",
                        "Most affordable fleet telemetry",
                    ],
                    "same_as": [],
                    "pages_with_org_schema": 1,
                },
            },
        },
        answers={
            Q_OFFER: answer(
                "supported", "Fleet telemetry subscriptions.", pages=(home,)
            ),
            Q_AUDIENCE: answer("supported", "Logistics operators.", pages=(home,)),
            Q_USE_CASE: answer("supported", "For fleet monitoring.", pages=(home,)),
            Q_DIFFERENTIATION: answer(
                "contradictory",
                "One page claims the fastest telemetry at $29/month; "
                "the other claims the most affordable at $99/month.",
                pages=both,
                missing=("which claim is current",),
            ),
            Q_SCOPE: answer(
                "contradictory",
                "$29/month cancel-anytime versus $99/month annual contract.",
                pages=both,
                missing=("the current pricing model",),
            ),
            Q_NEXT_STEP: answer("supported", "Start a trial.", pages=(pricing,)),
        },
        aax_result=weak_aax(),
    )


def missing_audience() -> SiteFixture:
    """(d) No audience statement anywhere on the site."""
    domain = "noaudience.example"
    home = HOME.format(d=domain)
    return SiteFixture(
        name="missing_audience",
        domain=domain,
        payload=_polished_payload(domain),
        answers={
            Q_OFFER: answer("supported", "Fleet sensor analytics.", pages=(home,)),
            Q_AUDIENCE: answer("unsupported", missing=("who this is for",)),
            Q_USE_CASE: answer("supported", "For fleet monitoring.", pages=(home,)),
            Q_DIFFERENTIATION: answer(
                "supported", "Per-vehicle pricing is stated.", pages=(home,)
            ),
            Q_SCOPE: answer(
                "supported", "Per-vehicle monthly subscription.", pages=(home,)
            ),
            Q_NEXT_STEP: answer("supported", "Start a trial.", pages=(home,)),
        },
        aax_result=clear_aax(),
    )


def clear_action_path() -> SiteFixture:
    """(e) Clear, evidenced action path: every answer grounded in the crawl."""
    domain = "clearpath.example"
    home = HOME.format(d=domain)
    pricing = PRICING.format(d=domain)
    return SiteFixture(
        name="clear_action_path",
        domain=domain,
        payload=_polished_payload(domain),
        answers={
            Q_OFFER: answer("supported", "Fleet sensor analytics.", pages=(home,)),
            Q_AUDIENCE: answer("supported", "Logistics operators.", pages=(home,)),
            Q_USE_CASE: answer(
                "supported",
                "For fleet monitoring and route planning.",
                pages=(home,),
            ),
            Q_DIFFERENTIATION: answer(
                "supported", "Per-vehicle pricing with no lock-in.", pages=(pricing,)
            ),
            Q_SCOPE: answer(
                "supported",
                "$29 per vehicle per month, cancel anytime.",
                pages=(pricing,),
            ),
            Q_NEXT_STEP: answer(
                "supported", "Start a trial from the pricing page.", pages=(pricing,)
            ),
        },
        aax_result=clear_aax(),
    )


FIXTURES = (
    unreachable_content,
    generic_copy,
    contradictory_claims,
    missing_audience,
    clear_action_path,
)


def run_exercise(fixture: SiteFixture) -> dict:
    """Run the real grounded answerability test with canned model answers.

    The mock replaces only the LLM call: page selection, grounding, and
    aggregation are the production code under test.
    """

    async def _fake(output_type, user, system, **kwargs):
        for fragment, canned in fixture.answers.items():
            if f"Question: {fragment}" in user:
                return canned
        raise AssertionError(f"unexpected question: {user[:120]}")

    with patch(
        "meshweave.ai.answerability.run_structured_test",
        new=AsyncMock(side_effect=_fake),
    ):
        return asyncio.run(run_answerability_test(fixture.payload))
