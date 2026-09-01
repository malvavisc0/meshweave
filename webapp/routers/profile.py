"""Authenticated profile and account-management pages."""

import os

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from webapp.db import get_session
from webapp.infra import templates
from webapp.models import ApiKey, User
from webapp.utils.auth import (
    clear_auth_cookie,
    destroy_all_sessions_for_user,
    get_auth_cookie_value,
    require_auth,
)
from webapp.utils.security import (
    page_csrf,
    set_csrf_session_cookie,
    verify_request_csrf,
)
from webapp.utils.url import _abs_url

router = APIRouter()


def _profile_keys(user_id: str) -> list[dict]:
    with get_session() as s:
        rows = (
            s.query(ApiKey)
            .filter(ApiKey.user_id == user_id)
            .order_by(ApiKey.created_at.desc())
            .all()
        )
        return [
            {
                "id": row.id,
                "name": row.name,
                "prefix": row.key_prefix,
                "created_at": (row.created_at).strftime("%Y-%m-%d"),
                "revoked": row.revoked_at is not None,
            }
            for row in rows
        ]


@router.get("/profile", response_class=HTMLResponse)
async def profile(request: Request) -> HTMLResponse:
    """Render the profile page with the user's API keys."""
    user = await require_auth(request)
    csrf_token, session_id, new_session = page_csrf(request)
    site_name = os.getenv("SITE_NAME", "MeshWeave")
    resp = templates.TemplateResponse(
        request,
        "profile.html",
        {
            "site_name": site_name,
            "page_title": f"Profile — {site_name}",
            "meta_description": "Manage your MeshWeave profile, API keys, and account data.",
            "abs_page_url": _abs_url(request, "/profile"),
            "csrf_token": csrf_token,
            "profile_user": user,
            "api_keys": _profile_keys(user.id),
            "profile_notice": request.query_params.get("notice"),
        },
    )
    set_csrf_session_cookie(resp, session_id, new_session)
    return resp


@router.post("/profile")
async def update_profile(
    request: Request,
    name: str = Form(""),
    company_name: str = Form(""),
    csrf_token: str | None = Form(None),
) -> RedirectResponse:
    """Update the signed-in user's name and company name."""
    user = await require_auth(request)
    verify_request_csrf(request, csrf_token)
    with get_session() as s:
        row = s.get(User, user.id)
        assert row is not None
        row.name = name.strip()[:255] or None
        row.company_name = company_name.strip()[:255] or None
    return RedirectResponse("/profile?notice=saved", status_code=303)


@router.post("/profile/delete")
async def delete_account(
    request: Request,
    confirmation: str = Form(""),
    csrf_token: str | None = Form(None),
) -> RedirectResponse:
    """Delete the account and all owned data after DELETE confirmation."""
    user = await require_auth(request)
    verify_request_csrf(request, csrf_token)
    if confirmation.strip() != "DELETE":
        return RedirectResponse("/profile?notice=delete_confirmation", status_code=303)
    session_id = get_auth_cookie_value(request)
    destroy_all_sessions_for_user(user.id)
    with get_session() as s:
        row = s.get(User, user.id)
        s.delete(row)
    response = RedirectResponse("/?notice=account_deleted", status_code=303)
    if session_id:
        clear_auth_cookie(response)
    return response
