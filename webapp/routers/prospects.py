import csv
import json
import uuid
from datetime import datetime, timezone
from io import StringIO
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import and_, func, or_
from sqlalchemy.exc import IntegrityError

from webapp.db import get_session
from webapp.models import Product, Prospect, ProspectContact
from webapp.utils.auth import require_auth
from webapp.utils.metrics import (
    contacts_create,
    products_create,
    products_update,
    prospects_patch,
    prospects_upsert,
)

router = APIRouter()


def _parse_cursor(cur: Optional[str]) -> Optional[Tuple[datetime, str]]:
    if not cur:
        return None
    try:
        ts_str, pid = cur.split("|", 1)
        ts = datetime.fromisoformat(ts_str)
        if not ts.tzinfo:
            ts = ts.replace(tzinfo=timezone.utc)
        return (ts, pid)
    except Exception:
        return None


def _make_cursor(dt: Optional[datetime], pid: Optional[str]) -> Optional[str]:
    if not dt or not pid:
        return None
    try:
        return f"{dt.isoformat()}|{pid}"
    except Exception:
        return None


@router.get("/api/prospects")
async def list_prospects(
    request: Request,
    status: Optional[str] = None,
    q: Optional[str] = None,
    tag: Optional[str] = None,
    limit: int = 25,
    cursor: Optional[str] = None,
):
    user = await require_auth(request)
    limit = max(1, min(100, limit))
    cur = _parse_cursor(cursor)

    items: List[Dict[str, Any]] = []
    next_cursor: Optional[str] = None

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
            qry = qry.filter(func.lower(func.coalesce(Prospect.tags, "")).like(like_tag))

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
                    "created_at": (
                        r.created_at or datetime.now(timezone.utc)
                    ).isoformat(),
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
        now = datetime.now(timezone.utc)
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
            row.updated_at = datetime.now(timezone.utc)
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
            created_at=datetime.now(timezone.utc),
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
            "created_at": (c.created_at or datetime.now(timezone.utc)).isoformat(),
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
    w.writerow(["email", "source_url", "social_url", "tags", "role_title", "created_at"])
    for r in rows:
        w.writerow(
            [
                r.email or "",
                r.source_url or "",
                r.social_url or "",
                r.tags or "",
                r.role_title or "",
                (r.created_at or datetime.now(timezone.utc)).isoformat(),
            ]
        )
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="contacts-{prospect_id}.csv"'
        },
    )


# Products minimal CRUD (owner-scoped)
@router.get("/api/products")
async def list_products(request: Request):
    user = await require_auth(request)
    with get_session() as s:
        rows = (
            s.query(Product)
            .filter(Product.user_id == user.id)
            .order_by(Product.created_at.desc())
            .all()
        )
    items = []
    for r in rows:
        items.append(
            {
                "id": r.id,
                "name": r.name,
                "website": r.website,
                "description": r.description,
                "icp": r.icp,
                "pricing": r.pricing,
                "tone": r.tone,
                "contact_info": r.contact_info,
                "defaults": json.loads(r.defaults_json) if r.defaults_json else {},
                "created_at": (r.created_at or datetime.now(timezone.utc)).isoformat(),
            }
        )
    return {"items": items}


@router.post("/api/products")
async def create_product(request: Request):
    user = await require_auth(request)
    body = await request.json()
    name = (body.get("name") or "").strip()
    description = (body.get("description") or "").strip()
    if not name or not description:
        raise HTTPException(status_code=400, detail="Missing required fields")
    website = (body.get("website") or "").strip() or None
    icp = (body.get("icp") or "").strip() or None
    pricing = body.get("pricing")
    tone = (body.get("tone") or "").strip() or None
    contact_info = (body.get("contact_info") or "").strip() or None
    defaults = body.get("defaults") or {}
    defaults_json = None
    try:
        defaults_json = json.dumps(defaults or {})
    except Exception:
        defaults_json = None

    with get_session() as s:
        r = Product(
            id=str(uuid.uuid4()),
            user_id=user.id,
            name=name,
            website=website,
            description=description,
            icp=icp,
            pricing=(
                json.dumps(pricing)
                if isinstance(pricing, (dict, list))
                else (pricing or None)
            ),
            tone=tone,
            contact_info=contact_info,
            defaults_json=defaults_json,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        s.add(r)
        s.flush()
        try:
            products_create.inc()
        except Exception:
            pass
        return {
            "id": r.id,
            "name": r.name,
            "website": r.website,
            "description": r.description,
            "icp": r.icp,
            "pricing": r.pricing,
            "tone": r.tone,
            "contact_info": r.contact_info,
            "defaults": json.loads(r.defaults_json) if r.defaults_json else {},
        }


@router.put("/api/products/{product_id}")
async def update_product(request: Request, product_id: str):
    user = await require_auth(request)
    body = await request.json()
    with get_session() as s:
        r = (
            s.query(Product)
            .filter(Product.id == product_id, Product.user_id == user.id)
            .one_or_none()
        )
        if not r:
            raise HTTPException(status_code=404, detail="Not found")
        changed = False
        for key in (
            "name",
            "website",
            "description",
            "icp",
            "pricing",
            "tone",
            "contact_info",
        ):
            if key in body:
                val = body.get(key)
                if key == "pricing" and isinstance(val, (dict, list)):
                    val = json.dumps(val)
                setattr(r, key, val)
                changed = True
        if "defaults" in body:
            try:
                r.defaults_json = json.dumps(body.get("defaults") or {})
                changed = True
            except Exception:
                pass
        if changed:
            r.updated_at = datetime.now(timezone.utc)
        s.flush()
        try:
            products_update.inc()
        except Exception:
            pass
        return {
            "id": r.id,
            "name": r.name,
            "website": r.website,
            "description": r.description,
            "icp": r.icp,
            "pricing": r.pricing,
            "tone": r.tone,
            "contact_info": r.contact_info,
            "defaults": json.loads(r.defaults_json) if r.defaults_json else {},
        }
