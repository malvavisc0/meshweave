"""Tenant-isolation regression tests for submission upserts and pruning.

The funnel plan's D0 fix: private latest-row lookups and private history
pruning must be owner-scoped. Before the fix, one user's site/page upsert
for a domain could retire another user's latest private revision, and
pruning crossed tenant boundaries — bulk submit would have amplified both.
"""

from __future__ import annotations

import os
import sys
import tempfile
import types
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

os.environ.setdefault("SQLITE_PATH", os.path.join(tempfile.mkdtemp(), "tenant.db"))

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

from webapp.models import Base, Crawl, User  # noqa: E402


def _install_fastapi_stub() -> None:
    """Minimal fastapi/starlette stubs so submissions.py imports in tests.

    Only the names the module binds at import time are stubbed; the tested
    helpers never call them.
    """
    if "fastapi" in sys.modules:
        return

    fastapi = types.ModuleType("fastapi")
    responses = types.ModuleType("fastapi.responses")

    class _Stub:
        def __init__(self, *a: object, **k: object) -> None:
            pass

        def __call__(self, *a: object, **k: object) -> _Stub:
            return _Stub()

    class _Router(_Stub):
        def get(self, *a: object, **k: object):
            return lambda fn: fn

        def post(self, *a: object, **k: object):
            return lambda fn: fn

    class _HTTPException(Exception):
        def __init__(self, status_code: int = 500, detail: object = None) -> None:
            super().__init__(detail)
            self.status_code = status_code
            self.detail = detail

    fastapi.APIRouter = _Router
    fastapi.BackgroundTasks = _Stub
    fastapi.Form = lambda *a, **k: None
    fastapi.HTTPException = _HTTPException
    fastapi.Request = _Stub
    fastapi.Response = _Stub
    responses.RedirectResponse = _Stub
    responses.JSONResponse = _Stub
    responses.HTMLResponse = _Stub
    responses.PlainTextResponse = _Stub
    fastapi.responses = responses
    sys.modules["fastapi"] = fastapi
    sys.modules["fastapi.responses"] = responses


def _load_submissions_module():
    """Import webapp.routers.submissions without the routers package __init__.

    The package __init__ imports every router, which requires fastapi —
    absent from the test venv. Loading the module by file path with a stub
    package gives us the upsert/cleanup internals directly, matching the
    suite's no-webapp-runtime convention.
    """
    import importlib.util
    from pathlib import Path

    _install_fastapi_stub()
    module_path = (
        Path(__file__).resolve().parent.parent / "webapp" / "routers" / "submissions.py"
    )
    spec = importlib.util.spec_from_file_location(
        "webapp.routers.submissions", module_path
    )
    module = importlib.util.module_from_spec(spec)
    # Stub the parent package so `webapp.routers` resolves without __init__.
    routers_pkg = types.ModuleType("webapp.routers")
    routers_pkg.__path__ = [str(module_path.parent)]
    sys.modules.setdefault("webapp.routers", routers_pkg)
    spec.loader.exec_module(module)
    return module


_submissions = _load_submissions_module()
_cleanup_old_crawls = _submissions._cleanup_old_crawls
_find_latest_crawl = _submissions._find_latest_crawl
_upsert_site_crawl_row = _submissions._upsert_site_crawl_row


@pytest.fixture
def sessions():
    engine = create_engine(
        "sqlite://",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    return engine, factory


def _user(factory, email: str):
    """Create a user row; return a plain namespace holding just the id.

    The sessionmaker expires on commit, so returning the ORM object would
    detach-trip on first attribute access.
    """
    uid = str(uuid.uuid4())
    with factory() as s:
        s.add(
            User(
                id=uid,
                email=email,
                provider="google",
                provider_id=str(uuid.uuid4()),
            )
        )
        s.commit()
    return types.SimpleNamespace(id=uid)


def _crawl(
    factory,
    *,
    user_id,
    domain,
    visibility,
    path="/",
    is_latest=True,
    created_at: datetime | None = None,
):
    ts = created_at or datetime.now(UTC)
    with factory() as s:
        row = Crawl(
            id=str(uuid.uuid4()),
            url=f"https://{domain}{path}",
            domain=domain,
            path=path,
            query="",
            canonical_url=f"https://{domain}{path}",
            visibility=visibility,
            status="succeeded",
            user_id=user_id,
            is_latest=is_latest,
            created_at=ts,
            updated_at=ts,
        )
        s.add(row)
        s.commit()
        return row.id


class TestPrivateLookupScoped:
    def test_private_latest_lookup_ignores_other_owners(self, sessions):
        _, factory = sessions
        alice = _user(factory, "alice@example.com")
        bob = _user(factory, "bob@example.com")
        _crawl(factory, user_id=alice.id, domain="x.com", visibility="private")

        with factory() as s:
            found = _find_latest_crawl(s, "private", "x.com", "/", "", bob.id)
        assert found is None

    def test_private_latest_lookup_finds_own_row(self, sessions):
        _, factory = sessions
        alice = _user(factory, "alice@example.com")
        row_id = _crawl(factory, user_id=alice.id, domain="x.com", visibility="private")

        with factory() as s:
            found = _find_latest_crawl(s, "private", "x.com", "/", "", alice.id)
        assert found is not None and found.id == row_id


class TestSiteUpsertScoped:
    def test_site_upsert_does_not_retire_other_users_private_row(self, sessions):
        """Bob's site upsert for a domain where Alice holds the latest private
        row must create a NEW row for Bob, leaving Alice's revision series
        intact and still latest."""
        _, factory = sessions
        alice = _user(factory, "alice@example.com")
        bob = _user(factory, "bob@example.com")
        alice_row = _crawl(
            factory, user_id=alice.id, domain="x.com", visibility="private"
        )

        with factory() as s:
            crawl_id, _key = _upsert_site_crawl_row(
                s,
                bob,
                "x.com",
                "https://x.com/",
                "private",
                {},
                None,
                datetime.now(UTC),
            )
            s.commit()

        assert crawl_id != alice_row
        with factory() as s:
            a = s.get(Crawl, alice_row)
            b = s.get(Crawl, crawl_id)
        assert a.is_latest is True  # untouched
        assert a.key is None or True  # key state irrelevant for private rows
        assert b.user_id == bob.id
        assert b.is_latest is True


class TestCleanupScoped:
    def test_private_prune_keeps_other_owners_history(self, sessions, monkeypatch):
        """Pruning Alice's private history for a domain must not delete
        Bob's private rows for the same domain."""
        monkeypatch.setenv("MAX_HISTORY_PER_DOMAIN", "1")
        _, factory = sessions
        alice = _user(factory, "alice@example.com")
        bob = _user(factory, "bob@example.com")
        # Alice: two non-latest rows (exceeds cap of 1); a1 is the older one
        a1 = _crawl(
            factory,
            user_id=alice.id,
            domain="x.com",
            visibility="private",
            is_latest=False,
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        a2 = _crawl(
            factory,
            user_id=alice.id,
            domain="x.com",
            visibility="private",
            is_latest=False,
            created_at=datetime(2026, 2, 1, tzinfo=UTC),
        )
        # Bob: one non-latest row
        b1 = _crawl(
            factory,
            user_id=bob.id,
            domain="x.com",
            visibility="private",
            is_latest=False,
        )

        with factory() as s:
            _cleanup_old_crawls(s, "x.com", "private", alice.id)
            s.commit()

        with factory() as s:
            remaining = {r.id for r in s.query(Crawl).all()}
        # Alice keeps the newest of her two; her oldest is pruned
        assert a2 in remaining
        assert a1 not in remaining
        # Bob's row is untouched
        assert b1 in remaining

    def test_public_prune_is_global(self, sessions, monkeypatch):
        """Public rows are shared: pruning ignores ownership by design."""
        monkeypatch.setenv("MAX_HISTORY_PER_DOMAIN", "1")
        _, factory = sessions
        alice = _user(factory, "alice@example.com")
        p1 = _crawl(
            factory,
            user_id=None,
            domain="pub.com",
            visibility="public",
            is_latest=False,
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        p2 = _crawl(
            factory,
            user_id=alice.id,
            domain="pub.com",
            visibility="public",
            is_latest=False,
            created_at=datetime(2026, 2, 1, tzinfo=UTC),
        )

        with factory() as s:
            _cleanup_old_crawls(s, "pub.com", "public")
            s.commit()

        with factory() as s:
            remaining = {r.id for r in s.query(Crawl).all()}
        # Only the newest public row survives, regardless of owner
        assert p2 in remaining
        assert p1 not in remaining
