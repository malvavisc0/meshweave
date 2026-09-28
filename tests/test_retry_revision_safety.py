"""Tests that retrying a succeeded crawl preserves its revision history.

Retrying a succeeded row must retire it (``is_latest=False``, key cleared,
payload/snapshot intact) and insert a fresh pending row — never reset in
place. Targets ``webapp/utils/revisions.py`` directly so no FastAPI
imports are needed; the router's status branch is a thin wrapper over
``replace_succeeded_crawl``.
"""

from __future__ import annotations

import os
import sys
import tempfile
import types
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

os.environ.setdefault("SQLITE_PATH", os.path.join(tempfile.mkdtemp(), "retry.db"))

if "prometheus_client" not in sys.modules:
    _fake_prom = types.ModuleType("prometheus_client")

    class _FakeMetric:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def labels(self, *args: object, **kwargs: object) -> _FakeMetric:
            return self

        def inc(self, *args: object, **kwargs: object) -> None:
            pass

        def observe(self, *args: object, **kwargs: object) -> None:
            pass

        def set(self, *args: object, **kwargs: object) -> None:
            pass

    _fake_prom.Counter = _FakeMetric
    _fake_prom.Gauge = _FakeMetric
    _fake_prom.Histogram = _FakeMetric
    _fake_prom.CONTENT_TYPE_LATEST = "text/plain"
    _fake_prom.generate_latest = lambda: b""
    sys.modules["prometheus_client"] = _fake_prom

from meshweave.scoring.composite import SCORING_VERSION  # noqa: E402
from webapp.models import Base, Crawl, ScoreSnapshot, User  # noqa: E402
from webapp.utils.revisions import (  # noqa: E402
    replace_succeeded_crawl,
    retire_series_latest,
)
from webapp.utils.times import ensure_utc  # noqa: E402

_UNSET = object()


@pytest.fixture
def sessions():
    """Committing session factory for the in-memory DB (get_session semantics)."""
    engine = create_engine(
        "sqlite://",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        future=True,
        expire_on_commit=False,
    )

    @contextmanager
    def get_session():
        s = factory()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise

    return get_session


def _make_user(s, user_id: str) -> None:
    s.add(User(id=user_id, email=f"{user_id}@example.com", provider_id=user_id))
    s.flush()


def _make_succeeded_crawl(
    s,
    *,
    user_id: str,
    visibility: str = "private",
    key: str | None = None,
    crawl_params: dict | None | object = _UNSET,
    domain: str = "example.com",
    path: str = "/",
    created_at: datetime | None = None,
    is_latest: bool = True,
) -> Crawl:
    url = f"https://{domain}{path}"
    ts = created_at or datetime(2026, 8, 1, tzinfo=UTC)
    row = Crawl(
        id=str(uuid.uuid4()),
        url=url,
        domain=domain,
        path=path,
        query="",
        canonical_url=url,
        key=key,
        visibility=visibility,
        status="succeeded",
        payload_json={"markdown": "old content"},
        user_id=user_id,
        scoring_version=SCORING_VERSION,
        listed=True,
        is_latest=is_latest,
        created_at=ts,
        updated_at=ts,
    )
    # Only set when provided: an explicit None serializes as the JSON
    # 'null' text while an omitted column stores SQL NULL — the two
    # page-scope shapes the shared scope predicate must both match.
    if crawl_params is not _UNSET:
        row.crawl_params = crawl_params
    s.add(row)
    s.add(
        ScoreSnapshot(
            id=str(uuid.uuid4()),
            crawl_id=row.id,
            user_id=user_id,
            domain=domain,
            score_json={
                "aeo": {
                    "composite": 50.0,
                    "rating": "Partially extractable",
                    "factors": {},
                },
                "geo": {"composite": 44.0, "rating": "Fragmented", "factors": {}},
                "recommendations": [],
            },
            scoring_version=SCORING_VERSION,
            created_at=ts,
            updated_at=ts,
        )
    )
    s.flush()
    return row


def _make_failed_crawl(s, *, user_id: str, domain: str = "failed.com") -> Crawl:
    url = f"https://{domain}/"
    row = Crawl(
        id=str(uuid.uuid4()),
        url=url,
        domain=domain,
        path="/",
        query="",
        canonical_url=url,
        visibility="private",
        status="failed",
        error="boom",
        user_id=user_id,
        scoring_version=SCORING_VERSION,
        listed=True,
        is_latest=True,
        created_at=datetime(2026, 8, 1, tzinfo=UTC),
        updated_at=datetime(2026, 8, 1, tzinfo=UTC),
    )
    s.add(row)
    s.flush()
    return row


def _assert_retired_old_row(old: Crawl) -> None:
    assert old.status == "succeeded"
    assert old.is_latest is False
    assert old.key is None
    assert old.payload_json == {"markdown": "old content"}
    assert old.score_snapshot is not None
    assert old.score_snapshot.score_json["aeo"]["composite"] == 50.0


def _assert_new_pending_row(new: Crawl, old: Crawl) -> None:
    assert new.status == "pending"
    assert new.is_latest is True
    assert new.payload_json is None
    assert new.user_id == "u1"
    assert new.domain == old.domain
    assert new.path == old.path
    assert new.query == old.query
    assert new.crawl_params == old.crawl_params


class TestReplaceSucceededCrawl:
    def test_retires_and_inserts_new_pending_row(self, sessions):
        with sessions() as s:
            _make_user(s, "u1")
            row = _make_succeeded_crawl(s, user_id="u1")
            old_id = row.id

        now = datetime.now(UTC)
        with sessions() as s:
            new_id = replace_succeeded_crawl(s, old_id, now)

        assert new_id is not None
        with sessions() as s:
            old = s.get(Crawl, old_id)
            new = s.get(Crawl, new_id)
            assert old is not None and new is not None
            _assert_retired_old_row(old)
            _assert_new_pending_row(new, old)

            latest_rows = (
                s.query(Crawl)
                .filter(
                    Crawl.domain == old.domain,
                    Crawl.visibility == old.visibility,
                    Crawl.is_latest.is_(True),
                )
                .all()
            )
            assert [r.id for r in latest_rows] == [new_id]

    def test_detached_source_row_does_not_block_replacement(self, sessions):
        """The helper re-fetches by id; a detached row cannot corrupt state.

        Regression guard for the original bug: mutating a row loaded in a
        previous session silently dropped the retire, leaving two
        ``is_latest=True`` rows (or a unique-key violation for public rows).
        """
        with sessions() as s:
            _make_user(s, "u1")
            row = _make_succeeded_crawl(s, user_id="u1")
            old_id = row.id
        # `row` is now detached, mirroring require_ownership's session scope.

        now = datetime.now(UTC)
        with sessions() as s:
            new_id = replace_succeeded_crawl(s, old_id, now)

        assert new_id is not None
        assert new_id != old_id
        with sessions() as s:
            latest = (
                s.query(Crawl)
                .filter(
                    Crawl.domain == "example.com",
                    Crawl.visibility == "private",
                    Crawl.is_latest.is_(True),
                )
                .all()
            )
            assert [r.id for r in latest] == [new_id]

    def test_public_key_carries_over(self, sessions):
        with sessions() as s:
            _make_user(s, "u1")
            row = _make_succeeded_crawl(
                s, user_id="u1", visibility="public", key="pubkey1"
            )
            old_id = row.id

        now = datetime.now(UTC)
        with sessions() as s:
            new_id = replace_succeeded_crawl(s, old_id, now)

        with sessions() as s:
            old = s.get(Crawl, old_id)
            new = s.get(Crawl, new_id)
            assert old.key is None
            assert new.key == "pubkey1"

    def test_rollback_in_caller_scope_discards_replacement(self, sessions):
        """Regression: replace_succeeded_crawl must not commit internally.

        The caller's session scope owns the transaction; an exception after
        the replace (e.g. a later write failing) must roll back the retire +
        insert together, never leave a half-committed revision swap.
        """
        with sessions() as s:
            _make_user(s, "u1")
            row = _make_succeeded_crawl(s, user_id="u1")
            old_id = row.id

        now = datetime.now(UTC)
        with pytest.raises(RuntimeError):
            with sessions() as s:
                new_id = replace_succeeded_crawl(s, old_id, now)
                assert new_id is not None
                raise RuntimeError("simulated post-replace failure")

        with sessions() as s:
            old = s.get(Crawl, old_id)
            assert old.status == "succeeded"
            assert old.is_latest is True
            assert old.key is None  # unchanged: private row had no key
            new_rows = (
                s.query(Crawl)
                .filter(Crawl.domain == "example.com", Crawl.status == "pending")
                .all()
            )
            assert new_rows == []

    def test_missing_row_returns_none(self, sessions):
        now = datetime.now(UTC)
        with sessions() as s:
            assert replace_succeeded_crawl(s, "missing", now) is None

    def test_non_succeeded_row_returns_none(self, sessions):
        with sessions() as s:
            _make_user(s, "u1")
            row = _make_failed_crawl(s, user_id="u1")
            failed_id = row.id

        now = datetime.now(UTC)
        with sessions() as s:
            assert replace_succeeded_crawl(s, failed_id, now) is None
        with sessions() as s:
            same = s.get(Crawl, failed_id)
            assert same.status == "failed"
            assert same.is_latest is True

    def test_history_cap_applies_to_retired_rows(self, sessions, monkeypatch):
        monkeypatch.setenv("MAX_HISTORY_PER_DOMAIN", "2")
        with sessions() as s:
            _make_user(s, "u1")
            # Three already-retired revisions, oldest first.
            for day in (1, 2, 3):
                _make_succeeded_crawl(
                    s,
                    user_id="u1",
                    domain="example.com",
                    created_at=datetime(2026, 8, day, tzinfo=UTC),
                    is_latest=False,
                )
            # The current latest revision to retry.
            latest = _make_succeeded_crawl(
                s,
                user_id="u1",
                domain="example.com",
                created_at=datetime(2026, 8, 10, tzinfo=UTC),
            )

        now = datetime.now(UTC)
        with sessions() as s:
            new_id = replace_succeeded_crawl(s, latest.id, now)
        assert new_id is not None

        with sessions() as s:
            retired = (
                s.query(Crawl)
                .filter(
                    Crawl.domain == "example.com",
                    Crawl.visibility == "private",
                    Crawl.is_latest.is_(False),
                )
                .all()
            )
            # Cap is 2 non-latest rows: the two oldest retired revisions
            # (Aug 1, Aug 2) were deleted; Aug 3 and the just-retired
            # latest (Aug 10) survive.
            assert len(retired) == 2
            surviving = sorted(ensure_utc(r.created_at) for r in retired)
            assert surviving == [
                datetime(2026, 8, 3, tzinfo=UTC),
                datetime(2026, 8, 10, tzinfo=UTC),
            ]
            current = (
                s.query(Crawl)
                .filter(
                    Crawl.domain == "example.com",
                    Crawl.visibility == "private",
                    Crawl.is_latest.is_(True),
                )
                .one()
            )
            assert current.id == new_id
            assert current.status == "pending"


class TestKeyInheritance:
    """The public short key must survive a retry of a non-latest revision."""

    def test_non_latest_retry_inherits_the_series_key(self, sessions):
        with sessions() as s:
            _make_user(s, "u1")
            old = _make_succeeded_crawl(
                s,
                user_id="u1",
                visibility="public",
                created_at=datetime(2026, 8, 1, tzinfo=UTC),
                is_latest=False,
            )
            _make_succeeded_crawl(
                s,
                user_id="u1",
                visibility="public",
                key="pubkey1",
                created_at=datetime(2026, 8, 2, tzinfo=UTC),
                is_latest=True,
            )
            old_id = old.id

        now = datetime.now(UTC)
        with sessions() as s:
            new_id = replace_succeeded_crawl(s, old_id, now)
        assert new_id is not None

        with sessions() as s:
            rows = (
                s.query(Crawl)
                .filter(Crawl.domain == "example.com", Crawl.visibility == "public")
                .all()
            )
            assert [r.id for r in rows if r.is_latest] == [new_id]
            keyed = [r for r in rows if r.key is not None]
            assert [r.key for r in keyed] == ["pubkey1"]
            assert keyed[0].id == new_id


class TestScopeIsolationOnRetire:
    """A site-scope retire or retry must never touch a page-scope latest."""

    @pytest.mark.parametrize("null_shape", ["json_null", "sql_null"])
    def test_site_scope_retry_spares_page_scope_latest(self, sessions, null_shape):
        page_params = None if null_shape == "json_null" else _UNSET
        with sessions() as s:
            _make_user(s, "u1")
            page_latest = _make_succeeded_crawl(
                s,
                user_id="u1",
                domain="scoped.com",
                crawl_params=page_params,
                created_at=datetime(2026, 8, 1, tzinfo=UTC),
                is_latest=True,
            )
            site_old = _make_succeeded_crawl(
                s,
                user_id="u1",
                domain="scoped.com",
                crawl_params={"max_pages": 5},
                created_at=datetime(2026, 8, 2, tzinfo=UTC),
                is_latest=False,
            )
            site_latest = _make_succeeded_crawl(
                s,
                user_id="u1",
                domain="scoped.com",
                crawl_params={"max_pages": 3},
                created_at=datetime(2026, 8, 3, tzinfo=UTC),
                is_latest=True,
            )
            page_id = page_latest.id
            site_old_id = site_old.id
            site_latest_id = site_latest.id

        now = datetime.now(UTC)
        with sessions() as s:
            new_id = replace_succeeded_crawl(s, site_old_id, now)
        assert new_id is not None

        with sessions() as s:
            assert s.get(Crawl, page_id).is_latest is True
            assert s.get(Crawl, site_latest_id).is_latest is False
            latests = (
                s.query(Crawl)
                .filter(Crawl.domain == "scoped.com", Crawl.is_latest.is_(True))
                .all()
            )
            assert sorted(r.id for r in latests) == sorted([new_id, page_id])
            new = s.get(Crawl, new_id)
            assert new.crawl_params is not None

    @pytest.mark.parametrize("null_shape", ["json_null", "sql_null"])
    def test_site_scope_retire_spares_page_scope_latest(self, sessions, null_shape):
        page_params = None if null_shape == "json_null" else _UNSET
        with sessions() as s:
            _make_user(s, "u1")
            page_latest = _make_succeeded_crawl(
                s,
                user_id="u1",
                domain="scoped.com",
                crawl_params=page_params,
                created_at=datetime(2026, 8, 1, tzinfo=UTC),
                is_latest=True,
            )
            mover = _make_succeeded_crawl(
                s,
                user_id="u1",
                domain="scoped.com",
                crawl_params={"max_pages": 5},
                created_at=datetime(2026, 8, 2, tzinfo=UTC),
                is_latest=False,
            )
            site_latest = _make_succeeded_crawl(
                s,
                user_id="u1",
                domain="scoped.com",
                crawl_params={"max_pages": 3},
                created_at=datetime(2026, 8, 3, tzinfo=UTC),
                is_latest=True,
            )
            page_id = page_latest.id
            mover_id = mover.id
            site_latest_id = site_latest.id

        with sessions() as s:
            mover_row = s.get(Crawl, mover_id)
            assert mover_row is not None
            retire_series_latest(s, mover_row)
            s.commit()

        with sessions() as s:
            assert s.get(Crawl, page_id).is_latest is True
            assert s.get(Crawl, site_latest_id).is_latest is False

    def test_page_scope_retire_spares_site_scope_latest(self, sessions):
        with sessions() as s:
            _make_user(s, "u1")
            page_old = _make_succeeded_crawl(
                s,
                user_id="u1",
                domain="scoped.com",
                crawl_params=None,
                created_at=datetime(2026, 8, 1, tzinfo=UTC),
                is_latest=False,
            )
            page_latest = _make_succeeded_crawl(
                s,
                user_id="u1",
                domain="scoped.com",
                created_at=datetime(2026, 8, 2, tzinfo=UTC),
                is_latest=True,
            )
            site_latest = _make_succeeded_crawl(
                s,
                user_id="u1",
                domain="scoped.com",
                crawl_params={"max_pages": 3},
                created_at=datetime(2026, 8, 3, tzinfo=UTC),
                is_latest=True,
            )
            page_old_id = page_old.id
            page_latest_id = page_latest.id
            site_latest_id = site_latest.id

        with sessions() as s:
            mover_row = s.get(Crawl, page_old_id)
            assert mover_row is not None
            retire_series_latest(s, mover_row)
            s.commit()

        with sessions() as s:
            assert s.get(Crawl, site_latest_id).is_latest is True
            assert s.get(Crawl, page_latest_id).is_latest is False
