import os

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from webapp.infra import templates
from webapp.utils.auth import require_auth
from webapp.utils.security import page_csrf, set_csrf_session_cookie
from webapp.utils.url import _abs_url

router = APIRouter()


@router.get("/products", response_class=HTMLResponse)
async def products_page(request: Request):
    """Products management page."""
    await require_auth(request)

    site_name = os.getenv("SITE_NAME", "MeshWeave")
    page_title = f"Products — {site_name}"
    meta_description = "Manage your products."

    csrf_token, session_id, new_session = page_csrf(request)

    resp = templates.TemplateResponse(
        request,
        "products.html",
        {
            "site_name": site_name,
            "page_title": page_title,
            "meta_description": meta_description,
            "abs_page_url": _abs_url(request, "/products"),
            "csrf_token": csrf_token,
        },
    )

    set_csrf_session_cookie(resp, session_id, new_session)
    return resp
