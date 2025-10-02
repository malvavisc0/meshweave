"""Legal pages router providing /privacy and /terms templates."""

import os
import json

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from webapp.infra import templates
from webapp.utils.url import _abs_url

router = APIRouter()


@router.get("/privacy", response_class=HTMLResponse)
async def privacy(request: Request):
    site_name = os.getenv("SITE_NAME", "Meshweave")
    page_title = f"Privacy Policy — {site_name}"
    # Plan-aligned description
    meta_description = (
        "Public by default when not signed in. Sign in to keep results private. "
        "We collect input URLs and crawl metadata to perform requested analysis."
    )
    abs_page_url = _abs_url(request, "/privacy")
    og_image_url = os.getenv("OG_IMAGE_URL") or None

    # JSON-LD: WebPage with mainEntity (LLM-first)
    try:
        json_ld = json.dumps(
            {
                "@context": "https://schema.org",
                "@type": "WebPage",
                "name": "Privacy Policy",
                "url": abs_page_url,
                "isPartOf": _abs_url(request, "/"),
                "contactPoint": {
                    "@type": "ContactPoint",
                    "email": os.getenv("FOOTER_CONTACT_EMAIL", "hello@meshweave.com"),
                },
                "mainEntity": {
                    "@type": "CreativeWork",
                    "name": "Privacy Policy",
                    "text": "Public by default when not signed in. Sign in to keep results private.",
                },
            }
        )
    except Exception:
        json_ld = None

    return templates.TemplateResponse(
        "privacy.html",
        {
            "request": request,
            "site_name": site_name,
            "page_title": page_title,
            "meta_description": meta_description,
            "abs_page_url": abs_page_url,
            "og_image_url": og_image_url,
            "json_ld": json_ld,
        },
    )


@router.get("/terms", response_class=HTMLResponse)
async def terms(request: Request):
    site_name = os.getenv("SITE_NAME", "Meshweave")
    page_title = f"Terms of Service — {site_name}"
    meta_description = "Use Meshweave for lawful analysis. Do not bypass paywalls or technical restrictions."
    abs_page_url = _abs_url(request, "/terms")
    og_image_url = os.getenv("OG_IMAGE_URL") or None

    # JSON-LD: WebPage with mainEntity (LLM-first)
    try:
        json_ld = json.dumps(
            {
                "@context": "https://schema.org",
                "@type": "WebPage",
                "name": "Terms of Service",
                "url": abs_page_url,
                "isPartOf": _abs_url(request, "/"),
                "mainEntity": {
                    "@type": "CreativeWork",
                    "name": "Terms of Service",
                    "text": "Use Meshweave for lawful analysis. Do not bypass paywalls or technical restrictions.",
                },
            }
        )
    except Exception:
        json_ld = None

    return templates.TemplateResponse(
        "terms.html",
        {
            "request": request,
            "site_name": site_name,
            "page_title": page_title,
            "meta_description": meta_description,
            "abs_page_url": abs_page_url,
            "og_image_url": og_image_url,
            "json_ld": json_ld,
        },
    )
