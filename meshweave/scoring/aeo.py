"""AEO (Answer Engine Optimization) factor scoring functions.

Each factor takes the crawl payload (dict) and returns a dict with:
  - score: float | None (0-100, or None if not auto-measurable)
  - weight: float
  - auto_measurable: bool
  - raw: dict (diagnostic data)
  - note: str | None (optional)
"""

from __future__ import annotations

from datetime import UTC


def score_schema(payload: dict) -> dict:
    """A1. Schema Implementation (20% weight, auto).

    Input: audit.schema_coverage, faq_analysis
    """
    audit = payload.get("audit") or {}
    schema_cov = audit.get("schema_coverage") or {}
    faq = payload.get("faq_analysis") or {}

    coverage_pct = schema_cov.get("coverage_pct") or 0
    schema_types = list((schema_cov.get("type_counts") or {}).keys())
    has_faq = "FAQPage" in schema_types
    has_howto = "HowTo" in schema_types
    faq_in_optimal = faq.get("answers_in_optimal_range") or 0

    # Base score = coverage percentage
    score = float(coverage_pct)
    # Bonuses
    if has_faq:
        score += 10
    if has_howto:
        score += 5
    if faq_in_optimal > 0:
        score += 10
    score = min(100.0, score)

    return {
        "score": score,
        "weight": 0.20,
        "auto_measurable": True,
        "raw": {
            "coverage_pct": coverage_pct,
            "pages_with_schema": schema_cov.get("pages_with_schema") or 0,
            "pages_without_schema": schema_cov.get("pages_without_schema") or 0,
            "schema_types": schema_types,
            "has_faq_schema": has_faq,
            "has_howto_schema": has_howto,
            "faq_in_optimal_range": faq_in_optimal,
        },
    }


def score_content_structure(payload: dict) -> dict:
    """A2. Content Structure Quality (20% weight, auto).

    Per-page scoring, then averaged across all pages.
    """
    # Collect pages — site crawls have markdowns (or pages), page crawls have page
    pages_data = []

    # Site crawl: pages live in payload["markdowns"]; payload["pages"] is a
    # derived view of the same pages, so use exactly one source to avoid
    # double-counting (which inflates pages_evaluated and skews the average).
    md_dict = payload.get("markdowns") or {}
    if md_dict and isinstance(md_dict, dict):
        for _url, page_data in md_dict.items():
            if isinstance(page_data, dict):
                pages_data.append(page_data)
    elif isinstance(md_dict, list):
        # Some payloads store pages as a list
        for item in md_dict:
            if isinstance(item, dict):
                pages_data.append(item)
    else:
        # Fall back to payload["pages"] only when there are no markdowns
        pages_list = payload.get("pages") or []
        if isinstance(pages_list, list):
            for item in pages_list:
                if isinstance(item, dict) and "page" in item:
                    pages_data.append(item)

    # Single page crawl
    if not pages_data:
        single_page = payload.get("page") or {}
        if single_page:
            pages_data.append(payload)

    if not pages_data:
        return {
            "score": None,
            "weight": 0.20,
            "auto_measurable": True,
            "raw": {"per_page_scores": {}, "site_average": 0, "pages_evaluated": 0},
        }

    per_page_scores: dict[str, float] = {}
    for pd in pages_data:
        page_score = _score_single_page(pd)
        # Use a meaningful key
        page_info = pd.get("page") or pd
        url = page_info.get("url") or page_info.get("canonical") or ""
        if not url:
            url = f"page_{len(per_page_scores) + 1}"
        per_page_scores[url] = page_score

    avg = sum(per_page_scores.values()) / len(per_page_scores) if per_page_scores else 0

    return {
        "score": min(100.0, avg),
        "weight": 0.20,
        "auto_measurable": True,
        "raw": {
            "per_page_scores": per_page_scores,
            "site_average": round(avg, 1),
            "pages_evaluated": len(pages_data),
        },
    }


def _score_single_page(page_data: dict) -> float:
    """Score a single page's content structure (0-100)."""
    # Extract headings and content_metrics from nested structure
    headings = page_data.get("headings") or {}
    metrics = page_data.get("content_metrics") or {}

    # Also check parent dict for page-level data
    if not headings and not metrics:
        page_info = page_data.get("page") or {}
        headings = page_info.get("headings") or headings
        metrics = page_info.get("content_metrics") or metrics

    pts = 0.0

    # h1_count == 1: +15
    h1_count = headings.get("h1_count") or len(headings.get("h1") or [])
    if h1_count == 1:
        pts += 15

    # heading_depth >= 2: +15
    depth = headings.get("depth") or 0
    if depth >= 2:
        pts += 15

    # has_lists (lists > 0): +10
    lists = metrics.get("lists") or 0
    if lists > 0:
        pts += 10

    # has_tables (tables > 0): +10
    tables = metrics.get("tables") or 0
    if tables > 0:
        pts += 10

    # word_count >= 300: +15
    words = metrics.get("words") or 0
    if words >= 300:
        pts += 15

    # word_count >= 1000: +10 bonus
    if words >= 1000:
        pts += 10

    # images_with_alt / images_total >= 0.8: +10
    img_total = metrics.get("images_total") or 0
    img_alt = metrics.get("images_with_alt") or 0
    if img_total > 0 and (img_alt / img_total) >= 0.8:
        pts += 10

    # paragraphs >= 5: +10
    paragraphs = metrics.get("paragraphs") or 0
    if paragraphs >= 5:
        pts += 10

    # headings_total >= 5: +10
    total_headings = headings.get("total") or 0
    if total_headings >= 5:
        pts += 10

    # faq bonus: +5
    # (checked at site level, not per-page in this implementation)

    return min(100.0, pts)


def score_freshness(payload: dict) -> dict:
    """A3. Freshness (5% weight, partial auto).

    Uses datePublished/dateModified from JSON-LD articles.
    Fallback: use crawl.updated_at or metadata dates.
    """
    from datetime import datetime

    dates: list[datetime] = []

    # Extract dates from page JSON-LD
    page = payload.get("page") or {}
    for ld in page.get("jsonld") or []:
        if not isinstance(ld, dict):
            continue
        for key in ("datePublished", "dateModified", "dateCreated"):
            val = ld.get(key)
            if val:
                try:
                    d = datetime.fromisoformat(str(val).replace("Z", "+00:00"))
                    if d.tzinfo is None:
                        d = d.replace(tzinfo=UTC)
                    dates.append(d)
                except Exception:
                    pass

    # Check markdowns for per-page JSON-LD dates
    for _url, md_data in (payload.get("markdowns") or {}).items():
        if not isinstance(md_data, dict):
            continue
        pg = md_data.get("page") or {}
        for ld in pg.get("jsonld") or []:
            if not isinstance(ld, dict):
                continue
            for key in ("datePublished", "dateModified", "dateCreated"):
                val = ld.get(key)
                if val:
                    try:
                        d = datetime.fromisoformat(str(val).replace("Z", "+00:00"))
                        if d.tzinfo is None:
                            d = d.replace(tzinfo=UTC)
                        dates.append(d)
                    except Exception:
                        pass

    if not dates:
        return {
            "score": None,  # excluded from composite when no date data
            "weight": 0.05,
            "auto_measurable": True,
            "raw": {
                "newest_date": None,
                "oldest_date": None,
                "avg_days_old": None,
                "pages_with_dates": 0,
            },
        }

    now = datetime.now(UTC)
    # Clamp to >= 0 so future-dated (scheduled) content can't pull the average
    # below zero and inflate the freshness score.
    days_old = [max(0, (now - d).days) for d in dates]
    avg_days = sum(days_old) / len(days_old) if days_old else 0

    if avg_days <= 30:
        score = 100.0
    elif avg_days <= 90:
        score = 80.0
    elif avg_days <= 180:
        score = 60.0
    elif avg_days <= 365:
        score = 40.0
    else:
        score = 20.0

    return {
        "score": score,
        "weight": 0.05,
        "auto_measurable": True,
        "raw": {
            "newest_date": max(dates).isoformat(),
            "oldest_date": min(dates).isoformat(),
            "avg_days_old": round(avg_days, 1),
            "pages_with_dates": len(dates),
        },
    }


def score_capture_rate(user_input: float | None = None) -> dict:
    """A4. Snippet / AI Overview Capture Rate (30%, NOT auto)."""
    return {
        "score": user_input,
        "weight": 0.30,
        "auto_measurable": False,
        "manual_input_guidance": (
            "Enter the % of your target keywords where you appear in "
            "featured snippets or AI Overviews. Check Google Search Console "
            "→ Performance → Search Appearance."
        ),
        "user_value": user_input,
        "raw": None,
    }


def score_query_match(user_input: float | None = None) -> dict:
    """A5. Query Match Precision (15%, NOT auto)."""
    return {
        "score": user_input,
        "weight": 0.15,
        "auto_measurable": False,
        "manual_input_guidance": (
            "Estimate how closely your content matches the natural language "
            "questions your audience asks. Check 'People Also Ask' for your "
            "target keywords."
        ),
        "user_value": user_input,
        "raw": None,
    }


def score_voice_rate(user_input: float | None = None) -> dict:
    """A6. Voice Selection Rate (10%, NOT auto)."""
    return {
        "score": user_input,
        "weight": 0.10,
        "auto_measurable": False,
        "manual_input_guidance": (
            "Test your target queries on Google Assistant, Siri, and Alexa. "
            "Enter the % where your content is the answer."
        ),
        "user_value": user_input,
        "raw": None,
    }
