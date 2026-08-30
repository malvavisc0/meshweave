"""Tests for the analysis revision diff builders.

Pure-logic unit tests for ``webapp/utils/diff.py`` against an in-memory
sqlite DB (seeding follows tests/test_stale_score_clearing.py). No FastAPI
app or TestClient — route behavior is covered by the webapp's own
environment; these tests stay CLI-dependency-free.
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import types
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

os.environ.setdefault("SQLITE_PATH", os.path.join(tempfile.mkdtemp(), "diff.db"))

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

from fastapi import HTTPException  # noqa: E402

import webapp.routers.analysis as analysis_mod  # noqa: E402
import webapp.utils.diff as diff_mod  # noqa: E402
from webapp.models import Base, Crawl, ScoreSnapshot  # noqa: E402


@pytest.fixture
def env(monkeypatch):
    """In-memory DB + patched diff module sessions + current user id."""
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

    monkeypatch.setattr(diff_mod, "get_session", get_session)
    return SimpleNamespace(
        get_session=get_session,
        current_user_id=str(uuid.uuid4()),
    )


def _make_crawl(
    s,
    *,
    user_id: str,
    domain: str = "example.com",
    path: str = "/p",
    query: str = "",
    visibility: str = "private",
    status: str = "succeeded",
    created_at: datetime | None = None,
    is_latest: bool = True,
    crawl_params: dict | None = None,
    key: str | None = None,
    aeo_score: float | None = None,
    geo_score: float | None = None,
    scoring_version: str = "1.0",
    has_manual_input: bool = False,
    payload: dict | None = None,
    listed: bool = True,
) -> Crawl:
    now = created_at or datetime.now(UTC)
    url = f"https://{domain}{path}"
    row = Crawl(
        id=str(uuid.uuid4()),
        url=url,
        domain=domain,
        path=path,
        query=query,
        canonical_url=url,
        key=key,
        visibility=visibility,
        status=status,
        payload_json=payload,
        user_id=user_id,
        crawl_params=crawl_params,
        aeo_score=aeo_score,
        geo_score=geo_score,
        scoring_version=scoring_version,
        has_manual_input=has_manual_input,
        listed=listed,
        is_latest=is_latest,
        created_at=now,
        updated_at=now,
    )
    s.add(row)
    s.flush()
    return row


def _make_snapshot(
    s,
    crawl_id: str,
    *,
    score_json: dict | None = None,
    aeo_score: float | None = None,
    geo_score: float | None = None,
    scoring_version: str = "1.0",
    has_manual_input: bool = False,
    domain: str = "example.com",
) -> ScoreSnapshot:
    snap = ScoreSnapshot(
        id=str(uuid.uuid4()),
        crawl_id=crawl_id,
        domain=domain,
        aeo_score=aeo_score,
        geo_score=geo_score,
        score_json=score_json or {},
        scoring_version=scoring_version,
        has_manual_input=has_manual_input,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    s.add(snap)
    s.flush()
    return snap


# ---------------------------------------------------------------------------
# find_previous_revision
# ---------------------------------------------------------------------------


class TestFindPreviousRevision:
    def test_returns_newest_succeeded_predecessor(self, env):
        t1 = datetime(2024, 1, 1, tzinfo=UTC)
        t2 = datetime(2024, 1, 2, tzinfo=UTC)
        t3 = datetime(2024, 1, 3, tzinfo=UTC)
        t_base = datetime(2024, 1, 4, tzinfo=UTC)
        other_user = str(uuid.uuid4())
        with env.get_session() as s:
            _make_crawl(
                s, user_id=env.current_user_id, status="succeeded", created_at=t1
            )
            _make_crawl(s, user_id=env.current_user_id, status="failed", created_at=t2)
            pred3 = _make_crawl(
                s, user_id=env.current_user_id, status="succeeded", created_at=t3
            )
            _make_crawl(s, user_id=other_user, status="succeeded", created_at=t3)
            _make_crawl(
                s,
                user_id=env.current_user_id,
                path="/other",
                status="succeeded",
                created_at=t3,
            )
            base = _make_crawl(
                s, user_id=env.current_user_id, status="succeeded", created_at=t_base
            )

        result = diff_mod.find_previous_revision(base)

        assert result is not None
        assert result.id == pred3.id

    def test_previous_revision_snapshot_is_eager_loaded(self, env):
        """The returned row's snapshot is usable outside the query session."""
        t1 = datetime(2024, 1, 1, tzinfo=UTC)
        t2 = datetime(2024, 1, 2, tzinfo=UTC)
        with env.get_session() as s:
            pred = _make_crawl(
                s, user_id=env.current_user_id, status="succeeded", created_at=t1
            )
            _make_snapshot(s, pred.id, score_json={"aeo": {"composite": 40.0}})
            base = _make_crawl(
                s, user_id=env.current_user_id, status="succeeded", created_at=t2
            )

        result = diff_mod.find_previous_revision(base)

        assert result is not None
        assert result.id == pred.id
        assert result.score_snapshot is not None
        assert result.score_snapshot.score_json["aeo"]["composite"] == 40.0

    def test_single_run_returns_none(self, env):
        with env.get_session() as s:
            base = _make_crawl(s, user_id=env.current_user_id, status="succeeded")
        assert diff_mod.find_previous_revision(base) is None

    def test_ignores_other_users_and_path(self, env):
        t = datetime(2024, 1, 1, tzinfo=UTC)
        other_user = str(uuid.uuid4())
        with env.get_session() as s:
            _make_crawl(s, user_id=other_user, status="succeeded", created_at=t)
            _make_crawl(
                s,
                user_id=env.current_user_id,
                path="/different",
                status="succeeded",
                created_at=t,
            )
            base = _make_crawl(
                s,
                user_id=env.current_user_id,
                status="succeeded",
                created_at=datetime(2024, 1, 2, tzinfo=UTC),
            )
        assert diff_mod.find_previous_revision(base) is None

    def test_ignores_opposite_scope(self, env):
        """A site-scope run is not the previous revision of a page run."""
        t1 = datetime(2024, 1, 1, tzinfo=UTC)
        t2 = datetime(2024, 1, 2, tzinfo=UTC)
        with env.get_session() as s:
            _make_crawl(
                s,
                user_id=env.current_user_id,
                status="succeeded",
                created_at=t1,
                crawl_params={"max_pages": 50},
            )
            base = _make_crawl(
                s,
                user_id=env.current_user_id,
                status="succeeded",
                created_at=t2,
            )
        assert diff_mod.find_previous_revision(base) is None

    def test_matches_same_scope(self, env):
        t1 = datetime(2024, 1, 1, tzinfo=UTC)
        t2 = datetime(2024, 1, 2, tzinfo=UTC)
        with env.get_session() as s:
            pred = _make_crawl(
                s,
                user_id=env.current_user_id,
                status="succeeded",
                created_at=t1,
                crawl_params={"max_pages": 50},
            )
            base = _make_crawl(
                s,
                user_id=env.current_user_id,
                status="succeeded",
                created_at=t2,
                crawl_params={},
            )
        result = diff_mod.find_previous_revision(base)
        assert result is not None
        assert result.id == pred.id


# ---------------------------------------------------------------------------
# list_revision_series
# ---------------------------------------------------------------------------


class TestListRevisionSeries:
    def test_only_succeeded_cross_visibility_oldest_first(self, env):
        t1 = datetime(2024, 1, 1, tzinfo=UTC)
        t2 = datetime(2024, 1, 2, tzinfo=UTC)
        t3 = datetime(2024, 1, 3, tzinfo=UTC)
        with env.get_session() as s:
            pub = _make_crawl(
                s,
                user_id=env.current_user_id,
                visibility="public",
                status="succeeded",
                created_at=t1,
            )
            failed = _make_crawl(
                s,
                user_id=env.current_user_id,
                visibility="private",
                status="failed",
                created_at=t2,
            )
            priv = _make_crawl(
                s,
                user_id=env.current_user_id,
                visibility="private",
                status="succeeded",
                created_at=t3,
            )
            base = _make_crawl(
                s,
                user_id=env.current_user_id,
                visibility="private",
                status="succeeded",
                created_at=datetime(2024, 1, 4, tzinfo=UTC),
            )

        series = diff_mod.list_revision_series(base)

        ids = [r["id"] for r in series]
        assert ids == [pub.id, priv.id, base.id]
        assert failed.id not in ids
        assert [r["visibility"] for r in series] == ["public", "private", "private"]
        assert all(r["scope"] == "page" for r in series)

    def test_excludes_opposite_scope(self, env):
        """Site crawls never appear in a page run's series (and vice versa)."""
        t1 = datetime(2024, 1, 1, tzinfo=UTC)
        t2 = datetime(2024, 1, 2, tzinfo=UTC)
        with env.get_session() as s:
            site = _make_crawl(
                s,
                user_id=env.current_user_id,
                status="succeeded",
                created_at=t1,
                crawl_params={"max_pages": 50},
            )
            page = _make_crawl(
                s,
                user_id=env.current_user_id,
                status="succeeded",
                created_at=t2,
            )
        page_series = diff_mod.list_revision_series(page)
        site_series = diff_mod.list_revision_series(site)
        assert [r["id"] for r in page_series] == [page.id]
        assert [r["id"] for r in site_series] == [site.id]


# ---------------------------------------------------------------------------
# build_score_diff
# ---------------------------------------------------------------------------


class TestBuildScoreDiff:
    def test_factor_statuses_and_deltas(self, env):
        old_score_json = {
            "aeo": {
                "composite": 50.0,
                "factors": {
                    "schema": {"score": 40.0},
                    "content_structure": {"score": 60.0},
                    "freshness": {"score": 80.0},
                },
            }
        }
        new_score_json = {
            "aeo": {
                "composite": 70.0,
                "factors": {
                    "schema": {"score": 60.0},
                    "content_structure": {"score": 50.0},
                    "freshness": {"score": None},
                    "query_match": {"score": 70.0},
                },
            }
        }
        with env.get_session() as s:
            old = _make_crawl(s, user_id=env.current_user_id)
            new = _make_crawl(s, user_id=env.current_user_id)
            _make_snapshot(s, old.id, score_json=old_score_json, aeo_score=50.0)
            _make_snapshot(s, new.id, score_json=new_score_json, aeo_score=70.0)
            old_ss = s.get(Crawl, old.id).score_snapshot
            new_ss = s.get(Crawl, new.id).score_snapshot

        diff = diff_mod.build_score_diff(old_ss, new_ss, old, new)
        by_key = {f["key"]: f for f in diff["factors"]}

        assert by_key["schema"]["status_change"] == "improved"
        assert by_key["schema"]["delta"] == 20.0
        assert by_key["content_structure"]["status_change"] == "regressed"
        assert by_key["content_structure"]["delta"] == -10.0
        assert by_key["freshness"]["status_change"] == "disappeared"
        assert by_key["freshness"]["old"] == 80.0
        assert by_key["freshness"]["new"] is None
        assert by_key["query_match"]["status_change"] == "appeared"
        assert by_key["query_match"]["new"] == 70.0
        assert diff["composites"]["aeo"] == {"old": 50.0, "new": 70.0, "delta": 20.0}

    def test_missing_snapshot_falls_back_to_row_scores(self, env):
        with env.get_session() as s:
            old = _make_crawl(s, user_id=env.current_user_id, aeo_score=30.0)
            new = _make_crawl(s, user_id=env.current_user_id, aeo_score=50.0)
            _make_snapshot(s, new.id, score_json={"aeo": {"composite": 999.0}})
            old_ss = s.get(Crawl, old.id).score_snapshot
            new_ss = s.get(Crawl, new.id).score_snapshot

        diff = diff_mod.build_score_diff(old_ss, new_ss, old, new)
        # old snapshot missing -> composite falls back to row.aeo_score (30)
        assert diff["composites"]["aeo"]["old"] == 30.0
        assert diff["composites"]["aeo"]["new"] == 999.0
        assert diff["composites"]["aeo"]["delta"] == 969.0
        assert diff["factors"] == []


# ---------------------------------------------------------------------------
# build_findings_diff
# ---------------------------------------------------------------------------


class TestBuildFindingsDiff:
    def test_resolved_new_and_factor_identity(self, env):
        old_recs = [
            {"factor": "schema", "title": "Add FAQ schema", "priority": "high"},
            {
                "factor": "content_structure",
                "title": "Enrich 3 thin page(s)",
                "priority": "medium",
            },
        ]
        new_recs = [
            {"factor": "schema", "title": "Add FAQ schema", "priority": "high"},
            {
                "factor": "content_structure",
                "title": "Enrich 5 thin page(s)",
                "priority": "medium",
            },
            {"factor": "eeat", "title": "Add Organization schema", "priority": "high"},
        ]
        with env.get_session() as s:
            old = _make_crawl(s, user_id=env.current_user_id)
            new = _make_crawl(s, user_id=env.current_user_id)
            _make_snapshot(s, old.id, score_json={"recommendations": old_recs})
            _make_snapshot(s, new.id, score_json={"recommendations": new_recs})
            old_ss = s.get(Crawl, old.id).score_snapshot
            new_ss = s.get(Crawl, new.id).score_snapshot

        diff = diff_mod.build_findings_diff(old_ss, new_ss)

        assert diff["resolved"] == []
        assert [r["factor"] for r in diff["new"]] == ["eeat"]
        # Factor-bearing recs with different embedded counts still match.
        assert diff["unchanged_count"] == 2

    def test_resolved_by_exact_title(self, env):
        old_recs = [{"title": "Add a robots.txt file", "priority": "low"}]
        new_recs = [{"title": "Add a sitemap", "priority": "medium"}]
        with env.get_session() as s:
            old = _make_crawl(s, user_id=env.current_user_id)
            new = _make_crawl(s, user_id=env.current_user_id)
            _make_snapshot(s, old.id, score_json={"recommendations": old_recs})
            _make_snapshot(s, new.id, score_json={"recommendations": new_recs})
            old_ss = s.get(Crawl, old.id).score_snapshot
            new_ss = s.get(Crawl, new.id).score_snapshot

        diff = diff_mod.build_findings_diff(old_ss, new_ss)

        assert [r["title"] for r in diff["resolved"]] == ["Add a robots.txt file"]
        assert [r["title"] for r in diff["new"]] == ["Add a sitemap"]


# ---------------------------------------------------------------------------
# build_content_diff
# ---------------------------------------------------------------------------


class TestBuildContentDiff:
    def test_page_scope_markdown_and_counts(self, env):
        old_payload = {
            "scope": "page",
            "page": {"title": "Old Title", "description": "old desc"},
            "markdown": "hello\nworld\n",
            "links": {"internal": ["a"], "external": ["b", "c"]},
            "emails": {"counts": {"total_unique": 1}},
        }
        new_payload = {
            "scope": "page",
            "page": {"title": "New Title", "description": "old desc"},
            "markdown": "hello\nthere\nworld\n",
            "links": {"internal": ["a"], "external": ["b"]},
            "emails": {"counts": {"total_unique": 3}},
        }
        diff = diff_mod.build_content_diff(old_payload, new_payload)

        assert diff["available"] is True
        assert diff["scope"] == "page"
        assert diff["title_desc"]["old"]["title"] == "Old Title"
        assert diff["title_desc"]["new"]["title"] == "New Title"
        assert diff["counts"]["external_links_count"] == {
            "old": 2,
            "new": 1,
            "delta": -1,
        }
        assert diff["counts"]["emails_count"] == {"old": 1, "new": 3, "delta": 2}
        assert "+there" in diff["markdown_diff_html"]
        assert diff["markdown_truncated"] is False

    def test_markdown_cap_truncates(self, env):
        old_payload = {"markdown": ""}
        new_payload = {"markdown": "x" * (diff_mod.MAX_MARKDOWN_BYTES + 10)}
        diff = diff_mod.build_content_diff(old_payload, new_payload)

        assert diff["markdown_truncated"] is True

    def test_site_scope_page_list(self, env):
        old_payload = {"scope": "site", "pages": [{"url": "https://d.com/a"}]}
        new_payload = {
            "scope": "site",
            "pages": [{"url": "https://d.com/a"}, {"url": "https://d.com/b"}],
        }
        diff = diff_mod.build_content_diff(old_payload, new_payload)

        assert diff["scope"] == "site"
        assert diff["page_list"]["added"] == ["https://d.com/b"]
        assert diff["page_list"]["removed"] == []
        assert diff["markdown_diff_html"] == ""

    def test_none_payloads(self, env):
        diff = diff_mod.build_content_diff(None, None)
        assert diff["available"] is False
        assert diff["counts"]["content_pages_count"] == {"old": 0, "new": 0, "delta": 0}
        assert diff["markdown_diff_html"] == ""


# ---------------------------------------------------------------------------
# build_comparison_notes
# ---------------------------------------------------------------------------


class TestBuildComparisonNotes:
    def test_scoring_version_and_manual_input_banners(self, env):
        with env.get_session() as s:
            old = _make_crawl(
                s,
                user_id=env.current_user_id,
                scoring_version="1.0",
                has_manual_input=False,
            )
            new = _make_crawl(
                s,
                user_id=env.current_user_id,
                scoring_version="2.0",
                has_manual_input=True,
            )
            diff = diff_mod.build_comparison_notes(old, new, None, None)

        assert len(diff) == 2
        assert any("scoring versions" in note for note in diff)
        assert any("manual inputs" in note for note in diff)

    def test_matching_meta_has_no_notes(self, env):
        with env.get_session() as s:
            old = _make_crawl(
                s,
                user_id=env.current_user_id,
                scoring_version="1.0",
                has_manual_input=False,
            )
            new = _make_crawl(
                s,
                user_id=env.current_user_id,
                scoring_version="1.0",
                has_manual_input=False,
            )
            assert diff_mod.build_comparison_notes(old, new, None, None) == []


# ---------------------------------------------------------------------------
# _resolve_vs_row (route helper; require_ownership is monkeypatched)
# ---------------------------------------------------------------------------


class TestResolveVsRow:
    """The ``?vs=`` row must be owned, succeeded, and in the same series."""

    @staticmethod
    def _patch_ownership(monkeypatch, row=None, exc: HTTPException | None = None):
        async def fake_require_ownership(request, crawl_id):
            if exc is not None:
                raise exc
            return row

        monkeypatch.setattr(analysis_mod, "require_ownership", fake_require_ownership)

    def test_returns_matching_succeeded_row(self, env, monkeypatch):
        with env.get_session() as s:
            base = _make_crawl(s, user_id=env.current_user_id)
            vs = _make_crawl(s, user_id=env.current_user_id)
        self._patch_ownership(monkeypatch, row=vs)
        result = asyncio.run(analysis_mod._resolve_vs_row(None, vs.id, base))
        assert result is vs

    @pytest.mark.parametrize("status", ["pending", "running", "failed", "cancelled"])
    def test_rejects_non_succeeded_status(self, env, monkeypatch, status):
        """Regression: a pending/running/failed vs row rendered a garbage diff."""
        with env.get_session() as s:
            base = _make_crawl(s, user_id=env.current_user_id)
            vs = _make_crawl(s, user_id=env.current_user_id, status=status)
        self._patch_ownership(monkeypatch, row=vs)
        assert asyncio.run(analysis_mod._resolve_vs_row(None, vs.id, base)) is None

    def test_rejects_scope_mismatch(self, env, monkeypatch):
        with env.get_session() as s:
            base = _make_crawl(s, user_id=env.current_user_id)
            vs = _make_crawl(
                s, user_id=env.current_user_id, crawl_params={"max_pages": 10}
            )
        self._patch_ownership(monkeypatch, row=vs)
        assert asyncio.run(analysis_mod._resolve_vs_row(None, vs.id, base)) is None

    @pytest.mark.parametrize("status_code", [401, 403, 404])
    def test_collapses_auth_and_missing_to_none(self, env, monkeypatch, status_code):
        """Regression: 404 from require_ownership used to propagate, breaking
        the uniform-collapse invariant (existence oracle via error detail)."""
        with env.get_session() as s:
            base = _make_crawl(s, user_id=env.current_user_id)
        self._patch_ownership(monkeypatch, exc=HTTPException(status_code=status_code))
        assert asyncio.run(analysis_mod._resolve_vs_row(None, "whatever", base)) is None
