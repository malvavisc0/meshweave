"""Tests for the re-check loop: since-last-run deltas and predictions."""

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

os.environ.setdefault("SQLITE_PATH", os.path.join(tempfile.mkdtemp(), "recheck.db"))

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

from webapp.models import Base, Crawl, ScoreSnapshot, User  # noqa: E402
from webapp.utils.diff import build_findings_diff, find_previous_revision  # noqa: E402


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

    import webapp.utils.diff as diff_mod

    original = diff_mod.get_session
    diff_mod.get_session = get_session
    yield get_session
    diff_mod.get_session = original


def _seed_series(s, *, aeo_old=50.0, aeo_new=60.0, geo_old=40.0, geo_new=44.0):
    """Two succeeded revisions of the same series with snapshots."""
    user_id = str(uuid.uuid4())
    s.add(User(id=user_id, email="u@example.com", provider_id=user_id))

    def _row(created_at, aeo, geo, aax, recs):
        row = Crawl(
            id=str(uuid.uuid4()),
            url="https://example.com/",
            domain="example.com",
            path="/",
            query="",
            canonical_url="https://example.com/",
            visibility="private",
            status="succeeded",
            payload_json={"page": {}},
            user_id=user_id,
            scoring_version="1.0",
            is_latest=False,
            created_at=created_at,
            updated_at=created_at,
        )
        s.add(row)
        s.add(
            ScoreSnapshot(
                crawl_id=row.id,
                user_id=user_id,
                domain="example.com",
                aeo_score=aeo,
                geo_score=geo,
                aeo_rating="Average",
                geo_rating="Emerging",
                score_json={
                    "aax": {"composite": aax} if aax is not None else {},
                    "recommendations": recs,
                },
                created_at=created_at,
            )
        )
        return row

    old = _row(
        datetime(2026, 8, 1, tzinfo=UTC),
        aeo_old,
        geo_old,
        None,
        [
            {
                "factor": "crawl_access",
                "pillar": "geo",
                "priority": "high",
                "title": "Publish an llms.txt file",
                "expected_points": 3.1,
            }
        ],
    )
    new = _row(datetime(2026, 8, 2, tzinfo=UTC), aeo_new, geo_new, 70.0, [])
    new.is_latest = True
    s.flush()
    return old, new


class TestFindingsDiffPredictions:
    def test_resolved_rec_gets_observed_delta(self, sessions):
        with sessions() as s:
            old, new = _seed_series(s)
            s.refresh(old)
            s.refresh(new)
            old_ss = old.score_snapshot
            new_ss = new.score_snapshot

        diff = build_findings_diff(old_ss, new_ss)
        assert len(diff["resolved"]) == 1
        resolved = diff["resolved"][0]
        # Predicted 3.1 GEO points; observed is the real composite delta.
        assert resolved["expected_points"] == 3.1
        assert resolved["observed_delta"] == 4.0  # 44.0 - 40.0

    def test_unpredicted_rec_gets_no_observed(self, sessions):
        with sessions() as s:
            user_id = str(uuid.uuid4())
            s.add(User(id=user_id, email="u@example.com", provider_id=user_id))
            ts = datetime(2026, 8, 1, tzinfo=UTC)
            row = Crawl(
                id=str(uuid.uuid4()),
                url="https://e.com/",
                domain="e.com",
                path="/",
                query="",
                canonical_url="https://e.com/",
                visibility="private",
                status="succeeded",
                payload_json={},
                user_id=user_id,
                scoring_version="1.0",
                is_latest=True,
                created_at=ts,
                updated_at=ts,
            )
            s.add(row)
            s.add(
                ScoreSnapshot(
                    crawl_id=row.id,
                    user_id=user_id,
                    domain="e.com",
                    aeo_score=10.0,
                    geo_score=10.0,
                    score_json={
                        "recommendations": [
                            {
                                "factor": "schema",
                                "pillar": "aeo",
                                "priority": "high",
                                "title": "Old rec",
                                "expected_points": None,
                            }
                        ],
                    },
                    created_at=ts,
                )
            )
            s.flush()
            ss = row.score_snapshot

        diff = build_findings_diff(ss, None)
        resolved = diff["resolved"][0]
        # Unpredicted recs carry no observed delta at all.
        assert "observed_delta" not in resolved

    def test_no_rec_match_means_no_observed_rows(self, sessions):
        with sessions() as s:
            old, new = _seed_series(s)
            old_ss = old.score_snapshot
            new_ss = new.score_snapshot

        diff = build_findings_diff(old_ss, new_ss)
        assert diff["resolved"][0]["observed_delta"] == 4.0


class TestPreviousRevision:
    def test_find_previous_revision(self, sessions):
        with sessions() as s:
            old, new = _seed_series(s)
            s.refresh(new)
        prev = find_previous_revision(new)
        assert prev is not None and prev.id == old.id

    def test_single_run_has_no_previous(self, sessions):
        with sessions() as s:
            ts = datetime(2026, 8, 1, tzinfo=UTC)
            user_id = str(uuid.uuid4())
            s.add(User(id=user_id, email="u@example.com", provider_id=user_id))
            row = Crawl(
                id=str(uuid.uuid4()),
                url="https://e.com/",
                domain="e.com",
                path="/",
                query="",
                canonical_url="https://e.com/",
                visibility="private",
                status="succeeded",
                payload_json={},
                user_id=user_id,
                scoring_version="1.0",
                is_latest=True,
                created_at=ts,
                updated_at=ts,
            )
            s.add(row)
            s.flush()
            s.refresh(row)
        assert find_previous_revision(row) is None
