"""Tests for the AAX queue durability and recovery."""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import types
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

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

from webapp.models import Base, Crawl  # noqa: E402
from webapp.services import scoring as scoring_svc  # noqa: E402


@pytest.fixture
def db_session(monkeypatch):
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

    monkeypatch.setattr(scoring_svc, "get_session", get_session)
    # Also patch the webapp.db module's get_session for functions that
    # import it directly rather than through the scoring module.
    import webapp.db

    monkeypatch.setattr(webapp.db, "get_session", get_session)
    return get_session


def _make_crawl(
    get_session,
    *,
    status: str = "succeeded",
    aax_status: str = "pending",
    aax_started_at: datetime | None = None,
) -> Crawl:
    """Create and flush a Crawl row for testing."""
    with get_session() as s:
        crawl = Crawl(
            url="https://example.com",
            domain="example.com",
            path="/",
            query="",
            canonical_url="https://example.com",
            visibility="public",
            status=status,
            aax_status=aax_status,
            aax_started_at=aax_started_at,
        )
        s.add(crawl)
        s.flush()
        return crawl


class TestEnqueueAax:
    """Test the enqueue_aax function."""

    def test_enqueue_marks_succeeded_crawl_as_pending(self, db_session):
        """A succeeded crawl gets aax_status='pending'."""
        crawl = _make_crawl(db_session, aax_status="failed")

        assert scoring_svc.enqueue_aax(crawl.id) is True
        with db_session() as s:
            row = s.get(Crawl, crawl.id)
            assert row.aax_status == "pending"
            assert row.aax_started_at is None

    def test_enqueue_ignores_non_succeeded_crawl(self, db_session):
        """A failed crawl cannot be enqueued."""
        crawl = _make_crawl(db_session, status="failed")

        assert scoring_svc.enqueue_aax(crawl.id) is False

    def test_enqueue_ignores_already_running(self, db_session):
        """A crawl already running AAX cannot be re-enqueued."""
        crawl = _make_crawl(db_session, aax_status="running")

        assert scoring_svc.enqueue_aax(crawl.id) is False


class TestClaimPendingAax:
    """Test the _claim_pending_aax atomic claim."""

    def test_claim_transitions_pending_to_running(self, db_session):
        """Claiming sets aax_status='running' and aax_started_at."""
        crawl = _make_crawl(db_session)

        assert scoring_svc._claim_pending_aax(crawl.id) is True
        with db_session() as s:
            row = s.get(Crawl, crawl.id)
            assert row.aax_status == "running"
            assert row.aax_started_at is not None

    def test_claim_fails_when_already_running(self, db_session):
        """Cannot claim a job already running."""
        crawl = _make_crawl(db_session, aax_status="running")

        assert scoring_svc._claim_pending_aax(crawl.id) is False


class TestResetStaleAax:
    """Test stale AAX job recovery."""

    def test_reset_stale_marks_old_running_as_pending(self, db_session):
        """Jobs running longer than AAX_STALE_MINUTES are reset."""
        old_time = datetime.now(UTC) - timedelta(minutes=60)
        crawl = _make_crawl(db_session, aax_status="running", aax_started_at=old_time)

        assert scoring_svc._reset_stale_aax() == 1
        with db_session() as s:
            row = s.get(Crawl, crawl.id)
            assert row.aax_status == "pending"
            assert row.aax_started_at is None

    def test_reset_stale_ignores_recent_running(self, db_session):
        """Recently started jobs are not reset."""
        recent_time = datetime.now(UTC) - timedelta(minutes=5)
        crawl = _make_crawl(
            db_session, aax_status="running", aax_started_at=recent_time
        )

        assert scoring_svc._reset_stale_aax() == 0
        with db_session() as s:
            row = s.get(Crawl, crawl.id)
            assert row.aax_status == "running"


class TestFetchPendingAaxIds:
    """Test pending job discovery."""

    def test_fetch_returns_succeeded_pending_only(self, db_session):
        """Only succeeded+pending crawls are returned."""
        c1 = _make_crawl(db_session, aax_status="pending")
        c2 = _make_crawl(db_session, status="failed", aax_status="pending")
        c3 = _make_crawl(db_session, aax_status="completed")

        ids = scoring_svc._fetch_pending_aax_ids()
        assert c1.id in ids
        assert c2.id not in ids
        assert c3.id not in ids


class TestMarkAaxTerminal:
    """Test terminal state marking."""

    def test_mark_completed(self, db_session):
        """Completed status is persisted."""
        crawl = _make_crawl(db_session, aax_status="running")

        scoring_svc._mark_aax_terminal(crawl.id, "completed", None)
        with db_session() as s:
            row = s.get(Crawl, crawl.id)
            assert row.aax_status == "completed"

    def test_mark_failed_with_error(self, db_session):
        """Failed status stores error message."""
        crawl = _make_crawl(db_session, aax_status="running")

        scoring_svc._mark_aax_terminal(crawl.id, "failed", "llm_timeout")
        with db_session() as s:
            row = s.get(Crawl, crawl.id)
            assert row.aax_status == "failed"
            assert "aax_failed" in (row.error or "")


class TestAaxWorker:
    """Test the background worker loop."""

    @pytest.mark.asyncio
    async def test_worker_processes_pending_job(self, db_session):
        """Worker picks up a pending job and completes it."""
        crawl = _make_crawl(db_session)

        stop_event = asyncio.Event()

        # Mock the actual AAX analysis to avoid LLM calls. The real
        # run_aax_for_crawl calls _mark_aax_terminal internally, so the
        # mock must do the same for the worker to observe completion.
        async def fake_run(crawl_id):
            scoring_svc._mark_aax_terminal(crawl_id, "completed", None)
            return {"composite": 50.0}

        with patch.object(scoring_svc, "run_aax_for_crawl", side_effect=fake_run):

            async def stop_after_delay():
                await asyncio.sleep(0.5)
                stop_event.set()

            await asyncio.gather(
                scoring_svc.aax_worker(stop_event),
                stop_after_delay(),
            )

        with db_session() as s:
            row = s.get(Crawl, crawl.id)
            assert row.aax_status == "completed"

    @pytest.mark.asyncio
    async def test_worker_recovers_stale_on_startup(self, db_session):
        """Worker resets stale running jobs on startup."""
        old_time = datetime.now(UTC) - timedelta(minutes=60)
        crawl = _make_crawl(db_session, aax_status="running", aax_started_at=old_time)

        stop_event = asyncio.Event()

        async def fake_run(crawl_id):
            scoring_svc._mark_aax_terminal(crawl_id, "completed", None)
            return {"composite": 50.0}

        with patch.object(scoring_svc, "run_aax_for_crawl", side_effect=fake_run):

            async def stop_after_delay():
                await asyncio.sleep(0.5)
                stop_event.set()

            await asyncio.gather(
                scoring_svc.aax_worker(stop_event),
                stop_after_delay(),
            )

        with db_session() as s:
            row = s.get(Crawl, crawl.id)
            assert row.aax_status == "completed"
