import csv
import json
import uuid
from datetime import UTC, datetime
from io import StringIO
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import and_, func, or_
from sqlalchemy.exc import IntegrityError

from webapp.db import get_session
from webapp.models import Prospect, ProspectContact
from webapp.utils.auth import require_auth
from webapp.utils.metrics import contacts_create, prospects_patch, prospects_upsert

router = APIRouter()


def _parse_cursor(cur: str | None) -> tuple[datetime, str] | None:
    if not cur:
        return None
    try:
        ts_str, pid = cur.split("|", 1)
        ts = datetime.fromisoformat(ts_str)
        if not ts.tzinfo:
            ts = ts.replace(tzinfo=UTC)
        return (ts, pid)
    except Exception:
        return None


def _make_cursor(dt: datetime | None, pid: str | None) -> str | None:
    if not dt or not pid:
        return None
    try:
        return f"{dt.isoformat()}|{pid}"
    except Exception:
        return None


@router.get("/api/prospects")
async def list_prospects(
    request: Request,
    status: str | None = None,
    q: str | None = None,
    tag: str | None = None,
    limit: int = 25,
    cursor: str | None = None,
):
    user = await require_auth(request)
    limit = max(1, min(100, limit))
    cur = _parse_cursor(cursor)

    items: list[dict[str, Any]] = []
    next_cursor: str | None = None

    with get_session() as s:
        qry = s.query(Prospect).filter(Prospect.user_id == user.id)
        if status:
            qry = qry.filter(func.lower(Prospect.status) == status.strip().lower())
        if q:
            like = f"%{q.strip().lower()}%"
            qry = qry.filter(
                or_(
                    func.lower(Prospect.domain).like(like),
                    func.lower(func.coalesce(Prospect.title, "")).like(like),
                )
            )
        if tag:
            like_tag = f"%{tag.strip().lower()}%"
            qry = qry.filter(
                func.lower(func.coalesce(Prospect.tags, "")).like(like_tag)
            )

        # Cursor: created_at desc, id desc pagination
        qry = qry.order_by(Prospect.created_at.desc(), Prospect.id.desc())
        if cur:
            ts, pid = cur
            qry = qry.filter(
                or_(
                    Prospect.created_at < ts,
                    and_(Prospect.created_at == ts, Prospect.id < pid),
                )
            )

        rows = qry.limit(limit + 1).all()
        for r in rows[:limit]:
            items.append(
                {
                    "id": r.id,
                    "domain": r.domain,
                    "url": r.url,
                    "title": r.title,
                    "status": r.status,
                    "tags": r.tags,
                    "notes": r.notes,
                    "socials": json.loads(r.socials_json) if r.socials_json else [],
                    "crawl_id": r.crawl_id,
                    "created_at": (r.created_at or datetime.now(UTC)).isoformat(),
                }
            )
        if len(rows) > limit:
            last = rows[limit - 1]
            next_cursor = _make_cursor(last.created_at, last.id)

    return {"items": items, "next_cursor": next_cursor}


def _stripped_or_none(body: dict, key: str):
    """Return a stripped optional string field, or None when empty."""
    return (body.get(key) or "").strip() or None


def _raw_or_none(body: dict, key: str):
    """Return a passthrough optional field value, or None when empty."""
    return (body.get(key) or None) or None


def _validate_prospect_domain(domain: str) -> str:
    """Validate the prospect domain; raises 400 when invalid.

    Args:
        domain: Normalized domain candidate.

    Returns:
        str: The validated domain.

    Raises:
        HTTPException: 400 when the domain is missing or malformed.
    """
    if not domain or "." not in domain or any(c.isspace() for c in domain):
        raise HTTPException(status_code=400, detail="Invalid domain")
    return domain


def _parse_prospect_fields(body: dict) -> dict:
    """Parse and normalize the non-domain prospect fields from the request body.

    Args:
        body: Parsed JSON request body.

    Returns:
        dict: Normalized prospect field values.
    """
    return {
        "url": _stripped_or_none(body, "url"),
        "title": _stripped_or_none(body, "title"),
        "status_val": (body.get("status") or "shortlisted").strip().lower(),
        "tags": _raw_or_none(body, "tags"),
        "notes": _raw_or_none(body, "notes"),
        "crawl_id": _raw_or_none(body, "crawl_id"),
        "socials": body.get("socials") or None,
    }


def _socials_json_of(socials) -> str | None:
    """Serialize socials to JSON text, or None when absent/unserializable."""
    socials_json = None
    try:
        if socials is not None:
            socials_json = json.dumps(socials)
    except Exception:
        socials_json = None
    return socials_json


def _apply_prospect_updates(
    row: Prospect, fields: dict, socials_json: str | None, now: datetime
) -> None:
    """Apply partial field updates to an existing prospect row.

    Args:
        row: Existing Prospect row to update in place.
        fields: Parsed prospect fields from the request body.
        socials_json: Serialized socials value, or None.
        now: Timestamp applied to updated_at.
    """
    row.url = fields["url"] if fields["url"] is not None else row.url
    row.title = fields["title"] if fields["title"] is not None else row.title
    row.status = fields["status_val"] or row.status
    row.tags = fields["tags"] if fields["tags"] is not None else row.tags
    row.notes = fields["notes"] if fields["notes"] is not None else row.notes
    row.crawl_id = (
        fields["crawl_id"] if fields["crawl_id"] is not None else row.crawl_id
    )
    if socials_json is not None:
        row.socials_json = socials_json
    row.updated_at = now


def _new_prospect(
    user, domain: str, fields: dict, socials_json: str | None, now: datetime
) -> Prospect:
    """Build a new Prospect row for the current user.

    Args:
        user: Authenticated user owning the prospect.
        domain: Validated prospect domain.
        fields: Parsed prospect fields from the request body.
        socials_json: Serialized socials value, or None.
        now: Timestamp applied to created_at/updated_at.

    Returns:
        Prospect: Unsaved new prospect row.
    """
    return Prospect(
        id=str(uuid.uuid4()),
        user_id=user.id,
        crawl_id=fields["crawl_id"],
        domain=domain,
        url=fields["url"],
        title=fields["title"],
        status=fields["status_val"] or "shortlisted",
        tags=fields["tags"],
        notes=fields["notes"],
        socials_json=socials_json,
        created_at=now,
        updated_at=now,
    )


def _prospect_response(row: Prospect) -> dict:
    """Serialize a Prospect row for API responses.

    Args:
        row: Prospect row to serialize.

    Returns:
        dict: JSON-compatible response payload.
    """
    return {
        "id": row.id,
        "domain": row.domain,
        "url": row.url,
        "title": row.title,
        "status": row.status,
        "tags": row.tags,
        "notes": row.notes,
        "socials": json.loads(row.socials_json) if row.socials_json else [],
        "crawl_id": row.crawl_id,
    }


@router.post("/api/prospects")
async def upsert_prospect(request: Request):
    """Create or update a prospect for the current user, keyed by domain."""
    user = await require_auth(request)
    body = await request.json()
    domain = (body.get("domain") or "").strip().lower()
    domain = _validate_prospect_domain(domain)
    fields = _parse_prospect_fields(body)
    socials_json = _socials_json_of(fields["socials"])

    with get_session() as s:
        row = (
            s.query(Prospect)
            .filter(Prospect.user_id == user.id, Prospect.domain == domain)
            .one_or_none()
        )
        now = datetime.now(UTC)
        if row:
            # Update partial fields
            _apply_prospect_updates(row, fields, socials_json, now)
        else:
            row = _new_prospect(user, domain, fields, socials_json, now)
            s.add(row)
        # metrics
        try:
            prospects_upsert.inc()
        except Exception:
            pass
        s.flush()
        return JSONResponse(content=_prospect_response(row))


@router.patch("/api/prospects/{prospect_id}")
async def patch_prospect(request: Request, prospect_id: str):
    user = await require_auth(request)
    body = await request.json()

    with get_session() as s:
        row = (
            s.query(Prospect)
            .filter(Prospect.id == prospect_id, Prospect.user_id == user.id)
            .one_or_none()
        )
        if not row:
            raise HTTPException(status_code=404, detail="Not found")

        changed = False
        for key in ("status", "tags", "notes", "url", "title", "crawl_id"):
            if key in body:
                setattr(row, key, body.get(key))
                changed = True
        if "socials" in body:
            try:
                row.socials_json = json.dumps(body.get("socials") or [])
                changed = True
            except Exception:
                pass
        if changed:
            row.updated_at = datetime.now(UTC)
        try:
            prospects_patch.inc()
        except Exception:
            pass
        s.flush()
        return {
            "id": row.id,
            "domain": row.domain,
            "url": row.url,
            "title": row.title,
            "status": row.status,
            "tags": row.tags,
            "notes": row.notes,
            "socials": json.loads(row.socials_json) if row.socials_json else [],
            "crawl_id": row.crawl_id,
        }


def _validate_contact_email(email: str) -> str:
    """Validate the contact email; raises 400 when invalid.

    Args:
        email: Normalized email candidate.

    Returns:
        str: The validated email.

    Raises:
        HTTPException: 400 when the email is missing or malformed.
    """
    if not email or "@" not in email or any(c.isspace() for c in email):
        raise HTTPException(status_code=400, detail="Invalid email")
    return email


def _parse_contact_fields(body: dict) -> dict:
    """Parse and normalize the non-email contact fields from the request body.

    Args:
        body: Parsed JSON request body.

    Returns:
        dict: Normalized contact field values.
    """
    return {
        "source_url": _stripped_or_none(body, "source_url"),
        "social_url": _stripped_or_none(body, "social_url"),
        "tags": _raw_or_none(body, "tags"),
        "role_title": _raw_or_none(body, "role_title"),
    }


def _require_owned_prospect(s, user, prospect_id: str) -> Prospect:
    """Load the prospect owned by the user; raises 404 when absent.

    Args:
        s: Active database session.
        user: Authenticated user owning the prospect.
        prospect_id: Prospect identifier.

    Returns:
        Prospect: The owned prospect row.

    Raises:
        HTTPException: 404 when the prospect is not found.
    """
    p: Prospect | None = (
        s.query(Prospect)
        .filter(Prospect.id == prospect_id, Prospect.user_id == user.id)
        .one_or_none()
    )
    if not p:
        raise HTTPException(status_code=404, detail="Prospect not found")
    return p


def _new_contact(prospect_id: str, email: str, fields: dict) -> ProspectContact:
    """Build a new ProspectContact row.

    Args:
        prospect_id: Prospect the contact belongs to.
        email: Validated contact email.
        fields: Parsed contact fields from the request body.

    Returns:
        ProspectContact: Unsaved new contact row.
    """
    return ProspectContact(
        id=str(uuid.uuid4()),
        prospect_id=prospect_id,
        email=email,
        source_url=fields["source_url"],
        social_url=fields["social_url"],
        tags=fields["tags"],
        role_title=fields["role_title"],
        created_at=datetime.now(UTC),
    )


def _contact_response(c: ProspectContact) -> dict:
    """Serialize a ProspectContact row for API responses.

    Args:
        c: Contact row to serialize.

    Returns:
        dict: JSON-compatible response payload.
    """
    return {
        "id": c.id,
        "prospect_id": c.prospect_id,
        "email": c.email,
        "source_url": c.source_url,
        "social_url": c.social_url,
        "tags": c.tags,
        "role_title": c.role_title,
        "created_at": (c.created_at or datetime.now(UTC)).isoformat(),
    }


@router.post("/api/prospects/{prospect_id}/contacts")
async def create_contact(request: Request, prospect_id: str):
    """Create a contact on a prospect owned by the current user."""
    user = await require_auth(request)
    body = await request.json()
    email = (body.get("email") or "").strip().lower()
    email = _validate_contact_email(email)
    fields = _parse_contact_fields(body)

    with get_session() as s:
        # ownership check of prospect
        _require_owned_prospect(s, user, prospect_id)

        c = _new_contact(prospect_id, email, fields)
        try:
            s.add(c)
            s.flush()
        except IntegrityError:
            raise HTTPException(status_code=409, detail="Contact already exists")
        try:
            contacts_create.inc()
        except Exception:
            pass
        return _contact_response(c)


@router.get("/api/prospects/{prospect_id}/contacts.csv")
async def export_contacts_csv(request: Request, prospect_id: str):
    user = await require_auth(request)
    with get_session() as s:
        p = (
            s.query(Prospect)
            .filter(Prospect.id == prospect_id, Prospect.user_id == user.id)
            .one_or_none()
        )
        if not p:
            raise HTTPException(status_code=404, detail="Prospect not found")
        rows = (
            s.query(ProspectContact)
            .filter(ProspectContact.prospect_id == prospect_id)
            .order_by(ProspectContact.created_at.desc())
            .all()
        )
    buf = StringIO()
    w = csv.writer(buf)
    w.writerow(
        ["email", "source_url", "social_url", "tags", "role_title", "created_at"]
    )
    for r in rows:
        w.writerow(
            [
                r.email or "",
                r.source_url or "",
                r.social_url or "",
                r.tags or "",
                r.role_title or "",
                (r.created_at or datetime.now(UTC)).isoformat(),
            ]
        )
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="contacts-{prospect_id}.csv"'
        },
    )


@router.get("/api/prospects/{prospect_id}/contacts")
async def list_contacts(request: Request, prospect_id: str):
    user = await require_auth(request)
    with get_session() as s:
        p = (
            s.query(Prospect)
            .filter(Prospect.id == prospect_id, Prospect.user_id == user.id)
            .one_or_none()
        )
        if not p:
            raise HTTPException(status_code=404, detail="Prospect not found")
        rows = (
            s.query(ProspectContact)
            .filter(ProspectContact.prospect_id == prospect_id)
            .order_by(ProspectContact.created_at.desc())
            .all()
        )
        items = [
            {
                "id": r.id,
                "prospect_id": r.prospect_id,
                "email": r.email,
                "source_url": r.source_url,
                "social_url": r.social_url,
                "tags": r.tags,
                "role_title": r.role_title,
                "created_at": (r.created_at or datetime.now(UTC)).isoformat(),
            }
            for r in rows
        ]
    return {"items": items}


@router.delete("/api/prospects/{prospect_id}/contacts/{contact_id}")
async def delete_contact(request: Request, prospect_id: str, contact_id: str):
    user = await require_auth(request)
    with get_session() as s:
        p = (
            s.query(Prospect)
            .filter(Prospect.id == prospect_id, Prospect.user_id == user.id)
            .one_or_none()
        )
        if not p:
            raise HTTPException(status_code=404, detail="Prospect not found")
        c = (
            s.query(ProspectContact)
            .filter(
                ProspectContact.id == contact_id,
                ProspectContact.prospect_id == prospect_id,
            )
            .one_or_none()
        )
        if not c:
            raise HTTPException(status_code=404, detail="Contact not found")
        s.delete(c)
        s.flush()
        return {"ok": True, "id": contact_id}


@router.delete("/api/prospects/{prospect_id}")
async def delete_prospect(request: Request, prospect_id: str):
    user = await require_auth(request)
    with get_session() as s:
        row = (
            s.query(Prospect)
            .filter(Prospect.id == prospect_id, Prospect.user_id == user.id)
            .one_or_none()
        )
        if not row:
            raise HTTPException(status_code=404, detail="Not found")
        s.delete(row)
        s.flush()
        return {"ok": True, "id": prospect_id}


# Products API endpoints removed from prospects router to avoid duplication.
# Use the canonical implementations in webapp.routers.api for /api/products.
