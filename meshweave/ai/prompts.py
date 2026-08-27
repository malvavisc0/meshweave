"""Prompt templates for AAX analysis tests.

Each function returns (user_prompt, system_prompt) for a specific test.

Test mapping (non-LLM tests like Contactability are not included here):
  - Test 2: Homepage Comprehension
  - Test 3: Meta Optimization
  - Test 5: Content Delta
  - Test 7: Email Validation
"""

from __future__ import annotations

import json

CHARS_PER_TOKEN = 4  # rough chars-per-token estimate for budget calculations

SYSTEM_BASE = (
    "You are an AI analysis agent evaluating websites for AI readiness. "
    "Always respond in valid JSON matching the requested schema exactly. "
    "Be precise and factual. If you don't know something, say so rather "
    "than guessing."
)


def homepage_comprehension_prompt(
    domain: str, homepage_markdown: str, max_chars: int = 50_000
) -> tuple[str, str]:
    """Test 2: What can the LLM understand from the homepage alone?"""
    if len(homepage_markdown) > max_chars:
        homepage_markdown = homepage_markdown[:max_chars] + "\n\n[...truncated...]"

    user = f"""You are an AI agent that has been given the URL https://{domain}/ and asked to evaluate the website. Here is the content of the homepage:

---
{homepage_markdown}
---

Based ONLY on this content, extract the following information.

Respond in this JSON format:
{{
  "brand": "company or brand name",
  "product": "what product or service they offer",
  "target_audience": "who this is for",
  "key_features": ["feature1", "feature2", ...],
  "call_to_action": "the primary CTA on the page",
  "clarity": "one of: clear, somewhat_clear, unclear",
  "information_density": "one of: dense, adequate, sparse, bloated",
  "would_remember": true or false
}}"""
    return user, SYSTEM_BASE


def meta_optimization_prompt(
    title: str,
    description: str,
    og_title: str,
    og_description: str,
    jsonld_summary: str,
    og_image: str = "",
    canonical: str = "",
    twitter_image: str = "",
) -> tuple[str, str]:
    """Test 3: Are the meta tags optimized for LLM consumption?"""
    user = f"""You are an AI agent evaluating a website's metadata. You have NOT visited the website — you only have its metadata tags:

Title: {title or "(empty)"}
Description: {description or "(empty)"}
OG Title: {og_title or "(empty)"}
OG Description: {og_description or "(empty)"}
OG Image: {og_image or "(empty)"}
Twitter Image: {twitter_image or "(empty)"}
Canonical URL: {canonical or "(empty)"}
JSON-LD: {jsonld_summary}

Based ONLY on this metadata, extract the following information.

Respond in this JSON format:
{{
  "brand": "company or brand name",
  "product": "what product or service they offer",
  "target_audience": "who this is for",
  "would_click_through": true or false,
  "completeness": "one of: complete, partial, minimal",
  "clarity": "one of: clear, somewhat_clear, unclear",
  "llm_optimization": "one of: optimized, adequate, poor",
  "missing_fields": ["field1", ...],
  "improvement_suggestions": ["suggestion1", ...]
}}
"""
    return user, SYSTEM_BASE


def email_validation_prompt(
    domain: str, emails_with_context: list[dict]
) -> tuple[str, str]:
    """Test 7: Validate which emails are actually reachable contacts."""
    email_lines = []
    for entry in emails_with_context:
        email = entry.get("email", "")
        source = entry.get("source", "unknown")
        page = entry.get("page", "unknown")
        email_lines.append(f"- {email} (found on {page}, via {source})")

    email_list = "\n".join(email_lines)

    user = f"""Here are email addresses found on {domain}:

{email_list}

Which of these emails would actually allow a human to contact
the company for sales, support, or general inquiries?

Consider:
- "noreply@" addresses are not valid contacts
- "privacy@" or "legal@" addresses are for legal matters, not sales/support
- Emails on tracking/analytics domains are not the company's contacts
- Obfuscated or garbled addresses may not be real

Respond in this JSON format:
{{
  "valid_contacts": [
    {{"email": "support@example.com", "reason": "explicit support address", "contact_type": "support"}}
  ],
  "rejected_contacts": [
    {{"email": "noreply@example.com", "reason": "no-reply address"}}
  ],
  "best_contact": "best email to reach the company, or null if none found",
  "confidence": "one of: high, medium, low"
}}"""
    return user, SYSTEM_BASE


def content_delta_prompt(domain: str, pages_content: str) -> tuple[str, str]:
    """Test 5: Does adding more pages improve AI understanding?"""
    user = f"""You are an AI agent that has been given the URL https://{domain}/ and asked to thoroughly understand the company and its offerings.

Here is the content from multiple pages of their website:

{pages_content}

---

Based on ALL this content, provide a comprehensive summary.
If pricing data is absent from the content, set pricing fields to null.

Respond in this JSON format:
{{
  "company": {{"name": "...", "description": "..."}},
  "product": {{"name": "...", "category": "...", "description": "...", "features": ["..."]}},
  "pricing": {{"model": "...", "tiers": ["..."]}},
  "target_audience": "...",
  "strengths": ["strength1", ...],
  "weaknesses": ["weakness1", ...],
  "coherence": "one of: consistent, somewhat_consistent, contradictory",
  "completeness": "one of: comprehensive, adequate, incomplete"
}}"""
    return user, SYSTEM_BASE


def aax_summary_prompt(
    domain: str,
    homepage_comprehension: dict | None = None,
    content_delta: dict | None = None,
) -> tuple[str, str]:
    """Generate a one-line diagnostic verdict for the hero card."""
    hc = homepage_comprehension or {}
    cd = content_delta or {}

    hc_text = ""
    if hc:
        hc_text = (
            f"Brand identified: {hc.get('brand', 'unknown')}. "
            f"Product: {hc.get('product', 'unknown')}. "
            f"Audience: {hc.get('target_audience', 'unknown')}. "
            f"Clarity: {hc.get('clarity', 'unknown')}. "
            f"CTA: {hc.get('call_to_action', 'none')}. "
            f"Remembered: {hc.get('would_remember', False)}."
        )
    else:
        hc_text = "No homepage comprehension data available."

    cd_text = ""
    if not cd:
        cd_text = "No content analysis data available."
    else:
        strengths = cd.get("strengths", [])
        weaknesses = cd.get("weaknesses", [])
        cd_text = (
            f"Strengths: {', '.join(strengths[:3]) if strengths else 'none'}. "
            f"Weaknesses: {', '.join(weaknesses[:3]) if weaknesses else 'none'}. "
            f"Coherence: {cd.get('coherence', 'unknown')}."
        )

    user = f"""You are an AI diagnostics agent evaluating https://{domain}/.

Homepage comprehension data:
{hc_text}

Content analysis data:
{cd_text}

Write EXACTLY ONE sentence (under 30 words) that describes how well
AI agents can understand and recommend this website. Be specific —
mention what the site does and who it's for. Start with a verb or
pronoun. Do NOT wrap your summary in quotation marks.

Example good outputs:
- AI agents can clearly identify Pangolin as a zero-trust access
  platform for IT teams and would confidently recommend it.
- AI agents struggle to understand this site's purpose due to sparse
  content and missing structured data.

Respond in this JSON format:
{{
  "summary": "your one-sentence verdict here"
}}"""
    return user, SYSTEM_BASE


def summarize_jsonld(jsonld: list) -> str:
    """Summarize JSON-LD objects for the meta optimization prompt.

    Includes the fields an LLM consuming the page's structured data would
    actually read — not just type/name/description. Without these, a
    FAQPage with a full mainEntity reports as an "empty type" and the
    meta test penalizes sites for data they do publish.
    """
    if not jsonld:
        return "None"

    flat_keys = (
        "@type",
        "name",
        "description",
        "applicationCategory",
        "operatingSystem",
        "softwareVersion",
        "url",
        "logo",
        "datePublished",
        "dateModified",
        "isAccessibleForFree",
        "priceCurrency",
        "contactType",
        "email",
        "availableLanguage",
    )
    nested_name_keys = ("author", "publisher", "provider")
    summaries = []
    for obj in jsonld[:5]:
        if not isinstance(obj, dict):
            continue
        summary: dict = {}
        for key in flat_keys:
            val = obj.get(key)
            if val:
                summary[key] = val
        for key in nested_name_keys:
            nested = obj.get(key)
            if isinstance(nested, dict) and nested.get("name"):
                summary[key] = nested["name"]
        contact_point = obj.get("contactPoint")
        if isinstance(contact_point, dict):
            summary["contactPoint"] = {
                k: contact_point[k]
                for k in ("contactType", "email", "telephone", "availableLanguage")
                if contact_point.get(k)
            }
        elif isinstance(contact_point, list):
            pts = []
            for cp in contact_point[:3]:
                if isinstance(cp, dict):
                    pts.append(
                        {
                            k: cp[k]
                            for k in ("contactType", "email", "telephone")
                            if cp.get(k)
                        }
                    )
            if pts:
                summary["contactPoint"] = pts
        offers = obj.get("offers")
        if isinstance(offers, dict):
            price = offers.get("price")
            if price is not None:
                summary["offers"] = {
                    k: offers[k] for k in ("price", "priceCurrency") if offers.get(k)
                }
        same_as = obj.get("sameAs")
        if isinstance(same_as, list) and same_as:
            summary["sameAs_count"] = len(same_as)
            summary["sameAs_sample"] = [str(u) for u in same_as[:3]]
        features = obj.get("featureList")
        if isinstance(features, list) and features:
            summary["featureList"] = [str(f) for f in features[:6]]
        main_entity = obj.get("mainEntity")
        if isinstance(main_entity, list) and main_entity:
            qa_pairs = []
            for e in main_entity:
                if not isinstance(e, dict) or not e.get("name"):
                    continue
                answer = e.get("acceptedAnswer") or {}
                text = answer.get("text") or ""
                # First sentence of the answer, capped — enough for the
                # meta test to see real Q&A content without dumping the
                # full FAQ into the prompt.
                snippet = text.split(". ")[0][:120] if text else ""
                qa_pairs.append(
                    {"question": str(e["name"]), "answer_excerpt": snippet}
                    if snippet
                    else {"question": str(e["name"])}
                )
            if qa_pairs:
                summary["mainEntity_count"] = len(main_entity)
                summary["mainEntity_qa"] = qa_pairs[:6]
        if summary:
            summaries.append(summary)
    return json.dumps(summaries, indent=2) if summaries else "None"


def select_pages_for_analysis(md_dict: dict, token_budget: int = 12000) -> list[dict]:
    """Select pages for content delta test within token budget.

    Priority: homepage first, then by content richness.
    """
    if not md_dict:
        return []

    pages = []
    tokens_used = 0
    chars_per_token = CHARS_PER_TOKEN

    # Priority URL patterns
    priority_patterns = ("/product", "/pricing", "/about", "/features")

    # Separate homepage from rest
    homepage_key = None
    for url in md_dict:
        normalized = url.rstrip("/").lower()
        # Match common homepage patterns
        if normalized in ("", "/", "homepage"):
            homepage_key = url
            break
        # Match full URLs with no meaningful path (e.g. "https://example.com")
        if "://" in normalized:
            after_scheme = normalized.split("://", 1)[-1]
            path_segments = after_scheme.split("/")[1:]
            if not any(path_segments):
                homepage_key = url
                break

    ordered_urls = []
    if homepage_key:
        ordered_urls.append(homepage_key)

    # Priority pages next
    for pattern in priority_patterns:
        for url in md_dict:
            if url != homepage_key and pattern in url.lower():
                if url not in ordered_urls:
                    ordered_urls.append(url)

    # Remaining by word count (descending)
    remaining = [
        (url, data) for url, data in md_dict.items() if url not in ordered_urls
    ]
    remaining.sort(
        key=lambda x: (x[1].get("content_metrics") or {}).get("words", 0),
        reverse=True,
    )
    ordered_urls.extend(url for url, _ in remaining)

    for url in ordered_urls:
        data = md_dict[url]
        md = data.get("markdown") or ""
        if not md:
            continue
        estimated_tokens = len(md) // chars_per_token
        if tokens_used + estimated_tokens > token_budget:
            remaining_budget = token_budget - tokens_used
            if remaining_budget > 500:
                truncated = md[: remaining_budget * chars_per_token]
                # Try to truncate at a sentence or paragraph boundary
                for sep in ("\n\n", "\n", ". ", "! ", "? "):
                    last = truncated.rfind(sep)
                    if last > len(truncated) // 2:
                        truncated = truncated[: last + len(sep)]
                        break
                md = truncated
            else:
                continue
        page_title = (data.get("page") or {}).get("title") or url
        pages.append({"url": url, "title": page_title, "markdown": md})
        # Use actual length (accounts for truncation)
        tokens_used += len(md) // chars_per_token

    return pages
