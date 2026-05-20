from webapp.models import Crawl
from webapp.utils.url import canonicalize_url, normalize_domain


def build_summary(row: Crawl, payload: dict | None) -> dict:
    """Build a computed summary object for a crawl payload.

    Parameters:
        row (Crawl): Database row for the analysis. Used as a fallback for values such as
            canonical URL and base domain if they are missing from the payload.
        payload (Optional[dict]): Parsed JSON payload produced by markdownify-crawler.
            May be None or partially malformed; this function is defensive against that.

    Returns:
        dict: Summary dictionary with the following shape:
            {
              "metrics": {
                "render": {
                  "final_url": str,
                  "response_status": int|None,
                  "network_requests": int|None,
                  "content_length": int|None,
                  "load_time_ms": float|int|None,
                  "cache_hit": bool|None,
                },
                "extraction": {
                  "base_domain": str,
                  "internal_count": int|None,
                  "external_count": int|None,
                  "total_candidates": int|None,
                  "unique_total": int|None,
                  "parse_time_ms": float|int|None,
                },
              },
              "emails": {
                "unique_count": int,
                "counts": dict,
              },
              "links": {
                "internal_count": int,
                "external_count": int,
                "top_external_domains": [{"domain": str, "count": int}, ...],
              },
              "seo_deltas": {
                "title_mismatch": bool,
                "description_mismatch": bool,
                "canonical_mismatch": bool,
                "og_missing": [str, ...],
              },
            }
        If any error occurs, an empty dict {} is returned.
    """
    summary: dict = {}
    try:
        payload_dict: dict = payload if isinstance(payload, dict) else {}
        # Prefer top-level page metadata; fallback to first page entry when missing
        pages_arr: list[dict] = payload_dict.get("pages") or []
        pg: dict = payload_dict.get("page") or {}
        if not pg:
            try:
                if (
                    isinstance(pages_arr, list)
                    and len(pages_arr) > 0
                    and isinstance(pages_arr[0], dict)
                ):
                    # Some payloads nest page metadata under pages[0].page
                    pg = pages_arr[0].get("page") or {}
            except Exception:
                pg = {}
        og: dict = pg.get("og") or {}
        metrics: dict = payload_dict.get("metrics") or {}
        # Derive render metrics strictly from the first page (home "/")
        try:
            first_page_metrics: dict = (
                (pages_arr[0].get("metrics") or {})
                if (
                    isinstance(pages_arr, list)
                    and len(pages_arr) > 0
                    and isinstance(pages_arr[0], dict)
                )
                else {}
            )
        except Exception:
            first_page_metrics = {}
        render: dict = first_page_metrics.get("render") or {}
        extraction: dict = metrics.get("extraction") or {}
        lks: dict = payload_dict.get("links") or {}
        em: dict = payload_dict.get("emails") or {}

        base_domain = (extraction.get("base_domain") or row.domain or "").strip()

        # Top external domains
        top_ext: dict[str, int] = {}
        for u in lks.get("external") or []:
            dom = normalize_domain(u)
            if dom:
                top_ext[dom] = top_ext.get(dom, 0) + 1
        top_external_domains: list[dict[str, object]] = [
            {"domain": d, "count": c}
            for d, c in sorted(top_ext.items(), key=lambda kv: (-kv[1], kv[0]))
        ]

        def _t(s):
            try:
                return (s or "").strip()
            except Exception:
                return ""

        seo_deltas = {
            "title_mismatch": _t(pg.get("title")) != _t(og.get("title")),
            "description_mismatch": _t(pg.get("description"))
            != _t(og.get("description")),
            "canonical_mismatch": canonicalize_url(_t(pg.get("canonical")))[3]
            != canonicalize_url(_t(row.canonical_url))[3],
            "og_missing": [
                k for k in ("title", "description", "image", "url") if not _t(og.get(k))
            ],
        }

        summary = {
            "metrics": {
                "render": {
                    "final_url": render.get("final_url") or "",
                    "response_status": render.get("response_status"),
                    "network_requests": render.get("network_requests"),
                    "content_length": render.get("content_length"),
                    "load_time_ms": render.get("load_time_ms"),
                    "cache_hit": render.get("cache_hit"),
                },
                "extraction": {
                    "base_domain": base_domain,
                    "internal_count": extraction.get("internal_count"),
                    "external_count": extraction.get("external_count"),
                    "total_candidates": extraction.get("total_candidates"),
                    "unique_total": extraction.get("unique_total"),
                    "parse_time_ms": extraction.get("parse_time_ms"),
                },
            },
            "emails": {
                "unique_count": (
                    len(em.get("unique") or [])
                    if em.get("unique")
                    else (
                        em.get("unique_count")
                        or em.get("counts", {}).get("total_unique", 0)
                    )
                ),
                "counts": (em.get("counts") or {}),
            },
            "links": {
                "internal_count": len(lks.get("internal") or []),
                "external_count": len(lks.get("external") or []),
                "top_external_domains": top_external_domains,
            },
            "seo_deltas": seo_deltas,
        }
    except Exception:
        summary = {}
    return summary
