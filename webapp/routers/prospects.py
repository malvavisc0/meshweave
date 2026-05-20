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


@router.post("/api/prospects")
async def upsert_prospect(request: Request):
    user = await require_auth(request)
    body = await request.json()
    domain = (body.get("domain") or "").strip().lower()
    if not domain or "." not in domain or any(c.isspace() for c in domain):
        raise HTTPException(status_code=400, detail="Invalid domain")

    url = (body.get("url") or "").strip() or None
    title = (body.get("title") or "").strip() or None
    status_val = (body.get("status") or "shortlisted").strip().lower()
    tags = (body.get("tags") or None) or None
    notes = (body.get("notes") or None) or None
    crawl_id = (body.get("crawl_id") or None) or None
    socials = body.get("socials") or None
    socials_json = None
    try:
        if socials is not None:
            socials_json = json.dumps(socials)
    except Exception:
        socials_json = None

    with get_session() as s:
        row = (
            s.query(Prospect)
            .filter(Prospect.user_id == user.id, Prospect.domain == domain)
            .one_or_none()
        )
        now = datetime.now(UTC)
        if row:
            # Update partial fields
            row.url = url if url is not None else row.url
            row.title = title if title is not None else row.title
            row.status = status_val or row.status
            row.tags = tags if tags is not None else row.tags
            row.notes = notes if notes is not None else row.notes
            row.crawl_id = crawl_id if crawl_id is not None else row.crawl_id
            if socials_json is not None:
                row.socials_json = socials_json
            row.updated_at = now
        else:
            row = Prospect(
                id=str(uuid.uuid4()),
                user_id=user.id,
                crawl_id=crawl_id,
                domain=domain,
                url=url,
                title=title,
                status=status_val or "shortlisted",
                tags=tags,
                notes=notes,
                socials_json=socials_json,
                created_at=now,
                updated_at=now,
            )
            s.add(row)
        # metrics
        try:
            prospects_upsert.inc()
        except Exception:
            pass
        s.flush()
        return JSONResponse(
            content={
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
        )


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


@router.post("/api/prospects/{prospect_id}/contacts")
async def create_contact(request: Request, prospect_id: str):
    user = await require_auth(request)
    body = await request.json()
    email = (body.get("email") or "").strip().lower()
    if not email or "@" not in email or any(c.isspace() for c in email):
        raise HTTPException(status_code=400, detail="Invalid email")
    source_url = (body.get("source_url") or "").strip() or None
    social_url = (body.get("social_url") or "").strip() or None
    tags = (body.get("tags") or None) or None
    role_title = (body.get("role_title") or None) or None

    with get_session() as s:
        # ownership check of prospect
        p = (
            s.query(Prospect)
            .filter(Prospect.id == prospect_id, Prospect.user_id == user.id)
            .one_or_none()
        )
        if not p:
            raise HTTPException(status_code=404, detail="Prospect not found")

        c = ProspectContact(
            id=str(uuid.uuid4()),
            prospect_id=prospect_id,
            email=email,
            source_url=source_url,
            social_url=social_url,
            tags=tags,
            role_title=role_title,
            created_at=datetime.now(UTC),
        )
        try:
            s.add(c)
            s.flush()
        except IntegrityError:
            raise HTTPException(status_code=409, detail="Contact already exists")
        try:
            contacts_create.inc()
        except Exception:
            pass
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
