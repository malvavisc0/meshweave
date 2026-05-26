"""GEO (Generative Engine Optimization) factor scoring functions.

Each factor takes the crawl payload (dict) and returns a dict with:
  - score: float | None (0-100, or None if not auto-measurable)
  - weight: float
  - auto_measurable: bool
  - raw: dict (diagnostic data)
  - note: str | None (optional)
"""

from __future__ import annotations


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
    if same_as_count == 0:
        same_as_score = 0
    elif same_as_count <= 2:
        same_as_score = 40
    elif same_as_count <= 5:
        same_as_score = 70
    else:
        same_as_score = 100

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

    Additive scoring up to 100.
    """
    audit = payload.get("audit") or {}
    entity = audit.get("entity") or {}
    schema_cov = audit.get("schema_coverage") or {}
    type_counts = schema_cov.get("type_counts") or {}
    schema_types = set(type_counts.keys())
    pages_with_org = entity.get("pages_with_org_schema") or 0

    # Check all JSON-LD for author/review/video
    has_author = False
    has_reviews = False
    has_video = False
    has_contact = False
    has_privacy = False
    has_terms = False

    all_jsonld = []
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

    for ld in all_jsonld:
        ld_type = (ld.get("@type") or "").lower()
        if "author" in ld or "author" in [k.lower() for k in ld.keys()]:
            has_author = True
        if ld_type in ("review", "aggregaterating", "product"):
            if ld.get("review") or ld.get("aggregateRating"):
                has_reviews = True
        if ld_type == "videoobject":
            has_video = True
        if ld_type == "contactpage" or ld_type == "contactpoint":
            has_contact = True

    # Check URL patterns for privacy/terms/contact
    all_urls = []
    all_urls.append(page.get("url") or "")
    all_urls.append(page.get("canonical") or "")
    for _url in md_dict.keys() if isinstance(md_dict, dict) else []:
        all_urls.append(str(_url))
    urls_text = " ".join(all_urls).lower()
    if "privacy" in urls_text or "terms" in urls_text:
        has_privacy = True
    if "terms" in urls_text or "legal" in urls_text:
        has_terms = True
    if "contact" in urls_text:
        has_contact = True

    pts = 0
    lower_types = {t.lower() for t in schema_types}
    if (
        pages_with_org > 0
        or "organization" in lower_types
        or "org" in lower_types
    ):
        pts += 15
    if has_author:
        pts += 15
    if has_reviews:
        pts += 15
    same_as = entity.get("same_as") or []
    if len(same_as) > 0:
        pts += 10
    if has_contact:
        pts += 8
    if has_privacy or has_terms:
        pts += 7
    if has_video:
        pts += 5
    pts = min(100, pts)

    return {
        "score": float(pts),
        "weight": 0.15,
        "auto_measurable": True,
        "raw": {
            "has_org_schema": pages_with_org > 0,
            "has_author_info": has_author,
            "has_reviews": has_reviews,
            "same_as_count": len(same_as),
            "has_contact": has_contact,
            "has_privacy": has_privacy or has_terms,
            "has_video": has_video,
        },
    }


def score_crawl_access(payload: dict) -> dict:
    """G4. LLM Crawl Accessibility (15% weight, auto when data available).

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

    # Bot access
    bots = robots.get("bots") or {}
    for bot_name, expected_pts in [
        ("GPTBot", 15),
        ("ClaudeBot", 12),
        ("PerplexityBot", 12),
    ]:
        status = str(bots.get(bot_name) or "").lower()
        if "allow" in status:
            pts += expected_pts

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
    md_dict = payload.get("markdowns") or {}
    pages = []
    if isinstance(md_dict, dict):
        for _url, pg in md_dict.items():
            if isinstance(pg, dict):
                pages.append(pg)
    elif isinstance(md_dict, list):
        pages = [p for p in md_dict if isinstance(p, dict)]

    # Track seen URLs for deduplication across sources
    seen_urls: set[str] = set()
    for pg in pages:
        url = (pg.get("page") or pg).get("url") or ""
        if url:
            seen_urls.add(url)

    # Also check payload["pages"], deduplicating by URL
    pages_list = payload.get("pages") or []
    if isinstance(pages_list, list):
        for item in pages_list:
            if isinstance(item, dict):
                url = (item.get("page") or item).get("url") or ""
                if url and url in seen_urls:
                    continue
                if url:
                    seen_urls.add(url)
                pages.append(item)

    if not pages:
        # Single page
        page = payload.get("page") or {}
        if page:
            pages = [page]

    total_pages = max(len(pages), 1)
    word_counts = []
    pages_with_code = 0
    pages_with_tables = 0
    content_pages_gt200 = 0

    for pg in pages:
        cm = pg.get("content_metrics") or {}
        # Fallback: check nested "page" key (single-page crawl structure)
        if not cm:
            page_info = pg.get("page") or {}
            cm = page_info.get("content_metrics") or {}
        words = cm.get("words") or 0
        word_counts.append(words)
        if words > 200:
            content_pages_gt200 += 1
        if (cm.get("code_blocks") or 0) > 0:
            pages_with_code += 1
        if (cm.get("tables") or 0) > 0:
            pages_with_tables += 1

    avg_words = sum(word_counts) / len(word_counts) if word_counts else 0

    # Average word count score (0-100)
    if avg_words < 200:
        word_score = 10
    elif avg_words < 500:
        word_score = 30
    elif avg_words < 1000:
        word_score = 50
    elif avg_words < 2000:
        word_score = 70
    elif avg_words < 5000:
        word_score = 90
    else:
        word_score = 100

    # Pages with 1000+ words ratio
    pages_gt1000 = sum(1 for w in word_counts if w >= 1000)
    depth_ratio = (pages_gt1000 / total_pages) * 100 if total_pages > 0 else 0

    # Has code blocks
    code_bonus = 100 if pages_with_code > 0 else 0

    # Has tables (original data signal)
    tables_bonus = 100 if pages_with_tables > 0 else 0

    # Unique content pages scaled (> 200 words)
    content_ratio = (
        min((content_pages_gt200 / total_pages) * 100, 100) if total_pages > 0 else 0
    )

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
            "pages_with_code": pages_with_code,
            "pages_with_tables": pages_with_tables,
        },
    }


def score_entity_consistency(payload: dict) -> dict:
    """G6. Cross-Platform Entity Consistency (10% weight, auto)."""
    audit = payload.get("audit") or {}
    entity = audit.get("entity") or {}

    name_consistent = entity.get("name_consistent", False)
    desc_consistent = entity.get("description_consistent", False)
    same_as = entity.get("same_as") or []
    name_variants = entity.get("name_variants") or []
    desc_variants = entity.get("description_variants") or []

    pts = 0
    if name_consistent:
        pts += 20
    if desc_consistent:
        pts += 15

    same_as_count = len(same_as)
    if same_as_count == 0:
        pts += 0
    elif same_as_count <= 2:
        pts += 20
    elif same_as_count <= 4:
        pts += 30
    else:
        pts += 40

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
