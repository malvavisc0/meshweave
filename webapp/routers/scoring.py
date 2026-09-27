"""Scoring methodology page router."""

import json
import os

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from meshweave.ai.answerability import ANSWERABILITY_QUESTIONS
from meshweave.scoring.composite import LENS_WEIGHTS
from meshweave.scoring.ratings import AAX_RATINGS, AEO_RATINGS, GEO_RATINGS
from webapp.infra import templates
from webapp.utils.scoring import FACTOR_DESCRIPTIONS, FACTOR_DISPLAY_NAMES
from webapp.utils.url import _abs_url

router = APIRouter()


def _factor_rows(lens: str) -> list[dict[str, str]]:
    """Methodology weight rows derived from the authoritative weight table."""
    return [
        {
            "name": FACTOR_DISPLAY_NAMES.get(key, key.replace("_", " ").title()),
            "weight": f"{weight:.0%}",
            "checks": FACTOR_DESCRIPTIONS.get(key, ""),
        }
        for key, weight in LENS_WEIGHTS[lens].items()
    ]


def _band_rows(ratings: list[tuple[int, int, str]]) -> list[dict[str, str]]:
    """Methodology rating rows derived from the authoritative band table."""
    return [{"range": f"{lo}\u2013{hi}", "label": label} for lo, hi, label in ratings]


@router.get("/methodology", response_class=HTMLResponse)
async def methodology_page(request: Request):
    """Scoring methodology page — explains the three checks, weights, and ratings."""
    site_name = os.getenv("SITE_NAME", "MeshWeave")
    page_title = f"Methodology — {site_name}"
    meta_description = (
        "How MeshWeave scores Reachable, Answerable, and Actionable: the "
        "factors, their weights, the six buyer questions, and the rating bands."
    )
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
                "description": (
                    "How MeshWeave scores Reachable, Answerable, and Actionable: "
                    "factors, weights, buyer questions, and rating bands."
                ),
                "mainEntity": {
                    "@type": "CreativeWork",
                    "name": "MeshWeave scoring framework",
                    "about": [
                        "Reachable: can AI agents get to the pages and tell who the company is?",
                        "Answerable: can AI agents answer a buyer's questions from the content?",
                        "Actionable: can AI agents tell what is sold and how to buy or get in touch?",
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
            "aeo_factors": _factor_rows("aeo"),
            "geo_factors": _factor_rows("geo"),
            "aax_factors": _factor_rows("aax"),
            "aeo_bands": _band_rows(AEO_RATINGS),
            "geo_bands": _band_rows(GEO_RATINGS),
            "aax_bands": _band_rows(AAX_RATINGS),
            "questions": [q.text for q in ANSWERABILITY_QUESTIONS],
        },
    )
