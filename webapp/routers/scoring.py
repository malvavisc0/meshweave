"""Scoring methodology page router."""

import os

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from webapp.infra import templates
from webapp.utils.url import _abs_url

router = APIRouter()


@router.get("/scoring", response_class=HTMLResponse)
async def scoring_page(request: Request):
    """Scoring methodology page — explains AEO/GEO factors, weights, and ratings."""
    site_name = os.getenv("SITE_NAME", "MeshWeave")
    page_title = f"Scoring Methodology — {site_name}"
    meta_description = (
        "Learn how MeshWeave scores websites for AEO and GEO. "
        "12 factors, weighted composites, auto-scored from crawl data."
    )
    abs_page_url = _abs_url(request, "/scoring")
    og_image_url = os.getenv("OG_IMAGE_URL") or None

    return templates.TemplateResponse(
        request,
        "scoring.html",
        {
            "site_name": site_name,
            "page_title": page_title,
            "meta_description": meta_description,
            "abs_page_url": abs_page_url,
            "og_image_url": og_image_url,
        },
    )
