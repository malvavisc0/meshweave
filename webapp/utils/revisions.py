"""Revision-preserving crawl replacement logic.

Used by the retry path so a succeeded row is retired (``is_latest=False``,
public key carried over) and a fresh ``pending`` row is inserted instead of
resetting in place — the revision-safety invariant the diff feature depends
on. Mirrors ``_replace_private_crawl`` / ``_replace_site_crawl`` in
``webapp/routers/submissions.py``, including the per-domain history cap.
"""

from __future__ import annotations

import os
from datetime import datetime

from sqlalchemy.orm import Session

from webapp.models import Crawl


def cleanup_old_crawls(session: Session, domain: str, visibility: str) -> None:
    """Delete oldest non-latest crawls beyond MAX_HISTORY_PER_DOMAIN limit."""
    max_history = int(os.getenv("MAX_HISTORY_PER_DOMAIN", "20"))
    old_rows = (
        session.query(Crawl)
        .filter(
            Crawl.domain == domain,
            Crawl.visibility == visibility,
            Crawl.is_latest == False,  # noqa: E712
        )
        .order_by(Crawl.created_at.desc(), Crawl.id.desc())
        .offset(max_history)
        .all()
    )
    for old in old_rows:
        session.delete(old)


def replace_succeeded_crawl(s: Session, row_id: str, now: datetime) -> str | None:
    """Retire a succeeded crawl and insert a fresh pending replacement row.

    The succeeded row is re-fetched in the write session, marked
    ``is_latest=False`` (its payload and snapshot stay intact), and a new
    ``pending`` row carrying the same address is inserted. The old public
    key, if any, carries over to the new row so public short-key URLs keep
    resolving to the latest revision.

    Args:
        s: The write session; the caller's session scope commits (see
            ``webapp.db.get_session``), so the retire+insert lands atomically
            with any other work in that scope.
        row_id: UUID of the succeeded crawl to replace.
        now: Timestamp stamped on the new row.

    Returns:
        str | None: The new crawl id, or None when the row vanished or is
        no longer succeeded.
    """
    db_row = s.get(Crawl, row_id)
    if db_row is None or db_row.status != "succeeded":
        return None
    old_key = db_row.key
    db_row.is_latest = False
    db_row.key = None
    new_row = Crawl(
        url=db_row.url,
        domain=db_row.domain,
        path=db_row.path,
        query=db_row.query,
        canonical_url=db_row.canonical_url,
        key=old_key,
        visibility=db_row.visibility,
        status="pending",
        payload_json=None,
        error=None,
        user_id=db_row.user_id,
        is_latest=True,
        created_at=now,
        updated_at=now,
    )
    # Assign only when set: an explicit None would serialize as JSON 'null'
    # instead of SQL NULL, splitting the page-scope series in two.
    if db_row.crawl_params:
        new_row.crawl_params = db_row.crawl_params
    s.add(new_row)
    s.flush()
    new_id = new_row.id
    try:
        cleanup_old_crawls(s, db_row.domain, db_row.visibility)
    except Exception:
        pass
    return new_id
