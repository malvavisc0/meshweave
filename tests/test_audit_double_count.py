"""Test that cross-page audits do not double-count the start page.

The start page is merged into ``markdowns`` by ``_build_payload``; the
audit functions (meta uniqueness, entity consistency, schema coverage)
must therefore not also receive the start-page meta as a separate
``(start)`` entry, or every site reports 6 audited pages for a 5-page
crawl and schema coverage drops (e.g. 83.3% instead of 100%).
"""

from meshweave.core import _build_payload


def _page_data(url: str) -> dict:
    return {
        "markdown": f"# {url}",
        "page_meta": {
            "title": f"Page {url}",
            "description": f"Description {url}",
            "og": {"title": f"OG {url}", "description": f"OGD {url}"},
            "canonical": url,
            "jsonld": [
                {
                    "@context": "https://schema.org",
                    "@type": "Organization",
                    "name": "MeshWeave",
                    "description": "desc",
                    "sameAs": [],
                }
            ],
        },
        "headings": {},
        "content_metrics": {"words": 10},
        "internal_links": [],
        "external_links": [],
        "extraction_metrics": {},
    }


def _crawl_result(urls: list[str]) -> dict:
    return {
        "markdowns": {u: {"page": _page_data(u)["page_meta"]} for u in urls},
        "visited": urls,
        "stop_reason": "max_depth",
        "seeded": 0,
    }


def test_build_payload_audits_each_page_once():
    urls = [
        "http://meshweave:8080/",
        "http://meshweave:8080/terms",
        "http://meshweave:8080/privacy",
    ]
    payload = _build_payload(
        page_data=_page_data(urls[0]),
        render_metrics={},
        all_emails=set(),
        emails_by_url={},
        deduped_sources=[],
        crawl_max_pages=25,
        origin=urls[0],
        crawl_result=_crawl_result(urls),
        sitemap_meta={},
        include_emails=False,
    )

    audit = payload["audit"]
    assert audit["meta"]["total_pages_checked"] == len(urls)
    # Schema coverage must count real pages only — 3/3, not 3/4.
    assert audit["schema_coverage"]["pages_with_schema"] == len(urls)
    assert audit["schema_coverage"]["pages_without_schema"] == 0
    assert audit["schema_coverage"]["coverage_pct"] == 100.0
    # No "(start)" pseudo-entry in the audited OG titles.
    assert "(start)" not in audit["meta"]["duplicate_og_titles"]
