"""GEO (Generative Engine Optimization) factor scoring functions.

Each factor takes the crawl payload (dict) and returns a dict with:
  - score: float | None (0-100, or None if not measurable)
  - weight: float (from the authoritative GEO weight table)
  - auto_measurable: bool
  - raw: dict (diagnostic data)
  - note: str | None (optional)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from meshweave.scoring.composite import GEO_WEIGHTS

# Additive crawl-access points a perfect site earns (robots 8 + bots 39 +
# llms.txt 15 + llms-full.txt 8 + sitemap 7); the factor is rescaled so
# this total reads as 100.
CRAWL_ACCESS_MAX_POINTS = 77

# Crawl-access points per signal, on the additive (pre-rescale) scale.
ROBOTS_POINTS = 8
LLMS_TXT_POINTS = 15
LLMS_FULL_TXT_POINTS = 8
SITEMAP_POINTS = 7
AI_BOT_POINTS: dict[str, int] = {"GPTBot": 15, "ClaudeBot": 12, "PerplexityBot": 12}

# E-E-A-T additive points per site-side trust signal.
ORG_POINTS = 30
AUTHOR_POINTS = 30
CONTACT_POINTS = 20
POLICY_POINTS = 20

# Additive entity-consistency points, rescaled to 0-100 by their total.
ENTITY_NAME_POINTS = 20
ENTITY_DESCRIPTION_POINTS = 15


def crawl_access_scale(points: float) -> float:
    """Convert additive crawl-access points to the 0-100 factor scale."""
    return points * 100.0 / CRAWL_ACCESS_MAX_POINTS


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
    """G2. Topical Authority / Entity Coverage (auto).

    Weighted site-wide evidence:
      - schema_coverage.coverage_pct: 0.35
      - schema type diversity: 0.25
      - entity.name_consistent: 0.15
      - entity.description_consistent: 0.15
      - content page ratio: 0.10

    The sameAs count is kept as raw evidence only — the presence of
    third-party profiles is not a score driver.
    """
    audit = payload.get("audit") or {}
    schema_cov = audit.get("schema_coverage") or {}
    entity = audit.get("entity") or {}

    coverage_pct = schema_cov.get("coverage_pct") or 0
    schema_types = list((schema_cov.get("type_counts") or {}).keys())

    same_as_count = len(entity.get("same_as") or [])

    # Content page ratio: pages with >300 words / total pages
    content_ratio = _content_page_ratio(payload)

    score = topical_authority_score(
        coverage_pct,
        len(schema_types),
        bool(entity.get("name_consistent")),
        bool(entity.get("description_consistent")),
        content_ratio,
    )

    return {
        "score": round(score, 1),
        "weight": GEO_WEIGHTS["topical_authority"],
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


def topical_authority_score(
    coverage_pct: float,
    type_count: int,
    name_consistent: bool,
    desc_consistent: bool,
    content_ratio: float,
) -> float:
    """Weighted topical-authority score from its site-side inputs."""
    diversity = min(type_count / 10.0, 1.0) * 100
    score = (
        coverage_pct * 0.35
        + diversity * 0.25
        + 100 * name_consistent * 0.15
        + 100 * desc_consistent * 0.15
        + content_ratio * 0.10
    )
    return min(100.0, score)


def _content_page_ratio(payload: dict) -> float:
    """Percentage of crawled pages with more than 300 words."""
    md_dict = payload.get("markdowns") or {}
    if not isinstance(md_dict, dict):
        return 0
    content_pages = sum(1 for pg in md_dict.values() if _md_page_words(pg) > 300)
    total_pages = max(len(md_dict), 1)
    return min((content_pages / total_pages) * 100, 100)


def _md_page_words(pg: Any) -> int:
    """Word count from a markdown page entry's content metrics."""
    if not isinstance(pg, dict):
        return 0
    cm = pg.get("content_metrics") or {}
    return cm.get("words") or 0


def score_eeat(payload: dict) -> dict:
    """G3. E-E-A-T Signals (auto).

    Additive scoring on site-side evidence: 30 organization identity +
    30 authorship + 20 contact + 20 policy pages. Review, video, and
    sameAs signals are kept as raw evidence only — third-party markers
    are not score drivers.
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
    )

    return {
        "score": float(pts),
        "weight": GEO_WEIGHTS["eeat"],
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
    has_author, has_reviews, has_video, has_contact = _jsonld_eeat_signals(all_jsonld)

    # Check URL patterns for privacy/terms/contact
    urls_text = _eeat_urls_text(payload)
    has_privacy = "privacy" in urls_text
    has_terms = "terms" in urls_text or "legal" in urls_text
    has_contact = has_contact or "contact" in urls_text

    return _EeatSignals(
        has_author=has_author,
        has_reviews=has_reviews,
        has_video=has_video,
        has_contact=has_contact,
        has_privacy=has_privacy,
        has_terms=has_terms,
    )


def _jsonld_eeat_signals(all_jsonld: list[dict]) -> tuple[bool, bool, bool, bool]:
    """(author, reviews, video, contact) flags found in JSON-LD blocks."""
    has_author = any(_ld_has_author(ld) for ld in all_jsonld)
    has_reviews = any(_ld_has_reviews(ld) for ld in all_jsonld)
    has_video = any(_ld_is_video(ld) for ld in all_jsonld)
    has_contact = any(_ld_has_contact(ld) for ld in all_jsonld)
    return has_author, has_reviews, has_video, has_contact


def _ld_has_author(ld: dict) -> bool:
    """True when the block carries author information at any key."""
    return "author" in ld or "author" in [k.lower() for k in ld.keys()]


def _ld_has_reviews(ld: dict) -> bool:
    """True when a review-like block carries review content."""
    ld_type = (ld.get("@type") or "").lower()
    if ld_type not in ("review", "aggregaterating", "product"):
        return False
    return bool(ld.get("review") or ld.get("aggregateRating"))


def _ld_is_video(ld: dict) -> bool:
    """True when the block is a VideoObject."""
    return (ld.get("@type") or "").lower() == "videoobject"


def _ld_has_contact(ld: dict) -> bool:
    """True when the block is or contains a ContactPoint."""
    ld_type = (ld.get("@type") or "").lower()
    # ContactPage at top level, or a ContactPoint nested inside
    # e.g. an Organization or WebPage block.
    return ld_type in ("contactpage", "contactpoint") or "contactpoint" in (
        k.lower() for k in ld.keys()
    )


def _collect_all_jsonld(payload: dict) -> list[dict]:
    """Gather all JSON-LD dicts from the start page and markdown pages."""
    all_jsonld: list[dict] = []
    all_jsonld.extend(_start_page_jsonld(payload))
    all_jsonld.extend(_markdowns_jsonld(payload))
    return all_jsonld


def _start_page_jsonld(payload: dict) -> list[dict]:
    """Dict JSON-LD blocks from the start page."""
    page = payload.get("page") or {}
    return _dict_jsonld_blocks(page.get("jsonld") or [])


def _markdowns_jsonld(payload: dict) -> list[dict]:
    """Dict JSON-LD blocks from every markdown page."""
    md_dict = payload.get("markdowns") or {}
    if not isinstance(md_dict, dict):
        return []
    blocks: list[dict] = []
    for _url, pg in md_dict.items():
        if isinstance(pg, dict):
            pg_data = pg.get("page") or pg
            blocks.extend(_dict_jsonld_blocks(pg_data.get("jsonld") or []))
    return blocks


def _dict_jsonld_blocks(items: Any) -> list[dict]:
    """Dict entries from a JSON-LD collection."""
    return [ld for ld in items if isinstance(ld, dict)]


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
) -> int:
    """Compute the additive E-E-A-T point total (capped at 100)."""
    pts = _org_points(pages_with_org, schema_types)
    pts += _signal_points(signals)
    return min(100, pts)


def _org_points(pages_with_org: int, schema_types: set[str]) -> int:
    """Points for publishing Organization schema anywhere on the site."""
    lower_types = {t.lower() for t in schema_types}
    if pages_with_org > 0 or "organization" in lower_types or "org" in lower_types:
        return ORG_POINTS
    return 0


def _signal_points(signals: _EeatSignals) -> int:
    """Points for authorship, contact, and policy-page signals."""
    pts = 0
    if signals.has_author:
        pts += AUTHOR_POINTS
    if signals.has_contact:
        pts += CONTACT_POINTS
    if signals.has_privacy or signals.has_terms:
        pts += POLICY_POINTS
    return pts


def score_crawl_access(payload: dict) -> dict:
    """G4. LLM Crawl Accessibility (auto when data available).

    Additive points: 8 robots.txt + up to 39 bot access (GPTBot 15,
    ClaudeBot 12, PerplexityBot 12 — half credit when partially
    restricted) + 15 llms.txt + 8 llms-full.txt + 7 sitemap, a 77-point
    total rescaled to 0-100 so a perfect site scores 100. Optional
    llms.txt evidence is scored here only, once.

    If robots/llms data is only a placeholder (page-scope crawl),
    returns null with a note.
    """
    robots = payload.get("robots") or {}
    llms = payload.get("llms_txt") or {}

    # Check if data is meaningful (not just the default placeholder)
    if _is_placeholder_access(robots, llms):
        return _placeholder_crawl_access()

    pts = _crawl_access_points(robots, llms)

    return {
        "score": round(crawl_access_scale(pts), 1),
        "weight": GEO_WEIGHTS["crawl_access"],
        "auto_measurable": True,
        "raw": _crawl_access_raw(robots, llms),
    }


def _is_placeholder_access(robots: dict, llms: dict) -> bool:
    """True when robots/llms data is only the page-scope placeholder."""
    note = robots.get("note") or llms.get("note") or ""
    llms_txt_exists = (llms.get("llms_txt") or {}).get("exists")
    return (
        "not checked" in note.lower()
        and not robots.get("exists")
        and not llms_txt_exists
    )


def _placeholder_crawl_access() -> dict:
    """Null-score result used when accessibility data was not collected."""
    return {
        "score": None,
        "weight": GEO_WEIGHTS["crawl_access"],
        "auto_measurable": True,
        "raw": None,
        "note": (
            "Re-analyze as domain for full accessibility score. "
            "robots.txt and llms.txt are only collected for "
            "domain-scope crawls."
        ),
    }


def _crawl_access_points(robots: dict, llms: dict) -> int:
    """Additive crawl-accessibility points (robots, bots, llms.txt, sitemap)."""
    pts = 0
    # robots.txt exists: +8
    if robots.get("exists"):
        pts += ROBOTS_POINTS
    # Bot access. "partially_restricted" means allowed site-wide except
    # specific paths (e.g. private API endpoints) — the content is still
    # crawlable, so those bots earn half credit.
    pts += _bot_access_points(robots.get("bots") or {})
    pts += _llms_txt_points(llms)
    pts += _sitemap_points(robots)
    return pts


def _bot_access_points(bots: dict) -> int:
    """Points for AI-bot crawl permissions, with half credit when partial."""
    return sum(bot_points(name, bots.get(name)) for name in AI_BOT_POINTS)


def bot_points(bot_name: str, status: Any) -> int:
    """Points one AI bot earns: full when allowed, half when partial."""
    full = AI_BOT_POINTS[bot_name]
    text = str(status or "").lower()
    if text == "allowed":
        return full
    if "partial" in text:
        return full // 2
    return 0


def _llms_txt_points(llms: dict) -> int:
    """Points for llms.txt (+15) and llms-full.txt (+8) presence."""
    pts = 0
    llms_txt_data = llms.get("llms_txt") or {}
    if llms_txt_data.get("exists"):
        pts += LLMS_TXT_POINTS
    llms_full_data = llms.get("llms_full_txt") or {}
    if llms_full_data.get("exists"):
        pts += LLMS_FULL_TXT_POINTS
    return pts


def _sitemap_points(robots: dict) -> int:
    """Points when robots.txt declares sitemaps."""
    sitemaps = robots.get("sitemaps") or []
    return SITEMAP_POINTS if sitemaps else 0


def _crawl_access_raw(robots: dict, llms: dict) -> dict:
    """Raw diagnostics for the crawl-access factor."""
    llms_txt_data = llms.get("llms_txt") or {}
    llms_full_data = llms.get("llms_full_txt") or {}
    sitemaps = robots.get("sitemaps") or []
    return {
        "robots_exists": robots.get("exists", False),
        "bot_statuses": dict(robots.get("bots") or {}),
        "llms_txt_exists": llms_txt_data.get("exists", False),
        "llms_full_txt_exists": llms_full_data.get("exists", False),
        "sitemap_count": len(sitemaps),
    }


def score_content_depth(payload: dict) -> dict:
    """G5. Content Depth & Originality (auto).

    ``raw.page_words`` keeps the per-page word counts so fixes can name
    the shallow pages and predict the score once they are expanded.
    """
    pages = _depth_pages(payload)

    total_pages = max(len(pages), 1)
    word_counts = _page_word_counts(pages)
    avg_words = sum(word_counts) / len(word_counts) if word_counts else 0
    metrics = _code_table_metrics(pages)

    score = content_depth_score(word_counts, metrics)

    return {
        "score": round(score, 1),
        "weight": GEO_WEIGHTS["content_depth"],
        "auto_measurable": True,
        "raw": {
            "avg_words": round(avg_words, 0),
            "total_pages": total_pages,
            "content_pages_gt200": sum(1 for w in word_counts if w > 200),
            "pages_with_code": metrics[0],
            "pages_with_tables": metrics[1],
            "page_words": _page_word_map(payload),
        },
    }


def _page_word_map(payload: dict) -> dict[str, int]:
    """Word count per crawled page key, from the markdowns mapping."""
    md_dict = payload.get("markdowns") or {}
    if not isinstance(md_dict, dict):
        return {}
    return {
        str(url): _md_page_words(pg)
        for url, pg in md_dict.items()
        if isinstance(pg, dict)
    }


def _depth_pages(payload: dict) -> list[dict]:
    """Pages to score: markdown/pages view, else the single page."""
    pages = _payload_pages(payload)
    if pages:
        return pages
    # Single page
    page = payload.get("page") or {}
    if page:
        return [page]
    return []


def content_depth_score(word_counts: list[int], metrics: tuple[int, int]) -> float:
    """Weighted content-depth score from word counts and page metrics.

    ``metrics`` is (pages with code blocks, pages with tables).
    """
    total_pages = max(len(word_counts), 1)
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

    code_bonus = 100 if metrics[0] > 0 else 0
    tables_bonus = 100 if metrics[1] > 0 else 0

    score = (
        word_score * 0.35
        + depth_ratio * 0.25
        + content_ratio * 0.15
        + code_bonus * 0.15
        + tables_bonus * 0.10
    )
    return min(100.0, score)


def _payload_pages(payload: dict) -> list[dict]:
    """Extract per-page dicts from markdowns/pages in the payload."""
    md_dict = payload.get("markdowns") or {}
    if isinstance(md_dict, dict) and md_dict:
        return _markdowns_dict_pages(md_dict)
    if isinstance(md_dict, list):
        return [p for p in md_dict if isinstance(p, dict)]
    # payload["pages"] is a derived view of markdowns; only use it as a
    # fallback when there are no markdowns, to avoid double-counting.
    return _derived_payload_pages(payload)


def _markdowns_dict_pages(md_dict: dict) -> list[dict]:
    """Page dicts from a non-empty markdowns mapping."""
    pages: list[dict] = []
    for _url, pg in md_dict.items():
        if isinstance(pg, dict):
            pages.append(pg)
    return pages


def _derived_payload_pages(payload: dict) -> list[dict]:
    """Page dicts from the derived payload['pages'] view."""
    pages_list = payload.get("pages") or []
    if not isinstance(pages_list, list):
        return []
    return [p for p in pages_list if isinstance(p, dict)]


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
    """G6. Entity Consistency across pages (auto).

    Scores only on-site evidence: 20 consistent name + 15 consistent
    description, rescaled to 0-100. sameAs links to outside profiles are
    kept as raw evidence only and add no points.
    """
    audit = payload.get("audit") or {}
    entity = audit.get("entity") or {}

    name_consistent = entity.get("name_consistent", False)
    desc_consistent = entity.get("description_consistent", False)

    pts = ENTITY_NAME_POINTS * bool(name_consistent) + ENTITY_DESCRIPTION_POINTS * bool(
        desc_consistent
    )
    score = pts * 100.0 / (ENTITY_NAME_POINTS + ENTITY_DESCRIPTION_POINTS)

    return {
        "score": round(score, 1),
        "weight": GEO_WEIGHTS["entity_consistency"],
        "auto_measurable": True,
        "raw": {
            "name_consistent": name_consistent,
            "desc_consistent": desc_consistent,
            "same_as": entity.get("same_as") or [],
            "name_variants": entity.get("name_variants") or [],
            "desc_variants": entity.get("description_variants") or [],
        },
    }
