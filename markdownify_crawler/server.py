from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import HttpUrl

from .core import crawl

app = FastAPI()


@app.get("/crawl")
async def crawl_endpoint(
    url: HttpUrl,
    crawl_internal: bool = False,
    crawl_max_pages: int = 25,
    same_domain_only: bool = True,
    include_emails: bool = True,
    deobfuscate_emails: bool = True,
    throttle_ms: int = 0,
    per_page_timeout: float = 15.0,
    force_refresh: bool = False,
):
    """
    HTTP GET /crawl: Render a page, collect metadata and links, optionally crawl internal pages, and extract emails.

    Query Parameters:
        url (HttpUrl): Required. Starting HTTP/HTTPS URL.
        crawl_internal (bool): If true, perform a same-site BFS crawl of internal links up to crawl_max_pages. Default: False.
        crawl_max_pages (int): Hard cap on total pages visited including the start page. Default: 25.
        same_domain_only (bool): If true, enforce that visited pages remain on the starting domain after redirects. Default: True.
        include_emails (bool): If true, extract email addresses on each visited page. Default: True.
        deobfuscate_emails (bool): If true, deobfuscate "at"/"dot" textual patterns before email extraction. Default: True.
        throttle_ms (int): Delay (milliseconds) between page fetches during crawl. Default: 0.
        per_page_timeout (float): Timeout (seconds) for each crawled page fetch. Default: 15.0.
        force_refresh (bool): If true, bypass HTML cache (disable_cache) for this run. Default: False.

    Returns:
        fastapi.responses.JSONResponse: JSON payload mirroring markdownify_crawler.core.crawl output:
            {
              "page": {...},
              "markdown": "...",
              "links": {"internal": [...], "external": [...]},
              "metrics": {"render": {...}, "extraction": {...}},
              "emails": {...},    # present if include_emails=True
              "crawl": {
                "enabled": bool,
                "start_url": str,
                "visited": [...],
                "limits": {"max_pages": int},
                "reason_stopped": str
              }
            }

    Notes:
        This endpoint delegates core logic to markdownify_crawler.core.crawl and returns its payload unchanged.
    """
    payload = await crawl(
        url=str(url),
        crawl_internal=crawl_internal,
        crawl_max_pages=crawl_max_pages,
        same_domain_only=same_domain_only,
        include_emails=include_emails,
        deobfuscate_emails=deobfuscate_emails,
        throttle_ms=throttle_ms,
        per_page_timeout=per_page_timeout,
        disable_cache=force_refresh,
        cache_dir=None,  # core will pick MARKDOWNIFY_CACHE_DIR or default
    )
    return JSONResponse(content=payload)
