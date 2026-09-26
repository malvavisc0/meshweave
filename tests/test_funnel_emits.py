"""Tests for funnel emit points and nudge selection.

Covers: analysis_completed/failed and recheck_completed detection in the
crawl persistence paths (known users only), and nudge selection rules.
"""

from __future__ import annotations

import os
import sys
import tempfile
import types
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest

os.environ.setdefault("SQLITE_PATH", os.path.join(tempfile.mkdtemp(), "emit.db"))

if "prometheus_client" not in sys.modules:
    _fake_prom = types.ModuleType("prometheus_client")

    class _FakeMetric:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def labels(self, *args: object, **kwargs: object) -> _FakeMetric:
            return self

        def inc(self, *args: object, **kwargs: object) -> None:
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

from webapp.models import (  # noqa: E402
    Base,
    Crawl,
    FunnelEvent,
    User,
)
from webapp.services import crawling as crawling_svc  # noqa: E402
from webapp.services import nudges as nudges_svc  # noqa: E402
from webapp.services import site_crawling as site_svc  # noqa: E402


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
    import webapp.utils.diff as diff_mod

    original = db_mod.get_session
    for mod in (crawling_svc, site_svc, diff_mod):
        mod.get_session = get_session
    yield get_session
    for mod in (crawling_svc, site_svc, diff_mod):
        mod.get_session = original


def _seed_user(get_session) -> str:
    uid = str(uuid.uuid4())
    with get_session() as s:
        s.add(User(id=uid, email="u@example.com", provider_id=uid))
    return uid


def _add_crawl(
    get_session,
    *,
    user_id: str | None = None,
    domain: str = "example.com",
    path: str = "/",
    created_at: datetime | None = None,
    status: str = "pending",
) -> str:
    cid = str(uuid.uuid4())
    with get_session() as s:
        s.add(
            Crawl(
                id=cid,
                url=f"https://{domain}{path}",
                domain=domain,
                path=path,
                query="",
                canonical_url=f"https://{domain}{path}",
                visibility="private",
                status=status,
                user_id=user_id,
                created_at=created_at or datetime.now(UTC),
                updated_at=datetime.now(UTC),
                scoring_version="1.0",
                is_latest=True,
            )
        )
    return cid


def _events(get_session, user_id: str) -> list[str]:
    with get_session() as s:
        rows = (
            s.query(FunnelEvent.event_type)
            .filter(FunnelEvent.user_id == user_id)
            .order_by(FunnelEvent.created_at.asc())
            .all()
        )
        return [r[0] for r in rows]


class TestPageCrawlEmits:
    def test_succeeded_known_user_emits_completed(self, sessions):
        user = _seed_user(sessions)
        crawl_id = _add_crawl(sessions, user_id=user)
        assert crawling_svc._persist_succeeded(crawl_id, {"page": {}}) is True
        assert _events(sessions, user) == ["analysis_completed"]

    def test_failed_known_user_emits_failed(self, sessions):
        user = _seed_user(sessions)
        crawl_id = _add_crawl(sessions, user_id=user)
        assert crawling_svc._persist_failed(crawl_id, "boom") is True
        assert _events(sessions, user) == ["analysis_failed"]

    def test_anonymous_crawl_emits_nothing(self, sessions):
        crawl_id = _add_crawl(sessions)
        assert crawling_svc._persist_succeeded(crawl_id, {"page": {}}) is True
        with sessions() as s:
            assert s.query(FunnelEvent).count() == 0

    def test_second_revision_emits_recheck(self, sessions):
        user = _seed_user(sessions)
        _add_crawl(
            sessions,
            user_id=user,
            created_at=datetime.now(UTC) - timedelta(days=1),
            status="succeeded",
        )
        new = _add_crawl(sessions, user_id=user)
        crawling_svc._persist_succeeded(new, {"page": {}})
        assert _events(sessions, user) == [
            "analysis_completed",
            "recheck_completed",
        ]


class TestSiteCrawlEmits:
    def test_succeeded_known_user_emits_completed(self, sessions):
        user = _seed_user(sessions)
        crawl_id = _add_crawl(sessions, user_id=user)
        assert (
            site_svc._persist_task_result(crawl_id, "succeeded", None, {"pages": []})
            is True
        )
        assert _events(sessions, user) == ["analysis_completed"]

    def test_failed_known_user_emits_failed(self, sessions):
        user = _seed_user(sessions)
        crawl_id = _add_crawl(sessions, user_id=user)
        assert site_svc._persist_task_result(crawl_id, "failed", "err", None) is True
        assert _events(sessions, user) == ["analysis_failed"]

    def test_cancelled_emits_nothing(self, sessions):
        user = _seed_user(sessions)
        crawl_id = _add_crawl(sessions, user_id=user)
        site_svc._persist_task_result(
            crawl_id, "cancelled", "cancelled_by_user", {"pages": []}
        )
        assert _events(sessions, user) == []


class TestNudgeSelection:
    def test_result_surface_anonymous_gets_nothing(self):
        assert nudges_svc.select_nudge("result", None) is None

    def test_result_owner_gets_no_save_pitch(self):
        state = types.SimpleNamespace(
            stage="registered", segment="standard", dismissed_nudges={}
        )
        nudge = nudges_svc.select_nudge("result", state, "owner")
        assert nudge is None or nudge["name"] != "save_analysis"

    def test_result_non_owner_offered_save(self):
        state = types.SimpleNamespace(
            stage="registered", segment="standard", dismissed_nudges={}
        )
        nudge = nudges_svc.select_nudge("result", state, "public_non_owner")
        assert nudge is not None and nudge["name"] == "save_analysis"
        assert nudge["cta"]["href"]

    def test_unknown_surface_returns_none(self):
        assert nudges_svc.select_nudge("nowhere", None) is None

    def test_dashboard_surface_offers_bulk_gate(self):
        state = types.SimpleNamespace(
            stage="registered", segment="standard", dismissed_nudges={}
        )
        nudge = nudges_svc.select_nudge("dashboard", state)
        assert nudge is not None and nudge["name"] == "bulk_gate"

    def test_api_consumer_skips_gate_nudges(self, sessions):
        user = _seed_user(sessions)
        from webapp.models import FunnelState

        with sessions() as s:
            s.add(FunnelState(user_id=user, stage="api_consumer"))
        with sessions() as s:
            state = s.get(FunnelState, user)
            s.expunge(state)
        nudge = nudges_svc.select_nudge("dashboard", state, None, False, True)
        assert nudge is not None and nudge["name"] == "first_call_hint"

    def test_api_consumer_with_used_key_gets_no_hint(self):
        state = types.SimpleNamespace(
            stage="api_consumer", segment="standard", dismissed_nudges={}
        )
        assert nudges_svc.select_nudge("dashboard", state, None, True, True) is None

    def test_first_call_hint_carries_example(self):
        state = types.SimpleNamespace(
            stage="api_consumer", segment="standard", dismissed_nudges={}
        )
        nudge = nudges_svc.select_nudge("dashboard", state, None, False, True)
        assert nudge is not None and "curl" in (nudge.get("example") or "")

    def test_services_offer_appears_after_min_analyses(self):
        """Plan A: engaged users without personnel get the services offer."""
        state = types.SimpleNamespace(
            stage="registered",
            segment="standard",
            analyses_count=3,
            dismissed_nudges={},
        )
        nudge = nudges_svc.select_nudge("result", state, "owner")
        assert nudge is not None and nudge["name"] == "services_offer"
        assert nudge["cta"]["href"] == "/contact"

    def test_services_offer_below_threshold_not_shown(self):
        state = types.SimpleNamespace(
            stage="registered",
            segment="standard",
            analyses_count=2,
            dismissed_nudges={},
        )
        nudge = nudges_svc.select_nudge("result", state, "owner")
        assert nudge is None or nudge["name"] != "services_offer"

    def test_services_offer_suppressed_after_inquiry(self):
        """A user who already contacted us is not re-pitched."""
        state = types.SimpleNamespace(
            stage="inquiry",
            segment="standard",
            analyses_count=5,
            dismissed_nudges={},
        )
        nudge = nudges_svc.select_nudge("result", state, "owner")
        assert nudge is None or nudge["name"] != "services_offer"
