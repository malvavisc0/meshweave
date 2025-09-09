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
):
    """
    Render a page, convert to markdown, classify links, optionally crawl internal pages, and extract emails.
    Delegates to markdownify_crawler.core.crawl and returns the same payload shape.
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
        cache_dir=None,  # core will pick MARKDOWNIFY_CACHE_DIR or default
    )
    return JSONResponse(content=payload)
