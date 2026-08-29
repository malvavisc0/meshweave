"""AEO (Answer Engine Optimization) factor scoring functions.

Each factor takes the crawl payload (dict) and returns a dict with:
  - score: float | None (0-100, or None if not auto-measurable)
  - weight: float
  - auto_measurable: bool
  - raw: dict (diagnostic data)
  - note: str | None (optional)
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


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
    faq_count = faq.get("count") or 0

    return {
        "score": _schema_score(
            coverage_pct, has_faq, has_howto, faq_count, faq_in_optimal
        ),
        "weight": 0.20,
        "auto_measurable": True,
        "raw": _schema_raw(
            schema_cov, coverage_pct, schema_types, has_faq, has_howto, faq_in_optimal
        ),
    }


def _schema_score(
    coverage_pct: Any,
    has_faq: bool,
    has_howto: bool,
    faq_count: int,
    faq_in_optimal: int,
) -> float:
    """Schema score: coverage plus FAQ/HowTo and FAQ-quality bonuses."""
    # Base score = coverage percentage
    score = float(coverage_pct)
    # Bonuses. The FAQ bonus requires at least half the answers in the
    # optimal range — a single in-range answer among many is not
    # "FAQ quality".
    if has_faq:
        score += 10
    if has_howto:
        score += 5
    if faq_count > 0 and faq_in_optimal >= max(1, faq_count // 2):
        score += 10
    return min(100.0, score)


def _schema_raw(
    schema_cov: dict,
    coverage_pct: Any,
    schema_types: list[str],
    has_faq: bool,
    has_howto: bool,
    faq_in_optimal: int,
) -> dict:
    """Raw diagnostics for the schema factor."""
    return {
        "coverage_pct": coverage_pct,
        "pages_with_schema": schema_cov.get("pages_with_schema") or 0,
        "pages_without_schema": schema_cov.get("pages_without_schema") or 0,
        "schema_types": schema_types,
        "has_faq_schema": has_faq,
        "has_howto_schema": has_howto,
        "faq_in_optimal_range": faq_in_optimal,
    }


def score_content_structure(payload: dict) -> dict:
    """A2. Content Structure Quality (20% weight, auto).

    Per-page scoring, then averaged across all pages.
    """
    # Collect pages — site crawls have markdowns (or pages), page crawls have page
    pages_data = _content_structure_pages(payload)

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


def _content_structure_pages(payload: dict) -> list[dict]:
    """Collect per-page dicts for content-structure scoring.

    Site crawls have markdowns (or a derived pages view); page crawls
    have a single page. One source is used to avoid double-counting.
    """
    pages_data = _markdowns_pages(payload)
    if not pages_data:
        single_page = payload.get("page") or {}
        if single_page:
            pages_data.append(payload)

    return pages_data


def _markdowns_pages(payload: dict) -> list[dict]:
    """Per-page dicts from markdowns, or the derived pages view."""
    md_dict = payload.get("markdowns") or {}
    if isinstance(md_dict, dict):
        return [p for p in md_dict.values() if isinstance(p, dict)]
    if isinstance(md_dict, list):
        # Some payloads store pages as a list
        return [item for item in md_dict if isinstance(item, dict)]
    # Fall back to payload["pages"] only when there are no markdowns
    return _derived_pages(payload)


def _derived_pages(payload: dict) -> list[dict]:
    """Page dicts from the derived payload['pages'] view."""
    pages_list = payload.get("pages") or []
    if not isinstance(pages_list, list):
        return []
    return [item for item in pages_list if isinstance(item, dict) and "page" in item]


def _score_single_page(page_data: dict) -> float:
    """Score a single page's content structure (0-100)."""
    headings, metrics = _page_headings_metrics(page_data)

    pts = _heading_points(headings)
    pts += _word_structure_points(metrics)
    pts += _image_alt_points(metrics)
    pts += _heading_volume_points(headings)

    # faq bonus: +5
    # (checked at site level, not per-page in this implementation)

    return min(100.0, pts)


def _heading_points(headings: dict) -> float:
    """Points for a single H1 and sufficient heading depth."""
    h1_count = headings.get("h1_count") or len(headings.get("h1") or [])
    depth = headings.get("depth") or 0
    pts = 0.0
    if h1_count == 1:
        pts += 15
    if depth >= 2:
        pts += 15
    return pts


def _word_structure_points(metrics: dict) -> float:
    """Points for lists, tables, paragraphs, and word volume."""
    pts = 0.0
    words = metrics.get("words") or 0
    if (metrics.get("lists") or 0) > 0:
        pts += 10
    if (metrics.get("tables") or 0) > 0:
        pts += 10
    if words >= 300:
        pts += 15
    if words >= 1000:
        pts += 10
    if (metrics.get("paragraphs") or 0) >= 5:
        pts += 10
    return pts


def _image_alt_points(metrics: dict) -> float:
    """Points when at least 80% of images carry alt text."""
    img_total = metrics.get("images_total") or 0
    img_alt = metrics.get("images_with_alt") or 0
    if img_total > 0 and (img_alt / img_total) >= 0.8:
        return 10.0
    return 0.0


def _heading_volume_points(headings: dict) -> float:
    """Points when the page has at least five headings."""
    if (headings.get("total") or 0) >= 5:
        return 10.0
    return 0.0


def _page_headings_metrics(page_data: dict) -> tuple[dict, dict]:
    """Extract headings and content_metrics, with nested-page fallback."""
    headings = page_data.get("headings") or {}
    metrics = page_data.get("content_metrics") or {}

    # Also check parent dict for page-level data
    if not headings and not metrics:
        page_info = page_data.get("page") or {}
        headings = page_info.get("headings") or headings
        metrics = page_info.get("content_metrics") or metrics

    return headings, metrics


def score_freshness(payload: dict) -> dict:
    """A3. Freshness (5% weight, partial auto).

    Uses datePublished/dateModified from JSON-LD articles.
    Fallback: use crawl.updated_at or metadata dates.

    The start page is merged into ``markdowns`` by the crawl pipeline, so
    dates are collected from *unique* pages only — never from both
    ``payload["page"]`` and a ``markdowns`` entry describing the same page.
    """
    from datetime import datetime

    # Collect one JSON-LD list per unique page. The start page appears in
    # payload["page"] *and* (for site crawls) as the origin entry in
    # markdowns; only one of the two is used to avoid double-counting its
    # dates. Deduplicate by the resolved page URL when available.
    page = payload.get("page") or {}
    md_dict = payload.get("markdowns") or {}
    origin_url = (page.get("url") or page.get("canonical") or "").rstrip("/")

    pages_jsonld = _collect_date_jsonld(page, md_dict, origin_url)
    dates, pages_with_dates = _extract_dates(pages_jsonld)

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

    score = _freshness_score(avg_days)

    return {
        "score": score,
        "weight": 0.05,
        "auto_measurable": True,
        "raw": {
            "newest_date": max(dates).isoformat(),
            "oldest_date": min(dates).isoformat(),
            "avg_days_old": round(avg_days, 1),
            "pages_with_dates": pages_with_dates,
        },
    }


def _collect_date_jsonld(
    page: dict,
    md_dict: Any,
    origin_url: str,
) -> list[list[dict]]:
    """One JSON-LD list per unique page, deduplicated by URL."""
    pages_jsonld: list[list[dict]] = []
    seen_urls: set[str] = set()
    start_ld = _dict_jsonld(page.get("jsonld") or [])
    if start_ld:
        pages_jsonld.append(start_ld)
        if origin_url:
            seen_urls.add(origin_url)
    if isinstance(md_dict, dict):
        _append_markdown_jsonld(pages_jsonld, seen_urls, md_dict)
    return pages_jsonld


def _dict_jsonld(items: Any) -> list[dict]:
    """Dict entries from a JSON-LD collection."""
    return [ld for ld in items if isinstance(ld, dict)]


def _append_markdown_jsonld(
    pages_jsonld: list[list[dict]],
    seen_urls: set[str],
    md_dict: dict,
) -> None:
    """Append each unique markdown page's JSON-LD, deduplicated by URL."""
    for url, md_data in md_dict.items():
        if not isinstance(md_data, dict):
            continue
        key = str(url).rstrip("/")
        if key in seen_urls:
            continue
        pg = md_data.get("page") or {}
        lds = _dict_jsonld(pg.get("jsonld") or [])
        if lds:
            pages_jsonld.append(lds)
            seen_urls.add(key)


def _extract_dates(pages_jsonld: list[list[dict]]) -> tuple[list[datetime], int]:
    """Collect published/modified/created dates and count pages that have them."""
    dates: list[datetime] = []
    pages_with_dates = 0
    for lds in pages_jsonld:
        page_had_date = False
        for ld in lds:
            for key in ("datePublished", "dateModified", "dateCreated"):
                val = ld.get(key)
                if val:
                    try:
                        d = datetime.fromisoformat(str(val).replace("Z", "+00:00"))
                        if d.tzinfo is None:
                            d = d.replace(tzinfo=UTC)
                        dates.append(d)
                        page_had_date = True
                    except Exception:
                        pass
        if page_had_date:
            pages_with_dates += 1
    return dates, pages_with_dates


def _freshness_score(avg_days: float) -> float:
    """Map average page age in days to a 0-100 freshness score band."""
    if avg_days <= 30:
        return 100.0
    if avg_days <= 90:
        return 80.0
    if avg_days <= 180:
        return 60.0
    if avg_days <= 365:
        return 40.0
    return 20.0


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
