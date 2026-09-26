"""Tests for the durable crawl queue's recovery invariants.

Covers the finding-7 fixes: a re-claimed job whose crawl was stranded in
``running`` gets reset and re-run; a crawl that already finished before
the worker died is never re-crawled; scope dispatch keys off
``crawl_params is not None``; a job timeout fails a hung crawl.
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import types
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest

os.environ.setdefault("SQLITE_PATH", os.path.join(tempfile.mkdtemp(), "queue.db"))

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
from sqlalchemy.pool import StaticPool  # noqa: E402

from webapp.models import Base, Crawl  # noqa: E402
from webapp.services import crawl_queue as cq  # noqa: E402


@pytest.fixture
def sessions():
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
        finally:
            s.close()

    import webapp.db as db_mod

    orig = db_mod.get_session
    cq.get_session = get_session
    yield engine, factory
    cq.get_session = orig


def _queued_crawl(
    factory,
    *,
    status="pending",
    queue_status="pending",
    params=None,
    minutes_ago=0,
) -> str:
    cid = str(uuid.uuid4())
    ts = datetime.now(UTC) - timedelta(minutes=minutes_ago)
    with factory() as s:
        s.add(
            Crawl(
                id=cid,
                url="https://q.com/",
                domain="q.com",
                path="/",
                query="",
                canonical_url="https://q.com/",
                visibility="private",
                status=status,
                user_id=None,
                crawl_params=params,
                payload_json={"ok": True} if status == "succeeded" else None,
                created_at=ts,
                updated_at=ts,
                queue_status=queue_status,
                queue_started_at=ts if queue_status == "running" else None,
            )
        )
        s.commit()
    return cid


class TestClaimRecovery:
    def test_claim_resets_stranded_running_crawl(self, sessions):
        """Re-claiming a pending job resets a stranded running crawl."""
        _, factory = sessions
        cid = _queued_crawl(factory, status="running", queue_status="pending")
        assert cq._claim_pending_job(cid) is True
        with factory() as s:
            assert s.get(Crawl, cid).status == "pending"

    def test_claim_leaves_terminal_crawl_alone(self, sessions):
        _, factory = sessions
        cid = _queued_crawl(factory, status="failed", queue_status="pending")
        assert cq._claim_pending_job(cid) is True
        with factory() as s:
            assert s.get(Crawl, cid).status == "failed"

    def test_stale_reclaim_resets_old_running_jobs(self, sessions):
        _, factory = sessions
        cid = _queued_crawl(
            factory, status="running", queue_status="running", minutes_ago=60
        )
        assert cq.reset_stale_jobs() == 1
        with factory() as s:
            assert s.get(Crawl, cid).queue_status == "pending"


class TestNoDoubleCrawl:
    @pytest.mark.asyncio
    async def test_finished_crawl_not_recrawled(self, sessions, monkeypatch):
        """Worker crash after crawl completion: job closes, no re-crawl."""
        _, factory = sessions
        cid = _queued_crawl(factory, status="succeeded", queue_status="pending")
        called = []
        monkeypatch.setattr(
            "webapp.services.crawling.run_crawl_task",
            lambda *a, **k: called.append(1),
        )
        await cq._process_job(cid)
        with factory() as s:
            row = s.get(Crawl, cid)
        assert row.queue_status == "done"
        assert row.status == "succeeded"
        assert called == []

    @pytest.mark.asyncio
    async def test_timeout_fails_hung_crawl(self, sessions, monkeypatch):
        _, factory = sessions
        cid = _queued_crawl(factory, status="pending", queue_status="pending")
        cq.QUEUE_JOB_TIMEOUT_SECONDS = 0

        async def _hang(crawl_id, *a, **k):
            # Simulate the real task: flips status to running, then hangs.
            with factory() as s:
                s.get(Crawl, crawl_id).status = "running"
                s.commit()
            await asyncio.sleep(10)

        async def _hang_site(crawl_id, *a, **k):
            await _hang(crawl_id)

        monkeypatch.setattr(
            "webapp.services.crawling.run_crawl_task",
            _hang,
        )
        monkeypatch.setattr(
            "webapp.services.site_crawling.run_site_crawl_task",
            _hang_site,
        )
        await cq._process_job(cid)
        with factory() as s:
            row = s.get(Crawl, cid)
        assert row.queue_status == "failed"
        assert row.status == "failed"


class TestScopeDispatch:
    def test_scope_keys_off_params_presence(self, sessions):
        """A site crawl with empty-but-present params is site, not page."""
        _, factory = sessions
        cid_page = _queued_crawl(factory, params=None)
        cid_site = _queued_crawl(factory, params={"max_pages": 5})
        assert cq._job_scope_owner_status(cid_page)[0] == "page"
        assert cq._job_scope_owner_status(cid_site)[0] == "site"
