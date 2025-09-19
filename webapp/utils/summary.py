from typing import Dict, List, Optional

from webapp.models import Crawl
from webapp.utils.url import normalize_domain


def build_summary(row: Crawl, payload: Optional[dict]) -> dict:
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
        payload_dict: Dict = payload if isinstance(payload, dict) else {}
        pg: Dict = payload_dict.get("page") or {}
        og: Dict = pg.get("og") or {}
        metrics: Dict = payload_dict.get("metrics") or {}
        pages_arr: List[Dict] = payload_dict.get("pages") or []
        # Derive render metrics strictly from the first page (home "/")
        try:
            first_page_metrics: Dict = (
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
        render: Dict = first_page_metrics.get("render") or {}
        extraction: Dict = metrics.get("extraction") or {}
        lks: Dict = payload_dict.get("links") or {}
        em: Dict = payload_dict.get("emails") or {}

        base_domain = (extraction.get("base_domain") or row.domain or "").strip()

        # Top external domains
        top_ext: Dict[str, int] = {}
        for u in lks.get("external") or []:
            dom = normalize_domain(u)
            if dom:
                top_ext[dom] = top_ext.get(dom, 0) + 1
        top_external_domains: List[Dict[str, object]] = [
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
            "canonical_mismatch": _t(pg.get("canonical")) != _t(row.canonical_url),
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
                "unique_count": len(em.get("unique") or []),
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
