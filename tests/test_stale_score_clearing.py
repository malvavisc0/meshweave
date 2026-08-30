"""Tests for clearing stale scores when a crawl retry fails."""

from __future__ import annotations

import os
import sys
import tempfile
import types
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime

import pytest

# webapp.db creates its sqlite parent directory at import time; point it
# at a temp dir before the import so it never touches /db.
os.environ.setdefault("SQLITE_PATH", os.path.join(tempfile.mkdtemp(), "test.db"))

# The webapp metrics module imports prometheus_client, which is a
# production (Docker image) dependency not installed in the test
# environment. Install a minimal fake before importing the services —
# the same approach conftest.py uses for playwright.
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

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

import webapp.services.crawling as crawling_svc  # noqa: E402
import webapp.services.site_crawling as site_svc  # noqa: E402
from webapp.models import Base, Crawl, ScoreSnapshot  # noqa: E402


@pytest.fixture
def sqlite_sessions(monkeypatch):
    """In-memory DB patched in place of the webapp session factory."""
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        future=True,
        expire_on_commit=False,
    )

    @contextmanager
    def get_session():
        s = session_factory()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    monkeypatch.setattr(crawling_svc, "get_session", get_session)
    monkeypatch.setattr(site_svc, "get_session", get_session)
    return get_session


def _make_scored_crawl(s) -> Crawl:
    """A previously-succeeded crawl with scores and a snapshot."""
    row = Crawl(
        id=str(uuid.uuid4()),
        url="https://example.com/",
        domain="example.com",
        path="/",
        query="",
        canonical_url="https://example.com/",
        visibility="public",
        status="succeeded",
        scoring_version="1.0",
        has_manual_input=False,
        listed=True,
        is_latest=True,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        aeo_score=75.0,
        geo_score=60.0,
        aeo_rating="Strong",
        geo_rating="Visible",
    )
    s.add(row)
    s.add(
        ScoreSnapshot(
            crawl_id=row.id,
            domain="example.com",
            aeo_score=75.0,
            geo_score=60.0,
            aeo_rating="Strong",
            geo_rating="Visible",
            score_json={"aeo": {"composite": 75.0}},
            scoring_version="1.0",
            has_manual_input=False,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
    )
    s.flush()
    return row


class TestPersistFailedClearsStaleScores:
    """A failed retry must not keep the previous run's report."""

    def test_persist_failed_clears_scores_and_snapshot(self, sqlite_sessions):
        with sqlite_sessions() as s:
            row = _make_scored_crawl(s)
            crawl_id = row.id

        assert crawling_svc._persist_failed(crawl_id, "boom")

        with sqlite_sessions() as s:
            row = s.get(Crawl, crawl_id)
            assert row.status == "failed"
            assert row.error == "boom"
            assert row.aeo_score is None
            assert row.geo_score is None
            assert row.aeo_rating is None
            assert row.geo_rating is None
            snap = (
                s.query(ScoreSnapshot)
                .filter(ScoreSnapshot.crawl_id == crawl_id)
                .one_or_none()
            )
            assert snap is None

    def test_persist_failed_on_never_scored_crawl(self, sqlite_sessions):
        with sqlite_sessions() as s:
            row = Crawl(
                id=str(uuid.uuid4()),
                url="https://example.com/",
                domain="example.com",
                path="/",
                query="",
                canonical_url="https://example.com/",
                visibility="public",
                status="running",
                scoring_version="1.0",
                has_manual_input=False,
                listed=True,
                is_latest=True,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
            s.add(row)
            crawl_id = row.id

        assert crawling_svc._persist_failed(crawl_id, "boom")
        with sqlite_sessions() as s:
            assert s.get(Crawl, crawl_id).status == "failed"

    def test_persist_failed_missing_row(self, sqlite_sessions):
        assert crawling_svc._persist_failed("missing", "boom") is False


class TestPersistTaskResultClearsStaleScores:
    """The site-crawl finish path shares the same staleness rule."""

    def test_failed_finish_clears_scores(self, sqlite_sessions):
        with sqlite_sessions() as s:
            row = _make_scored_crawl(s)
            crawl_id = row.id

        assert site_svc._persist_task_result(crawl_id, "failed", "boom", None)

        with sqlite_sessions() as s:
            row = s.get(Crawl, crawl_id)
            assert row.status == "failed"
            assert row.aeo_score is None
            assert row.geo_score is None
            assert (
                s.query(ScoreSnapshot)
                .filter(ScoreSnapshot.crawl_id == crawl_id)
                .one_or_none()
                is None
            )

    def test_cancelled_finish_clears_scores(self, sqlite_sessions):
        with sqlite_sessions() as s:
            row = _make_scored_crawl(s)
            crawl_id = row.id

        assert site_svc._persist_task_result(crawl_id, "cancelled", "stopped", None)
        with sqlite_sessions() as s:
            row = s.get(Crawl, crawl_id)
            assert row.status == "cancelled"
            assert row.aeo_score is None

    def test_succeeded_finish_keeps_scores(self, sqlite_sessions):
        with sqlite_sessions() as s:
            row = _make_scored_crawl(s)
            crawl_id = row.id

        assert site_svc._persist_task_result(crawl_id, "succeeded", None, {"x": 1})
        with sqlite_sessions() as s:
            row = s.get(Crawl, crawl_id)
            assert row.status == "succeeded"
            assert row.aeo_score == 75.0
            assert (
                s.query(ScoreSnapshot)
                .filter(ScoreSnapshot.crawl_id == crawl_id)
                .one_or_none()
                is not None
            )

    def test_missing_row(self, sqlite_sessions):
        # False means "row missing — stop post-write steps".
        assert site_svc._persist_task_result("missing", "failed", "x", None) is False
