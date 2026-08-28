"""GEO (Generative Engine Optimization) factor scoring functions.

Each factor takes the crawl payload (dict) and returns a dict with:
  - score: float | None (0-100, or None if not auto-measurable)
  - weight: float
  - auto_measurable: bool
  - raw: dict (diagnostic data)
  - note: str | None (optional)
"""

from __future__ import annotations

from dataclasses import dataclass


def _same_as_score(count: int) -> int:
    """Shared sameAs presence scale used across GEO factors.

    One bucketing for all factors so entity signals stay consistent:
    0 → 0, 1-2 → 40, 3-5 → 70, 6+ → 100.
    """
    if count == 0:
        return 0
    if count <= 2:
        return 40
    if count <= 5:
        return 70
    return 100


@dataclass
class _EeatSignals:
    """E-E-A-T signals collected across a payload."""

    has_author: bool
    has_reviews: bool
    has_video: bool
    has_contact: bool
    has_privacy: bool
    has_terms: bool


def score_topical_authority(payload: dict) -> dict:
    """G2. Topical Authority / Entity Coverage (20% weight, auto).

    Weighted factors:
      - schema_coverage.coverage_pct: 0.3
      - schema type diversity: 0.2
      - entity.name_consistent: 0.15
      - entity.description_consistent: 0.15
      - entity.same_as count: 0.1
      - content page ratio: 0.1
    """
    audit = payload.get("audit") or {}
    schema_cov = audit.get("schema_coverage") or {}
    entity = audit.get("entity") or {}

    coverage_pct = schema_cov.get("coverage_pct") or 0
    schema_types = list((schema_cov.get("type_counts") or {}).keys())
    diversity = min(len(schema_types) / 10.0, 1.0) * 100

    name_consistent = 100 if entity.get("name_consistent") else 0
    desc_consistent = 100 if entity.get("description_consistent") else 0

    same_as = entity.get("same_as") or []
    same_as_count = len(same_as)
    same_as_score = _same_as_score(same_as_count)

    # Content page ratio: pages with >300 words / total pages
    md_dict = payload.get("markdowns") or {}
    total_pages = max(len(md_dict), 1) if isinstance(md_dict, dict) else 1
    content_pages = 0
    if isinstance(md_dict, dict):
        for _url, pg in md_dict.items():
            if isinstance(pg, dict):
                cm = pg.get("content_metrics") or {}
                if (cm.get("words") or 0) > 300:
                    content_pages += 1
    content_ratio = (
        min((content_pages / total_pages) * 100, 100) if total_pages > 0 else 0
    )

    score = (
        coverage_pct * 0.3
        + diversity * 0.2
        + name_consistent * 0.15
        + desc_consistent * 0.15
        + same_as_score * 0.1
        + content_ratio * 0.1
    )
    score = min(100.0, score)

    return {
        "score": round(score, 1),
        "weight": 0.20,
        "auto_measurable": True,
        "raw": {
            "coverage_pct": coverage_pct,
            "schema_types_count": len(schema_types),
            "name_consistent": entity.get("name_consistent", False),
            "desc_consistent": entity.get("description_consistent", False),
            "same_as_count": same_as_count,
            "content_page_ratio": round(content_ratio, 1),
        },
    }


def score_eeat(payload: dict) -> dict:
    """G3. E-E-A-T Signals (15% weight, partial auto).

    Additive scoring: 15 organization + 15 author + 15 reviews + 10 sameAs
    + 8 contact + 7 privacy/terms + 5 video. The auto-measurable maximum
    is 75 (no reviews, no video) — the last 25 points require real
    customer proof and video content, which a site cannot grant itself.
    """
    audit = payload.get("audit") or {}
    entity = audit.get("entity") or {}
    schema_cov = audit.get("schema_coverage") or {}
    type_counts = schema_cov.get("type_counts") or {}
    schema_types = set(type_counts.keys())
    pages_with_org = entity.get("pages_with_org_schema") or 0

    signals = _collect_eeat_signals(payload)
    same_as = entity.get("same_as") or []

    pts = _eeat_points(
        pages_with_org=pages_with_org,
        schema_types=schema_types,
        signals=signals,
        same_as_size=len(same_as),
    )

    return {
        "score": float(pts),
        "weight": 0.15,
        "auto_measurable": True,
        "raw": {
            "has_org_schema": pages_with_org > 0,
            "has_author_info": signals.has_author,
            "has_reviews": signals.has_reviews,
            "same_as_count": len(same_as),
            "has_contact": signals.has_contact,
            "has_privacy": signals.has_privacy or signals.has_terms,
            "has_video": signals.has_video,
        },
    }


def _collect_eeat_signals(payload: dict) -> _EeatSignals:
    """Collect E-E-A-T signals (author/reviews/video/contact/privacy/terms)."""
    all_jsonld = _collect_all_jsonld(payload)

    has_author = False
    has_reviews = False
    has_video = False
    has_contact = False
    has_privacy = False
    has_terms = False

    for ld in all_jsonld:
        ld_type = (ld.get("@type") or "").lower()
        if "author" in ld or "author" in [k.lower() for k in ld.keys()]:
            has_author = True
        if ld_type in ("review", "aggregaterating", "product"):
            if ld.get("review") or ld.get("aggregateRating"):
                has_reviews = True
        if ld_type == "videoobject":
            has_video = True
        # ContactPage at top level, or a ContactPoint nested inside
        # e.g. an Organization or WebPage block.
        if ld_type in ("contactpage", "contactpoint") or "contactpoint" in (
            k.lower() for k in ld.keys()
        ):
            has_contact = True

    # Check URL patterns for privacy/terms/contact
    urls_text = _eeat_urls_text(payload)
    if "privacy" in urls_text:
        has_privacy = True
    if "terms" in urls_text or "legal" in urls_text:
        has_terms = True
    if "contact" in urls_text:
        has_contact = True

    return _EeatSignals(
        has_author=has_author,
        has_reviews=has_reviews,
        has_video=has_video,
        has_contact=has_contact,
        has_privacy=has_privacy,
        has_terms=has_terms,
    )


def _collect_all_jsonld(payload: dict) -> list[dict]:
    """Gather all JSON-LD dicts from the start page and markdown pages."""
    all_jsonld: list[dict] = []
    page = payload.get("page") or {}
    for ld in page.get("jsonld") or []:
        if isinstance(ld, dict):
            all_jsonld.append(ld)

    md_dict = payload.get("markdowns") or {}
    if isinstance(md_dict, dict):
        for _url, pg in md_dict.items():
            if isinstance(pg, dict):
                pg_data = pg.get("page") or pg
                for ld in pg_data.get("jsonld") or []:
                    if isinstance(ld, dict):
                        all_jsonld.append(ld)
    return all_jsonld


def _eeat_urls_text(payload: dict) -> str:
    """Concatenate all page URLs encountered in the payload, lowercased."""
    page = payload.get("page") or {}
    md_dict = payload.get("markdowns") or {}
    all_urls: list[str] = [page.get("url") or "", page.get("canonical") or ""]
    if isinstance(md_dict, dict):
        all_urls.extend(str(u) for u in md_dict.keys())
    return " ".join(all_urls).lower()


def _eeat_points(
    pages_with_org: int,
    schema_types: set[str],
    signals: _EeatSignals,
    same_as_size: int,
) -> int:
    """Compute the additive E-E-A-T point total (capped at 100)."""
    pts = 0
    lower_types = {t.lower() for t in schema_types}
    if pages_with_org > 0 or "organization" in lower_types or "org" in lower_types:
        pts += 15
    if signals.has_author:
        pts += 15
    if signals.has_reviews:
        pts += 15
    if same_as_size > 0:
        pts += 10
    if signals.has_contact:
        pts += 8
    if signals.has_privacy or signals.has_terms:
        pts += 7
    if signals.has_video:
        pts += 5
    return min(100, pts)


def score_crawl_access(payload: dict) -> dict:
    """G4. LLM Crawl Accessibility (15% weight, auto when data available).

    Additive scoring: 8 robots.txt + up to 39 bot access (GPTBot 15,
    ClaudeBot 12, PerplexityBot 12 — half credit when partially
    restricted) + 15 llms.txt + 8 llms-full.txt + 7 sitemap.
    Structural maximum: 77 — the remaining 23 points do not exist to be
    earned. Combined with other factor ceilings, the auto-only GEO
    composite tops out ≈84 ("Authoritative"); the "Dominant" rating is
    reachable only with manual citation input, by design.

    If robots/llms data is only a placeholder (page-scope crawl),
    returns null with a note.
    """
    robots = payload.get("robots") or {}
    llms = payload.get("llms_txt") or {}

    # Check if data is meaningful (not just the default placeholder)
    note = robots.get("note") or llms.get("note") or ""
    llms_txt_exists = (llms.get("llms_txt") or {}).get("exists")
    if (
        "not checked" in note.lower()
        and not robots.get("exists")
        and not llms_txt_exists
    ):
        return {
            "score": None,
            "weight": 0.15,
            "auto_measurable": True,
            "raw": None,
            "note": (
                "Re-analyze as domain for full accessibility score. "
                "robots.txt and llms.txt are only collected for "
                "domain-scope crawls."
            ),
        }

    pts = 0

    # robots.txt exists: +8
    if robots.get("exists"):
        pts += 8

    # Bot access. "partially_restricted" means allowed site-wide except
    # specific paths (e.g. private API endpoints) — the content is still
    # crawlable, so those bots earn half credit.
    bots = robots.get("bots") or {}
    for bot_name, expected_pts in [
        ("GPTBot", 15),
        ("ClaudeBot", 12),
        ("PerplexityBot", 12),
    ]:
        status = str(bots.get(bot_name) or "").lower()
        if status == "allowed":
            pts += expected_pts
        elif "partial" in status:
            pts += expected_pts // 2

    # llms.txt exists: +15
    llms_txt_data = llms.get("llms_txt") or {}
    if llms_txt_data.get("exists"):
        pts += 15

    # llms-full.txt exists: +8
    llms_full_data = llms.get("llms_full_txt") or {}
    if llms_full_data.get("exists"):
        pts += 8

    # XML sitemap: +7
    sitemaps = robots.get("sitemaps") or []
    if sitemaps:
        pts += 7

    pts = min(100, pts)

    return {
        "score": float(pts),
        "weight": 0.15,
        "auto_measurable": True,
        "raw": {
            "robots_exists": robots.get("exists", False),
            "bot_statuses": dict(bots),
            "llms_txt_exists": llms_txt_data.get("exists", False),
            "llms_full_txt_exists": llms_full_data.get("exists", False),
            "sitemap_count": len(sitemaps),
        },
    }


def score_content_depth(payload: dict) -> dict:
    """G5. Content Depth & Originality (10% weight, auto)."""
    pages = _payload_pages(payload)

    if not pages:
        # Single page
        page = payload.get("page") or {}
        if page:
            pages = [page]

    total_pages = max(len(pages), 1)
    word_counts = _page_word_counts(pages)
    avg_words = sum(word_counts) / len(word_counts) if word_counts else 0

    # Average word count score (0-100)
    word_score = _avg_words_score(avg_words)

    # Pages with 1000+ words ratio
    pages_gt1000 = sum(1 for w in word_counts if w >= 1000)
    depth_ratio = (pages_gt1000 / total_pages) * 100 if total_pages > 0 else 0

    content_pages_gt200 = sum(1 for w in word_counts if w > 200)
    # Unique content pages scaled (> 200 words)
    content_ratio = (
        min((content_pages_gt200 / total_pages) * 100, 100) if total_pages > 0 else 0
    )

    metrics = _code_table_metrics(pages)
    code_bonus = 100 if metrics[0] > 0 else 0
    tables_bonus = 100 if metrics[1] > 0 else 0

    score = (
        word_score * 0.35
        + depth_ratio * 0.25
        + content_ratio * 0.15
        + code_bonus * 0.15
        + tables_bonus * 0.10
    )
    score = min(100.0, score)

    return {
        "score": round(score, 1),
        "weight": 0.10,
        "auto_measurable": True,
        "raw": {
            "avg_words": round(avg_words, 0),
            "total_pages": total_pages,
            "content_pages_gt200": content_pages_gt200,
            "pages_with_code": metrics[0],
            "pages_with_tables": metrics[1],
        },
    }


def _payload_pages(payload: dict) -> list[dict]:
    """Extract per-page dicts from markdowns/pages in the payload."""
    md_dict = payload.get("markdowns") or {}
    pages: list[dict] = []
    if isinstance(md_dict, dict) and md_dict:
        for _url, pg in md_dict.items():
            if isinstance(pg, dict):
                pages.append(pg)
    elif isinstance(md_dict, list):
        pages = [p for p in md_dict if isinstance(p, dict)]
    else:
        # payload["pages"] is a derived view of markdowns; only use it as a
        # fallback when there are no markdowns, to avoid double-counting.
        pages_list = payload.get("pages") or []
        if isinstance(pages_list, list):
            pages = [p for p in pages_list if isinstance(p, dict)]
    return pages


def _page_word_counts(pages: list[dict]) -> list[int]:
    """Word counts across pages, with a nested-page fallback."""
    word_counts: list[int] = []
    for pg in pages:
        cm = pg.get("content_metrics") or {}
        # Fallback: check nested "page" key (single-page crawl structure)
        if not cm:
            page_info = pg.get("page") or {}
            cm = page_info.get("content_metrics") or {}
        word_counts.append(cm.get("words") or 0)
    return word_counts


def _code_table_metrics(pages: list[dict]) -> tuple[int, int]:
    """Count pages containing code blocks and tables."""
    pages_with_code = 0
    pages_with_tables = 0
    for pg in pages:
        cm = pg.get("content_metrics") or {}
        if not cm:
            page_info = pg.get("page") or {}
            cm = page_info.get("content_metrics") or {}
        if (cm.get("code_blocks") or 0) > 0:
            pages_with_code += 1
        if (cm.get("tables") or 0) > 0:
            pages_with_tables += 1
    return pages_with_code, pages_with_tables


def _avg_words_score(avg_words: float) -> float:
    """Map average word count to a 0-100 score band."""
    if avg_words < 200:
        return 10
    if avg_words < 500:
        return 30
    if avg_words < 1000:
        return 50
    if avg_words < 2000:
        return 70
    if avg_words < 5000:
        return 90
    return 100


def score_entity_consistency(payload: dict) -> dict:
    """G6. Cross-Platform Entity Consistency (10% weight, auto).

    Additive scoring: 20 consistent name + 15 consistent description +
    up to 40 for sameAs presence (shared _same_as_score scale, scaled).
    Auto-measurable maximum: 75 — a full 40 for sameAs requires 6+ org
    profiles, which most sites must accumulate externally.
    """
    audit = payload.get("audit") or {}
    entity = audit.get("entity") or {}

    name_consistent = entity.get("name_consistent", False)
    desc_consistent = entity.get("description_consistent", False)
    same_as = entity.get("same_as") or []
    name_variants = entity.get("name_variants") or []
    desc_variants = entity.get("description_variants") or []

    pts = 0.0
    if name_consistent:
        pts += 20
    if desc_consistent:
        pts += 15

    # sameAs on the shared scale (0/40/70/100), scaled to the 40-point
    # remainder of this factor. Uses the same buckets as topical
    # authority so one signal cannot be "good" in one factor and
    # "mediocre" in another.
    pts += _same_as_score(len(same_as)) * 0.4

    pts = min(100, pts)

    return {
        "score": float(pts),
        "weight": 0.10,
        "auto_measurable": True,
        "raw": {
            "name_consistent": name_consistent,
            "desc_consistent": desc_consistent,
            "same_as": same_as,
            "name_variants": name_variants,
            "desc_variants": desc_variants,
        },
    }


def score_citation(user_input: float | None = None) -> dict:
    """G1. AI Citation Frequency & Quality (30%, NOT auto)."""
    return {
        "score": user_input,
        "weight": 0.30,
        "auto_measurable": False,
        "manual_input_guidance": (
            "Search your brand on ChatGPT, Claude, and Perplexity. "
            "Estimate how often you're cited. Enter 0-100. "
            "Tier 1 (named+link)=1.0x, Tier 2 (named)=0.7x, "
            "Tier 3 (paraphrased)=0.3x"
        ),
        "user_value": user_input,
        "raw": None,
    }
