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
from webapp.utils.diff import scope_filter


def cleanup_old_crawls(
    session: Session, domain: str, visibility: str, user_id: str | None = None
) -> None:
    """Delete oldest non-latest crawls beyond MAX_HISTORY_PER_DOMAIN limit.

    Private history is pruned per owner: without ``user_id`` scoping, one
    user's pruning would delete another user's private revisions for the
    same domain. Public rows are shared and prune globally (``user_id``
    is None).
    """
    max_history = int(os.getenv("MAX_HISTORY_PER_DOMAIN", "20"))
    filters = [
        Crawl.domain == domain,
        Crawl.visibility == visibility,
        Crawl.is_latest == False,  # noqa: E712
    ]
    if visibility == "private":
        filters.append(Crawl.user_id == user_id)
    old_rows = (
        session.query(Crawl)
        .filter(*filters)
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
    ``pending`` row carrying the same address is inserted. The public
    key, if any, carries over — from the series' current latest when the
    retried row is a non-latest (which never holds the key itself) — so
    public short-key URLs keep resolving to the latest revision.

    Retiring only the retried row is not enough: when a non-latest
    revision is retried, the series' actual latest row must be retired
    too, or the insert mints a second ``is_latest=True`` row and bricks
    the ``one_or_none()`` lookups.

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
    # Land this row's own retire before the shared-series retire query
    # and the replacement insert below.
    s.flush()
    latest_key = _retire_series_latest(s, db_row)
    new_row = Crawl(
        url=db_row.url,
        domain=db_row.domain,
        path=db_row.path,
        query=db_row.query,
        canonical_url=db_row.canonical_url,
        key=latest_key or old_key,
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
    if db_row.crawl_params is not None:
        new_row.crawl_params = db_row.crawl_params
    s.add(new_row)
    s.flush()
    new_id = new_row.id
    try:
        cleanup_old_crawls(s, db_row.domain, db_row.visibility, db_row.user_id)
    except Exception:
        pass
    return new_id


def retire_series_latest(
    s: Session,
    replaced: Crawl,
    *,
    visibility: str | None = None,
) -> str | None:
    """Retire any other ``is_latest`` row in the series ``replaced`` joins.

    The series key is (visibility, user_id for private, domain, path,
    query, scope). A retry of an old revision leaves the series' real
    latest in place; without retiring it the insert below creates a
    double-latest series. ``visibility`` overrides the row's own value
    for the visibility-toggle path, where the row is moving into the
    target-visibility series and that series' current latest must step
    aside. Returns the short key cleared from the retired latest so a
    replacement row can inherit it (a non-latest row never holds the key
    itself).
    """
    vis = visibility if visibility is not None else replaced.visibility
    filters = [
        Crawl.is_latest == True,  # noqa: E712
        Crawl.id != replaced.id,
        Crawl.domain == replaced.domain,
        Crawl.path == replaced.path,
        Crawl.query == replaced.query,
        Crawl.visibility == vis,
    ]
    if vis == "private":
        filters.append(Crawl.user_id == replaced.user_id)
    filters.append(scope_filter(isinstance(replaced.crawl_params, dict)))
    carried_key = None
    for other in s.query(Crawl).filter(*filters).all():
        if carried_key is None:
            carried_key = other.key
        other.is_latest = False
        other.key = None
    # Land the retires before the caller joins the series (a visibility
    # flip or a replacement insert): flushed together, the joining
    # statement can reach uq_crawls_series_latest ahead of the retire
    # and trip the unique index on a transient double-latest.
    s.flush()
    return carried_key


_retire_series_latest = retire_series_latest
