"""AAX analysis orchestrator.

Checks preconditions, runs eligible tests concurrently, computes
the Contactability heuristic, and produces the aggregate AAX result.
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from typing import Any
from urllib.parse import urlsplit

from meshweave.ai.models import (
    AAXAnalysisResult,
    ContactabilityResult,
    ContentDeltaResult,
    EmailValidationResult,
    HomepageComprehensionResult,
    MetaOptimizationResult,
)
from meshweave.ai.observability import trace_attributes
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


async def run_aax_analysis(
    payload: dict,
    *,
    trace_user_id: str | None = None,
    trace_session_id: str | None = None,
) -> dict[str, Any]:
    """Run all AAX tests and return the analysis result as a dict.

    Returns a dict matching the AAXAnalysisResult schema, suitable
    for storage in ai_analysis_json.

    If AAX_ENABLED is not "true", returns {"status": "disabled"}.

    When Langfuse tracing is enabled, the LLM calls made by this run are
    grouped into a single Langfuse session so the whole analysis shows up as
    one unit: ``trace_session_id`` when provided (e.g. the originating crawl
    id), otherwise a fresh id per run. ``trace_user_id`` attributes the
    traces to the user who triggered the analysis. Both are ignored when
    tracing is disabled.
    """
    with trace_attributes(
        user_id=trace_user_id,
        session_id=trace_session_id or uuid.uuid4().hex,
        tags=["aax"],
    ):
        return await _run_aax_analysis(payload)


async def _run_aax_analysis(payload: dict) -> dict[str, Any]:
    """AAX analysis implementation — see ``run_aax_analysis``."""
    if os.getenv("AAX_ENABLED", "false").lower() != "true":
        return {"status": "disabled"}

    # Extract common data
    page = payload.get("page") or {}
    domain = payload.get("domain") or ""
    md_dict = payload.get("markdowns") or {}

    # Prefer the site's canonical identity (canonical/og:url host) over the
    # crawl host: when a staging host is crawled (e.g. internal docker
    # names), judging contact emails "same-domain" against the crawl host
    # produces false mismatches.
    canonical = page.get("canonical") or (page.get("og") or {}).get("url") or ""
    if canonical:
        canonical_host = urlsplit(canonical).hostname
        if canonical_host:
            domain = canonical_host

    # Get homepage markdown (reuse preconditions helper)
    from meshweave.ai.preconditions import _get_homepage_markdown

    homepage_md = _get_homepage_markdown(payload)

    # Check preconditions
    conditions = check_all(payload)
    skip_reasons = {k: v for k, v in conditions.items() if v is not None}

    # Run eligible tests concurrently
    tasks = _build_aax_tasks(
        conditions,
        domain=domain,
        homepage_md=homepage_md,
        page=page,
        md_dict=md_dict,
    )
    results, skip_reasons = await _gather_results(tasks, skip_reasons)

    # Test 6: Contactability (heuristic — no LLM)
    contactability = _compute_contactability(payload)

    # Test 7: Email Validation (LLM — only if emails were found)
    email_validation, skip_reasons = await _run_email_validation(
        payload, domain, skip_reasons
    )

    # Generate one-line summary verdict for the hero card
    summary_text = await _generate_aax_summary(domain, results)

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


def _build_aax_tasks(
    conditions: dict,
    *,
    domain: str,
    homepage_md: str,
    page: dict,
    md_dict: Any,
) -> dict[str, asyncio.Task]:
    """Schedule the eligible AAX LLM tests concurrently."""
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
        twitter = page.get("twitter") or {}
        p, s = meta_optimization_prompt(
            page.get("title") or "",
            page.get("description") or "",
            og.get("title") or "",
            og.get("description") or "",
            summarize_jsonld(page.get("jsonld") or []),
            og_image=og.get("image") or "",
            canonical=page.get("canonical") or "",
            twitter_image=twitter.get("image") or "",
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

    return tasks


async def _gather_results(
    tasks: dict[str, asyncio.Task],
    skip_reasons: dict[str, str],
) -> tuple[dict[str, Any], dict[str, str]]:
    """Await all scheduled tasks, collecting results and failures."""
    results: dict[str, Any] = {}
    if tasks:
        done = await asyncio.gather(*tasks.values(), return_exceptions=True)
        for key, result in zip(tasks.keys(), done):
            if isinstance(result, Exception):
                logger.warning("AAX test %s failed: %s", key, result)
                skip_reasons[key] = f"Test failed: {result}"
            else:
                results[key] = result
    return results, skip_reasons


async def _run_email_validation(
    payload: dict,
    domain: str,
    skip_reasons: dict[str, str],
) -> tuple[Any, dict[str, str]]:
    """Run the email-validation LLM test when quality emails exist."""
    quality_emails = _filter_quality_emails(payload)
    if not quality_emails:
        skip_reasons["email_validation"] = "No valid email addresses found to validate"
        return None, skip_reasons
    try:
        p, s = email_validation_prompt(domain, quality_emails)
        return await run_structured_test(EmailValidationResult, p, s), skip_reasons
    except Exception as e:
        logger.warning("Email validation test failed: %s", e)
        skip_reasons["email_validation"] = f"Test failed: {e}"
        return None, skip_reasons


async def _generate_aax_summary(domain: str, results: dict[str, Any]) -> str:
    """Generate the one-line summary verdict for the hero card."""
    from meshweave.ai.models import AAXSummaryResult

    try:
        hc_data = results.get("homepage_comprehension")
        cd_data = results.get("content_delta")
        hc_dict = _as_dict(hc_data)
        cd_dict = _as_dict(cd_data)
        p, s = aax_summary_prompt(domain, hc_dict, cd_dict)

        summary_result = await run_structured_test(AAXSummaryResult, p, s)
        return summary_result.summary
    except Exception as e:
        logger.warning("AAX summary generation failed: %s", e)
        return ""


def _as_dict(data: Any) -> dict | None:
    """Coerce a structured-test result to a plain dict, or None."""
    model_dump = getattr(data, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump()
        return dumped if isinstance(dumped, dict) else None
    return data if isinstance(data, dict) else None


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

    pts, result = _score_email_presence(pts, result, same_domain_emails, unique_emails)

    # mailto links
    if any("mailto" in _found_as(src) for src in sources):
        result.has_mailto = True
        pts += 10

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

    # Email on homepage or contact page. emails_by_url is keyed by the full
    # crawled URL (e.g. "https://example.com/"), so detect the homepage by its
    # root path rather than a literal "/" key.
    homepage_emails = _homepage_emails(by_url)
    contact_emails = _contact_emails(by_url, contact_pages)
    if homepage_emails or contact_emails:
        pts += 15

    # JSON-LD ContactPoint
    all_jsonld = _contactability_jsonld(payload, md_dict)
    if _has_contactpoint_schema(all_jsonld):
        result.has_contact_point_schema = True
        pts += 15

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
        pts += 10

    # Phone number in JSON-LD
    if any(ld.get("telephone") or ld.get("phone") for ld in all_jsonld):
        pts += 10

    # Penalties
    pts, penalties = _contactability_penalties(
        pts,
        penalties,
        unique_emails=unique_emails,
        has_mailto=result.has_mailto,
        by_url=by_url,
        homepage_emails=homepage_emails,
        contact_emails=contact_emails,
        same_domain_emails=same_domain_emails,
    )

    result.score = max(0.0, min(100.0, float(pts)))
    result.penalties = penalties

    return result


def _contactability_penalties(
    pts: int,
    penalties: list[str],
    *,
    unique_emails: list[str],
    has_mailto: bool,
    by_url: dict,
    homepage_emails: list[str],
    contact_emails: list[str],
    same_domain_emails: list[str],
) -> tuple[int, list[str]]:
    """Apply contactability point penalties and log their reasons."""
    if unique_emails and not has_mailto:
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

    return pts, penalties


def _found_as(src: Any) -> list[str]:
    """Normalize the ``found_as`` source field to a list of strings."""
    if not isinstance(src, dict):
        return []
    found_as = src.get("found_as", [])
    if isinstance(found_as, str):
        return [found_as]
    return list(found_as) if isinstance(found_as, list) else []


def _score_email_presence(
    pts: int,
    result: ContactabilityResult,
    same_domain_emails: list[str],
    unique_emails: list[str],
) -> tuple[int, ContactabilityResult]:
    """Award points for same-domain vs third-party email presence."""
    if same_domain_emails:
        result.has_email = True
        return pts + 20, result
    if unique_emails:
        result.has_email = True
        return pts + 5, result  # Third-party emails only
    return pts, result


def _homepage_emails(by_url: dict) -> list[str]:
    """Emails found on the homepage (root path) keyed in emails_by_url."""
    from urllib.parse import urlparse

    emails: list[str] = []
    for u, emails_list in by_url.items():
        if urlparse(u).path in ("", "/"):
            emails.extend(emails_list or [])
    return emails


def _contact_emails(by_url: dict, contact_pages: list[str]) -> list[str]:
    """Emails found on contact/about pages."""
    emails: list[str] = []
    for cp in contact_pages:
        emails.extend(by_url.get(cp, []) or [])
    return emails


def _contactability_jsonld(payload: dict, md_dict: dict) -> list[dict]:
    """All JSON-LD dicts from the start page and markdown pages."""
    all_jsonld: list[dict] = []
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
    return all_jsonld


def _has_contactpoint_schema(all_jsonld: list[dict]) -> bool:
    """True when any JSON-LD block declares a ContactPoint/contact type."""
    for ld in all_jsonld:
        ld_type = (ld.get("@type") or "").lower()
        if "contactpoint" in ld_type or "contact" in ld_type:
            return True
    return False


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

    email_pages = _email_page_map(by_url)
    email_sources = _email_source_map(sources)

    quality = []
    for email in unique_emails:
        item = _quality_item(email, email_pages, email_sources)
        if item is not None:
            quality.append(item)

    return quality


def _email_page_map(by_url: dict) -> dict[str, list[str]]:
    """Build reverse map: email → list of pages it was found on."""
    email_pages: dict[str, list[str]] = {}
    for url, email_list in by_url.items():
        if isinstance(email_list, list):
            for email in email_list:
                email_pages.setdefault(email, []).append(url)
    return email_pages


def _email_source_map(sources: list) -> dict[str, str]:
    """Build reverse map: email → source type (from found_as field)."""
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
    return email_sources


def _quality_item(
    email: str,
    email_pages: dict[str, list[str]],
    email_sources: dict[str, str],
) -> dict | None:
    """Return a quality email item, or None if the email should be skipped."""
    local_part = email.split("@")[0].lower() if "@" in email else ""

    # Skip known-bad prefixes
    if any(local_part.startswith(prefix) for prefix in _BAD_EMAIL_PREFIXES):
        return None

    # Skip very short local parts (likely garbled)
    if len(local_part) < 2:
        return None

    pages = email_pages.get(email, [])
    source = email_sources.get(email, "text")

    # Show page paths, not full crawl URLs: the crawl host can differ
    # from the site's canonical domain (staging/internal crawls), and a
    # host mismatch between the prompt's domain and the listed page
    # URLs reads as "emails not on the company's site".
    page_display = ", ".join(_url_path(p) for p in pages[:5]) or "unknown"

    return {
        "email": email,
        "source": source,
        "page": page_display,
    }


def _url_path(url: str) -> str:
    """Return '/path' for a URL, or the original string if unparseable."""
    try:
        parts = urlsplit(url)
        return parts.path or "/"
    except Exception:
        return url
