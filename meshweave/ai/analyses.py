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
    CitationSimulationResult,
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
    trace_user_email: str | None = None,
    trace_anonymous_user_id: str | None = None,
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
    traces to the user who triggered the analysis. Anonymous runs use their
    persistent browser ID instead. When available, the email is included as
    ``user_email`` metadata. All trace attributes are ignored when tracing is
    disabled.
    """
    with trace_attributes(
        user_id=trace_user_id or trace_anonymous_user_id,
        session_id=trace_session_id or uuid.uuid4().hex,
        tags=["aax"],
        metadata={"user_email": trace_user_email} if trace_user_email else None,
    ):
        return await _run_aax_analysis(payload)


async def _run_aax_analysis(payload: dict) -> dict[str, Any]:
    """AAX analysis implementation — see ``run_aax_analysis``."""
    if not _aax_enabled():
        return {"status": "disabled"}

    # Extract common data
    page = payload.get("page") or {}
    md_dict = payload.get("markdowns") or {}
    # Prefer the site's canonical identity (canonical/og:url host) over the
    # crawl host: when a staging host is crawled (e.g. internal docker
    # names), judging contact emails "same-domain" against the crawl host
    # produces false mismatches.
    domain = _canonical_domain(page, payload.get("domain") or "")

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
    # Email validation and the one-line summary run concurrently with the
    # three in-page tests. The summary synthesises the homepage-comprehension
    # and content-delta outputs, so it awaits those tasks directly.
    email_task = asyncio.create_task(
        _run_email_validation_task(payload, domain, skip_reasons)
    )
    summary_task = asyncio.create_task(
        _generate_aax_summary_task(
            domain, tasks.get("homepage_comprehension"), tasks.get("content_delta")
        )
    )

    results, skip_reasons = await _gather_results(tasks, skip_reasons)
    email_validation = await email_task
    summary_text = await summary_task

    # Citation simulation: grounded check that the brand is mentionable
    # and citable from the crawled pages. Fails soft — a skipped or
    # failed simulation never fails the AAX analysis.
    citation_sim = await _run_citation_simulation_task(payload)

    # Test 6: Contactability (heuristic — no LLM)
    contactability = _compute_contactability(payload)

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
        citation_sim=(
            CitationSimulationResult(**citation_sim) if citation_sim else None
        ),
        summary=summary_text,
        skip_reasons=skip_reasons,
    )

    return result.model_dump(mode="json")


async def _run_citation_simulation_task(payload: dict) -> dict | None:
    """Run the citation simulation, logging — never raising — failures."""
    from meshweave.ai.citation import run_citation_simulation

    try:
        return await run_citation_simulation(payload)
    except Exception as e:
        logger.warning("Citation simulation failed: %s", e)
        return None


def _aax_enabled() -> bool:
    """True when AAX analyses are enabled via the environment."""
    return os.getenv("AAX_ENABLED", "false").lower() == "true"


def _canonical_domain(page: dict, domain: str) -> str:
    """The site's canonical host, falling back to the crawl host."""
    canonical = page.get("canonical") or (page.get("og") or {}).get("url") or ""
    if not canonical:
        return domain
    canonical_host = urlsplit(canonical).hostname
    return canonical_host or domain


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
        tasks["homepage_comprehension"] = _homepage_comprehension_task(homepage_md)

    # Test 3: Meta Optimization
    if conditions.get("meta_optimization") is None:
        tasks["meta_optimization"] = _meta_optimization_task(page)

    # Test 5: Content Delta
    if conditions.get("content_delta") is None:
        task = _content_delta_task(domain, md_dict)
        if task:
            tasks["content_delta"] = task

    return tasks


def _homepage_max_chars() -> int:
    """Max homepage characters fed to the comprehension test."""
    try:
        return int(os.getenv("AAX_HOMEPAGE_MAX_CHARS", "50000"))
    except ValueError:
        return 50000


def _content_token_budget() -> int:
    """Token budget shared by all pages in the content-delta test."""
    try:
        return int(os.getenv("AAX_CONTENT_TOKEN_BUDGET", "24000"))
    except ValueError:
        return 24000


def _homepage_comprehension_task(homepage_md: str) -> asyncio.Task:
    """Create the homepage-comprehension structured-test task."""
    p, s = homepage_comprehension_prompt(
        homepage_markdown=homepage_md, max_chars=_homepage_max_chars()
    )
    return asyncio.create_task(run_structured_test(HomepageComprehensionResult, p, s))


def _meta_optimization_task(page: dict) -> asyncio.Task:
    """Create the meta-optimization structured-test task."""
    title, description, jsonld_summary, canonical = _meta_page_fields(page)
    og, twitter = _meta_social_fields(page)
    p, s = meta_optimization_prompt(
        title,
        description,
        og.get("title") or "",
        og.get("description") or "",
        jsonld_summary,
        og_image=og.get("image") or "",
        canonical=canonical,
        twitter_image=twitter.get("image") or "",
    )
    return asyncio.create_task(run_structured_test(MetaOptimizationResult, p, s))


def _meta_page_fields(page: dict) -> tuple[str, str, str, str]:
    """Title, description, JSON-LD summary, and canonical for the meta prompt."""
    return (
        page.get("title") or "",
        page.get("description") or "",
        summarize_jsonld(page.get("jsonld") or []),
        page.get("canonical") or "",
    )


def _meta_social_fields(page: dict) -> tuple[dict, dict]:
    """OG and Twitter card meta dicts, defaulted to empty dicts."""
    return page.get("og") or {}, page.get("twitter") or {}


def _content_delta_task(domain: str, md_dict: Any) -> asyncio.Task | None:
    """Create the content-delta task, or None when too few pages qualify."""
    selected_pages = select_pages_for_analysis(
        md_dict, token_budget=_content_token_budget()
    )
    if len(selected_pages) < 2:
        return None
    pages_text = _selected_pages_text(selected_pages)
    p, s = content_delta_prompt(pages_text)
    return asyncio.create_task(run_structured_test(ContentDeltaResult, p, s))


def _selected_pages_text(selected_pages: list[dict]) -> str:
    """Concatenate selected pages' titles and markdown for the prompt."""
    pages_text = ""
    for pg in selected_pages:
        pages_text += f"\n=== {pg['title'].upper()} ({pg['url']}) ===\n"
        pages_text += pg["markdown"] + "\n"
    return pages_text


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


async def _run_email_validation_task(
    payload: dict,
    domain: str,
    skip_reasons: dict[str, str],
) -> Any | None:
    """Run the email-validation LLM test when quality emails exist.

    Returns the validated result, or None when no quality emails were found.
    The skip reason is recorded on ``skip_reasons``.
    """
    quality_emails = _filter_quality_emails(payload)
    if not quality_emails:
        skip_reasons["email_validation"] = "No valid email addresses found to validate"
        return None
    try:
        p, s = email_validation_prompt(domain, quality_emails)
        return await run_structured_test(EmailValidationResult, p, s)
    except Exception as e:
        logger.warning("Email validation test failed: %s", e)
        skip_reasons["email_validation"] = f"Test failed: {e}"
        return None


async def _generate_aax_summary_task(
    domain: str,
    hc_task: asyncio.Task | None,
    cd_task: asyncio.Task | None,
) -> str:
    """Generate the one-line summary verdict for the hero card.

    Awaits the homepage-comprehension and content-delta tasks it
    synthesises, treating a failed or absent task as missing data so a
    single test failure does not also lose the summary.
    """
    from meshweave.ai.models import AAXSummaryResult

    try:
        hc_data = await _await_or_none(hc_task)
        cd_data = await _await_or_none(cd_task)
        hc_dict = _as_dict(hc_data)
        cd_dict = _as_dict(cd_data)
        p, s = aax_summary_prompt(hc_dict, cd_dict)

        summary_result = await run_structured_test(AAXSummaryResult, p, s)
        return summary_result.summary
    except Exception as e:
        logger.warning("AAX summary generation failed: %s", e)
        return ""


async def _await_or_none(task: asyncio.Task | None) -> Any:
    """Await a task, returning None when it is absent or failed."""
    if task is None:
        return None
    try:
        return await task
    except Exception:
        return None


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
    unique_emails, by_url, sources, md_dict, domain = _contactability_email_data(
        payload
    )
    contact_pages = _contact_page_urls(md_dict)

    result = ContactabilityResult()
    result.email_count = len(unique_emails)

    same_domain_emails = _same_domain_emails(unique_emails, domain)
    homepage_emails = _homepage_emails(by_url)
    contact_emails = _contact_emails(by_url, contact_pages)

    pts, result = _score_email_presence(0, result, same_domain_emails, unique_emails)
    pts, result = _apply_contact_points(
        pts,
        result,
        sources=sources,
        contact_pages=contact_pages,
        homepage_emails=homepage_emails,
        contact_emails=contact_emails,
    )
    pts, result = _apply_schema_points(pts, result, payload, md_dict, unique_emails)

    pts, penalties = _contactability_penalties(
        pts,
        [],
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


def _contactability_email_data(
    payload: dict,
) -> tuple[list, dict, list, dict, str]:
    """(unique emails, by_url, sources, markdowns, domain) from a payload."""
    emails = payload.get("emails") or {}
    return (
        emails.get("unique") or [],
        emails.get("by_url") or {},
        emails.get("sources") or [],
        payload.get("markdowns") or {},
        payload.get("domain") or "",
    )


def _same_domain_emails(unique_emails: list, domain: str) -> list[str]:
    """Emails whose address contains the site's domain."""
    if not domain:
        return []
    return [e for e in unique_emails if domain.lower() in e.lower()]


def _contact_page_urls(md_dict: dict) -> list[str]:
    """Crawled URLs that look like contact/about/support pages."""
    return [
        url
        for url in md_dict
        if any(p in url.lower() for p in ("/contact", "/about", "/support"))
    ]


def _has_mailto_link(sources: list) -> bool:
    """True when any email source was found via a mailto link."""
    return any("mailto" in _found_as(src) for src in sources)


def _has_generic_contact_email(unique_emails: list) -> bool:
    """True when any email uses a generic contact prefix."""
    generic_prefixes = ("support@", "info@", "hello@", "contact@", "help@")
    return any(
        any(e.lower().startswith(p) for p in generic_prefixes) for e in unique_emails
    )


def _has_phone_jsonld(all_jsonld: list[dict]) -> bool:
    """True when any JSON-LD block declares a phone number."""
    return any(ld.get("telephone") or ld.get("phone") for ld in all_jsonld)


def _apply_contact_points(
    pts: int,
    result: ContactabilityResult,
    *,
    sources: list,
    contact_pages: list[str],
    homepage_emails: list[str],
    contact_emails: list[str],
) -> tuple[int, ContactabilityResult]:
    """Award mailto, contact-page, and contact-email points."""
    if _has_mailto_link(sources):
        result.has_mailto = True
        pts += 10

    if contact_pages:
        result.has_contact_page = True
        pts += 10

    # Email on homepage or contact page. emails_by_url is keyed by the full
    # crawled URL (e.g. "https://example.com/"), so detect the homepage by its
    # root path rather than a literal "/" key.
    if homepage_emails or contact_emails:
        pts += 15

    return pts, result


def _apply_schema_points(
    pts: int,
    result: ContactabilityResult,
    payload: dict,
    md_dict: dict,
    unique_emails: list,
) -> tuple[int, ContactabilityResult]:
    """Award JSON-LD ContactPoint, social-link, and generic-email points."""
    all_jsonld = _contactability_jsonld(payload, md_dict)

    # JSON-LD ContactPoint
    if _has_contactpoint_schema(all_jsonld):
        result.has_contact_point_schema = True
        pts += 15

    # Social links (sameAs)
    entity = (payload.get("audit") or {}).get("entity") or {}
    same_as = entity.get("same_as") or []
    if same_as:
        result.has_social_links = True
        pts += 10

    if _has_generic_contact_email(unique_emails):
        pts += 10

    # Phone number in JSON-LD
    if _has_phone_jsonld(all_jsonld):
        pts += 10

    return pts, result


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
    all_jsonld.extend(_dict_jsonld_blocks(page.get("jsonld") or []))
    for _url, data in md_dict.items():
        if isinstance(data, dict):
            pg = data.get("page") or data
            all_jsonld.extend(_dict_jsonld_blocks(pg.get("jsonld") or []))
    return all_jsonld


def _dict_jsonld_blocks(items: Any) -> list[dict]:
    """Dict entries from a JSON-LD collection."""
    return [ld for ld in items if isinstance(ld, dict)]


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
