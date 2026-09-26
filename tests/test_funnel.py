"""Tests for the funnel service (webapp.services.funnel).

Covers the load-bearing invariants from the plan: emit transaction
atomicity, exactly-once agency counting under concurrency, stage
monotonicity, save-flow rejection cases, and the dismissal resurface
window.
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

os.environ.setdefault("SQLITE_PATH", os.path.join(tempfile.mkdtemp(), "funnel.db"))

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
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from meshweave.scoring.composite import SCORING_VERSION  # noqa: E402
from webapp.models import (  # noqa: E402
    Base,
    Crawl,
    FunnelEvent,
    FunnelState,
    User,
)
from webapp.services import funnel as funnel_svc  # noqa: E402


@pytest.fixture
def engine():
    engine = create_engine(
        "sqlite://",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def factory(engine):
    return sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        future=True,
        expire_on_commit=False,
    )


@contextmanager
def _session_scope(factory):
    s: Session = factory()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


@pytest.fixture
def user(factory) -> str:
    with _session_scope(factory) as s:
        uid = str(uuid.uuid4())
        s.add(User(id=uid, email="u@example.com", provider_id=uid))
        return uid


def _make_crawl(
    factory,
    *,
    user_id: str | None = None,
    anonymous_user_id: str | None = None,
    domain: str = "example.com",
    created_at: datetime | None = None,
) -> str:
    with _session_scope(factory) as s:
        cid = str(uuid.uuid4())
        s.add(
            Crawl(
                id=cid,
                url=f"https://{domain}/",
                domain=domain,
                path="/",
                query="",
                canonical_url=f"https://{domain}/",
                visibility="public",
                status="succeeded",
                user_id=user_id,
                anonymous_user_id=anonymous_user_id,
                created_at=created_at or datetime.now(UTC),
                updated_at=datetime.now(UTC),
                scoring_version=SCORING_VERSION,
                is_latest=True,
            )
        )
        return cid


def _events(factory, user_id: str) -> list[FunnelEvent]:
    with _session_scope(factory) as s:
        rows = (
            s.query(FunnelEvent)
            .filter(FunnelEvent.user_id == user_id)
            .order_by(FunnelEvent.created_at.asc())
            .all()
        )
        s.expunge_all()
        return rows


# --------------------------------------------------------------------------
# emit: transaction atomicity and anonymous no-op
# --------------------------------------------------------------------------


class TestEmitAtomicity:
    def test_rollback_leaves_no_orphan_events(self, factory, user):
        with pytest.raises(RuntimeError):
            with _session_scope(factory) as s:
                funnel_svc.emit(
                    s, user, funnel_svc.EVENT_ANALYSIS_FAILED, domain="x.com"
                )
                raise RuntimeError("boom")
        assert _events(factory, user) == []

    def test_emit_commits_with_state_change(self, factory, user):
        with _session_scope(factory) as s:
            funnel_svc.emit(
                s,
                user,
                funnel_svc.EVENT_ANALYSIS_COMPLETED,
                domain="example.com",
                scope="page",
            )
        events = _events(factory, user)
        assert [e.event_type for e in events] == ["analysis_completed"]
        assert events[0].payload == {"scope": "page"}

    def test_anonymous_emit_is_a_noop(self, factory):
        with _session_scope(factory) as s:
            funnel_svc.emit(s, None, funnel_svc.EVENT_ANALYSIS_COMPLETED, domain="x")
        with _session_scope(factory) as s:
            assert s.query(FunnelEvent).count() == 0
            assert s.query(FunnelState).count() == 0


# --------------------------------------------------------------------------
# Stage transitions are monotonic
# --------------------------------------------------------------------------


class TestStageTransitions:
    def test_stage_moves_forward(self, factory, user):
        with _session_scope(factory) as s:
            funnel_svc.emit(
                s, user, funnel_svc.EVENT_API_KEY_FIRST_USED, stage="api_consumer"
            )
        with _session_scope(factory) as s:
            assert s.get(FunnelState, user).stage == "api_consumer"

    def test_stage_never_demotes(self, factory, user):
        with _session_scope(factory) as s:
            funnel_svc.emit(
                s, user, funnel_svc.EVENT_API_KEY_FIRST_USED, stage="api_consumer"
            )
        with _session_scope(factory) as s:
            funnel_svc.emit(s, user, "analysis_saved", stage="registered")
        with _session_scope(factory) as s:
            assert s.get(FunnelState, user).stage == "api_consumer"

    def test_key_creation_alone_does_not_promote_stage(self, factory, user):
        """api_consumer starts at first use, not at key creation."""
        with _session_scope(factory) as s:
            funnel_svc.emit(s, user, funnel_svc.EVENT_API_KEY_CREATED, key_id="k1")
        with _session_scope(factory) as s:
            assert s.get(FunnelState, user).stage == "registered"

    def test_contact_mailto_moves_to_inquiry(self, factory, user):
        with _session_scope(factory) as s:
            funnel_svc.emit(
                s,
                user,
                funnel_svc.EVENT_CONTACT_MAILTO_CLICKED,
                surface="dashboard",
                stage="inquiry",
            )
        with _session_scope(factory) as s:
            assert s.get(FunnelState, user).stage == "inquiry"

    def test_customer_is_terminal(self, factory, user, monkeypatch):
        monkeypatch.setattr(funnel_svc, "get_session", _session_scope_factory(factory))
        assert funnel_svc.mark_customer(user, source="services") is True
        # Any later event cannot pull a customer back down.
        with _session_scope(factory) as s:
            funnel_svc.emit(
                s,
                user,
                funnel_svc.EVENT_API_KEY_CREATED,
                stage="api_consumer",
            )
        with _session_scope(factory) as s:
            assert s.get(FunnelState, user).stage == "customer"

    def test_mark_customer_exactly_once(self, factory, user, monkeypatch):
        monkeypatch.setattr(funnel_svc, "get_session", _session_scope_factory(factory))
        assert funnel_svc.mark_customer(user, source="api") is True
        assert funnel_svc.mark_customer(user, source="api") is False
        with _session_scope(factory) as s:
            events = (
                s.query(FunnelEvent)
                .filter(
                    FunnelEvent.user_id == user,
                    FunnelEvent.event_type == "became_customer",
                )
                .all()
            )
            assert len(events) == 1
            assert events[0].payload["source"] == "api"

    def test_mark_customer_rejects_bad_source(self, factory, user, monkeypatch):
        monkeypatch.setattr(funnel_svc, "get_session", _session_scope_factory(factory))
        with pytest.raises(ValueError):
            funnel_svc.mark_customer(user, source="bribe")


# --------------------------------------------------------------------------
# Gate offers: once per user per feature
# --------------------------------------------------------------------------


class TestGateTracking:
    def test_gate_hit_emits_once_per_user(self, factory, user, monkeypatch):
        monkeypatch.setattr(funnel_svc, "get_session", _session_scope_factory(factory))
        funnel_svc.emit_gated_feature_hit(user, feature="bulk_gate", surface="result")
        funnel_svc.emit_gated_feature_hit(user, feature="bulk_gate", surface="result")
        funnel_svc.emit_gated_feature_hit(
            user, feature="bulk_gate", surface="dashboard"
        )
        with _session_scope(factory) as s:
            events = (
                s.query(FunnelEvent)
                .filter(
                    FunnelEvent.user_id == user,
                    FunnelEvent.event_type == "gated_feature_hit",
                )
                .all()
            )
            assert len(events) == 1
            assert events[0].payload["feature"] == "bulk_gate"

    def test_gate_taken_emits_once_per_user(self, factory, user, monkeypatch):
        monkeypatch.setattr(funnel_svc, "get_session", _session_scope_factory(factory))
        funnel_svc.record_gate_taken(user, nudge="bulk_gate")
        funnel_svc.record_gate_taken(user, nudge="bulk_gate")
        with _session_scope(factory) as s:
            events = (
                s.query(FunnelEvent)
                .filter(
                    FunnelEvent.user_id == user,
                    FunnelEvent.event_type == "gated_feature_taken",
                )
                .all()
            )
            assert len(events) == 1

    def test_gate_hit_and_taken_are_distinct_events(self, factory, user, monkeypatch):
        monkeypatch.setattr(funnel_svc, "get_session", _session_scope_factory(factory))
        funnel_svc.emit_gated_feature_hit(
            user, feature="save_analysis", surface="result"
        )
        funnel_svc.record_gate_taken(user, nudge="save_analysis")
        with _session_scope(factory) as s:
            types = sorted(
                e.event_type
                for e in s.query(FunnelEvent).filter(FunnelEvent.user_id == user).all()
            )
            assert types == ["gated_feature_hit", "gated_feature_taken"]


# --------------------------------------------------------------------------
# Agency detection: exactly once per domain, exactly one upgrade
# --------------------------------------------------------------------------


class TestAgencyCounting:
    def test_increment_once_per_domain(self, factory, user):
        for _ in range(3):
            with _session_scope(factory) as s:
                funnel_svc.emit(
                    s, user, funnel_svc.EVENT_ANALYSIS_COMPLETED, domain="a.com"
                )
        with _session_scope(factory) as s:
            state = s.get(FunnelState, user)
            assert state.distinct_domains == 1
            assert state.analyses_count == 3
            assert state.segment == "standard"

    def test_threshold_trips_segment_upgraded_exactly_once(self, factory, user):
        domains = [f"d{i}.com" for i in range(funnel_svc.AGENCY_DOMAIN_THRESHOLD + 1)]
        for domain in domains:
            with _session_scope(factory) as s:
                funnel_svc.emit(
                    s, user, funnel_svc.EVENT_ANALYSIS_COMPLETED, domain=domain
                )
        events = _events(factory, user)
        upgrades = [e for e in events if e.event_type == "segment_upgraded"]
        assert len(upgrades) == 1
        with _session_scope(factory) as s:
            assert s.get(FunnelState, user).segment == "agency"

    def test_concurrent_completions_do_not_double_count(self, factory, user):
        """Two sessions inserting the same domain serialize to one insert."""
        with _session_scope(factory) as s1, _session_scope(factory) as s2:
            funnel_svc.emit(
                s1, user, funnel_svc.EVENT_ANALYSIS_COMPLETED, domain="race.com"
            )
            # Session 2 sees the row only after s1 commits; the ON CONFLICT
            # guard plus the unique constraint keep the count exact.
            funnel_svc.emit(
                s2, user, funnel_svc.EVENT_ANALYSIS_COMPLETED, domain="race.com"
            )
        with _session_scope(factory) as s:
            assert s.get(FunnelState, user).distinct_domains == 1

    def test_missing_domain_skips_agency_counting(self, factory, user):
        with _session_scope(factory) as s:
            funnel_svc.emit(s, user, funnel_svc.EVENT_ANALYSIS_COMPLETED)
        with _session_scope(factory) as s:
            state = s.get(FunnelState, user)
            assert state.distinct_domains == 0
            assert state.analyses_count == 1


# --------------------------------------------------------------------------
# Save flow: conditional single UPDATE
# --------------------------------------------------------------------------


class TestSaveOwnAnalysis:
    def test_successful_save(self, factory, user):
        anon = f"anon_{uuid.uuid4()}"
        crawl_id = _make_crawl(factory, anonymous_user_id=anon)
        with _session_scope(factory) as s:
            assert funnel_svc.save_own_analysis(s, user, crawl_id, anon) is True
        with _session_scope(factory) as s:
            row = s.get(Crawl, crawl_id)
            assert row.user_id == user
        events = _events(factory, user)
        assert [e.event_type for e in events] == ["analysis_saved"]
        assert events[0].crawl_id == crawl_id
        assert events[0].domain == "example.com"

    def test_rejects_wrong_cookie(self, factory, user):
        crawl_id = _make_crawl(factory, anonymous_user_id="anon_correct")
        with _session_scope(factory) as s:
            assert (
                funnel_svc.save_own_analysis(s, user, crawl_id, "anon_wrong") is False
            )
        with _session_scope(factory) as s:
            assert s.get(Crawl, crawl_id).user_id is None
        assert _events(factory, user) == []

    def test_rejects_already_owned(self, factory, user):
        other = str(uuid.uuid4())
        with _session_scope(factory) as s:
            s.add(User(id=other, email="o@example.com", provider_id=other))
        anon = f"anon_{uuid.uuid4()}"
        crawl_id = _make_crawl(factory, user_id=other, anonymous_user_id=anon)
        with _session_scope(factory) as s:
            assert funnel_svc.save_own_analysis(s, user, crawl_id, anon) is False
        with _session_scope(factory) as s:
            assert s.get(Crawl, crawl_id).user_id == other
        assert _events(factory, user) == []

    def test_rejects_missing_crawl(self, factory, user):
        with _session_scope(factory) as s:
            assert (
                funnel_svc.save_own_analysis(s, user, "missing-id", "anon_x") is False
            )

    def test_rejects_missing_cookie(self, factory, user):
        """A missing cookie must not adopt an anon-id-less ownerless crawl."""
        crawl_id = _make_crawl(factory)
        with _session_scope(factory) as s:
            assert funnel_svc.save_own_analysis(s, user, crawl_id, None) is False
        with _session_scope(factory) as s:
            assert s.get(Crawl, crawl_id).user_id is None
        assert _events(factory, user) == []


# --------------------------------------------------------------------------
# Nudge dismissal and resurface window
# --------------------------------------------------------------------------


class TestNudgeDismissal:
    def test_record_and_resurface_window(self, factory, user, monkeypatch):
        monkeypatch.setattr(funnel_svc, "get_session", _session_scope_factory(factory))
        assert funnel_svc.record_nudge_dismissal(user, "bulk_gate") is True
        with _session_scope(factory) as s:
            state = s.get(FunnelState, user)
            assert "bulk_gate" in (state.dismissed_nudges or {})
        assert funnel_svc.nudge_dismissed(state, "bulk_gate") is True

        # Outside the window, the nudge resurfaces.
        with _session_scope(factory) as s:
            row = s.get(FunnelState, user)
            dismissed = dict(row.dismissed_nudges or {})
            dismissed["bulk_gate"] = (
                datetime.now(UTC) - timedelta(days=funnel_svc.NUDGE_RESURFACE_DAYS + 1)
            ).isoformat()
            row.dismissed_nudges = dismissed
        with _session_scope(factory) as s:
            state = s.get(FunnelState, user)
        assert funnel_svc.nudge_dismissed(state, "bulk_gate") is False

    def test_unknown_nudge_name_rejected(self, factory, user, monkeypatch):
        monkeypatch.setattr(funnel_svc, "get_session", _session_scope_factory(factory))
        assert funnel_svc.record_nudge_dismissal(user, "not_a_nudge") is False


def _session_scope_factory(factory):
    """Return a zero-arg context manager factory matching get_session()."""

    @contextmanager
    def _get_session():
        with _session_scope(factory) as s:
            yield s

    return _get_session
