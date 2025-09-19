"""Legal pages router providing /privacy and /terms templates."""

import os

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from webapp.infra import templates
from webapp.utils.url import _abs_url

router = APIRouter()


@router.get("/privacy", response_class=HTMLResponse)
async def privacy(request: Request):
    site_name = os.getenv("SITE_NAME", "Markdownify Web App")
    page_title = f"Privacy Policy — {site_name}"
    meta_description = f"Privacy policy for {site_name}."
    abs_page_url = _abs_url(request, "/privacy")
    og_image_url = os.getenv("OG_IMAGE_URL") or None
    return templates.TemplateResponse(
        "privacy.html",
        {
            "request": request,
            "site_name": site_name,
            "page_title": page_title,
            "meta_description": meta_description,
            "abs_page_url": abs_page_url,
            "og_image_url": og_image_url,
        },
    )


@router.get("/terms", response_class=HTMLResponse)
async def terms(request: Request):
    site_name = os.getenv("SITE_NAME", "Markdownify Web App")
    page_title = f"Terms of Service — {site_name}"
    meta_description = f"Terms of service for {site_name}."
    abs_page_url = _abs_url(request, "/terms")
    og_image_url = os.getenv("OG_IMAGE_URL") or None
    return templates.TemplateResponse(
        "terms.html",
        {
            "request": request,
            "site_name": site_name,
            "page_title": page_title,
            "meta_description": meta_description,
            "abs_page_url": abs_page_url,
            "og_image_url": og_image_url,
        },
    )
