"""AAX analysis orchestrator.

Checks preconditions, runs eligible tests concurrently, computes
the Contactability heuristic, and produces the aggregate AAX result.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from meshweave.ai.models import (
    AAXAnalysisResult,
    ContactabilityResult,
    ContentDeltaResult,
    EmailValidationResult,
    HomepageComprehensionResult,
    MetaOptimizationResult,
)
from meshweave.ai.preconditions import check_all
from meshweave.ai.prompts import (
    aax_summary_prompt,
    content_delta_prompt,
    email_validation_prompt,
    homepage_comprehension_prompt,
    meta_optimization_prompt,
    select_pages_for_analysis,
    summarize_jsonld,
)
from meshweave.ai.runner import run_structured_test

logger = logging.getLogger(__name__)


async def run_aax_analysis(payload: dict) -> dict[str, Any]:
    """Run all AAX tests and return the analysis result as a dict.

    Returns a dict matching the AAXAnalysisResult schema, suitable
    for storage in ai_analysis_json.

    If AAX_ENABLED is not "true", returns {"status": "disabled"}.
    """
    if os.getenv("AAX_ENABLED", "false").lower() != "true":
        return {"status": "disabled"}

    # Extract common data
    page = payload.get("page") or {}
    domain = payload.get("domain") or ""
    md_dict = payload.get("markdowns") or {}

    # Get homepage markdown (reuse preconditions helper)
    from meshweave.ai.preconditions import _get_homepage_markdown

    homepage_md = _get_homepage_markdown(payload)

    # Check preconditions
    conditions = check_all(payload)
    skip_reasons = {k: v for k, v in conditions.items() if v is not None}

    # Run eligible tests concurrently
    tasks: dict[str, asyncio.Task] = {}

    # Test 2: Homepage Comprehension
    if conditions.get("homepage_comprehension") is None:
        p, s = homepage_comprehension_prompt(domain, homepage_md)
        tasks["homepage_comprehension"] = asyncio.create_task(
            run_structured_test(HomepageComprehensionResult, p, s)
        )

    # Test 3: Meta Optimization
    if conditions.get("meta_optimization") is None:
        og = page.get("og") or {}
        p, s = meta_optimization_prompt(
            page.get("title") or "",
            page.get("description") or "",
            og.get("title") or "",
            og.get("description") or "",
            summarize_jsonld(page.get("jsonld") or []),
        )
        tasks["meta_optimization"] = asyncio.create_task(
            run_structured_test(MetaOptimizationResult, p, s)
        )

    # Test 5: Content Delta
    if conditions.get("content_delta") is None:
        selected_pages = select_pages_for_analysis(md_dict)
        if len(selected_pages) >= 2:
            pages_text = ""
            for pg in selected_pages:
                pages_text += f"\n=== {pg['title'].upper()} ({pg['url']}) ===\n"
                pages_text += pg["markdown"] + "\n"
            p, s = content_delta_prompt(domain, pages_text)
            tasks["content_delta"] = asyncio.create_task(
                run_structured_test(ContentDeltaResult, p, s)
            )

    # Wait for all tasks
    results: dict[str, Any] = {}
    if tasks:
        done = await asyncio.gather(*tasks.values(), return_exceptions=True)
        for key, result in zip(tasks.keys(), done):
            if isinstance(result, Exception):
                logger.warning("AAX test %s failed: %s", key, result)
                skip_reasons[key] = f"Test failed: {result}"
            else:
                results[key] = result

    # Test 6: Contactability (heuristic — no LLM)
    contactability = _compute_contactability(payload)

    # Test 7: Email Validation (LLM — only if emails were found)
    email_validation = None
    quality_emails = _filter_quality_emails(payload)
    if quality_emails:
        try:
            p, s = email_validation_prompt(domain, quality_emails)
            email_validation = await run_structured_test(EmailValidationResult, p, s)
        except Exception as e:
            logger.warning("Email validation test failed: %s", e)
            skip_reasons["email_validation"] = f"Test failed: {e}"
    else:
        skip_reasons["email_validation"] = "No valid email addresses found to validate"

    # Generate one-line summary verdict for the hero card
    summary_text = ""
    try:
        hc_data = (
            results.get("homepage_comprehension")
        )
        cd_data = (
            results.get("content_delta")
        )
        hc_dict = (
            hc_data.model_dump()
            if hc_data and hasattr(hc_data, "model_dump")
            else (hc_data if isinstance(hc_data, dict) else None)
        )
        cd_dict = (
            cd_data.model_dump()
            if cd_data and hasattr(cd_data, "model_dump")
            else (cd_data if isinstance(cd_data, dict) else None)
        )
        p, s = aax_summary_prompt(domain, hc_dict, cd_dict)
        from meshweave.ai.models import AAXSummaryResult

        summary_result = await run_structured_test(
            AAXSummaryResult, p, s
        )
        summary_text = summary_result.summary
    except Exception as e:
        logger.warning("AAX summary generation failed: %s", e)

    # Build aggregate result
    completed = len(results) + (1 if email_validation else 0)
    skipped = len(skip_reasons)

    result = AAXAnalysisResult(
        status="completed",
        model_id=os.getenv("OPENAILIKE_LLM", "auto"),
        tests_completed=completed,
        tests_skipped=skipped,
        homepage_comprehension=results.get("homepage_comprehension"),
        meta_optimization=results.get("meta_optimization"),
        content_delta=results.get("content_delta"),
        contactability=contactability,
        email_validation=email_validation,
        llms_txt=payload.get("llms_txt"),
        summary=summary_text,
        skip_reasons=skip_reasons,
    )

    return result.model_dump(mode="json")


def _compute_contactability(payload: dict) -> ContactabilityResult:
    """Test 6: Pure heuristic scoring from crawl data.

    No LLM call — scores based on emails, contact pages, JSON-LD, social links.
    """
    pts = 0
    penalties: list[str] = []
    result = ContactabilityResult()

    # Email data
    emails = payload.get("emails") or {}
    unique_emails = emails.get("unique") or []
    by_url = emails.get("by_url") or {}
    sources = emails.get("sources") or []

    # Check for emails
    domain = payload.get("domain") or ""
    same_domain_emails = [
        e for e in unique_emails if domain and domain.lower() in e.lower()
    ]
    result.email_count = len(unique_emails)

    if same_domain_emails:
        result.has_email = True
        pts += 20
    elif unique_emails:
        result.has_email = True
        pts += 5  # Third-party emails only

    # mailto links
    for src in sources:
        found_as = src.get("found_as", []) if isinstance(src, dict) else []
        if isinstance(found_as, str):
            found_as = [found_as]
        if "mailto" in found_as:
            result.has_mailto = True
            pts += 10
            break

    # Contact/about page
    md_dict = payload.get("markdowns") or {}
    contact_pages = [
        url
        for url in md_dict
        if any(p in url.lower() for p in ("/contact", "/about", "/support"))
    ]
    if contact_pages:
        result.has_contact_page = True
        pts += 10

    # Email on homepage or contact page
    homepage_emails = by_url.get("/", []) or by_url.get("", [])
    contact_emails = []
    for cp in contact_pages:
        contact_emails.extend(by_url.get(cp, []) or [])
    if homepage_emails or contact_emails:
        pts += 15

    # JSON-LD ContactPoint
    all_jsonld = []
    page = payload.get("page") or {}
    for ld in page.get("jsonld") or []:
        if isinstance(ld, dict):
            all_jsonld.append(ld)
    for _url, data in md_dict.items():
        if isinstance(data, dict):
            pg = data.get("page") or data
            for ld in pg.get("jsonld") or []:
                if isinstance(ld, dict):
                    all_jsonld.append(ld)

    for ld in all_jsonld:
        ld_type = (ld.get("@type") or "").lower()
        if "contactpoint" in ld_type or "contact" in ld_type:
            result.has_contact_point_schema = True
            pts += 15
            break

    # Social links (sameAs)
    entity = (payload.get("audit") or {}).get("entity") or {}
    same_as = entity.get("same_as") or []
    if same_as:
        result.has_social_links = True
        pts += 10

    # Generic contact email
    generic_prefixes = ("support@", "info@", "hello@", "contact@", "help@")
    if any(
        any(e.lower().startswith(p) for p in generic_prefixes) for e in unique_emails
    ):
        result.has_generic_email = True
        pts += 10

    # Phone number in JSON-LD
    for ld in all_jsonld:
        if ld.get("telephone") or ld.get("phone"):
            result.has_phone = True
            pts += 10
            break

    # Penalties
    if unique_emails and not result.has_mailto:
        pts -= 10
        penalties.append("All emails are obfuscated-only (no mailto links)")

    # Emails only on legal pages
    legal_pages = [
        url
        for url in by_url
        if any(p in url.lower() for p in ("privacy", "terms", "legal"))
    ]
    if legal_pages and not homepage_emails and not contact_emails:
        pts -= 15
        penalties.append(
            "Emails only found on legal pages (not intended as contact points)"
        )

    # No same-domain emails
    if not same_domain_emails:
        pts = min(pts, 20)
        penalties.append("No same-domain email addresses found")

    result.score = max(0.0, min(100.0, float(pts)))
    result.penalties = penalties

    return result


# Known-bad email prefixes that should never be treated as valid contacts
_BAD_EMAIL_PREFIXES = (
    "noreply",
    "no-reply",
    "no_reply",
    "mailer-daemon",
    "postmaster",
    "bounce",
    "unsubscribe",
    "abuse",
    "spam",
    "daemon",
    "root",
    "nobody",
    "null",
    "devnull",
)


def _filter_quality_emails(payload: dict) -> list[dict]:
    """Filter out known-bad emails and return quality candidates.

    Returns a list of dicts with email, source, and page context
    for the LLM email validation test.
    """
    emails = payload.get("emails") or {}
    unique_emails = emails.get("unique") or []
    by_url = emails.get("by_url") or {}
    sources = emails.get("sources") or []

    if not unique_emails:
        return []

    # Build reverse map: email → list of pages it was found on
    email_pages: dict[str, list[str]] = {}
    for url, email_list in by_url.items():
        if isinstance(email_list, list):
            for email in email_list:
                email_pages.setdefault(email, []).append(url)

    # Build reverse map: email → source type (from found_as field)
    email_sources: dict[str, str] = {}
    if isinstance(sources, list):
        for src in sources:
            if isinstance(src, dict):
                found_as = src.get("found_as", [])
                if isinstance(found_as, list):
                    source_str = ", ".join(found_as) if found_as else "unknown"
                else:
                    source_str = str(found_as) if found_as else "unknown"
                email_sources[src.get("email", "")] = source_str

    quality = []
    for email in unique_emails:
        local_part = email.split("@")[0].lower() if "@" in email else ""

        # Skip known-bad prefixes
        if any(local_part.startswith(prefix) for prefix in _BAD_EMAIL_PREFIXES):
            continue

        # Skip very short local parts (likely garbled)
        if len(local_part) < 2:
            continue

        pages = email_pages.get(email, [])
        source = email_sources.get(email, "text")

        quality.append(
            {
                "email": email,
                "source": source,
                "page": ", ".join(pages[:3]) if pages else "unknown",
            }
        )

    return quality
