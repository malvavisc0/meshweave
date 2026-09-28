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
    domain="q.com",
) -> str:
    cid = str(uuid.uuid4())
    ts = datetime.now(UTC) - timedelta(minutes=minutes_ago)
    with factory() as s:
        s.add(
            Crawl(
                id=cid,
                url=f"https://{domain}/",
                domain=domain,
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


class TestAdoptStrandedFormJobs:
    """Form-path rows stranded by a restart join the durable queue.

    A retired row also carries queue_status=None + status=pending (the
    retire path clears its queue job), so the sweep must key off
    is_latest or it resurrects retired revisions and re-crawls them.
    """

    def test_adopts_stranded_pending_form_row(self, sessions, monkeypatch):
        _, factory = sessions
        monkeypatch.setattr(cq, "QUEUE_STALE_MINUTES", 0)
        cid = _queued_crawl(factory, queue_status=None, minutes_ago=60)
        assert cq.adopt_stranded_form_jobs() == 1
        with factory() as s:
            assert s.get(Crawl, cid).queue_status == "pending"

    def test_never_adopts_a_retired_row(self, sessions, monkeypatch):
        _, factory = sessions
        monkeypatch.setattr(cq, "QUEUE_STALE_MINUTES", 0)
        cid = _queued_crawl(factory, queue_status=None, minutes_ago=60)
        with factory() as s:
            row = s.get(Crawl, cid)
            row.is_latest = False
            row.key = None
            s.commit()
        assert cq.adopt_stranded_form_jobs() == 0
        with factory() as s:
            assert s.get(Crawl, cid).queue_status is None


def _committing(factory):
    """Committing session factory with get_session semantics."""

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

    return get_session


def _load_jobs():
    """Import webapp.routers.jobs without the routers package __init__.

    webapp.infra is stubbed: jinja2 is not installed in the root test
    environment and jobs.py only touches templates at render time.
    """
    import importlib.util
    from pathlib import Path

    from fastapi_stub import install_fastapi_stub

    install_fastapi_stub()
    if "webapp.infra" not in sys.modules:
        infra = types.ModuleType("webapp.infra")
        infra.templates = types.SimpleNamespace()
        sys.modules["webapp.infra"] = infra
    root = Path(__file__).resolve().parent.parent / "webapp" / "routers"
    if "webapp.routers" not in sys.modules:
        routers_pkg = types.ModuleType("webapp.routers")
        routers_pkg.__path__ = [str(root)]
        sys.modules["webapp.routers"] = routers_pkg
    existing = sys.modules.get("webapp.routers.jobs")
    if existing is not None:
        return existing
    spec = importlib.util.spec_from_file_location(
        "webapp.routers.jobs", root / "jobs.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["webapp.routers.jobs"] = module
    spec.loader.exec_module(module)
    return module


class TestFormRetryResetsQueueJob:
    """A bulk-origin row retried from the form rejoins the durable queue."""

    def test_reset_job_row_puts_row_back_in_adoptable_pool(self, sessions, monkeypatch):
        jobs = _load_jobs()
        _, factory = sessions
        monkeypatch.setattr(jobs, "get_session", _committing(factory))
        monkeypatch.setattr(cq, "QUEUE_STALE_MINUTES", 0)
        cid = _queued_crawl(
            factory, status="failed", queue_status="done", minutes_ago=60
        )
        assert jobs._reset_job_row(cid, datetime.now(UTC), require_idle=False) is True
        with factory() as s:
            row = s.get(Crawl, cid)
            assert row.status == "pending"
            assert row.queue_status is None
            assert row.queue_started_at is None
        assert cq.adopt_stranded_form_jobs() == 1
        with factory() as s:
            assert s.get(Crawl, cid).queue_status == "pending"


class TestBeginCrawlTransition:
    """Only pending/failed rows may enter running (no orphan re-crawl)."""

    def test_page_begin_crawl_refuses_succeeded(self, sessions, monkeypatch):
        from webapp.services import crawling

        _, factory = sessions
        monkeypatch.setattr(crawling, "get_session", _committing(factory))
        cid = _queued_crawl(factory, status="succeeded", queue_status=None)
        assert crawling._begin_crawl(cid, None, datetime.now(UTC)) is None
        with factory() as s:
            assert s.get(Crawl, cid).status == "succeeded"

    def test_page_begin_crawl_takes_pending_and_failed(self, sessions, monkeypatch):
        from webapp.services import crawling

        _, factory = sessions
        monkeypatch.setattr(crawling, "get_session", _committing(factory))
        for start in ("pending", "failed"):
            cid = _queued_crawl(
                factory, status=start, queue_status=None, domain=f"{start}.com"
            )
            url = crawling._begin_crawl(cid, None, datetime.now(UTC))
            assert url == f"https://{start}.com/"
            with factory() as s:
                assert s.get(Crawl, cid).status == "running"

    def test_site_begin_crawl_refuses_succeeded(self, sessions, monkeypatch):
        from webapp.services import site_crawling

        _, factory = sessions
        monkeypatch.setattr(site_crawling, "get_session", _committing(factory))
        cid = _queued_crawl(
            factory, status="succeeded", queue_status=None, params={"max_pages": 3}
        )
        start_url, row = site_crawling._begin_crawl_transition(cid, datetime.now(UTC))
        assert start_url is None
        assert row is None
        with factory() as s:
            assert s.get(Crawl, cid).status == "succeeded"

    def test_site_begin_crawl_takes_pending(self, sessions, monkeypatch):
        from webapp.services import site_crawling

        _, factory = sessions
        monkeypatch.setattr(site_crawling, "get_session", _committing(factory))
        cid = _queued_crawl(
            factory, status="pending", queue_status=None, params={"max_pages": 3}
        )
        start_url, row = site_crawling._begin_crawl_transition(cid, datetime.now(UTC))
        assert start_url == "https://q.com/"
        assert row is not None
        with factory() as s:
            assert s.get(Crawl, cid).status == "running"
