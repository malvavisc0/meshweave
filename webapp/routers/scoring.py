"""Scoring methodology page router."""

import json
import os

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from webapp.infra import templates
from webapp.utils.url import _abs_url

router = APIRouter()


@router.get("/methodology", response_class=HTMLResponse)
async def methodology_page(request: Request):
    """Scoring methodology page — explains AEO/GEO factors, weights, and ratings."""
    site_name = os.getenv("SITE_NAME", "MeshWeave")
    page_title = f"Methodology — {site_name}"
    meta_description = "Learn how MeshWeave computes AEO, GEO, and AAX and how to interpret each score as a diagnostic signal."
    abs_page_url = _abs_url(request, "/methodology")
    og_image_url = os.getenv("OG_IMAGE_URL") or None

    # JSON-LD: WebPage authored by the MeshWeave team, with the scoring
    # framework as mainEntity (LLM-first: extractable factor definitions).
    try:
        json_ld = json.dumps(
            {
                "@context": "https://schema.org",
                "@type": "WebPage",
                "name": "MeshWeave Scoring Methodology",
                "url": abs_page_url,
                "isPartOf": _abs_url(request, "/"),
                "author": {
                    "@type": "Organization",
                    "name": site_name,
                    "url": _abs_url(request, "/"),
                },
                "dateModified": "2026-08-27",
                "description": (
                    "How MeshWeave computes AEO, GEO, and AAX scores: "
                    "factors, weights, auto vs. manual inputs, and rating "
                    "bands."
                ),
                "mainEntity": {
                    "@type": "CreativeWork",
                    "name": "MeshWeave scoring framework",
                    "about": [
                        "Answer Engine Optimization (AEO)",
                        "Generative Engine Optimization (GEO)",
                        "AI Agent Experience (AAX)",
                    ],
                },
            }
        )
    except Exception:
        json_ld = None

    return templates.TemplateResponse(
        request,
        "scoring.html",
        {
            "site_name": site_name,
            "page_title": page_title,
            "meta_description": meta_description,
            "abs_page_url": abs_page_url,
            "og_image_url": og_image_url,
            "json_ld": json_ld,
        },
    )
